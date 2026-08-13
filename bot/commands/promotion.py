"""
/promotion command — how many more fans a club needs to climb to a better rank.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from models import Club
from services.promotion_calculator import compute_promotion, grade_for_rank

logger = logging.getLogger(__name__)


def _fmt(v: int) -> str:
    """Short human fan count: 1.2B / 3.4M / 5.6K."""
    v = int(v)
    if abs(v) >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f}B"
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.1f}K"
    return str(v)


class PromotionCommands(commands.Cog):
    """Rank-promotion progress command."""

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
        name="promotion",
        description="See how many more fans a club needs to climb to a better rank",
    )
    @app_commands.describe(
        club="The club to check",
        target_rank="Target position rank (default: the next milestone above your current rank)",
    )
    async def promotion(self, interaction: discord.Interaction, club: str, target_rank: int = None):
        await interaction.response.defer()

        try:
            if target_rank is not None and target_rank < 1:
                await interaction.followup.send("❌ Target rank must be 1 or higher.")
                return

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

            result = await compute_promotion(club_obj, target_rank=target_rank)
            if result is None:
                await interaction.followup.send(
                    f"❌ Couldn't fetch rank data for **{club}** right now. Try again shortly."
                )
                return

            if result.already_reached:
                if result.is_milestone:
                    msg = (
                        f"🏆 **{club}** is already at rank **#{result.current_rank}** — "
                        f"at or above the top milestone. Nothing left to climb!"
                    )
                else:
                    msg = (
                        f"🏆 **{club}** is already at rank **#{result.current_rank}**, "
                        f"at or above the target #{result.target_rank}."
                    )
                await interaction.followup.send(msg)
                return

            # Grades only mean something when the target is a grade boundary —
            # a hand-typed target_rank usually lands mid-band.
            current_grade = grade_for_rank(result.current_rank)
            target_grade = grade_for_rank(result.target_rank) if result.is_milestone else None

            title = f"📈 {club} — climb to Top {result.target_rank}"
            if target_grade:
                title += f" ({target_grade})"

            embed = discord.Embed(
                title=title,
                color=0x6366F1,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="Current standing",
                value=(
                    f"Rank **#{result.current_rank}**"
                    f"{f' · **{current_grade}**' if current_grade else ''}"
                    f" · {_fmt(result.our_fans)} monthly fans"
                ),
                inline=False,
            )
            embed.add_field(
                name=f"Fans needed to reach #{result.target_rank}",
                value=(
                    f"**+{_fmt(result.fans_needed)}** more fans total\n"
                    f"*(rank #{result.target_rank} currently sits at {_fmt(result.target_fans)})*"
                ),
                inline=False,
            )

            if result.daily_rate:
                pace_kind = "average this month" if result.rate_is_average else "recent"
                embed.add_field(
                    name="What you make daily",
                    value=f"~{_fmt(result.daily_rate)} fans/day ({pace_kind})",
                    inline=False,
                )
                if result.extra_per_day:
                    total_needed = result.daily_rate + result.extra_per_day
                    embed.add_field(
                        name=f"To reach it by month-end ({result.days_remaining} days left)",
                        value=(
                            f"**+{_fmt(result.extra_per_day)}/day** on top of that\n"
                            f"*(≈ {_fmt(total_needed)}/day total)*"
                        ),
                        inline=False,
                    )
            else:
                embed.add_field(
                    name="What you make daily",
                    value="*Not enough data yet.*",
                    inline=False,
                )
                if result.extra_per_day:
                    embed.add_field(
                        name=f"To reach it by month-end ({result.days_remaining} days left)",
                        value=f"about **{_fmt(result.extra_per_day)}/day**",
                        inline=False,
                    )

            embed.set_footer(
                text="Estimate — ranks shift daily and rival clubs are gaining too, "
                     "so the real climb usually takes longer."
            )
            await interaction.followup.send(embed=embed)
            logger.info(
                f"promotion sent for {club}: #{result.current_rank}→#{result.target_rank}, "
                f"need +{result.fans_needed} by {interaction.user}"
            )

        except Exception as e:
            logger.error(f"Error in promotion command: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")

    promotion.autocomplete("club")(club_autocomplete)
