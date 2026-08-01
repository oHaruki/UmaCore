"""JST competition-day arithmetic for the uma.moe ``daily_fans`` array.

uma.moe runs on JST. Everything about which array slot to read follows from that,
so this module owns the mapping and nothing else in the codebase should be doing
day arithmetic against ``daily_fans`` by hand.

Verified against the live API on 2026-07-25 (two independent circles, member sums
reconciled against the circle totals to <0.5%):

    daily_fans[i]  ==  each member's lifetime fan total at the END of JST day i+1

    JST day N spans  (N-1) 15:00 UTC  ->  N 15:00 UTC

So on a given moment there are exactly two slots of interest:

    live slot   = the JST day currently being raced   -> matches circle.live_points
    final slot  = the last JST day that has closed    -> matches circle.monthly_point

Concretely, at 08:10 UTC on 2026-07-25 (= 17:10 JST, JST day 25 in progress):

    slot[22] == circle.yesterday_points   (JST day 23, closed)
    slot[23] == circle.monthly_point      (JST day 24, last closed)  <- final
    slot[24] == circle.live_points        (JST day 25, in progress)  <- live
    slot[25] == 0                         (not started)

**Only the final slot may be used for quota accounting.** The live slot changes
every ~45-60min while the day is still running; recording it as a finished day
under-reports every member.

Date attribution
----------------
``data_date`` deliberately reproduces the labelling the bot has always used
(``jst_day - 1``) rather than the JST day number. Existing ``quota_history`` rows
are keyed on that convention and ``calculate_expected_fans`` counts days from
``join_date`` to ``data_date``, so re-labelling would shift every historical row
and silently change everyone's quota math. The bug this module fixes is *which
slot gets read*, not what the resulting day is called.
"""
import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

# uma.moe's competition clock. Fixed offset — JST has no DST.
JST = timezone(timedelta(hours=9))

# The daily finalization runs at JST midnight. Kept as a named constant because
# several log messages and the freshness check refer to it.
ROLLOVER_UTC_HOUR = 15


@dataclass(frozen=True)
class SlotTarget:
    """Which ``daily_fans`` slot to read, and what it means.

    Attributes:
        year:      calendar year of the month whose array holds this slot
        month:     calendar month of that array
        slot:      0-based index into ``daily_fans``
        jst_day:   the JST competition day this slot represents
        data_date: calendar date to attribute the data to (legacy labelling)
        is_live:   True when the day is still in progress (never persist these)
    """
    year: int
    month: int
    slot: int
    jst_day: date
    data_date: date
    is_live: bool

    @property
    def is_cross_month(self) -> bool:
        """True when the slot lives in a different month than ``data_date``.

        True when the slot is the previous month's rollover entry — i.e. this JST
        day is the 1st and its data lives in the prior month's array. Callers that
        derive a monthly total need to know, because the usual "first populated
        slot" baseline belongs to the wrong month.
        """
        return (self.year, self.month) != (self.jst_day.year, self.jst_day.month)

    def describe(self) -> str:
        kind = "live/in-progress" if self.is_live else "finalized"
        return (f"{self.year}-{self.month:02d} slot[{self.slot}] "
                f"= JST day {self.jst_day.isoformat()} ({kind}), "
                f"reported as {self.data_date.isoformat()}")


def jst_now(now_utc: Optional[datetime] = None) -> datetime:
    """Current time on uma.moe's clock."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(JST)


def in_progress_jst_day(now_utc: Optional[datetime] = None) -> date:
    """The JST competition day currently being raced (matches ``live_points``)."""
    return jst_now(now_utc).date()


def last_closed_jst_day(now_utc: Optional[datetime] = None) -> date:
    """The most recent JST day that has finalized (matches ``monthly_point``)."""
    return in_progress_jst_day(now_utc) - timedelta(days=1)


def slot_for_jst_day(jst_day: date) -> int:
    """0-based index into that month's ``daily_fans`` for a JST day."""
    return jst_day.day - 1


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def slot_location(jst_day: date) -> Tuple[int, int, int]:
    """Which ``(year, month, slot)`` actually holds this JST day's data.

    Mostly ``(its own month, day - 1)``, with one exception that matters.

    Every ``daily_fans`` array is 32 slots regardless of month length, and the
    slot past the month's last day holds **day 1 of the following month**.
    Verified 2026-08-01 on circle 860280110: June (30 days) populates slots 1..30
    with slot[30] = July 1, and July (31 days) populates 0..31 with slot[31] =
    August 1. Cross-checked — June slot[30] equals July slot[0] for members
    present in both.

    This matters because uma.moe rejects a request for a month it considers
    unstarted (``HTTP 400 "circle month cannot be in the future"``), so on day 1
    the new month is not fetchable at all — its data is only reachable through the
    previous month's rollover slot. Reading day 1 from the previous month also
    keeps the previous day in the *same* array, so day-over-day differences stay
    computable across the boundary.
    """
    if jst_day.day != 1:
        return jst_day.year, jst_day.month, jst_day.day - 1
    prev = jst_day - timedelta(days=1)          # last day of the previous month
    return prev.year, prev.month, days_in_month(prev.year, prev.month)


def _target(jst_day: date, is_live: bool) -> SlotTarget:
    year, month, slot = slot_location(jst_day)
    return SlotTarget(
        year=year,
        month=month,
        slot=slot,
        jst_day=jst_day,
        # Legacy labelling — see module docstring.
        data_date=jst_day - timedelta(days=1),
        is_live=is_live,
    )


def resolve_finalized(now_utc: Optional[datetime] = None) -> SlotTarget:
    """The slot safe to persist: the last fully-closed JST day.

    This is what quota accounting must read. Replaces the old
    "probe whether slot > 0 and fall back a day" heuristic, which broke once
    uma.moe started populating the in-progress slot live.

    One exception, at the month boundary. Monthly fan totals are measured from a
    member's first populated slot, so on JST day 1 of a month every member's
    monthly total is zero by construction — there is no earlier slot to subtract
    from. Reporting that would show a whole club dropping to 0, and because
    ``data_date`` is ``jst_day - 1`` it would write those zeros under a
    *previous-month* date, on top of real history.

    So day 1 is deferred: keep reporting the previous month's final day until the
    new month has a second closed day. That is what the old Day-1 branch achieved
    by fetching the previous month, and it keeps reported dates gap-free. The cost
    is that the previous month's last row is rewritten with identical values for a
    day or two, which the ``(member_id, date)`` upsert absorbs.
    """
    closed = last_closed_jst_day(now_utc)
    if closed.day == 1:
        closed -= timedelta(days=1)
    return _target(closed, is_live=False)


def resolve_live(now_utc: Optional[datetime] = None) -> SlotTarget:
    """The in-progress slot: display only, never persisted as a finished day."""
    return _target(in_progress_jst_day(now_utc), is_live=True)


def next_rollover_utc(now_utc: Optional[datetime] = None) -> datetime:
    """When the current JST day will finalize (next 15:00 UTC)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    rollover = now_utc.replace(hour=ROLLOVER_UTC_HOUR, minute=0, second=0, microsecond=0)
    if rollover <= now_utc:
        rollover += timedelta(days=1)
    return rollover


def parse_api_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse a uma.moe ISO timestamp (``2026-07-25T06:20:01Z``) as aware UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
