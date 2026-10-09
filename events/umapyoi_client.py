"""
umapyoi.net news client — the source for everything that isn't a gacha banner.

umapyoi mirrors the official Global notices word for word. They are prose, not a
schedule, but every period is written in one fixed form under a heading naming
it — ``10:00 p.m., Sep 28–9:59 p.m., Oct 12, 2026 (UTC)`` — and those are the
times the game actually runs on. uma.moe's dates for story events, Champions
Meeting, Legend Races and campaigns drift from them, so the notices win for all
of those; banners stay on uma.moe and GameTora.

Polling is cheap: the newest page on every poll, the deeper history only every
few hours, since older notices don't change.
"""
import asyncio
import html
import logging
import re
import time
from calendar import timegm
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from .client import GameEvent, PERMANENT_WINDOW

logger = logging.getLogger(__name__)

API_BASE = "https://umapyoi.net/api/v1/en/news"
SITE_BASE = "https://umapyoi.net/en/news"

#: The API serves at most 32 posts per call. Six pages reach back ~5 months,
#: past the start of the longest-running celebrations.
PAGE_SIZE = 32
DEEP_PAGES = 6
DEEP_TTL = 6 * 3600

MAX_DETAIL = 180

#: How far a campaign's earliest period may predate its notice before that
#: period is taken to be a leftover from an earlier part.
STALE_PERIOD = 24 * 3600

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_TIME = r"\d{1,2}:\d{2} [ap]\.m\."

#: ``10:00 p.m., Sep 28–9:59 p.m., Oct 12, 2026 (UTC)``. The start date is
#: dropped for same-day ranges and the year is occasionally missing.
_RANGE = re.compile(
    rf"(?P<t1>{_TIME})(?:, (?P<m1>{_MONTH}) (?P<d1>\d{{1,2}})(?:, (?P<y1>\d{{4}}))?)?"
    rf"\s*[–—-]\s*"
    rf"(?P<t2>{_TIME}), (?:(?P<m2>{_MONTH}) )?(?P<d2>\d{{1,2}})(?:, (?P<y2>\d{{4}}))? \(UTC\)"
)

#: Periods that come after the event itself has closed.
_AFTERMATH = re.compile(r"reward collection|display period", re.I)

#: Gacha notices — banners come from uma.moe and GameTora. Case-sensitive, so
#: "Up to 100 free scouts!" (a campaign) is kept.
_SCOUTS = re.compile(r"\bScouts\b")

#: Dated notices that aren't events.
_NOT_EVENTS = re.compile(
    r"^Issues? with|maintenance|webstore|compensation|has ended|ending soon"
    r"|is coming in|^Data Update|^Updates? to", re.I)

_TITLES = [
    (re.compile(r'^The story event "?(?P<name>.+?)"? (?:is here|is coming soon)!$'), "story"),
    (re.compile(r"Champions Meeting: (?P<name>.+?) (?:is here|has begun)!$"), "champions_meeting"),
    (re.compile(r"^A Legend Race (?:is here|is coming soon)!$"), "legend_race"),
    (re.compile(r'^The event "?(?P<name>.+?)"? (?:is here|is coming soon)!$'), "event"),
    (re.compile(r'Career scenario "?(?P<name>.+?)"? is here!$'), "scenario"),
    (re.compile(r"^(?P<name>.+?) (?:is here|has begun|now available|coming soon|out now)!$",
                re.I), "campaign"),
]

#: Releases that stay in the game, announced without a closing date.
_PERMANENT = {"scenario"}

_STORY_CARD = re.compile(r"Event-Exclusive SSR Support Card: (.+)")

_BLOCK = re.compile(r"<br\s*/?>|</?(?:p|div|li|ul|ol|h\d|tr|td|th|table)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
_IMG = re.compile(r'<img[^>]+src="([^"]+)"', re.I)


def _lines(message: str) -> list[str]:
    text = _TAG.sub("", _BLOCK.sub("\n", message or ""))
    text = html.unescape(text).replace("\xa0", " ")
    return [" ".join(line.split()) for line in text.split("\n") if line.strip()]


def _clock(text: str) -> tuple[int, int]:
    hm, half = text.split(" ")
    hour, minute = (int(x) for x in hm.split(":"))
    return hour % 12 + (12 if half.startswith("p") else 0), minute


def _month(text: Optional[str]) -> Optional[int]:
    return _MONTHS.get(text[:3].lower()) if text else None


def _near_year(month: int, day: int, posted: datetime) -> int:
    """The year that puts a yearless date closest to when it was posted."""
    best = posted.year
    for year in (posted.year - 1, posted.year, posted.year + 1):
        if abs(datetime(year, month, day, tzinfo=timezone.utc) - posted) < \
                abs(datetime(best, month, day, tzinfo=timezone.utc) - posted):
            best = year
    return best


def parse_range(m: re.Match, posted: datetime) -> Optional[tuple[int, int]]:
    """One matched period as unix ``(start, end)``.

    The end is the last second of the stated minute, matching GameTora's
    ``…:59:59`` convention, so "9:59 p.m." reads back as 9:59 p.m.
    """
    m2 = _month(m["m2"]) or _month(m["m1"])
    if m2 is None:
        return None
    d2 = int(m["d2"])
    y2 = int(m["y2"]) if m["y2"] else _near_year(m2, d2, posted)

    m1 = _month(m["m1"]) or m2
    d1 = int(m["d1"]) if m["d1"] else d2
    if m["y1"]:
        y1 = int(m["y1"])
    else:
        y1 = y2 - 1 if m1 > m2 else y2

    try:
        h1, n1 = _clock(m["t1"])
        h2, n2 = _clock(m["t2"])
        start = timegm((y1, m1, d1, h1, n1, 0))
        end = timegm((y2, m2, d2, h2, n2, 59))
    except ValueError:
        return None
    return (start, end) if end > start else None


def periods(lines: list[str], posted: datetime) -> list[tuple[str, int, int]]:
    """Every dated period in a notice, with the heading it sits under.

    A range on the line straight after another range shares its heading —
    Legend Races list their two halves that way.
    """
    out = []
    label = ""
    for line in lines:
        found = [r for r in (parse_range(m, posted) for m in _RANGE.finditer(line)) if r]
        if not found:
            label = line
            continue
        out += [(label, start, end) for start, end in found]
    return out


def _campaign_name(text: str) -> str:
    """A headline cut down to a name: "A special Umayuru Celebration" ->
    "Umayuru Celebration". Story and event titles are quoted names and are
    left exactly as written."""
    name = text.strip().rstrip("!").strip()
    name = re.sub(r"^(?:A|An)\s+(?:special\s+|new\s+)?", "", name)
    name = re.sub(r"\s+event$", "", name, flags=re.I)
    return name[:1].upper() + name[1:]


def classify(title: str) -> Optional[tuple[str, str]]:
    """``(kind, name)`` for a notice announcing something dated, else None."""
    title = " ".join(title.split())
    if _SCOUTS.search(title) or _NOT_EVENTS.search(title):
        return None
    for pattern, kind in _TITLES:
        m = pattern.search(title)
        if m:
            name = (m.groupdict().get("name") or "").strip()
            return kind, _campaign_name(name) if kind == "campaign" else name
    return "campaign", _campaign_name(title)


def _after(lines: list[str], heading: str) -> Optional[str]:
    """The line following a heading, e.g. the conditions under "Race Conditions"."""
    for i, line in enumerate(lines[:-1]):
        if line.lower() == heading.lower():
            return lines[i + 1]
    return None


def _short(text: str) -> str:
    return text if len(text) <= MAX_DETAIL else text[:MAX_DETAIL - 1].rstrip() + "…"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def post_to_event(post: dict) -> Optional[GameEvent]:
    """Turn one notice into the event it announces, if it announces one."""
    if post.get("label_name_en") == "Media":
        return None
    found = classify(post.get("title") or "")
    if not found:
        return None
    kind, name = found

    post_at = int(post.get("post_at") or 0)
    posted = datetime.fromtimestamp(post_at, tz=timezone.utc)
    lines = _lines(post.get("message") or "")
    dated = [p for p in periods(lines, posted) if not _AFTERMATH.search(p[0])]

    if kind in _PERMANENT:
        start, end = post_at, None
    elif not dated:
        return None
    else:
        start = min(p[1] for p in dated)
        end = max(p[2] for p in dated)
        if kind == "campaign" and post_at - start > STALE_PERIOD and post_at < end:
            # A "Part 2" notice repeats periods still running from Part 1;
            # what it announces starts with the notice itself. Hours of lag
            # are normal — Bonus Star Piece notices land 7h after the bonus.
            start = post_at

    detail = ""
    if kind in ("champions_meeting", "legend_race"):
        conditions = _after(lines, "Race Conditions") or ""
        if kind == "legend_race":
            race, _, rest = conditions.partition(" / ")
            name = f"{race} Legend Race" if race else "Legend Race"
            conditions = rest
        detail = conditions
    elif kind == "story":
        card = next((m[1] for m in map(_STORY_CARD.search, lines) if m), "")
        detail = f"Event SSR: {card}" if card else ""
    elif len(dated) == 1 and "period" not in dated[0][0].lower():
        # A lone period under its own name — Bonus Star Piece names the race.
        detail = dated[0][0]

    if not name:
        return None

    images = _IMG.findall(post.get("message") or "")
    image = post.get("image") or (images[0] if images else None)
    alt = next((src for src in images if src != image), None)

    day = time.strftime("%Y%m%d", time.gmtime(start))
    return GameEvent(
        key=f"news:{kind}:{_slug(name)}:{day}",
        kind=kind,
        name=name,
        start=start,
        end=end,
        image=image or None,
        image_alt=alt,
        url=f"{SITE_BASE}/{post.get('announce_id')}",
        detail=_short(detail),
    )


def _same(a: GameEvent, b: GameEvent) -> bool:
    """Two notices about one event: the "coming soon" and the "is here", or
    Champions Meeting's league-selection and race-day posts."""
    a_end, b_end = a.end or 2 ** 62, b.end or 2 ** 62
    return (a.kind == b.kind and _slug(a.name) == _slug(b.name)
            and a.start < b_end and b.start < a_end)


def events_from_posts(posts: list[dict]) -> list[GameEvent]:
    """Every event announced across these notices, the newest notice winning
    when several describe the same one."""
    ordered = sorted(posts, key=lambda p: (int(p.get("post_at") or 0),
                                           int(p.get("announce_id") or 0)))
    events: list[GameEvent] = []
    for post in ordered:
        try:
            event = post_to_event(post)
        except Exception as e:
            logger.warning(f"umapyoi notice {post.get('announce_id')} not parsed: {e}")
            continue
        if event:
            events = [e for e in events if not _same(e, event)] + [event]
    return events


class UmapyoiNewsClient:
    """Reads the Global notices and normalises them into :class:`GameEvent`."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._posts: dict[int, dict] = {}
        self._deep_at: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"User-Agent": "UmaCore-Bot/1.0 (+https://umapyoi.net)",
                         "Accept-Encoding": "gzip, deflate"},
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _page(self, offset: int) -> list[dict]:
        session = await self._get_session()
        async with session.get(f"{API_BASE}/latest/{PAGE_SIZE}/{offset}") as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        if not isinstance(data, list):
            raise ValueError(f"unexpected news payload: {type(data).__name__}")
        return data

    async def fetch_posts(self) -> list[dict]:
        """Recent notices: the first page every call, the rest every few hours.

        A failed refresh falls back to what is already held, so a blip upstream
        costs freshness rather than every non-banner event.
        """
        deep = time.time() - self._deep_at > DEEP_TTL
        pages = DEEP_PAGES if deep else 1
        try:
            results = await asyncio.gather(*(self._page(i * PAGE_SIZE) for i in range(pages)))
        except Exception as e:
            if not self._posts:
                raise
            logger.warning(f"umapyoi refresh failed, using {len(self._posts)} cached notices: {e}")
            return list(self._posts.values())

        fetched = {p["announce_id"]: p for page in results for p in page if "announce_id" in p}
        if deep:
            self._posts = fetched
            self._deep_at = time.time()
        else:
            self._posts.update(fetched)
        return list(self._posts.values())

    async def fetch_events(self) -> list[GameEvent]:
        """Every announced Global event that hasn't finished, oldest start first."""
        now = int(time.time())
        events = [e for e in events_from_posts(await self.fetch_posts())
                  if (e.end > now if e.end is not None
                      else now - e.start <= PERMANENT_WINDOW)]
        events.sort(key=lambda e: e.start)
        return events
