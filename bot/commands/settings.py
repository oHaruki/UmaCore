"""
Channel and bot settings commands
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
import logging
import pytz

from typing import Optional, Union

from models import Club, ChannelName
from services import MonthlyInfoService, channel_names
from utils.timezone_helper import resolve_timezone
from utils.audit import log_audit
from utils.permissions import ensure_can_manage

logger = logging.getLogger(__name__)


# Discord error codes worth telling apart. They send you to different settings,
# and naming the wrong one costs someone an afternoon on a permission that was
# never the problem.
MISSING_ACCESS = 50001
MISSING_PERMISSIONS = 50013

_GRANT_STEPS = (
    "Open the channel → **Edit Channel** → **Permissions** → add **my role** "
    "specifically → allow it there. Check the **category** above the channel too: "
    "a deny there applies to every channel inside it that doesn't override it."
)


def _forbidden_advice(result: dict, channel) -> str:
    """What to actually change when Discord refuses.

    Leads with the permission *we* resolve as missing rather than with Discord's
    error code, because the code is not the reliable signal it looks like. A
    refused channel edit comes back as 50001 Missing Access whether the bot
    cannot see the channel or simply lacks Manage Channels on it — observed on
    2026-09-01, where 50001 arrived with View Channel plainly granted and only
    Manage Channels missing. Reading that code literally sent the admin to fix
    visibility, which was never the problem.

    Our own reading is trustworthy here in a way it is not when *granting*: it
    can only under-report, so a permission it calls missing beside a refusal from
    Discord is missing twice over.
    """
    missing = result.get("missing")
    code = result.get("code")

    # Before anything about permissions: a timeout counterfeits a permission
    # problem exactly, and no amount of granting will fix one.
    if result.get("timeout"):
        return (
            f"{result['timeout']}\n\n"
            f"Nothing is wrong with {channel.mention}'s permissions — remove the "
            f"timeout from me in the member list (right-click me → **Timeout** → "
            f"remove), then try again."
        )

    if missing:
        names = ", ".join(f"**{m}**" for m in missing)
        return (
            f"{names} {'is' if len(missing) == 1 else 'are'} missing on "
            f"{channel.mention} itself.\n\n"
            f"Granting it server-wide is not enough: a **deny** on the channel, or "
            f"on its category, overrides the server-wide permission — and so does "
            f"simply never allowing it there. {_GRANT_STEPS}\n\n"
            f"Two things that catch people: granting it to *your* role rather than "
            f"mine, and picking *Manage Permissions* or *Manage Roles* instead of "
            f"**Manage Channels**."
        )

    if missing == []:
        # We resolve every permission as held and Discord still refuses. The two
        # disagree, so say so plainly instead of sending someone to re-grant a
        # permission the server already shows as granted.
        return (
            f"Odd one: I resolve every permission I need on {channel.mention} as "
            f"granted, and Discord refused anyway (code `{code}`).\n\n"
            f"Worth checking whether the channel was moved into a different "
            f"category, or its permissions changed in the last moment. If it keeps "
            f"happening, this is worth reporting with the line below."
        )

    # missing is None: the cached member gave no trustworthy reading.
    return (
        f"Discord refused (code `{code}`), and I can't read my own permissions on "
        f"{channel.mention} reliably enough to say which one is at fault.\n\n"
        f"Check that I have both **View Channel** and **Manage Channels** there. "
        f"{_GRANT_STEPS}"
    )


class SettingsCommands(commands.Cog):
    """Channel and bot configuration commands"""
    
    def __init__(self, bot):
        self.bot = bot
        self.monthly_info_service = MonthlyInfoService()
    
    async def club_autocomplete(self, interaction: discord.Interaction, current: str):
        """Autocomplete for club names visible in this guild"""
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
    
    @app_commands.command(name="set_report_channel", description="Set the channel for daily reports")
    async def set_report_channel(self, interaction: discord.Interaction, channel: discord.TextChannel, club: str):
        """Set the channel where daily reports will be posted"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return

            if not await ensure_can_manage(interaction, club_obj):
                return

            await club_obj.set_channels(report_channel_id=channel.id)
            
            embed = discord.Embed(
                title=f"✅ Report Channel Updated - {club}",
                description=f"Daily reports will now be posted to {channel.mention}",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            await interaction.followup.send(embed=embed)
            await log_audit(
                interaction, 'club.update', 'club',
                entity_id=club_obj.club_id, club_id=club_obj.club_id,
                details={'changes': {'report_channel_id': str(channel.id)}},
            )
            logger.info(f"Report channel for {club} set to {channel.name} ({channel.id}) by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in set_report_channel: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="set_alert_channel", description="Set the channel for alerts (bombs, kicks)")
    async def set_alert_channel(self, interaction: discord.Interaction, channel: discord.TextChannel, club: str):
        """Set the channel where alerts will be posted"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return

            if not await ensure_can_manage(interaction, club_obj):
                return

            await club_obj.set_channels(alert_channel_id=channel.id)
            
            embed = discord.Embed(
                title=f"✅ Alert Channel Updated - {club}",
                description=f"Alerts (bomb warnings, kick notifications) will now be posted to {channel.mention}",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            await interaction.followup.send(embed=embed)
            await log_audit(
                interaction, 'club.update', 'club',
                entity_id=club_obj.club_id, club_id=club_obj.club_id,
                details={'changes': {'alert_channel_id': str(channel.id)}},
            )
            logger.info(f"Alert channel for {club} set to {channel.name} ({channel.id}) by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Error in set_alert_channel: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="channel_settings", description="View current channel configuration")
    async def channel_settings(self, interaction: discord.Interaction, club: str):
        """View current channel settings"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return

            if not await ensure_can_manage(interaction, club_obj):
                return

            embed = discord.Embed(
                title=f"⚙️ Channel Settings - {club}",
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            # Report channel
            if club_obj.report_channel_id:
                report_channel = self.bot.get_channel(club_obj.report_channel_id)
                if report_channel:
                    embed.add_field(
                        name="📊 Daily Reports Channel",
                        value=f"{report_channel.mention} (ID: {club_obj.report_channel_id})",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="📊 Daily Reports Channel",
                        value=f"⚠️ Channel not found (ID: {club_obj.report_channel_id})",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="📊 Daily Reports Channel",
                    value="❌ Not configured",
                    inline=False
                )
            
            # Alert channel
            if club_obj.alert_channel_id:
                alert_channel = self.bot.get_channel(club_obj.alert_channel_id)
                if alert_channel:
                    embed.add_field(
                        name="🚨 Alerts Channel",
                        value=f"{alert_channel.mention} (ID: {club_obj.alert_channel_id})",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="🚨 Alerts Channel",
                        value=f"⚠️ Channel not found (ID: {club_obj.alert_channel_id})",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="🚨 Alerts Channel",
                    value="⚠️ Not configured (using reports channel)",
                    inline=False
                )
            
            # Monthly info board
            channel_id, message_id = await club_obj.get_monthly_info_location()
            if channel_id and message_id:
                info_channel = self.bot.get_channel(channel_id)
                if info_channel:
                    embed.add_field(
                        name="📋 Monthly Info Board",
                        value=f"{info_channel.mention}\n[Jump to message](https://discord.com/channels/{interaction.guild_id}/{channel_id}/{message_id})",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="📋 Monthly Info Board",
                        value="⚠️ Channel not found",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="📋 Monthly Info Board",
                    value="❌ Not posted (use `/post_monthly_info`)",
                    inline=False
                )
            
            embed.set_footer(text="Use /set_report_channel and /set_alert_channel to configure")
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in channel_settings: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(name="post_monthly_info", description="Post the monthly info board (auto-updates)")
    async def post_monthly_info(self, interaction: discord.Interaction, club: str, channel: discord.TextChannel = None):
        """Post or update the monthly information board"""
        await interaction.response.defer()
        
        try:
            club_obj = await Club.get_by_name(club)
            if not club_obj:
                await interaction.followup.send(f"❌ Club '{club}' not found")
                return
            
            if not club_obj.belongs_to_guild(interaction.guild_id):
                await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
                return

            if not await ensure_can_manage(interaction, club_obj):
                return

            # Use current channel if none specified
            target_channel = channel or interaction.channel
            
            club_tz = resolve_timezone(club_obj.timezone)
            current_datetime = datetime.now(club_tz)
            current_date = current_datetime.date()
            
            embed = await self.monthly_info_service.create_monthly_info_embed(
                club_obj.club_id,
                club_obj.club_name,
                current_date,
                club_obj.quota_period
            )
            
            message = await target_channel.send(embed=embed)
            
            # Save the message location so it can be auto-updated later
            await club_obj.set_monthly_info_location(target_channel.id, message.id)
            
            embed_response = discord.Embed(
                title="✅ Monthly Info Board Posted",
                description=f"Posted in {target_channel.mention} for **{club_obj.club_name}**\n\n"
                           f"This message will auto-update when quota changes.\n"
                           f"[Jump to board](https://discord.com/channels/{interaction.guild_id}/{target_channel.id}/{message.id})",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            embed_response.add_field(
                name="📝 Note",
                value="The board location has been saved and will persist through bot restarts.",
                inline=False
            )
            
            await interaction.followup.send(embed=embed_response)
            await log_audit(
                interaction, 'club.update', 'club',
                entity_id=club_obj.club_id, club_id=club_obj.club_id,
                details={'changes': {'monthly_info_channel_id': str(target_channel.id)}},
            )
            logger.info(f"Monthly info board posted for {club_obj.club_name} in {target_channel.name} by {interaction.user} - saved location")
            
        except Exception as e:
            logger.error(f"Error in post_monthly_info: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Error: {str(e)}")
    
    @app_commands.command(
        name="set_channel_name",
        description="Make a channel's name display this club's rank or fans",
    )
    @app_commands.describe(
        club="Which club's figures to display",
        channel="The channel to rename - usually a locked voice channel",
        template="Name with tokens, e.g. 'Rank #{rank}'. Leave empty to stop tracking.",
    )
    async def set_channel_name(
        self, interaction: discord.Interaction, club: str,
        channel: Union[discord.VoiceChannel, discord.StageChannel, discord.TextChannel],
        template: Optional[str] = None,
    ):
        """Bind a channel's name to a club's live figures, or unbind it.

        The name is rewritten after each live update (hourly, for clubs running
        the live board) and after the daily scrape. The first change happens
        immediately, so you can see whether the template reads the way you wanted
        instead of waiting an hour to find out.
        """
        await interaction.response.defer()

        club_obj = await Club.get_by_name(club)
        if not club_obj:
            await interaction.followup.send(f"❌ Club '{club}' not found")
            return
        if not club_obj.belongs_to_guild(interaction.guild_id):
            await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
            return
        if not await ensure_can_manage(interaction, club_obj):
            return

        if template is None:
            if await ChannelName.remove(channel.id):
                await interaction.followup.send(
                    f"✅ {channel.mention} no longer tracks **{club}**. "
                    f"Its current name stays as it is."
                )
                await log_audit(
                    interaction, 'club.update', 'club',
                    entity_id=club_obj.club_id, club_id=club_obj.club_id,
                    details={'changes': {'channel_name': None,
                                         'channel_id': str(channel.id)}},
                )
            else:
                await interaction.followup.send(
                    f"ℹ️ {channel.mention} wasn't tracking anything."
                )
            return

        bad = channel_names.unknown_tokens(template)
        if bad:
            listed = ", ".join("`{" + t + "}`" for t in bad)
            available = ", ".join("`{" + t + "}`" for t in channel_names.TOKENS)
            await interaction.followup.send(
                f"❌ Unknown token{'s' if len(bad) > 1 else ''}: {listed}\n"
                f"Available: {available}"
            )
            return

        # Advisory only. The permission is resolved from a cache that can be
        # incomplete, so a False here is a hint, not a verdict — the rename below
        # is what actually settles it.
        allowed = channel_names.can_rename(channel, interaction.guild.me)

        if not club_obj.is_circle_id_valid():
            await interaction.followup.send(club_obj.get_circle_id_help_message())
            return

        await ChannelName.upsert(club_obj.club_id, channel.id, template)

        # Only this channel. Renaming the club's other channels here would spend
        # their rename budget for no reason, and — the bug that hid a refusal for
        # an hour of debugging — would let one of them succeed and be reported as
        # if this one had.
        result = await channel_names.refresh_now(self.bot, club_obj, only=channel.id)
        outcome = result.get("per_channel", {}).get(channel.id, {})
        status = outcome.get("status")

        embed = discord.Embed(
            title="✅ Channel name tracking enabled",
            description=f"{channel.mention} now follows **{club}**",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Template", value=f"`{template}`", inline=False)

        if status == "updated":
            embed.add_field(name="Now showing", value=f"`{outcome['name']}`", inline=False)

        elif status == "forbidden":
            embed.colour = discord.Color.orange()
            embed.title = "⚠️ Saved, but the rename was refused"
            embed.add_field(
                name="Discord refused",
                value=(f"The setting is saved and retries on every update.\n\n"
                       f"{_forbidden_advice(outcome, channel)}"),
                inline=False,
            )
            # The raw answer, so a report of this carries the detail that
            # separates the causes instead of a guess at which it was.
            embed.add_field(
                name="What Discord said",
                value=(f"`{outcome.get('code')} {outcome.get('detail') or 'Forbidden'}`\n"
                       f"My own reading: `{outcome.get('access') or 'unavailable'}`"),
                inline=False,
            )

        elif status == "not_cached":
            embed.colour = discord.Color.orange()
            embed.title = "⚠️ Saved, but I can't see that channel"
            embed.add_field(
                name="Not visible to me",
                value=(f"Saved, and it retries on every update. I can't currently "
                       f"resolve {channel.mention} — usually a **View Channel** "
                       f"permission I'm missing there."),
                inline=False,
            )

        elif status in ("http_error", "error"):
            embed.colour = discord.Color.orange()
            embed.title = "⚠️ Saved, but the rename failed"
            embed.add_field(
                name="Error",
                value=f"`{outcome.get('detail', 'unknown')}`\nIt retries on the next update.",
                inline=False,
            )

        else:
            # No attempt was made: uma.moe had nothing to render from.
            embed.add_field(
                name="⏳ No figures yet",
                value=f"Uma.moe has nothing to show for **{club}** right now. "
                      f"This is how it will read: `{channel_names.preview(template)}`",
                inline=False,
            )

        if allowed is False and status not in ("updated", "forbidden"):
            embed.add_field(
                name="Heads up",
                value=(f"I don't appear to have **Manage Channels** on "
                       f"{channel.mention}. I couldn't confirm that just now, so it "
                       f"may be wrong — but if the name never changes, start there."),
                inline=False,
            )

        if club_obj.live_board_enabled:
            cadence = "Updates hourly from live data, and again after the daily scrape."
        else:
            cadence = ("Updates hourly from live data. This club has no live board, "
                       "so it now gets an hourly uma.moe read of its own for this.")
        embed.set_footer(text=cadence)

        await interaction.followup.send(embed=embed)
        await log_audit(
            interaction, 'club.update', 'club',
            entity_id=club_obj.club_id, club_id=club_obj.club_id,
            details={'changes': {'channel_name': template,
                                 'channel_id': str(channel.id)}},
        )
        logger.info(f"Channel {channel.id} bound to {club} as {template!r} by {interaction.user}")

    @app_commands.command(
        name="channel_names",
        description="List the channels whose names track this club, and the tokens you can use",
    )
    @app_commands.describe(club="Which club", refresh="Update those channels right now")
    async def channel_names_cmd(self, interaction: discord.Interaction, club: str,
                                refresh: bool = False):
        """Show a club's tracking channels, with what each one currently reads."""
        await interaction.response.defer()

        club_obj = await Club.get_by_name(club)
        if not club_obj:
            await interaction.followup.send(f"❌ Club '{club}' not found")
            return
        if not club_obj.belongs_to_guild(interaction.guild_id):
            await interaction.followup.send(f"❌ Club '{club}' is not registered in this server.")
            return

        if refresh and not await ensure_can_manage(interaction, club_obj):
            return

        result = await channel_names.refresh_now(self.bot, club_obj) if refresh else None

        rows = await ChannelName.get_for_club(club_obj.club_id)
        embed = discord.Embed(
            title=f"🔤 Channel names - {club}",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )

        if rows:
            for row in rows:
                target = self.bot.get_channel(row.channel_id)
                where = target.mention if target else f"`{row.channel_id}` (not found)"
                state = "" if row.enabled else " · paused"
                showing = f"\nShowing `{row.last_rendered}`" if row.last_rendered else ""
                embed.add_field(
                    name=f"{where}{state}",
                    value=f"`{row.template}`{showing}",
                    inline=False,
                )
        else:
            embed.description = (
                f"No channels are tracking **{club}** yet.\n"
                f"Set one with `/set_channel_name club:{club} channel:#some-vc "
                f"template:Rank #{{rank}}`"
            )

        embed.add_field(
            name="Tokens",
            value="\n".join("`{" + t + "}` - " + d for t, d in channel_names.TOKENS.items()),
            inline=False,
        )

        if result is not None:
            embed.set_footer(
                text=f"Refreshed now: {result['updated']} updated, "
                     f"{result['skipped']} unchanged, {result['failed']} failed"
            )
        else:
            embed.set_footer(
                text="Discord throttles renames, so a channel changes at most "
                     "once every few minutes."
            )

        await interaction.followup.send(embed=embed)

    # Apply autocomplete
    set_report_channel.autocomplete('club')(club_autocomplete)
    set_alert_channel.autocomplete('club')(club_autocomplete)
    channel_settings.autocomplete('club')(club_autocomplete)
    set_channel_name.autocomplete('club')(club_autocomplete)
    channel_names_cmd.autocomplete('club')(club_autocomplete)
    post_monthly_info.autocomplete('club')(club_autocomplete)


async def setup(bot):
    """Setup function for loading the cog"""
    await bot.add_cog(SettingsCommands(bot))