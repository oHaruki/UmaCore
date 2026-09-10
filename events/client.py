"""
GameTora data client for Uma Musume Global events, gacha banners and missions.

GameTora ships its site data as static JSON behind a manifest mapping each data
key to a content hash: fetch the manifest, then ``{key}.{hash}.json``. All feeds
here are the ``en/`` (Global) ones.
"""
import asyncio
import time
import logging
from dataclasses import dataclass, replace
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

MANIFEST_URL = "https://gametora.com/data/manifests/umamusume.json"
DATA_BASE = "https://gametora.com/data/umamusume"
IMAGE_BASE = "https://gametora.com/images/umamusume"
MEDIA_BASE = "https://media.gametora.com/umamusume"
SITE_BASE = "https://gametora.com/umamusume"

# GameTora's "no end date" marker (2049-01-01) for permanent content.
PERMANENT_CUTOFF = 2493068400

# How long a permanent mission set counts as current, matching GameTora's own
# front page — otherwise launch-tutorial missions would stay "live" forever.
PERMANENT_WINDOW = 21 * 24 * 3600

# How long a fetched manifest is reused before re-fetching.
MANIFEST_TTL = 300


def _seconds(ts) -> int:
    """GameTora mixes second and millisecond timestamps between feeds."""
    ts = int(ts or 0)
    return ts // 1000 if ts > 10 ** 11 else ts


@dataclass(frozen=True)
class GameEvent:
    """One dated thing happening on the Global server.

    ``key`` is the announcement identity — stable across polls and unique across
    feeds, so the announcer can ask "have I posted this yet" with a primary key
    lookup rather than by matching names.
    """
    key: str
    kind: str            # gacha_char | gacha_support | mission | story | champions_meeting | legend_race
    name: str
    start: int
    end: Optional[int]   # None for permanent content
    image: Optional[str]
    url: str
    detail: str = ""     # extra line for the embed (pickup names, race conditions)
    #: Used when ``image`` doesn't resolve. Global art is preferred over the
    #: Japanese original, but not every event has an English version uploaded.
    image_alt: Optional[str] = None

    @property
    def is_permanent(self) -> bool:
        return self.end is None

    def is_live(self, now: Optional[int] = None) -> bool:
        now = now or int(time.time())
        if self.start > now:
            return False
        if self.end is None:
            return now - self.start <= PERMANENT_WINDOW
        return now < self.end


def page_url(path: str, key: str) -> str:
    """A GameTora link unique to one event.

    Discord merges same-message embeds that share a ``url`` into one gallery
    embed. GameTora has one page per category, not per event, so uniqueness
    comes from a fragment — invisible to the server, so the link still opens
    the right page.
    """
    return f"{SITE_BASE}/{path}#{key.replace(':', '-')}"


def gacha_banner_image(gacha_id: int) -> str:
    return f"{IMAGE_BASE}/en/gacha/img_bnr_gacha_{gacha_id}.png"


def mission_logo_image(event_id: int) -> str:
    return f"{IMAGE_BASE}/en/missions/tex_campaign_mission_logo_{int(event_id):05d}.png"


def story_event_image(event_id: int) -> str:
    return f"{MEDIA_BASE}/story_events/thumb_title/en/{event_id}.png"


def char_image_url(card_id: int) -> str:
    """Character standing art, used as the embed thumbnail."""
    return f"{IMAGE_BASE}/characters/chara_stand_{card_id // 100}_{card_id}.png"


def support_image_url(support_id: int) -> str:
    return f"{IMAGE_BASE}/supports/tex_support_card_{support_id}.png"


class GametoraClient:
    """Reads the Global feeds and normalises them into :class:`GameEvent`."""

    # Feeds that carry dated content. Card-name lookups are fetched separately
    # and only when a gacha banner actually needs them.
    FEEDS = [
        "en/gacha/char-standard",
        "en/gacha/support-standard",
        "en/missions/limited",
        "en/storyevents",
        "en/events/champions-meeting",
        "en/events/legend-race",
    ]

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._manifest: Optional[dict] = None
        self._manifest_at: float = 0.0
        # Card names are ~840KB across both files, so they are cached against the
        # manifest hashes that produced them and only re-read when those change.
        self._names: dict[int, str] = {}
        self._names_hash: Optional[tuple] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={
                    "User-Agent": "UmaCore-Bot/1.0 (+https://gametora.com)",
                    # GameTora defaults to brotli, which aiohttp can only decode
                    # with an optional binding installed — omit it from what we
                    # accept so a missing binding can't break every fetch.
                    "Accept-Encoding": "gzip, deflate",
                },
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _fetch(self, url: str):
        session = await self._get_session()
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def manifest(self, force: bool = False) -> dict:
        if not force and self._manifest and time.time() - self._manifest_at < MANIFEST_TTL:
            return self._manifest
        self._manifest = await self._fetch(MANIFEST_URL)
        self._manifest_at = time.time()
        return self._manifest

    async def _fetch_key(self, manifest: dict, key: str):
        hash_ = manifest.get(key)
        if not hash_:
            logger.warning(f"GameTora manifest has no key '{key}' — feed skipped")
            return None
        return await self._fetch(f"{DATA_BASE}/{key}.{hash_}.json")

    async def image_exists(self, url: str) -> bool:
        """Whether a banner image actually resolves.

        Banner URLs are derived from an id, not given in the data, so an entry
        can point at art that was never uploaded.
        """
        try:
            session = await self._get_session()
            async with session.head(url, allow_redirects=True) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug(f"Image check failed for {url}: {e}")
            return False

    # ------------------------------------------------------------------ #
    #  Card names                                                         #
    # ------------------------------------------------------------------ #

    async def _card_names(self, manifest: dict) -> dict[int, str]:
        """card_id / support_id -> display name, for naming gacha pickups.

        Character card ids and support card ids don't overlap, so one flat map
        serves both.
        """
        stamp = (manifest.get("character-cards"), manifest.get("support-cards"))
        if self._names and stamp == self._names_hash:
            return self._names

        names: dict[int, str] = {}
        chars, supports = await asyncio.gather(
            self._fetch_key(manifest, "character-cards"),
            self._fetch_key(manifest, "support-cards"),
            return_exceptions=True,
        )

        if isinstance(chars, list):
            for c in chars:
                cid = c.get("card_id")
                if cid:
                    title = c.get("title_en_gl") or ""
                    names[cid] = f"{title} {c.get('name_en', '')}".strip()
        elif isinstance(chars, Exception):
            logger.warning(f"Could not load character-cards for pickup names: {chars}")

        if isinstance(supports, list):
            for s in supports:
                sid = s.get("support_id")
                if sid:
                    title = s.get("title_en") or ""
                    names[sid] = f"{title} {s.get('char_name', '')}".strip()
        elif isinstance(supports, Exception):
            logger.warning(f"Could not load support-cards for pickup names: {supports}")

        if names:
            self._names = names
            self._names_hash = stamp
        return self._names

    # ------------------------------------------------------------------ #
    #  Normalisation                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _window(start: int, end: int) -> tuple[int, Optional[int]]:
        return start, (None if end >= PERMANENT_CUTOFF else end)

    def _gacha_events(self, rows, kind: str, names: dict[int, str]) -> list[GameEvent]:
        out = []
        label = "Character Banner" if kind == "gacha_char" else "Support Card Banner"
        for row in rows or []:
            gid = row.get("id")
            if not gid:
                continue
            start, end = self._window(_seconds(row.get("start")), _seconds(row.get("end")))
            if end is None:
                # The permanent "special" pools live in this shape too; they are
                # not news and would otherwise be announced once, forever.
                continue
            pickups = [p[0] for p in (row.get("pickups") or []) if p]
            picked = [names.get(p, str(p)) for p in pickups]
            out.append(GameEvent(
                key=f"gacha:{gid}",
                kind=kind,
                name=", ".join(n for n in picked if n) or label,
                start=start,
                end=end,
                image=gacha_banner_image(gid),
                url=page_url("gacha", f"gacha:{gid}"),
                detail=label,
            ))
        return out

    def _mission_events(self, rows) -> list[GameEvent]:
        out = []
        # This feed is a bare list on Global but has been seen wrapped as
        # {"events": [...]}; accept both rather than silently returning nothing.
        if isinstance(rows, dict):
            rows = rows.get("events", [])
        for row in rows or []:
            eid = row.get("eventId")
            if not eid:
                continue
            start, end = self._window(_seconds(row.get("startDate")), _seconds(row.get("endDate")))
            # eventOriginal is the Global client's own wording; eventEn is
            # GameTora's translation of the JP name and only fits the JP server.
            name = row.get("eventOriginal") or row.get("eventEn") or f"Mission Event #{eid}"
            missions = row.get("missions") or []
            out.append(GameEvent(
                key=f"mission:{eid}",
                kind="mission",
                name=name,
                start=start,
                end=end,
                image=mission_logo_image(eid),
                url=page_url("missions" if end else "missions/permanent", f"mission:{eid}"),
                detail=f"{len(missions)} missions" if missions else "",
            ))
        return out

    def _story_events(self, rows) -> list[GameEvent]:
        out = []
        for row in rows or []:
            eid = row.get("event_id")
            if not eid:
                continue
            start, end = self._window(_seconds(row.get("startDate")), _seconds(row.get("endDate")))
            en = row.get("storyEventEn")
            name = (en if isinstance(en, str) and en else None) or row.get("storyEventOriginal") \
                or f"Story Event #{eid}"
            out.append(GameEvent(
                key=f"story:{eid}",
                kind="story",
                name=name,
                start=start,
                end=end,
                image=story_event_image(eid),
                url=page_url("events", f"story:{eid}"),
                detail="Story Event",
            ))
        return out

    def _cm_events(self, rows) -> list[GameEvent]:
        out = []
        for row in rows or []:
            rid = row.get("id")
            if rid is None:
                continue
            start, end = self._window(_seconds(row.get("start")), _seconds(row.get("end")))
            race = row.get("race") or {}
            distance = race.get("distance")
            out.append(GameEvent(
                key=f"cm:{rid}",
                kind="champions_meeting",
                name=row.get("name") or f"Champions Meeting #{rid}",
                start=start,
                end=end,
                image=None,
                url=page_url("events", f"cm:{rid}"),
                detail=f"{distance}m" if distance else "Champions Meeting",
            ))
        return out

    def _legend_events(self, rows) -> list[GameEvent]:
        out = []
        for row in rows or []:
            rid = row.get("id")
            if rid is None:
                continue
            start, end = self._window(_seconds(row.get("start")), _seconds(row.get("end")))
            out.append(GameEvent(
                key=f"legend:{rid}",
                kind="legend_race",
                name=row.get("name_en") or row.get("name_jp") or f"Legend Race #{rid}",
                start=start,
                end=end,
                image=None,
                url=page_url("events", f"legend:{rid}"),
                detail="Legend Race",
            ))
        return out

    async def fetch_events(self) -> list[GameEvent]:
        """Every dated Global item that hasn't finished yet, oldest start first.

        Includes items that haven't started — GameTora publishes schedules ahead
        of time, and the announcer needs to see one before it goes live in order
        to notice the moment it does.
        """
        manifest = await self.manifest()
        results = await asyncio.gather(
            *[self._fetch_key(manifest, key) for key in self.FEEDS],
            return_exceptions=True,
        )

        feeds = {}
        for key, result in zip(self.FEEDS, results):
            if isinstance(result, Exception):
                logger.error(f"GameTora feed '{key}' failed: {result}")
                feeds[key] = None
            else:
                feeds[key] = result

        names = {}
        if feeds["en/gacha/char-standard"] or feeds["en/gacha/support-standard"]:
            try:
                names = await self._card_names(manifest)
            except Exception as e:
                # Pickup names are decoration; the banner art already shows who
                # is on it, so a failure here must not cost us the announcement.
                logger.warning(f"Pickup name lookup failed, falling back to ids: {e}")

        events: list[GameEvent] = []
        events += self._gacha_events(feeds["en/gacha/char-standard"], "gacha_char", names)
        events += self._gacha_events(feeds["en/gacha/support-standard"], "gacha_support", names)
        events += self._mission_events(feeds["en/missions/limited"])
        events += self._story_events(feeds["en/storyevents"])
        events += self._cm_events(feeds["en/events/champions-meeting"])
        events += self._legend_events(feeds["en/events/legend-race"])

        now = int(time.time())
        events = [
            e for e in events
            if (e.end > now if e.end is not None
                else now - e.start <= PERMANENT_WINDOW)
        ]
        events.sort(key=lambda e: e.start)
        return events

    async def fetch_live_events(self) -> list[GameEvent]:
        now = int(time.time())
        return [e for e in await self.fetch_events() if e.is_live(now)]

    async def gacha_end_times(self) -> dict[int, int]:
        """Authoritative gacha end timestamps, keyed by start timestamp.

        uma.moe derives a banner's end from a duration field rather than reading
        it out of the game, and since April 2026 that has run exactly one daily
        rollover early on every Global banner (23 of 61 matched historically).
        GameTora carries the real value. Start times agree across both sources
        on all 61, which is what makes the start usable as the join key.

        Only gacha is affected — uma.moe's campaign end times match GameTora's
        on 49 of 52 — so nothing else is corrected from here.
        """
        manifest = await self.manifest()
        feeds = await asyncio.gather(
            self._fetch_key(manifest, "en/gacha/char-standard"),
            self._fetch_key(manifest, "en/gacha/support-standard"),
            return_exceptions=True,
        )

        ends: dict[int, int] = {}
        for feed in feeds:
            if isinstance(feed, Exception):
                logger.warning(f"Gacha end-time lookup failed: {feed}")
                continue
            for row in feed or []:
                start, end = _seconds(row.get("start")), _seconds(row.get("end"))
                if start and end and end < PERMANENT_CUTOFF:
                    ends[start] = end
        return ends


async def strip_missing_images(events: list[GameEvent], image_exists) -> list[GameEvent]:
    """Resolve each event's art, falling back before giving up.

    ``image`` is the preferred version (English where we can build a URL for it)
    and ``image_alt`` the original. An event keeps whichever resolves; only when
    neither does is it left without art, since an embed claiming an image it
    cannot load renders as a blank strip.

    Takes the checker as an argument rather than reading it off a client, so the
    announcer and the commands share one implementation and the tests exercise
    it rather than a copy.
    """
    async def resolve(event: GameEvent) -> Optional[str]:
        for candidate in (event.image, event.image_alt):
            if candidate and await image_exists(candidate):
                return candidate
        return None

    resolved = await asyncio.gather(*(resolve(e) for e in events))
    out = []
    for event, image in zip(events, resolved):
        if image != event.image:
            logger.info(f"Art for {event.key}: {event.image} -> {image}")
        out.append(replace(event, image=image) if image != event.image else event)
    return out
