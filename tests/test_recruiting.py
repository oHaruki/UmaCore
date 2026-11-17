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


# ---- club snapshot ----
from services.recruiting import club_snapshot


def _circle(members, **circle):
    return {"circle": {"name": "Horsecore", "member_count": None, **circle}, "members": members}


def test_club_snapshot_averages_closed_days_per_member():
    payload = _circle([
        {"daily_fans": [100, 200, 300, 350]},     # here all month: 200 over 2 closed days
        {"daily_fans": [0, 0, 1000, 1500]},       # joined day 2: nothing closed to average yet
        {"daily_fans": [0, 0, 0, 5000]},          # joined today
        {"daily_fans": [50, 80, 0, 0]},           # left
    ], member_count=3, monthly_rank=412, monthly_point=9000)
    snap = club_snapshot(payload, live_slot=3)
    assert snap["members"] == 3
    assert snap["rank"] == 412
    assert snap["daily_avg"] == 100
    assert snap["circle_name"] == "Horsecore"


def test_club_snapshot_skips_transfer_markers_and_counts_rows_without_member_count():
    payload = _circle([
        {"daily_fans": [-1, -1, 400, 700, 900]},  # transferred in on day 2: 300 over 1 day
        {"daily_fans": [10, 20, 30, 40, 50]},     # 30 over 3 days
    ], live_rank=900)
    snap = club_snapshot(payload, live_slot=4)
    assert snap["members"] == 2
    assert snap["rank"] == 900
    assert snap["daily_avg"] == round(330 / 4)


def test_club_snapshot_none_before_rows_exist():
    assert club_snapshot(_circle([]), live_slot=0) is None


def test_club_snapshot_first_day_has_no_average():
    snap = club_snapshot(_circle([{"daily_fans": [100, 150]}]), live_slot=1)
    assert snap["members"] == 1
    assert snap["daily_avg"] is None
