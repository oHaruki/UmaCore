"""Snapshot of a uma.moe profile for the website's LFC posts."""
from datetime import date

from services.recruiting import snapshot

PROFILE = {
    "trainer": {
        "account_id": 277366264188,
        "name": " Scoff ",
        "team_class": 6,
        "team_evaluation_point": 41250,
        "follower_num": 120,
    },
    "circle": {"circle_id": 123456, "name": "Horsecore"},
    "fan_history": {
        "monthly": [
            {"year": 2026, "month": 10, "rank": 900},
            {"year": 2026, "month": 11, "rank": 412},
        ],
        "rolling": {"gain_30d": 90_000_000, "rank_30d": 380},
        "alltime": {"total_fans": 1_200_000_000, "rank": 5000},
    },
}


def test_snapshot_reads_the_board_fields():
    snap = snapshot("277366264188", PROFILE, date(2026, 11, 16))
    assert snap["trainer_name"] == "Scoff"
    assert snap["trainer_id"] == "277366264188"
    assert snap["circle_id"] == "123456"
    assert snap["circle_name"] == "Horsecore"
    assert snap["team_class"] == 6
    assert snap["team_rating"] == 41250
    assert snap["monthly_fan_rank"] == 412  # this month, not the newest-looking row
    assert snap["daily_avg"] == 3_000_000
    assert snap["alltime_fans"] == 1_200_000_000


def test_snapshot_without_circle_or_history():
    snap = snapshot("277366264188", {"trainer": {"name": "Solo"}, "circle": None})
    assert snap["circle_id"] is None and snap["circle_name"] is None
    assert snap["daily_avg"] is None and snap["monthly_fan_rank"] is None


def test_snapshot_needs_a_name():
    assert snapshot("1", {"trainer": {"name": "  "}}) is None
