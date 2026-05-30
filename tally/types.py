"""Shared types for the tally renderer — decoupled from rat's original package."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

PillTier = Literal["done", "yes", "no"]


@dataclass(frozen=True)
class MemberReport:
    viewer_id: int
    trainer_name: str
    days_elapsed: int
    total: int
    previous_total: int
    daily_avg: float
    expected_so_far: int
    quota_total: int
    on_target: bool
    off_by: int
    needed_per_day: float
    low_days: int
    latest_day_delta: int
    join_day: int
    pill_tier: PillTier
    staff_role: Optional[str] = None


@dataclass
class Circle:
    name: str
    monthly_rank: Optional[int] = None


@dataclass
class Config:
    monthly_quota: int
    low_day_threshold: int = 500_000
    expected_fans_style: str = "numbers"
    show_daily_avg: bool = True
    show_on_pace: bool = True
    show_needed_per_day: bool = True
    show_days_below_threshold: bool = False
    show_latest_day: bool = True
    pin_leader: bool = False
    highlight_leader: bool = False
    on_pace_color: tuple = (59, 130, 246)
    finished_color: tuple = (37, 99, 235)
    off_pace_color: tuple = (210, 70, 70)
    club_logo: Optional[Path] = None
