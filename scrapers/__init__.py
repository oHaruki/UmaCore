"""
Scrapers package
"""
from .base_scraper import BaseScraper, StaleDataError
from .chronogenesis_scraper import ChronoGenesisScraper
from .umamoe_api_scraper import UmaMoeAPIScraper
from .umamoe_profile import fetch_trainer_profile
from .umamoe_leaderboard import fetch_entry_at_rank, fetch_own_standing

__all__ = [
    'BaseScraper',
    'StaleDataError',
    'ChronoGenesisScraper',
    'UmaMoeAPIScraper',
    'fetch_trainer_profile',
    'fetch_entry_at_rank',
    'fetch_own_standing',
]
