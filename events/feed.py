"""
The event feed: uma.moe for what's happening, GameTora for when gacha ends.

uma.moe is the source of record here because it covers Global content GameTora
has no data for at all — Champions Meeting, story events, scenario releases and
the smaller recurring events. Its one weak spot is gacha end times, which it
derives rather than reads, so those are corrected from GameTora where the two
describe the same banner.

If GameTora is unreachable the feed still works; banners just carry uma.moe's
derived end time, which runs about a day early.
"""
import logging
import time
from dataclasses import replace

from .client import GametoraClient, GameEvent, strip_missing_images
from .umamoe_client import UmaMoeEventsClient

logger = logging.getLogger(__name__)

#: Kinds whose end time comes from the game's own data rather than a duration.
CORRECTED_KINDS = {"gacha_char", "gacha_support", "gacha_paid"}


class EventFeed:
    """Owns both clients and hands out one merged list of events."""

    def __init__(self, umamoe: UmaMoeEventsClient = None,
                 gametora: GametoraClient = None):
        self.umamoe = umamoe or UmaMoeEventsClient()
        self.gametora = gametora or GametoraClient()

    async def close(self):
        await self.umamoe.close()
        await self.gametora.close()

    async def fetch_events(self) -> list[GameEvent]:
        """Every confirmed Global item that hasn't finished, oldest start first."""
        events = await self.umamoe.fetch_events()

        try:
            ends = await self.gametora.gacha_end_times()
        except Exception as e:
            # Decoration, not substance: without it banners keep uma.moe's own
            # end time rather than the feed failing.
            logger.warning(f"Gacha end-time correction unavailable: {e}")
            ends = {}

        if ends:
            events = [self._correct(e, ends) for e in events]

        events.sort(key=lambda e: e.start)
        return events

    @staticmethod
    def _correct(event: GameEvent, ends: dict[int, int]) -> GameEvent:
        if event.kind not in CORRECTED_KINDS:
            return event
        actual = ends.get(event.start)
        if actual is None or actual == event.end:
            return event
        return replace(event, end=actual)

    async def fetch_live_events(self) -> list[GameEvent]:
        now = int(time.time())
        return [e for e in await self.fetch_events() if e.is_live(now)]

    async def image_exists(self, url: str) -> bool:
        """Banner art lives on uma.moe now, but the check is host-agnostic."""
        return await self.gametora.image_exists(url)

    async def verified_live_events(self) -> list[GameEvent]:
        live = await self.fetch_live_events()
        return await strip_missing_images(live, self.image_exists)
