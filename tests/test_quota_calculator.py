"""
Sample tests for the Umamusume bot

To run tests:
pip install pytest pytest-asyncio
pytest tests/
"""
import pytest
from datetime import date
from services.quota_calculator import QuotaCalculator


class TestQuotaCalculator:
    """Tests for QuotaCalculator service"""
    
    def test_calculate_days_active_joined_this_month(self):
        """A member who joined mid-month counts from their join date."""
        calculator = QuotaCalculator()

        # Same day
        assert calculator.calculate_days_active_in_month(
            date(2024, 11, 1), date(2024, 11, 1)
        ) == 1

        # 5 days apart
        assert calculator.calculate_days_active_in_month(
            date(2024, 11, 1), date(2024, 11, 5)
        ) == 5

    def test_calculate_days_active_joined_earlier_month(self):
        """A member who joined before this month counts from the 1st."""
        calculator = QuotaCalculator()

        assert calculator.calculate_days_active_in_month(
            date(2024, 9, 14), date(2024, 11, 5)
        ) == 5


    def test_calculate_deficit_surplus(self):
        """Test deficit/surplus calculation"""
        calculator = QuotaCalculator()
        
        # Surplus
        actual = 5_500_000
        expected = 5_000_000
        assert calculator.calculate_deficit_surplus(actual, expected) == 500_000
        
        # Deficit
        actual = 4_500_000
        expected = 5_000_000
        assert calculator.calculate_deficit_surplus(actual, expected) == -500_000
        
        # Exactly on track
        actual = 5_000_000
        expected = 5_000_000
        assert calculator.calculate_deficit_surplus(actual, expected) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestDaysBehind:
    """The streak was cut off at 10 because only the last 10 rows were read."""

    def test_counts_a_streak_longer_than_ten_days(self, monkeypatch):
        import asyncio
        from types import SimpleNamespace
        from models.quota_history import QuotaHistory

        # Behind every day from Oct 6 to Oct 18, on track before that.
        rows = [SimpleNamespace(date=date(2026, 10, d), deficit_surplus=-1 if d >= 6 else 1)
                for d in range(18, 0, -1)]

        async def last_n(member_id, n):
            return rows[:n]

        monkeypatch.setattr(QuotaHistory, "get_last_n_days", last_n)
        got = asyncio.run(QuotaCalculator()._calculate_days_behind(None, -1, date(2026, 10, 19)))
        assert got == 14
