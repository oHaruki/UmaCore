"""Alliance leaderboard embed and daily gain."""
from datetime import date
from uuid import uuid4

from services.alliance import ClubRow, Standings, TrainerRow, build_embed, day_gain


def test_day_gain():
    d17, d18 = date(2026, 11, 17), date(2026, 11, 18)
    assert day_gain(5_000_000, 3_000_000, d17, d18) == 2_000_000
    assert day_gain(4_000_000, None, None, date(2026, 11, 1)) == 4_000_000  # month starts at zero
    assert day_gain(9_000_000, None, None, d18) is None                    # joined mid month
    assert day_gain(9_000_000, 1_000_000, date(2026, 11, 2), d18) is None  # gap in scrapes
    assert day_gain(1_000, 2_000, d17, d18) == 0                           # never negative


def standings(public=False):
    return Standings(
        alliance_id=uuid4(), name="Horse Family", slug="horse-family", is_public=public,
        month_start=date(2026, 11, 1), latest_date=date(2026, 11, 18),
        clubs=[ClubRow("Main", 2, 60_000_000, 4_000_000, 120), ClubRow("Sister", 1, 20_000_000)],
        trainers=[
            TrainerRow("Teio_", "Main", 40_000_000, 3_000_000),
            TrainerRow("Mac", "Sister", 20_000_000, None),
            TrainerRow("Spe", "Main", 20_000_000, 1_000_000),
        ],
    )


def test_embed_lists_clubs_then_trainers():
    e = build_embed(standings())
    assert e.title == "🏆 Horse Family"
    assert e.url is None
    d = e.description
    assert "November 2026 · 2 clubs · 3 trainers · 80.0M fans" in d
    assert "**Main** 60.0M · avg 30.0M · +4.0M today · #120" in d
    assert "**Sister** 20.0M · avg 20.0M" in d
    assert d.index("**Main**") < d.index("**Sister**") < d.index("Top trainers")
    assert "Teio\\_ (Main) · 40.0M" in d
    assert e.footer.text == "Data through Nov 18"


def test_public_alliance_links_its_page():
    assert build_embed(standings(public=True)).url == "https://umacore.app/alliance/horse-family"


def test_empty_alliance():
    s = Standings(uuid4(), "Solo", "solo", False, None, None, clubs=[ClubRow("Main")])
    e = build_embed(s)
    assert "No data yet" in e.description
    assert "Top trainers" not in e.description
