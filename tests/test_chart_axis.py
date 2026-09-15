"""Tests for the progress chart's x-axis ordering.

A mid-month joiner's series starts on their join day, and Plotly builds a
categorical axis from the order it first sees each value — so whichever trace
is added first decides where its dates land on the axis.
"""
from bot.commands.charts import _ordered_dates


def series(days: list[int]) -> dict:
    return {"dates": [f"{d:02d}.09" for d in days], "fans": [0] * len(days)}


def test_mid_month_joiner_listed_first_does_not_reorder_axis():
    member_data = {
        "Joined on the 12th": series([12, 13, 14]),
        "Here since day 1": series(list(range(1, 15))),
    }
    assert _ordered_dates(member_data) == [f"{d:02d}.09" for d in range(1, 15)]


def test_dates_are_deduplicated_across_members():
    member_data = {"a": series([1, 2, 3]), "b": series([2, 3])}
    assert _ordered_dates(member_data) == ["01.09", "02.09", "03.09"]


def test_orders_by_month_before_day():
    member_data = {"a": {"dates": ["31.08", "01.09", "02.09"], "fans": [0, 0, 0]}}
    assert _ordered_dates(member_data) == ["31.08", "01.09", "02.09"]


def test_empty_input():
    assert _ordered_dates({}) == []
