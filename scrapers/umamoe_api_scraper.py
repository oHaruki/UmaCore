"""
Uma.moe API scraper for club data fetching.

Slot selection lives in :mod:`utils.jst_calendar` — see that module for the
verified mapping between ``daily_fans`` indices and JST competition days.

The short version: uma.moe now updates the in-progress day's slot every ~45-60min
(``live_points``), where it used to write each slot once at the daily finalize.
Quota accounting therefore reads the *last closed* JST day, chosen from the clock
and confirmed against ``circle.last_updated`` — never by probing whether a slot
happens to be non-zero, which no longer distinguishes "finalized" from "still
running".
"""
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
import logging
import aiohttp
from datetime import datetime, date, timezone

from scrapers.base_scraper import BaseScraper, StaleDataError
from config.settings import (
    UMAMOE_API_KEY, UMAMOE_SLOT_ERROR_FRACTION,
    UMAMOE_CHECKSUM_TOLERANCE, UMAMOE_CHECKSUM_MIN_ABS,
)
from utils.rate_limiter import umamoe_limiter, PRIORITY_BACKGROUND
from utils.api_metrics import track_api_call
from utils.jst_calendar import (
    SlotTarget, resolve_finalized, resolve_live, parse_api_timestamp,
    in_progress_jst_day, ROLLOVER_UTC_HOUR,
)

logger = logging.getLogger(__name__)


@dataclass
class CircleMeta:
    """Circle-level fields from one API response."""
    name: Optional[str] = None
    member_count: Optional[int] = None
    monthly_rank: Optional[int] = None
    last_month_rank: Optional[int] = None
    yesterday_rank: Optional[int] = None
    live_rank: Optional[int] = None
    monthly_point: Optional[int] = None
    yesterday_points: Optional[int] = None
    live_points: Optional[int] = None
    last_updated: Optional[datetime] = None
    last_live_update: Optional[datetime] = None
    yesterday_updated: Optional[datetime] = None

    @classmethod
    def from_response(cls, payload: dict) -> "CircleMeta":
        c = (payload or {}).get("circle") or {}
        return cls(
            name=c.get("name"),
            member_count=c.get("member_count"),
            monthly_rank=c.get("monthly_rank"),
            last_month_rank=c.get("last_month_rank"),
            yesterday_rank=c.get("yesterday_rank"),
            live_rank=c.get("live_rank"),
            monthly_point=c.get("monthly_point"),
            yesterday_points=c.get("yesterday_points"),
            live_points=c.get("live_points"),
            last_updated=parse_api_timestamp(c.get("last_updated")),
            last_live_update=parse_api_timestamp(c.get("last_live_update")),
            yesterday_updated=parse_api_timestamp(c.get("yesterday_updated")),
        )


@dataclass
class MemberGain:
    """One member's contribution to the in-progress day."""
    trainer_id: str
    name: str
    gained_today: int
    month_total: int


@dataclass
class LiveSnapshot:
    """In-progress standing for the live board. Never persisted as a finished day."""
    circle_id: str
    jst_day: date
    as_of: Optional[datetime]
    live_points: Optional[int]
    live_rank: Optional[int]
    monthly_point: Optional[int]
    monthly_rank: Optional[int]
    yesterday_points: Optional[int] = None
    members: Dict[str, Dict] = field(default_factory=dict)
    gains: List[MemberGain] = field(default_factory=list)

    @property
    def gained_today(self) -> Optional[int]:
        """Fans earned so far in the in-progress day, club-wide.

        Summed from the member rows rather than derived from the circle totals, so
        the headline figure always agrees with the roster displayed beneath it.

        The circle-level route (``live_points - monthly_point``) is only a fallback
        now: a freshly opened competition month keeps serving the *previous*
        month's ``monthly_point`` for a while, which made that subtraction wildly
        negative and clamp to zero while members visibly had fans.
        """
        if self.gains:
            return sum(g.gained_today for g in self.gains)
        if self.live_points is None or self.monthly_point is None:
            return None
        return max(0, self.live_points - self.monthly_point)

    @property
    def rank_delta(self) -> Optional[int]:
        """Places gained since the last finalize. Positive = climbing."""
        if self.live_rank is None or self.monthly_rank is None:
            return None
        return self.monthly_rank - self.live_rank

    def top_gainers(self, n: int = 10) -> List[MemberGain]:
        return sorted(self.gains, key=lambda g: g.gained_today, reverse=True)[:n]

    @property
    def active_today(self) -> int:
        return sum(1 for g in self.gains if g.gained_today > 0)


class UmaMoeAPIScraper(BaseScraper):
    """Scraper using Uma.moe API for fast data retrieval"""

    def __init__(self, circle_id: str, now_utc: Optional[datetime] = None,
                 priority: int = PRIORITY_BACKGROUND):
        self.circle_id = circle_id
        # Interactive callers (slash commands, web API) pass
        # PRIORITY_INTERACTIVE so they aren't queued behind a scrape clump.
        self.priority = priority
        self.base_url = "https://uma.moe/api/v4/circles"
        # Injectable clock so the slot mapping is testable without freezing time.
        self._now_utc = now_utc

        self._target: Optional[SlotTarget] = None
        self._meta = CircleMeta()
        self._data_date: Optional[date] = None
        self.current_day_count = 1
        super().__init__(self.base_url)

    # ------------------------------------------------------------------ fetch

    async def _fetch_month(self, session: aiohttp.ClientSession, year: int, month: int) -> Optional[dict]:
        """Fetch API data for a specific year/month. Returns parsed JSON or None on failure."""
        params = {"circle_id": self.circle_id, "year": year, "month": month}
        # Gate every outbound request through the shared limiter so the bot stays
        # under the API's per-minute cap no matter how many clubs fire at once.
        await umamoe_limiter.acquire(self.priority)
        async with track_api_call("uma.moe", "circles", context=str(self.circle_id)) as m:
            async with session.get(self.base_url, params=params,
                                   timeout=aiohttp.ClientTimeout(total=30)) as response:
                m["status_code"] = response.status
                if response.status != 200:
                    error_text = await response.text()
                    if response.status == 429:
                        logger.error(
                            f"⛔ RATE LIMITED by Uma.moe API (429) for {year}-{month:02d} — "
                            f"slow down or check API key limits. Response: {error_text[:200]}"
                        )
                    else:
                        logger.error(
                            f"Uma.moe API returned status {response.status} "
                            f"for {year}-{month:02d}: {error_text[:200]}"
                        )
                    return None
                data = await response.json()
                m["ok"] = True
                return data

    def _session(self) -> aiohttp.ClientSession:
        headers = {"Accept-Encoding": "gzip, deflate"}
        if UMAMOE_API_KEY:
            headers["X-API-Key"] = UMAMOE_API_KEY
        return aiohttp.ClientSession(headers=headers)

    # ----------------------------------------------------------------- public

    async def scrape(self) -> Dict[str, Dict]:
        """Fetch the last fully-closed JST day's data for quota accounting.

        Raises:
            StaleDataError: the target day hasn't finalized for this circle yet.
            ValueError: the request failed or the response was unusable.
        """
        target = resolve_finalized(self._now_utc)
        self._target = target
        self._data_date = target.data_date
        self.current_day_count = target.slot + 1

        logger.info(f"Fetching circle {self.circle_id}: {target.describe()}")

        async with self._session() as session:
            payload = await self._fetch_month(session, target.year, target.month)

        if not payload:
            raise ValueError(f"API request failed for {target.year}-{target.month:02d}")

        self._meta = CircleMeta.from_response(payload)
        self._log_freshness()
        self._assert_finalized(target)
        self._drop_ranks_if_period_mismatch(target)

        members = payload.get("members")
        if members is None:
            logger.error("API response missing 'members' field")
            raise ValueError("Invalid API response structure")
        if not members:
            logger.warning("No members found in API response")
            return {}

        logger.info(f"API returned {len(members)} members")
        self._sanity_check_roster(members)

        parsed = self._parse_members(members, target.slot)
        self._verify_checksum(members, target.slot, self._meta.monthly_point, "monthly_point")
        logger.info(f"Successfully parsed {len(parsed)} active members from API")
        return parsed

    async def fetch_live(self) -> Optional[LiveSnapshot]:
        """Fetch the in-progress JST day for display purposes only.

        Returns ``None`` on any failure — the live board is best-effort and must
        never break a caller. Nothing here may be written to ``quota_history``.
        """
        target = resolve_live(self._now_utc)
        try:
            async with self._session() as session:
                payload = await self._fetch_month(session, target.year, target.month)
            if not payload:
                return None

            meta = CircleMeta.from_response(payload)
            members = payload.get("members") or []
            parsed = self._parse_members(members, target.slot) if members else {}
            gains = self._member_gains(members, target.slot)

            # A newly opened competition month exists before uma.moe publishes any
            # member rows for it — observed 2026-08-01 17:23 UTC, where August
            # returned HTTP 200 with an empty member list, null live fields and
            # July's monthly_point still attached. There is nothing to show yet, so
            # report no snapshot rather than an empty one; the caller then leaves
            # the previous board alone instead of blanking it.
            if not gains:
                logger.info(
                    f"circle {self.circle_id}: no live rows yet for "
                    f"{target.year}-{target.month:02d} slot {target.slot} "
                    f"(competition day {target.data_date}) — skipping this refresh"
                )
                return None

            # circle.live_points is null once a competition period has closed, so
            # fall back to what the member rows add up to.
            live_points = meta.live_points
            if live_points is None:
                live_points = self._checksum_total(members, target.slot) or None
            else:
                self._verify_checksum(members, target.slot, meta.live_points,
                                      "live_points")

            return LiveSnapshot(
                circle_id=str(self.circle_id),
                jst_day=target.jst_day,
                as_of=meta.last_live_update or meta.last_updated,
                live_points=live_points,
                live_rank=meta.live_rank,
                monthly_point=meta.monthly_point,
                monthly_rank=meta.monthly_rank,
                yesterday_points=meta.yesterday_points,
                members=parsed,
                gains=gains,
            )
        except Exception as e:
            logger.warning(f"Live fetch failed for circle {self.circle_id}: {e}")
            return None

    # ------------------------------------------------------------- freshness

    def _log_freshness(self) -> None:
        m = self._meta
        final = m.last_updated.isoformat() if m.last_updated else "?"
        live = m.last_live_update.isoformat() if m.last_live_update else "?"
        points = f"{m.monthly_point:,}" if m.monthly_point is not None else "?"
        logger.info(
            f"🕒 circle {self.circle_id} freshness: last_updated={final}, "
            f"last_live_update={live}, monthly_point={points}"
        )

    def _assert_finalized(self, target: SlotTarget) -> None:
        """Confirm uma.moe has finalized the target JST day for this circle.

        JST day N closes at ``N 15:00 UTC``, so a write at or after that moment
        means the slot holds a settled value rather than a running total.

        Uses the newest of ``yesterday_updated`` and ``last_updated``. The former
        is the finalize marker proper — measured 2026-08-02, every circle reported
        exactly ``15:01:00Z``. ``last_updated`` alone is not safe to gate on: it is
        touched at unrelated times, and around the 2026-08 rollover it read
        ``10:00:28Z`` on a day that had already finalized at 15:01, which failed
        this check, exhausted the scheduler's retries and lost a day of history.

        Replaces the older "did any member's fan count grow?" heuristic, which
        could not tell a not-yet-published day from a genuinely quiet one.
        """
        closed_at = datetime(
            target.jst_day.year, target.jst_day.month, target.jst_day.day,
            ROLLOVER_UTC_HOUR, 0, tzinfo=timezone.utc,
        )
        stamps = [t for t in (self._meta.yesterday_updated, self._meta.last_updated)
                  if t is not None]
        if not stamps:
            # Nothing to check against — don't block on a missing field.
            logger.warning(
                f"circle {self.circle_id}: no finalize timestamp in response, "
                f"proceeding without a freshness guarantee"
            )
            return

        newest = max(stamps)
        if newest < closed_at:
            raise StaleDataError(
                f"JST day {target.jst_day} closed at {closed_at.isoformat()} but uma.moe "
                f"last wrote circle {self.circle_id} at {newest.isoformat()} — "
                f"the rollout hasn't reached this circle yet."
            )

    def _drop_ranks_if_period_mismatch(self, target: SlotTarget) -> None:
        """Rank fields always describe the *current* JST competition month.

        Around a month boundary the target day can belong to the period that just
        ended, in which case the ranks in this response describe a different
        (nearly empty) period. Drop them rather than display something false.
        """
        current_period = in_progress_jst_day(self._now_utc)
        if (current_period.year, current_period.month) == (target.jst_day.year, target.jst_day.month):
            return
        logger.warning(
            f"Competition period rollover: target JST day {target.jst_day} belongs to "
            f"{target.jst_day.year}-{target.jst_day.month:02d} but uma.moe is now reporting "
            f"{current_period.year}-{current_period.month:02d}. Dropping rank data."
        )
        self._meta.monthly_rank = None
        self._meta.last_month_rank = None
        self._meta.yesterday_rank = None

    def _sanity_check_roster(self, members: List[dict]) -> None:
        """The members array retains people who left mid-month, so it should never
        be *shorter* than the live roster. Shorter means a truncated response."""
        expected = self._meta.member_count
        if expected and len(members) < expected:
            logger.warning(
                f"circle {self.circle_id}: response has {len(members)} member rows but "
                f"member_count is {expected} — response may be incomplete."
            )

    def _reference_day_gain(self) -> Optional[int]:
        """One full day of club-wide fan gain, used to scale the checksum.

        ``monthly_point`` and ``yesterday_points`` are both finalized totals
        exactly one JST day apart, so their difference is a complete day.
        """
        m = self._meta
        if m.monthly_point is None or m.yesterday_points is None:
            return None
        gain = m.monthly_point - m.yesterday_points
        return gain if gain > 0 else None

    @staticmethod
    def _member_gains(members: List[dict], live_slot: int) -> List["MemberGain"]:
        """Per-member fans earned in the in-progress day.

        The live slot holds each member's running total including today; the slot
        before it is yesterday's close. The difference is today's contribution.

        Needs no month-boundary case: the slot is the competition day, so the
        preceding day is always slot-1 in the same array, and slot 0 is the
        month's baseline.
        """
        gains: List[MemberGain] = []
        prev_slot = live_slot - 1
        if prev_slot < 0:
            return gains

        for m in members:
            fans = m.get("daily_fans") or []
            if live_slot >= len(fans) or fans[live_slot] <= 0:
                continue                      # left the club, or not racing yet
            prev = fans[prev_slot] if prev_slot < len(fans) else 0
            if prev <= 0:
                prev = fans[live_slot]        # joined today: no prior day to diff

            gained = max(0, fans[live_slot] - prev)
            baseline = next((v for v in fans[:live_slot + 1] if v > 0), 0)
            month_total = max(0, fans[live_slot] - baseline)

            gains.append(MemberGain(
                trainer_id=str(m.get("viewer_id")),
                name=m.get("trainer_name") or "?",
                gained_today=gained,
                month_total=month_total,
            ))
        return gains

    @staticmethod
    def _checksum_total(members: List[dict], slot: int) -> int:
        """Club total computed the way uma.moe's ``monthly_point`` is.

        Counts members who have since left: their gains up to their last active
        day still belong to the club's month, even though the parsed output drops
        them (their target slot reads 0). Without this, a productive member
        leaving late in the month produces a discrepancy larger than a full day's
        fans and looks exactly like a slot-index error.
        """
        total = 0
        for m in members:
            window = (m.get("daily_fans") or [])[:slot + 1]
            positives = [v for v in window if v > 0]
            if len(positives) < 2:
                continue
            total += positives[-1] - positives[0]
        return total

    def _verify_checksum(self, members: List[dict], slot: int,
                         expected_total: Optional[int], label: str) -> None:
        """Cross-check our own club total against uma.moe's.

        Reading the wrong slot puts us off by about one day's fans, so that is
        what the discrepancy is measured against — not a percentage of the month.
        The leftover residual is a few million fans in absolute terms regardless
        of club size, which is a large *percentage* on a small club (measured
        0.17% on a 1.66B club vs 2.62% on a 125M one, both ~2-17% of a day).
        """
        if not expected_total or not members:
            return

        ours = self._checksum_total(members, slot)
        if not ours:
            return
        diff = abs(ours - expected_total)

        day_gain = self._reference_day_gain()
        if day_gain:
            ratio = diff / day_gain
            if ratio > UMAMOE_SLOT_ERROR_FRACTION:
                logger.error(
                    f"⚠️ circle {self.circle_id}: parsed total {ours:,} differs from "
                    f"{label} {expected_total:,} by {diff:,} — that is {ratio:.0%} of a "
                    f"day's fans ({day_gain:,}), so we are likely reading the wrong "
                    f"slot. Check utils/jst_calendar.py against the API."
                )
            else:
                logger.debug(
                    f"Checksum OK vs {label}: {ours:,} vs {expected_total:,} "
                    f"(off by {diff:,} = {ratio:.1%} of a day)"
                )
            return

        # No full-day reference means yesterday_points is absent, which in practice
        # only happens while a competition period is turning over — and during that
        # window the circle totals themselves are in flux. Measured across the
        # 2026-08 rollover: July's monthly_point fell from 1,837,269,789 to
        # 1,165,044,213 between two reads while the member rows stayed put, so
        # comparing against it flagged every club at 63-91%.
        #
        # Skip rather than compare against a figure known to be unreliable. A real
        # slot-index error still shows up on any ordinary day, when the reference
        # is sound.
        logger.debug(
            f"Checksum skipped for circle {self.circle_id} vs {label}: no "
            f"yesterday_points to scale against (parsed {ours:,}, {label} "
            f"{expected_total:,}, {diff / expected_total:.1%} apart)"
        )

    # --------------------------------------------------------------- parsing

    def _parse_members(self, members: List[dict], slot: int) -> Dict[str, Dict]:
        """Convert lifetime cumulative fans into monthly cumulative fans up to ``slot``.

        Uma.moe returns LIFETIME totals. Monthly is derived by subtracting each
        member's first non-zero entry (their total when they joined / the month
        began). That convention is preserved from the original implementation —
        it reproduces uma.moe's own ``monthly_point`` to within ~0.3%.
        """
        parsed: Dict[str, Dict] = {}

        for member in members:
            viewer_id = member.get("viewer_id")
            trainer_name = member.get("trainer_name")
            lifetime_fans = member.get("daily_fans") or []

            if not viewer_id or not trainer_name:
                logger.warning(
                    f"Skipping member with missing data: viewer_id={viewer_id}, name={trainer_name}"
                )
                continue

            if slot >= len(lifetime_fans):
                logger.warning(
                    f"Slot {slot} exceeds array length {len(lifetime_fans)} for {trainer_name}"
                )
                continue

            if lifetime_fans[slot] == 0:
                logger.debug(f"Skipping inactive member (left club): {trainer_name} (ID: {viewer_id})")
                continue

            join_day, baseline = self._detect_baseline(lifetime_fans, slot)

            # Negative values are transfer markers — treat them as 0.
            monthly_fans = [
                0 if lifetime_fans[i] <= 0 else lifetime_fans[i] - baseline
                for i in range(slot + 1)
            ]

            parsed[str(viewer_id)] = {
                "name": trainer_name,
                "trainer_id": str(viewer_id),
                "fans": monthly_fans,
                "join_day": join_day,
            }

            logger.debug(
                f"Parsed {trainer_name}: joined day {join_day}, "
                f"lifetime: {baseline:,} → {lifetime_fans[slot]:,}, "
                f"monthly: {monthly_fans[-1]:,}"
            )

        return parsed

    @staticmethod
    def _detect_baseline(lifetime_fans: List[int], slot: int) -> Tuple[int, int]:
        """Return ``(join_day, starting_lifetime_fans)`` for a member.

        Transferred members carry their pre-existing career total into the array,
        so it can be flat and positive from day 1 rather than starting at 0 — the
        first positive day is just their old total, not when they joined. If that
        value never changes across the whole window, there's no evidence of
        progress since then (an active member would show growth), so treat them as
        having joined today rather than back-charging quota for unconfirmed days.
        """
        window = lifetime_fans[:slot + 1]
        baseline_idx = next((i for i, f in enumerate(window) if f > 0), None)

        if baseline_idx is None:
            return max(1, slot), 0

        baseline = window[baseline_idx]
        fully_flat = all(f == baseline for f in window[baseline_idx:])
        # The slot index IS the competition day, so a member first seen at slot D
        # joined on day D. Slot 0 is the month's baseline rather than a raceable
        # day, so anyone present there counts as joining on day 1.
        join_day = slot if fully_flat else max(1, baseline_idx)
        return join_day, baseline

    # --------------------------------------------------------------- getters

    def get_current_day(self) -> int:
        """1-based day number of the slot that was read."""
        return self.current_day_count

    def get_data_date(self) -> Optional[date]:
        """Calendar date the scraped data is attributed to."""
        return self._data_date

    def get_slot_target(self) -> Optional[SlotTarget]:
        """The resolved slot for the last scrape (diagnostics)."""
        return self._target

    def get_fetched_period(self) -> Tuple[Optional[int], Optional[int]]:
        """``(year, month)`` of the data array actually fetched.

        Differs from the calendar month around a boundary, where the last closed
        JST day still belongs to the previous month.
        """
        if self._target is None:
            return None, None
        return self._target.year, self._target.month

    def get_monthly_rank(self) -> Optional[int]:
        return self._meta.monthly_rank

    def get_last_month_rank(self) -> Optional[int]:
        return self._meta.last_month_rank

    def get_yesterday_rank(self) -> Optional[int]:
        return self._meta.yesterday_rank

    def get_meta(self) -> CircleMeta:
        """Full circle-level metadata from the last response."""
        return self._meta

    def get_freshness(self) -> Dict:
        """Freshness timestamps from the last response, for diagnostics."""
        m = self._meta
        return {
            "last_updated": m.last_updated.isoformat() if m.last_updated else None,
            "last_live_update": m.last_live_update.isoformat() if m.last_live_update else None,
            "yesterday_updated": m.yesterday_updated.isoformat() if m.yesterday_updated else None,
            "monthly_point": m.monthly_point,
            "live_points": m.live_points,
        }
