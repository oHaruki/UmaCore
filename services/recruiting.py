"""
Stats for the website's recruiting board.

LFC posts keep a snapshot of the trainer's uma.moe profile, club listings a
snapshot of the circle. Both are taken when the post is saved and refreshed
while it is listed, so the board never depends on the daily check running.
"""
import json
import logging
from datetime import date
from typing import Optional
from uuid import UUID

from config.database import db
from scrapers import ProfileUnavailable, UmaMoeAPIScraper, fetch_profile_checked
from scrapers.umamoe_api_scraper import CircleMeta
from services.trainer_card_renderer import _pick_monthly_entry, _safe_int
from utils.jst_calendar import resolve_live
from utils.rate_limiter import PRIORITY_BACKGROUND, PRIORITY_INTERACTIVE

logger = logging.getLogger(__name__)

# Posts not bumped for this long drop off the board, so they stop costing requests too.
LISTED_DAYS = 30
REFRESH_AFTER_HOURS = 20
CLUB_REFRESH_AFTER_HOURS = 3
REFRESH_BATCH = 40


class ClubStatsUnavailable(Exception):
    """uma.moe couldn't be asked about a circle right now."""


def club_snapshot(payload: dict, live_slot: int) -> Optional[dict]:
    """The board's view of one month of circle data, or None with no member rows yet.

    Averages use closed days only (the slot before the live one), per member
    from the day they show up, so someone who joined last week isn't diluted.
    """
    members = payload.get("members") or []
    if not members:
        return None
    meta = CircleMeta.from_response(payload)
    closed = live_slot - 1

    def at(fans: list, i: int) -> int:
        return fans[i] if 0 <= i < len(fans) and fans[i] else 0

    current = gained = days = 0
    for m in members:
        fans = m.get("daily_fans") or []
        if at(fans, closed) <= 0 and at(fans, live_slot) <= 0:
            continue                          # left the club
        current += 1
        if at(fans, closed) <= 0:
            continue                          # joined today, nothing closed yet
        # negative entries are transfer markers, the first positive slot is the arrival
        joined = next(i for i, v in enumerate(fans[:closed + 1]) if v and v > 0)
        if closed > joined:
            gained += fans[closed] - fans[joined]
            days += closed - joined

    return {
        "circle_name": (meta.name or "").strip() or None,
        "members": meta.member_count or current,
        "rank": meta.monthly_rank or meta.live_rank,
        "monthly_fans": meta.monthly_point,
        "daily_avg": round(gained / days) if days else None,
    }


async def club_lookup(circle_id: str, background: bool = False) -> Optional[dict]:
    """Snapshot for a circle. Raises ``ClubStatsUnavailable`` if uma.moe can't be asked."""
    target = resolve_live()
    scraper = UmaMoeAPIScraper(
        circle_id, priority=PRIORITY_BACKGROUND if background else PRIORITY_INTERACTIVE
    )
    try:
        async with scraper._session() as session:
            payload = await scraper._fetch_month(session, target.year, target.month)
    except Exception as e:
        raise ClubStatsUnavailable(str(e)) from e
    if payload is None:
        raise ClubStatsUnavailable("uma.moe request failed")
    return club_snapshot(payload, target.slot)


async def refresh_club(club_id: UUID, background: bool = False) -> Optional[dict]:
    """Fetch and store a club's board numbers. None when there's nothing to show yet.

    A failed or empty fetch keeps the last good numbers; checked_at still moves
    so the hourly task doesn't hammer a circle uma.moe has nothing for.
    """
    circle_id = await db.fetchval(
        "SELECT circle_id FROM clubs WHERE club_id = $1 AND is_active", club_id
    )
    if not circle_id or not str(circle_id).isdigit():
        return None
    try:
        snap = await club_lookup(str(circle_id), background=background)
    except ClubStatsUnavailable:
        await db.execute(
            """
            INSERT INTO recruit_club_stats (club_id, checked_at) VALUES ($1, NOW())
            ON CONFLICT (club_id) DO UPDATE SET checked_at = NOW()
            """,
            club_id,
        )
        raise
    await db.execute(
        """
        INSERT INTO recruit_club_stats (club_id, stats, checked_at, updated_at)
        VALUES ($1, $2::jsonb, NOW(), CASE WHEN $2::jsonb IS NULL THEN NULL ELSE NOW() END)
        ON CONFLICT (club_id) DO UPDATE SET
            stats = COALESCE(EXCLUDED.stats, recruit_club_stats.stats),
            updated_at = COALESCE(EXCLUDED.updated_at, recruit_club_stats.updated_at),
            checked_at = NOW()
        """,
        club_id, json.dumps(snap) if snap else None,
    )
    return snap


async def refresh_due_clubs(limit: int = REFRESH_BATCH) -> int:
    """Refresh the stalest listed clubs. Returns how many were updated."""
    rows = await db.fetch(
        f"""
        SELECT p.club_id FROM recruit_club_posts p
        JOIN clubs c ON c.club_id = p.club_id AND c.is_active
        LEFT JOIN recruit_club_stats s ON s.club_id = p.club_id
        WHERE p.is_open
          AND p.bumped_at > NOW() - INTERVAL '{LISTED_DAYS} days'
          AND (s.checked_at IS NULL
               OR s.checked_at < NOW() - INTERVAL '{CLUB_REFRESH_AFTER_HOURS} hours')
        ORDER BY s.checked_at NULLS FIRST
        LIMIT $1
        """,
        limit,
    )
    updated = 0
    for row in rows:
        try:
            if await refresh_club(row["club_id"], background=True):
                updated += 1
        except ClubStatsUnavailable as e:
            logger.info(f"Recruit club stats refresh paused: {e}")
            break
    if updated:
        logger.info(f"Refreshed uma.moe stats for {updated} recruiting club(s)")
    return updated


def snapshot(account_id: str, profile: dict, today: Optional[date] = None) -> Optional[dict]:
    """The board's view of a profile, or None when it has no trainer name."""
    today = today or date.today()
    trainer = profile.get("trainer") or {}
    name = (trainer.get("name") or "").strip()
    if not name:
        return None

    circle = profile.get("circle") or {}
    fan_history = profile.get("fan_history") or {}
    monthly = _pick_monthly_entry(fan_history.get("monthly") or [], today) or {}
    rolling = fan_history.get("rolling") or {}
    alltime = fan_history.get("alltime") or {}

    gain_30d = _safe_int(rolling.get("gain_30d"))
    return {
        "trainer_id": str(trainer.get("account_id") or account_id),
        "trainer_name": name,
        "circle_id": str(circle["circle_id"]) if circle.get("circle_id") else None,
        "circle_name": (circle.get("name") or "").strip() or None,
        "team_class": _safe_int(trainer.get("team_class") or trainer.get("best_team_class")),
        "team_rating": _safe_int(trainer.get("team_evaluation_point")),
        "followers": _safe_int(trainer.get("follower_num")),
        "monthly_fan_rank": _safe_int(monthly.get("rank")),
        "gain_30d": gain_30d,
        "gain_30d_rank": _safe_int(rolling.get("rank_30d")),
        "daily_avg": gain_30d // 30 if gain_30d and gain_30d > 0 else None,
        "alltime_fans": _safe_int(alltime.get("total_fans")),
        "alltime_rank": _safe_int(alltime.get("rank")),
    }


async def lookup(account_id: str, background: bool = False) -> Optional[dict]:
    """Snapshot for a trainer ID. None if uma.moe has no such trainer.

    Raises ``ProfileUnavailable`` when uma.moe can't be asked, so a caller never
    tells someone their ID is wrong because uma.moe was down.
    """
    priority = PRIORITY_BACKGROUND if background else PRIORITY_INTERACTIVE
    profile = await fetch_profile_checked(account_id, priority)
    if profile is None:
        return None
    snap = snapshot(account_id, profile)
    if snap is None:
        raise ProfileUnavailable("profile has no trainer name")
    return snap


async def refresh_due_posts(limit: int = REFRESH_BATCH) -> int:
    """Refresh the stalest listed LFC posts. Returns how many were updated."""
    rows = await db.fetch(
        f"""
        SELECT post_id, trainer_id FROM recruit_player_posts
        WHERE is_open
          AND bumped_at > NOW() - INTERVAL '{LISTED_DAYS} days'
          AND (stats_updated_at IS NULL
               OR stats_updated_at < NOW() - INTERVAL '{REFRESH_AFTER_HOURS} hours')
        ORDER BY stats_updated_at NULLS FIRST
        LIMIT $1
        """,
        limit,
    )
    updated = 0
    for row in rows:
        try:
            snap = await lookup(row["trainer_id"], background=True)
        except ProfileUnavailable as e:
            # uma.moe down or no key: every other post would fail the same way.
            logger.info(f"Recruit stats refresh paused: {e}")
            break

        if snap is None:
            # ID no longer resolves; keep the old numbers but don't retry every hour.
            await db.execute(
                "UPDATE recruit_player_posts SET stats_updated_at = NOW() WHERE post_id = $1",
                row["post_id"],
            )
            continue

        await db.execute(
            """
            UPDATE recruit_player_posts
            SET stats = $2::jsonb, trainer_name = $3, stats_updated_at = NOW()
            WHERE post_id = $1
            """,
            row["post_id"], json.dumps(snap), snap["trainer_name"],
        )
        updated += 1

    if updated:
        logger.info(f"Refreshed uma.moe stats for {updated} recruiting post(s)")
    return updated
