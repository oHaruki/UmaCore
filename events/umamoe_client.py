"""
uma.moe timeline client — the primary event source.

uma.moe publishes a versioned resource manifest; ``banner_timeline.json`` in it
carries every dated thing in the game across both servers, with a
``global_release_date`` and an ``is_confirmed`` flag per entry. That is a much
wider net than GameTora casts for Global — Champions Meeting, story events,
scenario releases and the smaller recurring events are all in here and are not
available from GameTora's Global feeds at all.

Only confirmed entries are surfaced. The file also carries uma.moe's predictions
for content that hasn't been announced for Global yet, which are explicitly
guesses (and noisy — the same campaign can appear several times at different
dates), so they are dropped rather than announced as fact.

Polling is cheap: the payload is ~157KB gzipped and the endpoint honours
``If-None-Match``, so an unchanged timeline costs a 304 and no body at all.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from config.settings import UMAMOE_API_KEY

from .client import (
    GameEvent, PERMANENT_CUTOFF, PERMANENT_WINDOW,
    gacha_banner_image, mission_logo_image, page_url as gt_page_url,
)

logger = logging.getLogger(__name__)

SITE_BASE = "https://uma.moe"
TIMELINE_URL = f"{SITE_BASE}/resources/current/banner_timeline.json.gz"

#: uma.moe's event type -> our embed kind. Anything unmapped is still announced,
#: under its own type, rather than being silently dropped when they add one.
KIND_BY_TYPE = {
    "character_banner": "gacha_char",
    "support_card_banner": "gacha_support",
    "paid_banner": "gacha_paid",
    "campaign": "mission",
    "story_event": "story",
    "champions_meeting": "champions_meeting",
    "legend_race": "legend_race",
    "scenario_release": "scenario",
    "factor_research": "factor_research",
    "league_of_heroes": "league_of_heroes",
    "masters_challenge": "masters_challenge",
    "racing_carnival": "racing_carnival",
    "strongest_team": "strongest_team",
    "trainer_skills_test": "trainer_skills_test",
}


def timeline_url(key: str) -> str:
    """uma.moe's timeline covers every event type on one page. The fragment
    keeps each embed's url distinct, which is what stops Discord folding them
    into a single gallery."""
    return f"{SITE_BASE}/timeline#{key.replace(':', '-')}"


#: Descriptions are news copy written for a web page, not an embed. Champions
#: Meeting uses them for the race conditions, which is worth keeping; the news
#: items run to full paragraphs, which is not.
MAX_DETAIL = 180

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: Optional[str]) -> str:
    """Turn uma.moe's HTML description into a single embed-safe line."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", " · ", text, flags=re.I)
    text = _TAG_RE.sub("", text).replace("&amp;", "&").strip()
    text = " ".join(text.split())
    if len(text) > MAX_DETAIL:
        text = text[:MAX_DETAIL - 1].rstrip() + "…"
    return text


def _unix(value) -> Optional[int]:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).astimezone(timezone.utc).timestamp())
    except ValueError:
        return None


def image_url(image_path: Optional[str]) -> Optional[str]:
    if not image_path:
        return None
    return f"{SITE_BASE}/{image_path.lstrip('/')}"


class UmaMoeEventsClient:
    """Reads uma.moe's timeline and normalises it into :class:`GameEvent`."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._etag: Optional[str] = None
        self._cached: Optional[dict] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "User-Agent": "UmaCore-Bot/1.0",
                "Accept-Encoding": "gzip, deflate",
            }
            if UMAMOE_API_KEY:
                headers["X-API-Key"] = UMAMOE_API_KEY
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30), headers=headers,
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _fetch_timeline(self) -> dict:
        """The raw timeline, re-using the cached copy while the ETag holds.

        The resource endpoint needs an API key; without one every request is a
        403 and the feed simply has no uma.moe half.
        """
        session = await self._get_session()
        headers = {"If-None-Match": self._etag} if self._etag else {}
        async with session.get(TIMELINE_URL, headers=headers) as resp:
            if resp.status == 304 and self._cached is not None:
                return self._cached
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            self._etag = resp.headers.get("ETag")
            self._cached = data
            return data

    @staticmethod
    def _artwork(raw: dict, kind: str, key: str) -> tuple[Optional[str], Optional[str], str]:
        """Pick the art and the link, preferring English where it exists.

        uma.moe hosts the Japanese original for everything. GameTora hosts the
        English art Global players actually see in game, but only for banners and
        mission campaigns — and uma.moe hands us the ids to build those URLs:
        ``gacha_id`` for banners, ``campaign-<mission id>`` for campaigns.

        Art and link come from the same site so the two never disagree, and the
        Japanese art rides along as the fallback for when GameTora turns out not
        to have that particular id.
        """
        original = image_url(raw.get("image_path"))

        gacha_id = raw.get("gacha_id")
        if gacha_id and kind.startswith("gacha"):
            return (gacha_banner_image(gacha_id), original,
                    gt_page_url("gacha", key))

        mission_id = re.fullmatch(r"campaign-(\d+)", str(raw.get("id") or ""))
        if mission_id:
            return (mission_logo_image(int(mission_id.group(1))), original,
                    gt_page_url("missions", key))

        # Everything else — story events, Champions Meeting, the news-derived
        # campaigns — has no English art to point at, so it stays on uma.moe.
        return original, None, timeline_url(key)

    def _to_event(self, raw: dict) -> Optional[GameEvent]:
        start = _unix(raw.get("global_release_date"))
        if not start:
            return None

        end = _unix(raw.get("estimated_end_date"))
        if end is not None and end >= PERMANENT_CUTOFF:
            end = None

        raw_type = raw.get("type") or "event"
        kind = KIND_BY_TYPE.get(raw_type, raw_type)
        name = raw.get("title") or raw_type.replace("_", " ").title()

        detail = _clean(raw.get("description"))
        if not detail:
            # Banners carry no description; the pickups are the useful line.
            names = raw.get("related_characters") or raw.get("related_support_card_names")
            if names:
                detail = ", ".join(names[:4])
                if len(names) > 4:
                    detail += f" +{len(names) - 4} more"

        event_id = raw.get("id")
        if event_id is None:
            return None

        key = f"umamoe:{event_id}"
        image, image_alt, url = self._artwork(raw, kind, key)
        return GameEvent(
            key=key,
            kind=kind,
            name=name,
            start=start,
            end=end,
            image=image,
            image_alt=image_alt,
            url=url,
            detail=detail,
        )

    async def fetch_events(self) -> list[GameEvent]:
        """Every confirmed Global item that hasn't finished yet.

        Unconfirmed entries are uma.moe's own forecast of when JP content will
        reach Global. They move, they duplicate, and announcing one as though it
        were scheduled would be worse than saying nothing.
        """
        data = await self._fetch_timeline()
        now = int(datetime.now(timezone.utc).timestamp())

        events: list[GameEvent] = []
        for raw in data.get("events") or []:
            if not raw.get("is_confirmed"):
                continue
            event = self._to_event(raw)
            if event is None:
                continue
            if event.end is None:
                if now - event.start > PERMANENT_WINDOW:
                    continue
            elif event.end <= now:
                continue
            events.append(event)

        events.sort(key=lambda e: e.start)
        return events
