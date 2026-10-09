"""
The event feed: the official notices for events, uma.moe for banners and
mission sets, GameTora for when those end.

umapyoi mirrors the official Global notices, so story events, Champions
Meeting, Legend Races and campaigns are read from the notices themselves.
uma.moe supplies what the notices don't carry in a usable form — banners, with
their pickups and art, and the game's own mission sets — and GameTora corrects
their end times, which uma.moe derives rather than reads.

Each source fails on its own: losing one costs its share of the feed, not all
of it.
"""
import asyncio
import logging
import re
import time
from dataclasses import replace
from typing import Optional

from .client import GametoraClient, GameEvent, strip_missing_images
from .umamoe_client import UmaMoeEventsClient
from .umapyoi_client import UmapyoiNewsClient

logger = logging.getLogger(__name__)

#: Kinds whose end time comes from the game's own data rather than a duration.
CORRECTED_KINDS = {"gacha_char", "gacha_support", "gacha_paid"}

#: uma.moe's ends run up to a day early, so its entries are fetched with this
#: much slack and filtered again once corrected — otherwise a banner vanishes
#: on its last day.
END_SLACK = 2 * 24 * 3600

_MISSION_KEY = re.compile(r"umamoe:campaign-(\d+)")


def mission_id(event: GameEvent) -> Optional[int]:
    """The game's mission id for an uma.moe mission set, else None."""
    m = _MISSION_KEY.fullmatch(event.key)
    return int(m.group(1)) if m else None


class EventFeed:
    """Owns all three clients and hands out one merged list of events."""

    def __init__(self, umamoe: UmaMoeEventsClient = None,
                 gametora: GametoraClient = None,
                 news: UmapyoiNewsClient = None):
        self.umamoe = umamoe or UmaMoeEventsClient()
        self.gametora = gametora or GametoraClient()
        self.news = news or UmapyoiNewsClient()
        #: Sources that failed on the last fetch, for /events_status.
        self.problems: list[str] = []

    async def close(self):
        await self.umamoe.close()
        await self.gametora.close()
        await self.news.close()

    async def _banners_and_missions(self) -> list[GameEvent]:
        events = [e for e in await self.umamoe.fetch_events(ended_within=END_SLACK)
                  if e.kind in CORRECTED_KINDS or mission_id(e) is not None]

        gacha, missions = await asyncio.gather(
            self.gametora.gacha_end_times(),
            self.gametora.mission_end_times(),
            return_exceptions=True,
        )
        # Decoration, not substance: without it entries keep uma.moe's own end.
        if isinstance(gacha, Exception):
            logger.warning(f"Gacha end-time correction unavailable: {gacha}")
            gacha = {}
        if isinstance(missions, Exception):
            logger.warning(f"Mission end-time correction unavailable: {missions}")
            missions = {}

        return [self._correct(e, gacha, missions) for e in events]

    @staticmethod
    def _correct(event: GameEvent, gacha: dict[int, int],
                 missions: dict[int, int]) -> GameEvent:
        if event.kind in CORRECTED_KINDS:
            actual = gacha.get(event.start)
        else:
            mid = mission_id(event)
            actual = missions.get(mid) if mid is not None else None
        if actual is None or actual == event.end:
            return event
        return replace(event, end=actual)

    async def fetch_events(self) -> list[GameEvent]:
        """Every confirmed Global item that hasn't finished, oldest start first.

        Raises only when every source is down.
        """
        results = await asyncio.gather(
            self._banners_and_missions(), self.news.fetch_events(),
            return_exceptions=True,
        )

        events: list[GameEvent] = []
        problems = []
        for source, result in zip(("uma.moe", "umapyoi"), results):
            if isinstance(result, Exception):
                logger.error(f"{source} events unavailable: {result}")
                problems.append(f"{source}: {result}")
            else:
                events += result
        self.problems = problems
        if len(problems) == len(results):
            raise results[0]

        now = int(time.time())
        events = [e for e in events if e.end is None or e.end > now]
        events.sort(key=lambda e: e.start)
        return events

    async def fetch_live_events(self) -> list[GameEvent]:
        now = int(time.time())
        return [e for e in await self.fetch_events() if e.is_live(now)]

    async def image_exists(self, url: str) -> bool:
        """Art lives on several hosts; the check is host-agnostic."""
        return await self.gametora.image_exists(url)

    async def verified_live_events(self) -> list[GameEvent]:
        live = await self.fetch_live_events()
        return await strip_missing_images(live, self.image_exists)
