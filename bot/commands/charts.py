"""
Chart commands for visualizing member fan progression
"""
import calendar
import discord
from discord import app_commands
from discord.ext import commands
import io
import math
import logging
from datetime import date, datetime
import pytz
import aiohttp

from models import Club, QuotaHistory, QuotaRequirement
from scrapers import UmaMoeAPIScraper
from utils.timezone_helper import resolve_timezone

UMAMOE_API_URL = "https://uma.moe/api/v4/circles"


async def _fetch_previous_month_totals(circle_id: str, year: int, month: int) -> list[dict]:
    """
    Fetch last month's final fan totals for each member directly from the API.

    Returns a list of dicts sorted by fans desc:
        {name, fans, joined_mid_month}
    where `fans` is the member's total fans earned that month and
    `joined_mid_month` is True if they weren't in the club on day 1.
    """
    params = {"circle_id": circle_id, "year": year, "month": month}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            UMAMOE_API_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                raise ValueError(f"Uma.moe API returned HTTP {resp.status}")
            data = await resp.json()

    members = data.get("members", [])
    results = []

    for m in members:
        name = m.get("trainer_name")
        lifetime_fans: list[int] = m.get("daily_fans", [])
        if not name or not lifetime_fans:
            continue

        # Find first and last non-zero values
        starting_fans = None
        final_fans = None
        joined_mid_month = False

        for i, val in enumerate(lifetime_fans):
            if val > 0:
                if starting_fans is None:
                    starting_fans = val
                    joined_mid_month = (i > 0)
                final_fans = val  # keep updating to get the last non-zero

        if starting_fans is None or final_fans is None:
            continue  # never active this month

        results.append({
            "name": name,
            "fans": final_fans - starting_fans,
            "joined_mid_month": joined_mid_month,
        })

    results.sort(key=lambda x: x["fans"], reverse=True)
    return results

logger = logging.getLogger(__name__)


async def _fetch_via_scraper(circle_id: str) -> tuple[dict[str, dict], int, int, int]:
    """
    Fetch full-month fan progression by reusing UmaMoeAPIScraper.

    Returns (member_data, current_day, fetched_year, fetched_month) where
    member_data maps trainer_name -> {dates: [str], fans: [int]} using
    dd.mm date strings and monthly-cumulative fan values.
    """
    scraper = UmaMoeAPIScraper(circle_id)
    parsed_data = await scraper.scrape()

    current_day = scraper.current_day_count
    year = scraper._fetched_year
    month = scraper._fetched_month

    member_data: dict[str, dict] = {}
    for data in parsed_data.values():
        name = data["name"]
        join_day = data["join_day"]
        fans_array: list[int] = data["fans"]  # monthly cumulative, index 0 = day 1

        dates: list[str] = []
        fans: list[int] = []
        for day_idx, monthly_val in enumerate(fans_array):
            day_num = day_idx + 1
            if day_num < join_day:
                continue  # skip pre-join zeros
            dates.append(date(year, month, day_num).strftime("%d.%m"))
            fans.append(monthly_val)

        if dates:
            member_data[name] = {"dates": dates, "fans": fans}

    return member_data, current_day, year, month


def _build_chart(member_data: dict[str, dict]) -> bytes:
    """Render the Plotly chart and return PNG bytes."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for name, data in member_data.items():
        fig.add_trace(go.Scatter(
            x=data["dates"],
            y=data["fans"],
            mode="lines",
            name=name,
            line=dict(width=2),
            hovertemplate=f"<b>{name}</b><br>%{{x}}<br>%{{y:,.0f}} fans<extra></extra>",
        ))

    all_fans = [v for d in member_data.values() for v in d["fans"]]
    max_val = max(all_fans) if all_fans else 1_000_000

    # Compute clean Y-axis ticks
    raw_step = max_val / 8
    magnitude = 10 ** math.floor(math.log10(raw_step)) if raw_step > 0 else 1
    tick_step = max(round(raw_step / magnitude) * magnitude, 1)
    tick_vals = list(range(0, int(max_val * 1.15) + tick_step, tick_step))

    def fmt_fans(v: int) -> str:
        if v >= 1_000_000_000:
            return f"{v / 1_000_000_000:.1f}B"
        elif v >= 1_000_000:
            return f"{v / 1_000_000:.0f}M"
        elif v >= 1_000:
            return f"{v / 1_000:.0f}K"
        return str(v)

    fig.update_layout(
        template="plotly_dark",
        title=dict(
            text="Member Progression",
            font=dict(size=18, color="white"),
            x=0,
            xref="paper",
            pad=dict(l=10),
        ),
        paper_bgcolor="#111827",
        plot_bgcolor="#111827",
        xaxis=dict(
            showgrid=True,
            gridcolor="#2d3748",
            gridwidth=1,
            tickfont=dict(size=11, color="#a0aec0"),
            tickangle=0,
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="#2d3748",
            gridwidth=1,
            tickvals=tick_vals,
            ticktext=[fmt_fans(v) for v in tick_vals],
            tickfont=dict(size=11, color="#a0aec0"),
        ),
        legend=dict(
            font=dict(size=10, color="#e2e8f0"),
            bgcolor="rgba(0,0,0,0)",
            borderwidth=0,
        ),
        margin=dict(l=70, r=20, t=60, b=40),
        width=1100,
        height=max(500, 60 + len(member_data) * 22),
        hovermode="x unified",
    )

    return fig.to_image(format="png", scale=2)


class ChartCommands(commands.Cog):
    """Chart and visualization commands"""

    def __init__(self, bot):
        self.bot = bot

    async def club_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            club_names = await Club.get_names_for_guild(interaction.guild_id)
            return [
                app_commands.Choice(name=name, value=name)
                for name in club_names
                if current.lower() in name.lower()
            ][:25]
        except Exception as e:
            logger.error(f"Error in club autocomplete: {e}")
            return []

    @app_commands.command(
        name="progress_chart",
        description="View fan progression chart for all members this month"
    )
    async def progress_chart(self, interaction: discord.Interaction, club: str):
        """Generate a cumulative fan progression line chart for all active club members."""
        await interaction.response.defer()

        try:
            import plotly.graph_objects  # noqa: F401 — verify installed early
        except ImportError:
            await interaction.followup.send(
                "❌ Plotly is not installed. Run `pip install plotly kaleido`."
            )
            return

        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found.")
                return

            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(
                    f"❌ Club '{club}' is not registered in this server."
                )
                return

            club_tz = resolve_timezone(club_obj.timezone)
            now = datetime.now(club_tz)

            display_month_label = now.strftime("%B %Y")
            member_data: dict[str, dict] | None = None

            # Uma.moe API path: full month data from the scraper
            if club_obj.circle_id and club_obj.is_circle_id_valid():
                try:
                    member_data, _, fetched_year, fetched_month = await _fetch_via_scraper(
                        club_obj.circle_id
                    )
                    display_month_label = datetime(fetched_year, fetched_month, 1).strftime("%B %Y")
                    logger.info(
                        f"progress_chart: fetched {len(member_data)} members from API for {club}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Uma.moe API fetch failed for chart ({club}), falling back to DB: {e}"
                    )
                    member_data = None

            # DB fallback for ChronoGenesis clubs or API failures
            if member_data is None:
                rows = await QuotaHistory.get_current_month_for_club(
                    club_obj.club_id, now.year, now.month
                )
                if not rows:
                    await interaction.followup.send(
                        f"❌ No data available for **{club}** this month yet."
                    )
                    return
                member_data = {}
                for row in rows:
                    name = row["trainer_name"]
                    if name not in member_data:
                        member_data[name] = {"dates": [], "fans": []}
                    member_data[name]["dates"].append(row["date"].strftime("%d.%m"))
                    member_data[name]["fans"].append(row["cumulative_fans"])

            if not member_data:
                await interaction.followup.send(
                    f"❌ No member data found for **{club}** this month."
                )
                return

            try:
                img_bytes = _build_chart(member_data)
            except Exception as e:
                logger.error(f"Failed to render chart image: {e}", exc_info=True)
                await interaction.followup.send(
                    "❌ Failed to render chart image. "
                    "Make sure `kaleido` is installed: `pip install kaleido`"
                )
                return

            file = discord.File(io.BytesIO(img_bytes), filename="progress_chart.png")
            embed = discord.Embed(
                title=f"📈 Member Progression — {club}",
                description=f"{display_month_label} · {len(member_data)} members",
                color=0x3B82F6,
                timestamp=discord.utils.utcnow(),
            )
            embed.set_image(url="attachment://progress_chart.png")
            embed.set_footer(text="Cumulative fan count over the month")

            await interaction.followup.send(embed=embed, file=file)
            logger.info(
                f"progress_chart sent for {club} ({len(member_data)} members) "
                f"by {interaction.user}"
            )

        except Exception as e:
            logger.error(f"Error in progress_chart: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    progress_chart.autocomplete("club")(club_autocomplete)

    @app_commands.command(
        name="previous_month",
        description="View last month's fan stats for all members"
    )
    async def previous_month(self, interaction: discord.Interaction, club: str, quota: int = None):
        """Show a full recap of last month's quota performance fetched directly from the API.

        quota: Override the monthly quota target (optional — useful if the DB value is stale
               after a reset deleted last month's quota history).
        """
        await interaction.response.defer()

        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found.")
                return

            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(
                    f"❌ Club '{club}' is not registered in this server."
                )
                return

            if not club_obj.circle_id or not club_obj.is_circle_id_valid():
                await interaction.followup.send(
                    f"❌ **{club}** has no valid circle ID — this command requires the Uma.moe API."
                )
                return

            club_tz = resolve_timezone(club_obj.timezone)
            now = datetime.now(club_tz)

            # Determine previous month
            if now.month == 1:
                prev_year, prev_month = now.year - 1, 12
            else:
                prev_year, prev_month = now.year, now.month - 1

            days_in_month = calendar.monthrange(prev_year, prev_month)[1]
            month_label = datetime(prev_year, prev_month, 1).strftime("%B %Y")

            results = await _fetch_previous_month_totals(
                club_obj.circle_id, prev_year, prev_month
            )

            if not results:
                await interaction.followup.send(
                    f"❌ No data found for **{club}** in {month_label}."
                )
                return

            # Determine monthly target
            if quota is not None:
                # User explicitly provided the quota — use it directly as the monthly total
                monthly_target = quota
                quota_source = f"provided ({quota:,})"
            else:
                # Try DB — may fall back to club default if reset_month wiped the history
                last_day = date(prev_year, prev_month, days_in_month)
                daily_quota = await QuotaRequirement.get_quota_for_date(club_obj.club_id, last_day)
                monthly_target = daily_quota * days_in_month
                quota_source = f"from DB ({daily_quota:,}/day × {days_in_month}d)"

            # Compute summary stats
            hit_quota = [m for m in results if m["fans"] >= monthly_target]
            total_club_fans = sum(m["fans"] for m in results)

            def fmt(v: int) -> str:
                if v >= 1_000_000_000:
                    return f"{v / 1_000_000_000:.2f}B"
                elif v >= 1_000_000:
                    return f"{v / 1_000_000:.1f}M"
                elif v >= 1_000:
                    return f"{v / 1_000:.1f}K"
                return str(v)

            embed = discord.Embed(
                title=f"📊 {month_label} — {club}",
                color=0x6366F1,
                timestamp=discord.utils.utcnow(),
            )

            embed.add_field(
                name="📅 Month Overview",
                value=(
                    f"**Days:** {days_in_month}\n"
                    f"**Monthly target:** {fmt(monthly_target)} *({quota_source})*\n"
                    f"**Club total:** {fmt(total_club_fans)}\n"
                    f"**Quota achieved:** {len(hit_quota)}/{len(results)} members"
                ),
                inline=False,
            )

            if results:
                top = results[0]
                diff = top["fans"] - monthly_target
                diff_str = f"+{fmt(diff)}" if diff >= 0 else f"-{fmt(abs(diff))}"
                embed.add_field(
                    name="🏆 Top Performer",
                    value=f"**{top['name']}** — {fmt(top['fans'])} ({diff_str})",
                    inline=False,
                )

            # Build member list as a code block (sorted by fans desc, already sorted)
            lines = []
            for i, m in enumerate(results, 1):
                diff = m["fans"] - monthly_target
                diff_str = f"+{fmt(diff)}" if diff >= 0 else f"-{fmt(abs(diff))}"
                status = "✅" if m["fans"] >= monthly_target else "❌"
                mid = "†" if m["joined_mid_month"] else " "
                lines.append(f"{i:>2}. {status}{mid} {m['name']:<20} {fmt(m['fans']):>8}  ({diff_str})")

            # Split into chunks that fit within Discord's 1024-char field limit
            chunk, chunks = [], []
            for line in lines:
                if sum(len(l) + 1 for l in chunk) + len(line) > 980:
                    chunks.append("\n".join(chunk))
                    chunk = []
                chunk.append(line)
            if chunk:
                chunks.append("\n".join(chunk))

            for idx, chunk_text in enumerate(chunks):
                field_name = "📋 Member Results" if idx == 0 else "\u200b"
                embed.add_field(
                    name=field_name,
                    value=f"```\n{chunk_text}\n```",
                    inline=False,
                )

            embed.set_footer(text="† joined mid-month  •  Data sourced from Uma.moe API")

            await interaction.followup.send(embed=embed)
            logger.info(
                f"previous_month sent for {club} ({month_label}, "
                f"{len(results)} members) by {interaction.user}"
            )

        except Exception as e:
            logger.error(f"Error in previous_month: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    previous_month.autocomplete("club")(club_autocomplete)


async def setup(bot):
    await bot.add_cog(ChartCommands(bot))
