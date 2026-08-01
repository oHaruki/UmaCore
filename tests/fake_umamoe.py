"""A fake uma.moe circle API, modelled on responses captured 2026-07-25.

Exists so the JST slot logic can be driven across a whole month — including the
month boundary, which is otherwise only reachable by waiting for one.

Semantics reproduced from the live API (circles 452414222 / 883951941):

* ``daily_fans`` is always **32 slots**, whatever the month's length
* slot 0 is the baseline (the total at the end of the previous month) and slots
  1..N are the N competition days, so slot ``i`` of month M is JST day
  ``date(M, 1) + i days`` — the last slot falls on the 1st of the NEXT month
* JST day N spans ``(N-1) 15:00 UTC -> N 15:00 UTC``, and its racing counts
  towards competition day N-1
* the in-progress day's slot is populated and **grows through the day**
* days not yet raced, and slots past the month's length, are ``0``
* a member who left reads ``0`` from their leave day on (observed: "Sven")
* a transferred member can carry a **negative** marker (observed: "Histo", -707M)
* ``last_updated`` is per-circle, written at the 15:00 UTC finalize
* ``last_live_update`` is global and advances every ~45-60min
* ``monthly_point`` counts members who have since left, which the scraper skips —
  this is the mechanism behind the few-million-fan residual seen on real clubs
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

JST = timezone(timedelta(hours=9))
ARRAY_SLOTS = 32
ROLLOVER_HOUR = 15


def days_in_month(year: int, month: int) -> int:
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    return (nxt - timedelta(days=1)).day


def jst_day_of(now_utc: datetime) -> date:
    """The JST competition day in progress at this UTC moment."""
    return now_utc.astimezone(JST).date()


def jst_day_progress(now_utc: datetime) -> float:
    """How far through the in-progress JST day we are, in [0, 1)."""
    jst = now_utc.astimezone(JST)
    start = jst.replace(hour=0, minute=0, second=0, microsecond=0)
    return (jst - start).total_seconds() / 86400.0


@dataclass
class FakeMember:
    viewer_id: int
    name: str
    start_lifetime: int          # lifetime total before the simulation's first day
    daily_gain: int
    joined: date                 # first JST day they race for this club
    left: Optional[date] = None  # first JST day they are gone
    transfer_marker: bool = False  # emit a negative value on their first slot

    def raced_days_through(self, day: date, sim_start: date) -> int:
        """Days raced from the later of (sim_start, joined) through `day`."""
        first = max(self.joined, sim_start)
        if day < first:
            return 0
        last = day if self.left is None else min(day, self.left - timedelta(days=1))
        return max(0, (last - first).days + 1)

    def lifetime_at_end_of(self, day: date, sim_start: date) -> int:
        return self.start_lifetime + self.daily_gain * self.raced_days_through(day, sim_start)

    def is_member_on(self, day: date) -> bool:
        return day >= self.joined and (self.left is None or day < self.left)


@dataclass
class FakeUmaMoe:
    members: List[FakeMember]
    circle_id: int = 452414222
    name: str = "FakeCircle"
    sim_start: date = date(2026, 6, 1)
    finalize_lag_sec: int = 44      # observed: last_updated at 15:00:44
    live_interval_min: int = 50
    calls: List[tuple] = field(default_factory=list)

    # ---------------------------------------------------------------- helpers

    def _slot_value(self, m: FakeMember, year: int, month: int, slot: int,
                    now_utc: datetime) -> int:
        """What `daily_fans[slot]` holds for this member right now."""
        if slot > days_in_month(year, month):
            return 0                                  # past the month's last slot
        # slot i is JST day (first of month + i): slot 0 is the baseline day and
        # the final slot lands on the 1st of the following month.
        day = date(year, month, 1) + timedelta(days=slot)

        if not m.is_member_on(day):
            # Left the club, or hadn't joined. A transferred member emits a
            # negative marker on the slot before their first raced day.
            if m.transfer_marker and day == m.joined - timedelta(days=1):
                return -m.start_lifetime
            return 0

        in_progress = jst_day_of(now_utc)
        if day > in_progress:
            return 0                                  # not raced yet
        if day == in_progress:
            # Live: yesterday's close plus a partial day.
            base = m.lifetime_at_end_of(day - timedelta(days=1), self.sim_start)
            return base + int(m.daily_gain * jst_day_progress(now_utc))
        return m.lifetime_at_end_of(day, self.sim_start)

    def _member_baseline(self, m: FakeMember, year: int, month: int,
                         now_utc: datetime) -> int:
        """The member's first positive slot value for this month.

        Measured on real clubs, uma.moe's ``monthly_point`` reconciles with
        ``sum(fans[slot] - fans[first_positive])`` to within ~0.2-0.8%, so that is
        the baseline the fake uses too. (Using the true end-of-previous-month
        value instead would put the fake a full day out of step with the API.)
        """
        for i in range(days_in_month(year, month) + 1):
            v = self._slot_value(m, year, month, i, now_utc)
            if v > 0:
                return v
        return 0

    def _month_total_at(self, year: int, month: int, slot: int,
                        now_utc: datetime) -> int:
        """uma.moe's own club total: everyone's gains this month, leavers included."""
        if slot < 0 or slot > days_in_month(year, month):
            return 0
        total = 0
        month_start = date(year, month, 1)
        for m in self.members:
            v = self._slot_value(m, year, month, slot, now_utc)
            baseline = self._member_baseline(m, year, month, now_utc)
            if v > 0:
                total += v - baseline
            elif m.left is not None and month_start <= m.left <= month_start + timedelta(days=slot):
                # Left mid-month: their earned fans still count for the club, but
                # their slot reads 0 so the scraper cannot see them. This is the
                # mechanism behind the residual observed on real clubs.
                total += max(0, m.lifetime_at_end_of(m.left - timedelta(days=1),
                                                     self.sim_start) - baseline)
        return total

    def _last_finalize_utc(self, now_utc: datetime) -> datetime:
        """The 15:00 UTC finalize that most recently ran for this circle.

        Includes ``finalize_lag_sec``, so a moment between 15:00 and the lag
        correctly reports *yesterday's* finalize — the circle hasn't been written
        yet. Without this the fake would hand back a future timestamp.
        """
        f = now_utc.replace(hour=ROLLOVER_HOUR, minute=0, second=0, microsecond=0)
        stamp = f + timedelta(seconds=self.finalize_lag_sec)
        if stamp > now_utc:
            stamp = f - timedelta(days=1) + timedelta(seconds=self.finalize_lag_sec)
        return stamp

    # ---------------------------------------------------------------- payload

    def payload(self, year: int, month: int, now_utc: datetime) -> dict:
        """The response uma.moe would return for (circle, year, month) right now."""
        self.calls.append((year, month, now_utc))

        in_progress = jst_day_of(now_utc)
        closed = in_progress - timedelta(days=1)

        def slot_of(jst: date):
            """Slot in THIS month's array for a JST day, or None if out of range."""
            offset = (jst - date(year, month, 1)).days
            return offset if 0 <= offset <= days_in_month(year, month) else None

        live_slot = slot_of(in_progress)
        final_slot = slot_of(closed)

        members = []
        for m in self.members:
            fans = [self._slot_value(m, year, month, i, now_utc) for i in range(ARRAY_SLOTS)]
            members.append({
                "id": m.viewer_id, "circle_id": self.circle_id,
                "viewer_id": m.viewer_id, "trainer_name": m.name,
                "shame_score": 0, "year": year, "month": month,
                "daily_fans": fans,
                "last_updated": self._live_stamp(now_utc).isoformat().replace("+00:00", "Z"),
            })

        prev_slot = slot_of(closed - timedelta(days=1))

        circle = {
            "circle_id": self.circle_id, "name": self.name,
            "member_count": sum(1 for m in self.members if m.is_member_on(in_progress)),
            "created_at": "2025-07-11T05:31:17Z", "archived": False,
            "last_updated": self._last_finalize_utc(now_utc).isoformat().replace("+00:00", "Z"),
            "yesterday_updated": self._last_finalize_utc(now_utc).replace(
                second=0).isoformat().replace("+00:00", "Z"),
            "last_live_update": self._live_stamp(now_utc).isoformat().replace("+00:00", "Z"),
            "monthly_rank": 101, "last_month_rank": 67, "yesterday_rank": 99, "live_rank": 101,
            "monthly_point": self._month_total_at(year, month, final_slot, now_utc)
                             if final_slot is not None else 0,
            "yesterday_points": self._month_total_at(year, month, prev_slot, now_utc)
                                if prev_slot is not None else 0,
            "live_points": self._month_total_at(year, month, live_slot, now_utc)
                           if live_slot is not None else 0,
        }
        return {"circle": circle, "members": members, "club_rank": 8,
                "fans_to_next_tier": 0, "fans_to_lower_tier": 0}

    def _live_stamp(self, now_utc: datetime) -> datetime:
        """Most recent global live refresh, quantised to the interval."""
        minutes = (now_utc.hour * 60 + now_utc.minute) // self.live_interval_min * self.live_interval_min
        return now_utc.replace(hour=minutes // 60, minute=minutes % 60, second=1, microsecond=0)


def default_roster() -> List[FakeMember]:
    """A roster covering the member shapes seen on real clubs."""
    base = date(2026, 6, 1)
    roster = [
        FakeMember(1001, "Steady", 800_000_000, 5_000_000, joined=base),
        FakeMember(1002, "Grinder", 750_000_000, 9_000_000, joined=base),
        FakeMember(1003, "Casual", 500_000_000, 1_200_000, joined=base),
        # joins partway through July
        FakeMember(1004, "LateJoin", 300_000_000, 4_000_000, joined=date(2026, 7, 9)),
        # leaves partway through July -> drives the monthly_point residual
        FakeMember(1005, "Quitter", 600_000_000, 3_000_000, joined=base,
                   left=date(2026, 7, 18)),
        # transferred in with a negative marker, like the real "Histo"
        FakeMember(1006, "Transferred", 707_279_154, 6_000_000,
                   joined=date(2026, 7, 2), transfer_marker=True),
        # joins right at the month boundary
        FakeMember(1007, "BoundaryJoin", 200_000_000, 2_500_000, joined=date(2026, 8, 1)),
    ]
    return roster
