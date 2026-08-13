"""/promotion must point a club at the grade band directly above it.

`PROMOTION_MILESTONES` used to be a hand-picked list — [10, 50, 100, 500, 3000] —
with nothing between 500 and 3000. `next_milestone` returns the best milestone
better than your rank, so every club from #501 to #2999 was told to climb to Top
500. Reported 2026-08-13: a B+ club at #1483 was pointed at Top 500 (+908M fans,
four grades up) instead of Top 1000, the A band immediately above it.

The milestones are now derived from the real grade bands, so a promotion target
is always exactly one grade up.
"""
import pytest

from config.settings import CLUB_RANK_GRADES, PROMOTION_MILESTONES
from services.promotion_calculator import grade_for_rank, next_milestone


class TestTheReportedCase:
    def test_b_plus_club_climbs_to_the_a_band(self):
        """#1483 is B+; the next step is Top 1000 (A), not Top 500 (A+)."""
        assert next_milestone(1483) == 1_000
        assert grade_for_rank(1483) == "B+"
        assert grade_for_rank(next_milestone(1483)) == "A"

    def test_the_a_club_alongside_it_was_already_right(self):
        """#507 is A and genuinely does climb to Top 500 (A+)."""
        assert next_milestone(507) == 500
        assert grade_for_rank(507) == "A"
        assert grade_for_rank(next_milestone(507)) == "A+"

    def test_no_rank_is_pointed_more_than_one_grade_up(self):
        """The actual invariant that broke. Sweep the whole leaderboard."""
        for rank in range(2, 10_001):
            target = next_milestone(rank)
            if target is None:
                continue
            bounds = [b for b, _ in CLUB_RANK_GRADES]
            assert bounds.index(target) == bounds.index(
                next(b for b in bounds if rank <= b)
            ) - 1


class TestGradeForRank:
    @pytest.mark.parametrize("rank,grade", [
        (1, "SS"), (10, "SS"),
        (11, "S+"), (30, "S+"),
        (31, "S"), (100, "S"),
        (101, "A+"), (500, "A+"),
        (501, "A"), (1_000, "A"),
        (1_001, "B+"), (3_000, "B+"),
        (3_001, "B"), (5_000, "B"),
        (5_001, "C+"), (7_000, "C+"),
        (7_001, "C"), (10_000, "C"),
    ])
    def test_band_edges(self, rank, grade):
        assert grade_for_rank(rank) == grade

    def test_below_the_last_band_has_no_grade(self):
        assert grade_for_rank(10_001) is None

    def test_unknown_rank_has_no_grade(self):
        assert grade_for_rank(None) is None


class TestMilestones:
    def test_every_grade_boundary_is_a_milestone(self):
        assert PROMOTION_MILESTONES == [b for b, _ in CLUB_RANK_GRADES]

    def test_the_gap_that_caused_the_bug_is_gone(self):
        assert 1_000 in PROMOTION_MILESTONES

    def test_top_of_the_ladder_has_nothing_left_to_climb(self):
        assert next_milestone(1) is None
        assert next_milestone(10) is None

    def test_a_rank_off_the_bottom_still_gets_a_target(self):
        assert next_milestone(50_000) == 10_000
