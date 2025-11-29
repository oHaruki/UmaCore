"""
Base scraper abstract class
"""
from abc import ABC, abstractmethod
from typing import Dict, List
from datetime import date
import logging

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Abstract base class for web scrapers"""
    
    def __init__(self, url: str):
        self.url = url
    
    @abstractmethod
    async def scrape(self) -> Dict[str, List[int]]:
        """
        Scrape the website and return member data
        
        Returns:
            Dict mapping trainer_name -> list of cumulative fan counts per day
            Example: {
                "TrainerName1": [1000000, 2100000, 3050000],  # Day 1, 2, 3
                "TrainerName2": [950000, 2000000, 3200000]
            }
        """
        pass
    
    @abstractmethod
    def get_current_day(self) -> int:
        """Get the current day number (1-indexed)"""
        pass
    
    def detect_monthly_reset(self, previous_data: Dict[str, int], current_data: Dict[str, List[int]]) -> bool:
        """
        Detect if a monthly reset has occurred
        
        Args:
            previous_data: Dict of trainer_name -> previous cumulative fans
            current_data: Dict of trainer_name -> list of cumulative fans
        
        Returns:
            True if reset detected, False otherwise
        """
        if not previous_data or not current_data:
            return False
        
        # Check if any member's latest cumulative count is significantly lower than before
        for trainer_name, fan_counts in current_data.items():
            if trainer_name in previous_data:
                latest_count = fan_counts[-1] if fan_counts else 0
                previous_count = previous_data[trainer_name]
                
                # If current count is less than half of previous, likely a reset
                if latest_count < previous_count * 0.5:
                    logger.info(f"Monthly reset detected: {trainer_name} went from {previous_count} to {latest_count}")
                    return True
        
        return False
