"""
Champions Meeting schedule for the skill maps, read from uma.moe's timeline -
the same feed the event announcements use - so the current and next CM are
known without a game data rebuild.

uma.moe describes each CM's race as "Chukyo - Turf<br>1200m - Short -
Counterclockwise<br>Firm - Winter - Sunny"; that is matched to a course. Its end
dates for Champions Meeting run about two days early, so a CM counts as current
for its usual nine-day run from the start date.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from .gamedata import CourseChoice, GameData, RaceEnv

logger = logging.getLogger(__name__)

CM_RUN = 9 * 24 * 3600
_TRACKS = {"ooi": "oi"}
_GROUNDS = {"firm": 1, "good": 2, "soft": 3, "heavy": 4}
_SEASONS = {"spring": 1, "summer": 2, "autumn": 3, "fall": 3, "winter": 4}
_WEATHERS = {"sunny": 1, "cloudy": 2, "rain": 3, "rainy": 3, "snow": 4, "snowy": 4}


@dataclass(frozen=True)
class ScheduledCm:
    key: str
    name: str
    start: int
    end: int
    choice: CourseChoice


def _unix(value) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def parse_cm(raw: dict, data: GameData) -> Optional[ScheduledCm]:
    """One uma.moe Champions Meeting entry, or None if its race can't be matched to a course."""
    parts = [p.strip() for p in re.split(r"<br\s*/?>", raw.get("description") or "")]
    if len(parts) < 3:
        return None
    m_track = re.fullmatch(r"(.+?) - (Turf|Dirt)", parts[0])
    m_dist = re.match(r"(\d+)m\b", parts[1])
    start = _unix(raw.get("global_release_date"))
    if not (m_track and m_dist and start):
        return None
    track = m_track.group(1).strip().lower()
    track = _TRACKS.get(track, track)
    surface = 1 if m_track.group(2) == "Turf" else 2
    distance = int(m_dist.group(1))
    matches = [c for c in data.courses.values()
               if c.track.lower() == track and c.surface == surface and c.distance == distance]
    if not matches:
        return None
    text = " ".join(parts).lower()
    side = 2 if "inner" in text else 3 if "outer" in text else None
    # Kyoto 1400/1600 and Niigata 2000 come in two layouts; their graded races use the outer one.
    course = next((c for c in matches if c.inout == side), None) or \
        next((c for c in matches if c.inout == 3), matches[0])
    words = [w.strip().lower() for w in parts[2].split(" - ")]
    env = RaceEnv(
        ground=next((_GROUNDS[w] for w in words if w in _GROUNDS), 1),
        season=next((_SEASONS[w] for w in words if w in _SEASONS), 1),
        weather=next((_WEATHERS[w] for w in words if w in _WEATHERS), 1),
    )
    name = raw.get("title") or "Champions Meeting"
    end = max(_unix(raw.get("estimated_end_date")) or 0, start + CM_RUN)
    key = f"cm:{raw.get('id')}"
    return ScheduledCm(key, name, start, end, CourseChoice(key, f"{name} - {course.label}", course, env, name))


class CmSchedule:
    """Confirmed Champions Meetings from uma.moe, refreshed by the cog."""

    def __init__(self, fetch_timeline: Callable[[], Awaitable[dict]], data: GameData):
        self._fetch = fetch_timeline
        self._data = data
        self.entries: list = []
        self.updated = 0.0

    async def refresh(self) -> None:
        timeline = await self._fetch()
        entries = []
        for raw in timeline.get("events") or []:
            if raw.get("type") != "champions_meeting" or not raw.get("is_confirmed"):
                continue
            cm = parse_cm(raw, self._data)
            if cm:
                entries.append(cm)
        self.entries = sorted(entries, key=lambda e: e.start)
        self.updated = time.time()
        logger.info(f"Skill map CM schedule: {len(self.entries)} Champions Meetings from uma.moe")

    def choices(self, now: Optional[float] = None) -> list:
        """The running CM and the next one, as course choices labelled for the autocomplete."""
        now = time.time() if now is None else now
        running = [e for e in self.entries if e.start <= now < e.end]
        upcoming = [e for e in self.entries if e.start > now]
        out = []
        if running:
            cm = running[-1]
            out.append(_labelled(cm, "Current CM"))
        if upcoming:
            cm = upcoming[0]
            out.append(_labelled(cm, f"Next CM, {time.strftime('%b %d', time.gmtime(cm.start))}"))
        return out


def _labelled(cm: ScheduledCm, when: str) -> CourseChoice:
    c = cm.choice
    return CourseChoice(c.key, f"{when}: {cm.name} - {c.course.label}", c.course, c.env, cm.name)
