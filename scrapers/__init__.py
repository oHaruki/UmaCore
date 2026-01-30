"""
Scrapers package
"""
from .base_scraper import BaseScraper
from .chronogenesis_scraper import ChronoGenesisScraper
from .umamoe_api_scraper import UmaMoeAPIScraper

__all__ = ['BaseScraper', 'ChronoGenesisScraper', 'UmaMoeAPIScraper']
