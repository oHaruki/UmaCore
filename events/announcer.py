"""
Polls the event feed and announces newly-live Uma Musume events.

The loop is deliberately dumb: fetch everything dated, keep what is live now,
subtract what we have already posted, announce the rest. All the state that
makes "new" meaningful lives in ``announced_events`` — the upstream sources
re-serve the same entries on every poll and have no notion of what any given
bot has seen.
"""
import logging
import time

import discord
from discord.ext import commands, tasks

from config.settings import (
    EVENTS_POLL_MINUTES, EVENTS_MAX_BACKFILL_SEC,
)
from utils.permissions import post_requirements, missing_channel_permissions

from .client import GameEvent, strip_missing_images
from .feed import EventFeed
from .embeds import event_embed
from .store import EventFeedChannel, AnnouncedEvents

logger = logging.getLogger(__name__)


class EventAnnouncer(commands.Cog):
    """Background poller + fan-out to every guild that opted in."""

    def __init__(self, bot: commands.Bot, client: EventFeed):
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

        # Keys are namespaced by source ("umamoe:123"). A namespace we have no
        # record of at all means we've never read that source, not that all of
        # its content just went live — true on a fresh database, and true again
        # the first time the feed changes source. Either way, recording it
        # silently is what stops a month of banners landing in the channel.
        seen_namespaces = {k.split(":", 1)[0] for k in known}
        unseen = [e for e in fresh if e.key.split(":", 1)[0] not in seen_namespaces]
        if unseen:
            await AnnouncedEvents.mark_many(unseen)
            logger.info(f"Event feed seeded {len(unseen)} live events from a new "
                        f"source (nothing announced)")
            seeded = {e.key for e in unseen}
            fresh = [e for e in fresh if e.key not in seeded]
            if not fresh:
                return []

        # An entry added weeks after it started is a data edit upstream, not
        # news.
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

        # Some entries carry placeholder art paths that were never uploaded.
        # Better no image than a blank strip.
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
