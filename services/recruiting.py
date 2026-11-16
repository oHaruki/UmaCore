"""
Player stats for the website's recruiting board.

Club listings read their numbers live from the club's own scrape data, so only
LFC posts need anything stored: a snapshot of the trainer's uma.moe profile,
taken when the post is saved and refreshed about once a day while it is listed.
"""
import json
import logging
from datetime import date
from typing import Optional

from config.database import db
from scrapers import ProfileUnavailable, fetch_profile_checked
from services.trainer_card_renderer import _pick_monthly_entry, _safe_int
from utils.rate_limiter import PRIORITY_BACKGROUND, PRIORITY_INTERACTIVE

logger = logging.getLogger(__name__)

# Posts not bumped for this long drop off the board, so they stop costing requests too.
LISTED_DAYS = 30
REFRESH_AFTER_HOURS = 20
REFRESH_BATCH = 40


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
