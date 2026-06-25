"""Builds a trainer card PNG for a single member.

Gathers the same status data the old ``/my_status`` embed showed (quota
progress, deficit, bomb, streak, club rank) straight from the DB, optionally
enriches it with the uma.moe trainer profile (team rating, global fan ranks,
follower count), and hands it to the Pillow renderer.

Returns a path to a temp PNG; the caller owns deletion.
"""
import asyncio
import logging
import tempfile
from datetime import date as date_class
from pathlib import Path
from typing import Optional

from models import Member, QuotaHistory, Bomb, Club, QuotaRequirement
from scrapers import fetch_trainer_profile
from services.portrait_cache import get_portrait_path
from tally.render.trainer_card import TrainerCardData, render

logger = logging.getLogger(__name__)


def _safe_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _pick_monthly_entry(monthly: list, today: date_class) -> Optional[dict]:
    """Choose the fan-ranking row for the current month, else the newest."""
    if not monthly:
        return None
    for row in monthly:
        if row.get("year") == today.year and row.get("month") == today.month:
            return row
    # Fall back to the most recent (sort by year, month descending)
    try:
        return sorted(
            monthly,
            key=lambda r: (r.get("year", 0), r.get("month", 0)),
            reverse=True,
        )[0]
    except Exception:
        return monthly[0]


async def build_trainer_card_data(member: Member) -> Optional[TrainerCardData]:
    """Assemble all card data for a member, or None if there's no quota data."""
    latest = await QuotaHistory.get_latest_for_member(member.member_id)
    if not latest:
        return None

    active_bomb = await Bomb.get_active_for_member(member.member_id)
    club = await Club.get_by_id(member.club_id)
    club_name = club.club_name if club else "Unknown Club"

    if club:
        daily_quota = await QuotaRequirement.get_quota_for_date(club.club_id, date_class.today())
    else:
        daily_quota = 1_000_000

    # Progress %
    if latest.expected_fans > 0:
        progress_pct = int((latest.cumulative_fans / latest.expected_fans) * 100)
    else:
        progress_pct = 0

    # History-derived stats
    history = await QuotaHistory.get_last_n_days(member.member_id, 100)
    days_active = len(history) if history else 1
    avg_daily = latest.cumulative_fans / max(1, days_active)

    streak_days = 0
    if latest.deficit_surplus >= 0 and history:
        streak_days = 1
        for record in history[1:]:
            if record.deficit_surplus >= 0:
                streak_days += 1
            else:
                break

    best_day = 0
    for i in range(len(history) - 1):
        gain = history[i].cumulative_fans - history[i + 1].cumulative_fans
        if gain > best_day:
            best_day = gain

    # Chronological daily series for the progression sparkline. History is newest
    # first; reverse it and keep only the current reporting month so the line
    # reflects this month's cumulative climb (fans reset monthly).
    chrono = sorted(history, key=lambda r: r.date)
    chrono = [r for r in chrono if r.date.year == latest.date.year and r.date.month == latest.date.month]
    daily_cumulative = [r.cumulative_fans for r in chrono]
    daily_expected = [r.expected_fans for r in chrono]

    # Club rank by surplus/deficit
    club_rank, club_total, percentile = await _compute_club_rank(member)

    # uma.moe enrichment (best-effort)
    api = await _fetch_api_fields(member, date_class.today())

    # Resolve the leader portrait (downloads + caches once per character)
    portrait_path = None
    leader_card = api.get("leader_chara_dress_id")
    if leader_card:
        p = await get_portrait_path(leader_card)
        portrait_path = str(p) if p else None

    return TrainerCardData(
        trainer_name=member.trainer_name,
        trainer_id=member.trainer_id,
        club_name=club_name,
        is_active=member.is_active,
        manually_deactivated=member.manually_deactivated,
        join_date=member.join_date,
        last_updated=latest.date,
        cumulative_fans=latest.cumulative_fans,
        expected_fans=latest.expected_fans,
        deficit_surplus=latest.deficit_surplus,
        days_behind=latest.days_behind,
        daily_quota=daily_quota,
        progress_pct=progress_pct,
        avg_daily=avg_daily,
        best_day=best_day,
        streak_days=streak_days,
        days_active=days_active,
        club_rank=club_rank,
        club_total=club_total,
        percentile=percentile,
        bomb_days_remaining=active_bomb.days_remaining if active_bomb else None,
        bomb_warning=(active_bomb is None and latest.days_behind == 2),
        daily_cumulative=daily_cumulative,
        daily_expected=daily_expected,
        portrait_path=portrait_path,
        **api,
    )


async def _compute_club_rank(member: Member) -> tuple:
    """Return (rank, total, percentile) for the member within their club."""
    all_members = await Member.get_all_active(member.club_id)
    rankings = []
    for m in all_members:
        h = await QuotaHistory.get_latest_for_member(m.member_id)
        if h:
            rankings.append((m.member_id, h.deficit_surplus))
    rankings.sort(key=lambda x: x[1], reverse=True)

    rank = 0
    for idx, (mid, _) in enumerate(rankings, start=1):
        if mid == member.member_id:
            rank = idx
            break
    total = len(rankings)
    percentile = 100 - int((rank / total) * 100) if total > 0 else 0
    return rank, total, percentile


async def _fetch_api_fields(member: Member, today: date_class) -> dict:
    """Pull optional uma.moe profile fields. Always returns a dict (may be empty-ish)."""
    fields = {
        "has_api_data": False,
        "team_evaluation_point": None,
        "team_class": None,
        "follower_num": None,
        "rank_score": None,
        "comment": None,
        "leader_chara_dress_id": None,
        "club_monthly_rank": None,
        "monthly_fan_rank": None,
        "gain_30d": None,
        "gain_30d_rank": None,
        "alltime_rank": None,
        "alltime_total_fans": None,
    }

    profile = await fetch_trainer_profile(member.trainer_id)
    if not profile:
        return fields

    trainer = profile.get("trainer") or {}
    circle = profile.get("circle") or {}
    fan_history = profile.get("fan_history") or {}

    fields["team_evaluation_point"] = _safe_int(trainer.get("team_evaluation_point"))
    fields["team_class"] = _safe_int(trainer.get("team_class") or trainer.get("best_team_class"))
    fields["follower_num"] = _safe_int(trainer.get("follower_num"))
    fields["rank_score"] = _safe_int(trainer.get("rank_score"))
    fields["comment"] = trainer.get("comment")
    fields["leader_chara_dress_id"] = _safe_int(trainer.get("leader_chara_dress_id"))
    fields["club_monthly_rank"] = _safe_int(circle.get("monthly_rank"))

    monthly_entry = _pick_monthly_entry(fan_history.get("monthly") or [], today)
    if monthly_entry:
        fields["monthly_fan_rank"] = _safe_int(monthly_entry.get("rank"))

    rolling = fan_history.get("rolling") or {}
    fields["gain_30d"] = _safe_int(rolling.get("gain_30d"))
    fields["gain_30d_rank"] = _safe_int(rolling.get("rank_30d"))

    alltime = fan_history.get("alltime") or {}
    fields["alltime_rank"] = _safe_int(alltime.get("rank"))
    fields["alltime_total_fans"] = _safe_int(alltime.get("total_fans"))

    # Consider enrichment successful if we got any of the headline fields.
    fields["has_api_data"] = any(
        fields[k] is not None
        for k in ("team_evaluation_point", "follower_num", "rank_score", "monthly_fan_rank")
    )
    return fields


async def generate_trainer_card(member: Member) -> Optional[Path]:
    """Build and render a trainer card PNG. Returns the temp path, or None."""
    data = await build_trainer_card_data(member)
    if data is None:
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    out_path = Path(tmp.name)

    await asyncio.get_running_loop().run_in_executor(None, lambda: render(data, out_path))
    logger.info(f"Trainer card generated for {member.trainer_name} → {out_path}")
    return out_path
