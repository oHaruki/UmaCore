"""Generates a PNG quota report using rat's tally renderer (rattenschwe1f/uma-fan-tally-tool).

Fetches data from our own DB (quota_history + members) and maps it into MemberReport
objects that the renderer understands. Rat's compute.py is skipped entirely — we
already have all derived values stored.
"""
import asyncio
import logging
import os
import tempfile
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from uuid import UUID

from config.database import db
from tally.render.classic import render
from tally.types import Circle, Config, MemberReport

logger = logging.getLogger(__name__)


async def generate_tally_image(
    club_id: UUID,
    club_name: str,
    report_date: date,
    daily_quota: int,
    monthly_rank: Optional[int] = None,
) -> Path:
    """
    Query quota data for the club on report_date, render a PNG, and return
    the path to the temp file. Caller is responsible for deleting it after use.
    """
    yesterday = report_date - timedelta(days=1)
    month_start = report_date.replace(day=1)
    days_in_month = monthrange(report_date.year, report_date.month)[1]
    monthly_quota = daily_quota * days_in_month

    rows, bomb_rows = await asyncio.gather(
        db.fetch(
            """
            SELECT
                m.member_id,
                m.trainer_name,
                m.join_date,
                qh.cumulative_fans,
                qh.expected_fans,
                qh.deficit_surplus,
                prev.cumulative_fans AS previous_fans
            FROM quota_history qh
            JOIN members m ON m.member_id = qh.member_id
            LEFT JOIN quota_history prev
                ON prev.member_id = qh.member_id
                AND prev.date = $3
            WHERE qh.club_id = $1
              AND qh.date = $2
              AND m.is_active = TRUE
            ORDER BY qh.cumulative_fans DESC
            """,
            club_id, report_date, yesterday,
        ),
        db.fetch(
            "SELECT member_id, days_remaining FROM bombs WHERE club_id = $1 AND is_active = TRUE",
            club_id,
        ),
    )

    if not rows:
        raise ValueError(f"No quota data found for club {club_name} on {report_date}")

    active_bombs: dict[str, int] = {str(r["member_id"]): r["days_remaining"] for r in bomb_rows}

    reports: list[MemberReport] = []
    for row in rows:
        total          = row["cumulative_fans"] or 0
        previous_total = row["previous_fans"] or 0
        expected       = row["expected_fans"] or 0
        deficit        = row["deficit_surplus"] or 0

        join_date: date = row["join_date"]
        effective_start = max(join_date, month_start)
        join_day_num    = max(1, (join_date - month_start).days + 1)
        days_elapsed    = max(1, (report_date - effective_start).days + 1)
        days_remaining  = days_in_month - (report_date - month_start).days

        daily_avg      = total / days_elapsed if days_elapsed else 0.0
        needed_per_day = max(0.0, (monthly_quota - total) / days_remaining) if days_remaining > 0 else 0.0
        off_by         = max(0, expected - total)
        on_target      = deficit >= 0
        quota_complete = total >= monthly_quota

        if quota_complete:
            pill_tier = "done"
        elif on_target:
            pill_tier = "yes"
        else:
            pill_tier = "no"

        latest_day_delta = max(0, total - previous_total)
        viewer_id = int(row["member_id"].int % (2**31))

        member_id_str = str(row["member_id"])
        bomb_days = active_bombs.get(member_id_str)
        trainer_name = row["trainer_name"]
        if bomb_days is not None:
            trainer_name = f"{trainer_name}  [bomb: {bomb_days}d]"

        reports.append(MemberReport(
            viewer_id=viewer_id,
            trainer_name=trainer_name,
            days_elapsed=days_elapsed,
            total=total,
            previous_total=previous_total,
            daily_avg=daily_avg,
            expected_so_far=expected,
            quota_total=monthly_quota,
            on_target=on_target,
            off_by=off_by,
            needed_per_day=needed_per_day,
            low_days=0,
            latest_day_delta=latest_day_delta,
            join_day=join_day_num,
            pill_tier=pill_tier,
            staff_role=None,
        ))

    circle = Circle(name=club_name, monthly_rank=monthly_rank)
    config = Config(monthly_quota=monthly_quota)

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    out_path = Path(tmp.name)

    await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: render(circle, reports, report_date, config, out_path),
    )

    logger.info(f"Tally image generated for {club_name} ({len(reports)} members) → {out_path}")
    return out_path
