"""
Polls GameTora and announces newly-live Uma Musume events.

The loop is deliberately dumb: fetch everything dated, keep what is live now,
subtract what we have already posted, announce the rest. All the state that
makes "new" meaningful lives in ``announced_events`` — GameTora re-serves the
same entries on every poll and has no notion of what any given bot has seen.
"""
import logging
import time

import discord
from discord.ext import commands, tasks

from config.settings import (
    EVENTS_POLL_MINUTES, EVENTS_MAX_BACKFILL_SEC,
)
from utils.permissions import post_requirements, missing_channel_permissions

from .client import GametoraClient, GameEvent, strip_missing_images
from .embeds import event_embed
from .store import EventFeedChannel, AnnouncedEvents

logger = logging.getLogger(__name__)


class EventAnnouncer(commands.Cog):
    """Background poller + fan-out to every guild that opted in."""

    def __init__(self, bot: commands.Bot, client: GametoraClient):
        self.bot = bot
        self.client = client
        self.last_poll: float = 0.0
        self.last_error: str = ""

    async def cog_load(self):
        self.poll.change_interval(minutes=EVENTS_POLL_MINUTES)
        self.poll.start()
        logger.info(f"Event feed poller started (every {EVENTS_POLL_MINUTES}m)")

    async def cog_unload(self):
        self.poll.cancel()

    @tasks.loop(minutes=15)
    async def poll(self):
        try:
            await self.run_once()
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Event feed poll failed: {e}", exc_info=True)

    @poll.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()

    async def run_once(self) -> list[GameEvent]:
        """One poll cycle. Returns the events that were announced."""
        now = int(time.time())
        events = await self.client.fetch_events()
        self.last_poll = time.time()
        self.last_error = ""

        live = [e for e in events if e.is_live(now)]
        known = await AnnouncedEvents.keys()
        fresh = [e for e in live if e.key not in known]

        if not fresh:
            return []

        # An empty record means we've never checked, not that everything live
        # just went live — record it silently so enabling the feed doesn't dump
        # a month of banners into the channel.
        if not known:
            await AnnouncedEvents.mark_many(fresh)
            logger.info(f"Event feed seeded with {len(fresh)} live events (nothing announced)")
            return []

        # A GameTora backfill (an entry added weeks after it started) is a data
        # edit, not news.
        stale = [e for e in fresh if now - e.start > EVENTS_MAX_BACKFILL_SEC]
        if stale:
            await AnnouncedEvents.mark_many(stale)
            logger.info(f"Event feed skipped {len(stale)} back-dated entries: "
                        f"{', '.join(e.key for e in stale)}")

        fresh = [e for e in fresh if now - e.start <= EVENTS_MAX_BACKFILL_SEC]
        if not fresh:
            return []

        fresh.sort(key=lambda e: e.start)
        targets = await EventFeedChannel.all()

        # Banner art is derived from an id, so a feed entry can point at an image
        # that was never uploaded. Better no image than a blank strip.
        fresh = await strip_missing_images(fresh, self.client.image_exists)

        for event in fresh:
            embed = event_embed(event, now)
            for target in targets:
                await self._post(target, embed, event)

            await AnnouncedEvents.mark(event.key, event.kind, event.name,
                                       event.start, event.end)

        logger.info(f"Event feed announced {len(fresh)} event(s) to "
                    f"{len(targets)} channel(s): {', '.join(e.key for e in fresh)}")
        return fresh

    async def _post(self, target: EventFeedChannel, embed: discord.Embed,
                    event: GameEvent) -> bool:
        """Send one announcement, treating a refusal as a log line, not a crash.

        A single guild that revoked the bot's permissions must not stop the
        remaining guilds from getting the announcement, and must not leave the
        event unmarked — an unmarked event is re-announced on every poll for as
        long as it runs.
        """
        channel = self.bot.get_channel(target.channel_id)
        if channel is None:
            logger.warning(f"Event feed channel {target.channel_id} "
                           f"(guild {target.guild_id}) is not visible to the bot")
            return False

        content = f"<@&{target.ping_role_id}>" if target.ping_role_id else None
        try:
            await channel.send(
                content=content,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            return True
        except discord.Forbidden:
            me = getattr(getattr(channel, "guild", None), "me", None)
            try:
                lacking = missing_channel_permissions(channel, me, *post_requirements(channel))
            except Exception:
                lacking = None
            detail = f" — missing {', '.join(lacking)}" if lacking else ""
            logger.error(f"Can't announce {event.key} in #{target.channel_id} "
                         f"(guild {target.guild_id}){detail}")
            return False
        except discord.HTTPException as e:
            logger.error(f"Can't announce {event.key} in #{target.channel_id} — "
                         f"HTTP {e.status}, code {e.code}: {e.text}")
            return False
