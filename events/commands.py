"""
Slash commands for the Uma Musume event feed.

  /events                what is running right now (anyone)
  /set_events_channel    turn the announcement feed on for this server
  /disable_events_channel
  /events_status         when the poller last ran, and what it would post next
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from .announcer import EventAnnouncer
from .client import GametoraClient, strip_missing_images
from .embeds import event_embed, event_embeds, chunk
from .store import EventFeedChannel, AnnouncedEvents

logger = logging.getLogger(__name__)

NOTHING_ON = "Nothing is running on Global right now."


class EventCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, client: GametoraClient):
        self.bot = bot
        self.client = client

    def _announcer(self) -> Optional[EventAnnouncer]:
        return self.bot.get_cog("EventAnnouncer")

    async def _live_embeds(self) -> list[discord.Embed]:
        """Everything running now, one embed each, art verified."""
        live = await self.client.fetch_live_events()
        live = await strip_missing_images(live, self.client.image_exists)
        return event_embeds(live, int(time.time()))

    @staticmethod
    async def _send_listing(send, embeds: list[discord.Embed]) -> None:
        """Post a listing through ``send``, which is a channel or a followup.

        Just the embeds — they say what they are. Discord takes ten per message
        and there is no reason to expect a listing to stay under that forever, so
        it is chunked rather than truncated.
        """
        if not embeds:
            await send(content=NOTHING_ON)
            return
        for batch in chunk(embeds):
            await send(embeds=batch)

    @app_commands.command(
        name="events",
        description="Current Uma Musume Global banners, missions and events",
    )
    async def events(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            embeds = await self._live_embeds()
        except Exception as e:
            logger.error(f"/events failed: {e}", exc_info=True)
            await interaction.followup.send(
                "Couldn't reach GameTora just now. Try again in a minute."
            )
            return
        await self._send_listing(interaction.followup.send, embeds)

    @app_commands.command(
        name="set_events_channel",
        description="Announce new Uma Musume events in a channel as they go live",
    )
    @app_commands.describe(
        channel="Where announcements are posted",
        ping_role="Optional role to mention with each announcement",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_events_channel(self, interaction: discord.Interaction,
                                 channel: discord.TextChannel,
                                 ping_role: Optional[discord.Role] = None):
        await interaction.response.defer()

        # Post the current state now rather than checking permissions against the
        # cache: an actual send is the only authoritative answer, and it doubles
        # as proof to the admin that the channel works.
        try:
            await self._send_listing(channel.send, await self._live_embeds())
        except discord.Forbidden:
            await interaction.followup.send(
                f"I can't post in {channel.mention}.\n"
                f"Edit Channel → Permissions → add me → allow **View Channel**, "
                f"**Send Messages** and **Embed Links**, then run this again."
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Discord refused the test post in {channel.mention} "
                f"(code `{e.code}`). Nothing was saved."
            )
            return
        except Exception as e:
            logger.error(f"set_events_channel: GameTora fetch failed: {e}", exc_info=True)
            await interaction.followup.send(
                "Couldn't reach GameTora to verify the feed. Try again in a minute."
            )
            return

        await EventFeedChannel.set(interaction.guild_id, channel.id,
                                   ping_role.id if ping_role else None)

        ping = f" and mention {ping_role.mention}" if ping_role else ""
        await interaction.followup.send(
            f"New banners, mission events and story events will be posted in "
            f"{channel.mention}{ping} as they go live.\n"
            f"I've posted what's running now as a check.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        logger.info(f"Event feed enabled in guild {interaction.guild_id} "
                    f"→ #{channel.id} by {interaction.user}")

    @app_commands.command(
        name="disable_events_channel",
        description="Stop announcing new Uma Musume events in this server",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def disable_events_channel(self, interaction: discord.Interaction):
        await interaction.response.defer()
        removed = await EventFeedChannel.clear(interaction.guild_id)
        await interaction.followup.send(
            "Event announcements are off for this server."
            if removed else
            "Event announcements weren't on for this server."
        )

    @app_commands.command(
        name="events_status",
        description="Event feed health: where it posts and when it last checked",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def events_status(self, interaction: discord.Interaction):
        await interaction.response.defer()

        target = await EventFeedChannel.get(interaction.guild_id)
        announcer = self._announcer()

        embed = discord.Embed(title="Event feed", colour=0x3498db)

        if target:
            channel = self.bot.get_channel(target.channel_id)
            where = channel.mention if channel else f"`{target.channel_id}` (not visible to me)"
            ping = f" · pinging <@&{target.ping_role_id}>" if target.ping_role_id else ""
            embed.add_field(name="Posting to", value=f"{where}{ping}", inline=False)
        else:
            embed.add_field(
                name="Posting to",
                value="Nowhere — run `/set_events_channel` to switch it on.",
                inline=False,
            )

        if announcer and announcer.last_poll:
            checked = datetime.fromtimestamp(announcer.last_poll, tz=timezone.utc)
            embed.add_field(name="Last checked",
                            value=discord.utils.format_dt(checked, "R"), inline=True)
        else:
            embed.add_field(name="Last checked", value="Not yet", inline=True)

        embed.add_field(name="Events on record",
                        value=str(await AnnouncedEvents.count()), inline=True)

        if announcer and announcer.last_error:
            embed.add_field(name="Last error", value=f"`{announcer.last_error[:200]}`",
                            inline=False)

        try:
            live = await self.client.fetch_live_events()
            embed.add_field(name="Live right now", value=str(len(live)), inline=True)
        except Exception as e:
            embed.add_field(name="Live right now", value=f"GameTora unreachable: {e}",
                            inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="events_preview",
        description="Preview the announcement embed for the newest live event",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def events_preview(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            live = await self.client.fetch_live_events()
        except Exception as e:
            await interaction.followup.send(f"GameTora unreachable: {e}", ephemeral=True)
            return

        if not live:
            await interaction.followup.send("Nothing is live right now.", ephemeral=True)
            return

        newest = max(live, key=lambda e: e.start)
        newest, = await strip_missing_images([newest], self.client.image_exists)
        await interaction.followup.send(embed=event_embed(newest, int(time.time())),
                                        ephemeral=True)


async def setup(bot: commands.Bot):
    # One client for both cogs: it caches the manifest and the ~840KB card-name
    # tables, and there is no reason to hold two copies of either.
    client = GametoraClient()
    bot._gametora_client = client
    await bot.add_cog(EventAnnouncer(bot, client))
    await bot.add_cog(EventCommands(bot, client))


async def teardown(bot: commands.Bot):
    client = getattr(bot, "_gametora_client", None)
    if client:
        await client.close()
