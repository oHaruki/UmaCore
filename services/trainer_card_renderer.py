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
from tally.render.team_card import (
    TeamCardData,
    TeamMemberCard,
    render as render_team,
)

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


async def build_trainer_card_data(
    member: Member, profile: Optional[dict] = None
) -> Optional[TrainerCardData]:
    """Assemble all card data for a member, or None if there's no quota data.

    ``profile`` may be a pre-fetched uma.moe profile dict (so the orchestrator
    can fetch it once and reuse it for the team card). When ``None``, it's
    fetched here as before.
    """
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
    api = await _fetch_api_fields(member, date_class.today(), profile=profile)

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


async def _fetch_api_fields(
    member: Member, today: date_class, profile: Optional[dict] = None
) -> dict:
    """Pull optional uma.moe profile fields. Always returns a dict (may be empty-ish).

    Accepts a pre-fetched ``profile`` to avoid hitting the API twice when the
    caller also needs team-stadium data.
    """
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

    if profile is None:
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

    out_path = await _render_to_temp(lambda p: render(data, p))
    logger.info(f"Trainer card generated for {member.trainer_name} → {out_path}")
    return out_path


# ── team-stadium parsing ─────────────────────────────────────────────────────

def _first(d: dict, *keys):
    """Return the first present key's value from ``d`` (handles API name drift)."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _parse_team_member(raw: dict) -> TeamMemberCard:
    """Map one raw team_stadium entry to a TeamMemberCard (defensive on names)."""
    distance = {
        "short":  _safe_int(_first(raw, "proper_distance_short", "distance_short")),
        "mile":   _safe_int(_first(raw, "proper_distance_mile", "distance_mile")),
        "middle": _safe_int(_first(raw, "proper_distance_middle", "distance_middle")),
        "long":   _safe_int(_first(raw, "proper_distance_long", "distance_long")),
    }
    ground = {
        "turf": _safe_int(_first(raw, "proper_ground_turf", "ground_turf")),
        "dirt": _safe_int(_first(raw, "proper_ground_dirt", "ground_dirt")),
    }
    style_apt = {
        "front": _safe_int(_first(raw, "proper_running_style_nige", "proper_running_style_front", "running_style_nige")),
        "pace":  _safe_int(_first(raw, "proper_running_style_senko", "proper_running_style_senkou", "proper_running_style_pace")),
        "late":  _safe_int(_first(raw, "proper_running_style_sashi", "proper_running_style_late")),
        "end":   _safe_int(_first(raw, "proper_running_style_oikomi", "proper_running_style_ooikomi", "proper_running_style_end")),
    }
    skills = raw.get("skills")
    skill_count = len(skills) if isinstance(skills, list) else 0

    return TeamMemberCard(
        card_id=_safe_int(_first(raw, "card_id", "trained_chara_id", "chara_id")),
        distance_type=_safe_int(raw.get("distance_type")),
        member_id=_safe_int(raw.get("member_id")),
        skill_count=skill_count,
        speed=_safe_int(raw.get("speed")) or 0,
        stamina=_safe_int(raw.get("stamina")) or 0,
        power=_safe_int(raw.get("power")) or 0,
        guts=_safe_int(raw.get("guts")) or 0,
        wiz=_safe_int(_first(raw, "wiz", "wisdom", "wit")) or 0,
        rarity=_safe_int(raw.get("rarity")),
        talent_level=_safe_int(_first(raw, "talent_level", "talent_lv")),
        running_style=_safe_int(raw.get("running_style")),
        rank_score=_safe_int(raw.get("rank_score")),
        team_rating=_safe_int(_first(raw, "team_rating", "rating")),
        fans=_safe_int(raw.get("fans")),
        distance=distance,
        ground=ground,
        style_apt=style_apt,
    )


def _extract_team_stadium(profile: Optional[dict]) -> list:
    """Pull the raw team_stadium array from a profile dict (top-level or nested)."""
    if not isinstance(profile, dict):
        return []
    raw = profile.get("team_stadium")
    if raw is None:
        trainer = profile.get("trainer") or {}
        raw = trainer.get("team_stadium")
    return raw if isinstance(raw, list) else []


async def build_team_card_data(
    member: Member, profile: Optional[dict], status_api: dict
) -> Optional[TeamCardData]:
    """Assemble the team card from a profile's team_stadium. None when absent."""
    raw_members = _extract_team_stadium(profile)
    if not raw_members:
        return None

    members = [_parse_team_member(r) for r in raw_members if isinstance(r, dict)]
    if not members:
        return None

    # Resolve portraits concurrently (cached on disk after first fetch).
    async def _resolve(m: TeamMemberCard):
        if m.card_id:
            p = await get_portrait_path(m.card_id)
            m.portrait_path = str(p) if p else None

    await asyncio.gather(*(_resolve(m) for m in members))

    club = await Club.get_by_id(member.club_id)
    return TeamCardData(
        trainer_name=member.trainer_name,
        trainer_id=member.trainer_id,
        club_name=club.club_name if club else "Unknown Club",
        members=members,
        team_evaluation_point=status_api.get("team_evaluation_point"),
        team_class=status_api.get("team_class"),
        has_api_data=bool(status_api.get("has_api_data")),
    )


async def _render_to_temp(render_fn) -> Path:
    """Render via ``render_fn(path)`` on the executor; return the temp PNG path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    out_path = Path(tmp.name)
    await asyncio.get_running_loop().run_in_executor(None, lambda: render_fn(out_path))
    return out_path


async def generate_member_cards(member: Member) -> tuple:
    """Build both the status card and (when available) the team card.

    Fetches the uma.moe profile once and reuses it for both. Returns
    ``(status_path, team_path)`` where either may be ``None`` (status is ``None``
    only when there's no quota data at all; team is ``None`` when the trainer has
    no/private team_stadium).
    """
    profile = await fetch_trainer_profile(member.trainer_id)

    status_data = await build_trainer_card_data(member, profile=profile)
    if status_data is None:
        return None, None

    status_path = await _render_to_temp(lambda p: render(status_data, p))
    logger.info(f"Trainer card generated for {member.trainer_name} → {status_path}")

    team_path = None
    status_api = {
        "team_evaluation_point": status_data.team_evaluation_point,
        "team_class": status_data.team_class,
        "has_api_data": status_data.has_api_data,
    }
    try:
        team_data = await build_team_card_data(member, profile, status_api)
        if team_data is not None:
            team_path = await _render_to_temp(lambda p: render_team(team_data, p))
            logger.info(f"Team card generated for {member.trainer_name} → {team_path}")
    except Exception as e:
        # Team card is a best-effort extra; never let it break /my_status.
        logger.warning(f"Failed to build team card for {member.trainer_name}: {e}", exc_info=True)
        team_path = None

    return status_path, team_path
