"""Full repair of a club's quota history from uma.moe.

``/recalculate`` used to recompute expected fans and deficits from whatever
``cumulative_fans`` was already stored — so if the stored fan counts themselves
were wrong it faithfully recomputed the wrong answer. Every row written before
the JST slot fix holds an in-progress day's total rather than a finished one, and
no amount of recomputation would repair that.

This rewrites the fan counts too, straight from uma.moe's month array, then
recomputes everything that derives from them. It is the migration path for data
written by the old code, and the general "something looks wrong, fix it" tool.

What it does, in order:

1. Fetch the month from uma.moe (authoritative)
2. Re-derive each member's join day and correct stored join dates
3. Rewrite ``cumulative_fans`` for every day, filling in missing days
4. Recompute ``expected_fans``, ``deficit_surplus`` and ``days_behind``
5. Clear every bomb and re-evaluate from the corrected history
"""
import calendar
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from config.database import db
from models import Bomb, Club, Member
from models.quota_requirement import QuotaSchedule
from scrapers.umamoe_api_scraper import UmaMoeAPIScraper
from services.bomb_manager import BombManager
from services.quota_calculator import QuotaCalculator
from utils.rate_limiter import PRIORITY_INTERACTIVE

logger = logging.getLogger(__name__)


@dataclass
class RecalcResult:
    month: Optional[Tuple[int, int]] = None
    rows_written: int = 0
    rows_added: int = 0
    fans_corrected: int = 0
    join_dates_fixed: int = 0
    bombs_cleared: int = 0
    bombs_activated: int = 0
    activated_names: List[str] = field(default_factory=list)
    members_not_in_api: int = 0
    warnings: List[str] = field(default_factory=list)

    @property
    def repaired_anything(self) -> bool:
        return bool(self.fans_corrected or self.join_dates_fixed or self.rows_added)


def _resolve_join_date(join_day: int, ref: date) -> date:
    """Turn a 1-based join day in the fetched month into a real date."""
    last_day = calendar.monthrange(ref.year, ref.month)[1]
    if 1 <= join_day <= last_day:
        return date(ref.year, ref.month, join_day)
    # Beyond the month's length: it belongs to the previous month.
    if ref.month == 1:
        return date(ref.year - 1, 12, join_day)
    return date(ref.year, ref.month - 1, join_day)


async def recalculate_club(club: Club, current_date: date) -> RecalcResult:
    """Repair a club's current-month history from uma.moe. See module docstring."""
    result = RecalcResult()

    if not (club.circle_id and club.is_circle_id_valid()):
        result.warnings.append(
            "No valid circle_id — fan counts cannot be re-fetched, so only "
            "deficits and bombs were recomputed from stored values."
        )
        await _recompute_derived_only(club, current_date, result)
        await _reevaluate_bombs(club, current_date, result)
        return result

    scraper = UmaMoeAPIScraper(club.circle_id, priority=PRIORITY_INTERACTIVE)
    try:
        scraped = await scraper.scrape()
    except Exception as e:
        logger.warning(f"recalculate: scrape failed for {club.club_name}: {e}")
        result.warnings.append(
            f"Could not reach uma.moe ({type(e).__name__}) — fan counts were left "
            f"as stored; deficits and bombs were still recomputed."
        )
        await _recompute_derived_only(club, current_date, result)
        await _reevaluate_bombs(club, current_date, result)
        return result

    year, month = scraper.get_fetched_period()
    ref = scraper.get_data_date() or current_date
    result.month = (year, month)

    schedule = await QuotaSchedule.load(club.club_id)
    members = await Member.get_all_active(club.club_id)
    by_trainer = {str(m.trainer_id): m for m in members if m.trainer_id}

    for trainer_id, data in scraped.items():
        member = by_trainer.get(str(trainer_id))
        if member is None:
            continue                       # new to us; a normal scrape will add them

        # --- join date -------------------------------------------------------
        join_day = data.get("join_day")
        if join_day:
            detected = _resolve_join_date(join_day, ref)
            if detected != member.join_date:
                logger.info(
                    f"recalculate: {member.trainer_name} join_date "
                    f"{member.join_date} → {detected}"
                )
                await member.update_join_date(detected)
                result.join_dates_fixed += 1

        # --- fan counts and everything derived from them ---------------------
        await _rewrite_member_month(
            club, member, data.get("fans") or [], join_day or 1,
            year, month, schedule, result,
        )

    result.members_not_in_api = sum(
        1 for m in members if str(m.trainer_id) not in scraped
    )

    await _reevaluate_bombs(club, current_date, result)
    return result


async def _rewrite_member_month(club: Club, member: Member, fans: List[int],
                                join_day: int, year: int, month: int,
                                schedule: QuotaSchedule, result: RecalcResult) -> None:
    """Rewrite one member's month from the API array.

    Array index ``i`` maps to ``date(year, month, i)`` — the same mapping the web
    backfill uses. Index 0 belongs to the previous month and is skipped.
    """
    existing = {
        row["date"]: row
        for row in await db.fetch(
            "SELECT date, cumulative_fans FROM quota_history "
            "WHERE member_id = $1 AND date_part('year', date) = $2 "
            "AND date_part('month', date) = $3",
            member.member_id, year, month,
        )
    }

    consecutive = 0
    for i in range(max(1, join_day), len(fans)):
        day = i
        if day > calendar.monthrange(year, month)[1]:
            break
        comp_date = date(year, month, day)
        correct_fans = fans[i]

        expected = await QuotaCalculator.calculate_expected_fans(
            club.club_id, member.join_date, comp_date, club.quota_period,
            schedule=schedule,
        )
        deficit = correct_fans - expected
        consecutive = consecutive + 1 if deficit < 0 else 0

        prior = existing.get(comp_date)
        if prior is None:
            result.rows_added += 1
        elif prior["cumulative_fans"] != correct_fans:
            result.fans_corrected += 1
            logger.info(
                f"recalculate: {member.trainer_name} {comp_date} fans "
                f"{prior['cumulative_fans']:,} → {correct_fans:,}"
            )

        await db.execute(
            """
            INSERT INTO quota_history
                (member_id, club_id, date, cumulative_fans, expected_fans,
                 deficit_surplus, days_behind)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (member_id, date) DO UPDATE SET
                cumulative_fans = $4, expected_fans = $5,
                deficit_surplus = $6, days_behind = $7, created_at = NOW()
            """,
            member.member_id, club.club_id, comp_date,
            correct_fans, expected, deficit, consecutive,
        )
        result.rows_written += 1


async def _recompute_derived_only(club: Club, current_date: date,
                                  result: RecalcResult) -> None:
    """Fallback when uma.moe is unavailable: recompute from stored fan counts."""
    schedule = await QuotaSchedule.load(club.club_id)
    for member in await Member.get_all_active(club.club_id):
        rows = await db.fetch(
            "SELECT id, date, cumulative_fans FROM quota_history "
            "WHERE member_id = $1 AND date_part('year', date) = $2 "
            "AND date_part('month', date) = $3 ORDER BY date ASC",
            member.member_id, current_date.year, current_date.month,
        )
        consecutive = 0
        for row in rows:
            expected = await QuotaCalculator.calculate_expected_fans(
                club.club_id, member.join_date, row["date"], club.quota_period,
                schedule=schedule,
            )
            deficit = row["cumulative_fans"] - expected
            consecutive = consecutive + 1 if deficit < 0 else 0
            await db.execute(
                "UPDATE quota_history SET expected_fans = $1, deficit_surplus = $2, "
                "days_behind = $3 WHERE id = $4",
                expected, deficit, consecutive, row["id"],
            )
            result.rows_written += 1


async def _reevaluate_bombs(club: Club, current_date: date,
                            result: RecalcResult) -> None:
    """Clear every bomb and re-derive them from the corrected history."""
    if not club.bombs_enabled:
        return

    cleared = await db.execute(
        "UPDATE bombs SET is_active = FALSE, deactivation_date = $1 "
        "WHERE club_id = $2 AND is_active = TRUE",
        current_date, club.club_id,
    )
    try:
        result.bombs_cleared = int(cleared.split()[-1])
    except (ValueError, IndexError):
        result.bombs_cleared = 0

    activated = await BombManager().check_and_activate_bombs(club, current_date)
    result.bombs_activated = len(activated)
    for bomb in activated:
        member = await Member.get_by_id(bomb.member_id)
        if member:
            result.activated_names.append(member.trainer_name)
