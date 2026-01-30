"""
Uma.moe API scraper for fast, reliable club data fetching
"""
from typing import Dict
import logging
import aiohttp
from datetime import datetime

from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class UmaMoeAPIScraper(BaseScraper):
    """Scraper using Uma.moe API for fast data retrieval"""
    
    def __init__(self, circle_id: str):
        """
        Initialize scraper with circle_id
        
        Args:
            circle_id: The circle/club ID from Uma.moe (e.g., "860280110")
        """
        self.circle_id = circle_id
        self.base_url = "https://uma.moe/api/v4/circles"
        self.current_day_count = 1
        super().__init__(self.base_url)
    
    async def scrape(self) -> Dict[str, Dict]:
        """
        Scrape club data from Uma.moe API
        
        Returns:
            Dict mapping viewer_id -> member data:
            {
                "viewer_id": {
                    "name": "TrainerName",
                    "trainer_id": "viewer_id",
                    "fans": [day1_monthly, day2_monthly, ...],
                    "join_day": 1
                }
            }
        """
        try:
            now = datetime.now()
            year = now.year
            month = now.month
            
            logger.info(f"Fetching data from Uma.moe API for circle {self.circle_id}...")
            
            params = {
                "circle_id": self.circle_id,
                "year": year,
                "month": month
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Uma.moe API returned status {response.status}: {error_text[:200]}")
                        raise ValueError(f"API request failed with status {response.status}")
                    
                    data = await response.json()
                    logger.info(f"Successfully fetched data from Uma.moe API")
            
            if not data or "members" not in data:
                logger.error("API response missing 'members' field")
                raise ValueError("Invalid API response structure")
            
            members = data.get("members", [])
            logger.info(f"API returned {len(members)} members")
            
            if not members:
                logger.warning("No members found in API response")
                return {}
            
            parsed_data = self._parse_api_data(members)
            logger.info(f"Successfully parsed {len(parsed_data)} active members from API")
            
            return parsed_data
            
        except aiohttp.ClientError as e:
            logger.error(f"Network error while fetching from Uma.moe API: {e}")
            raise
        except Exception as e:
            logger.error(f"Error during Uma.moe API scraping: {e}")
            raise
    
    def _parse_api_data(self, members: list) -> Dict[str, Dict]:
        """
        Parse API member data into scraper format
        
        CRITICAL: Uma.moe returns LIFETIME cumulative fans, but the quota system 
        expects fans earned THIS MONTH ONLY. We must convert by subtracting starting fans.
        
        Args:
            members: List of member dicts from API
        
        Returns:
            Dict mapping viewer_id -> member data
        """
        parsed_data = {}
        
        # Use calendar day, just like ChronoGenesis does
        # This ensures calculator expects the right number of days
        from datetime import datetime
        calendar_day = datetime.now().day
        current_day = calendar_day
        self.current_day_count = current_day
        
        logger.info(f"Current day: {current_day} (using calendar day like ChronoGenesis)")
        
        # Parse each member and convert lifetime fans to monthly fans
        for member in members:
            viewer_id = member.get("viewer_id")
            trainer_name = member.get("trainer_name")
            lifetime_fans = member.get("daily_fans", [])
            
            if not viewer_id or not trainer_name:
                logger.warning(f"Skipping member with missing data: viewer_id={viewer_id}, name={trainer_name}")
                continue
            
            # Skip members who left the club (0 fans on current day)
            current_day_index = current_day - 1
            if current_day_index >= len(lifetime_fans):
                logger.warning(f"Current day {current_day} exceeds array length for {trainer_name}")
                continue
            
            current_day_lifetime_fans = lifetime_fans[current_day_index]
            if current_day_lifetime_fans == 0:
                logger.debug(f"Skipping inactive member (left club): {trainer_name} (ID: {viewer_id})")
                continue
            
            viewer_id_str = str(viewer_id)
            
            # Detect join day (first non-zero value) and starting lifetime fans
            join_day = 1
            starting_lifetime_fans = 0
            
            for idx, fans in enumerate(lifetime_fans[:current_day], start=1):
                if fans > 0:
                    join_day = idx
                    starting_lifetime_fans = fans
                    break
            
            # Convert lifetime cumulative fans to monthly cumulative fans
            # Formula: monthly_fans = lifetime_fans - starting_lifetime_fans
            monthly_fans = []
            for day_idx in range(current_day):
                lifetime_total = lifetime_fans[day_idx]
                
                if lifetime_total == 0:
                    # Member hasn't joined yet
                    fans_this_month = 0
                else:
                    # Fans earned this month = current lifetime - starting lifetime
                    fans_this_month = lifetime_total - starting_lifetime_fans
                
                monthly_fans.append(fans_this_month)
            
            parsed_data[viewer_id_str] = {
                "name": trainer_name,
                "trainer_id": viewer_id_str,
                "fans": monthly_fans,
                "join_day": join_day
            }
            
            logger.debug(
                f"Parsed {trainer_name}: joined day {join_day}, "
                f"lifetime: {starting_lifetime_fans:,} → {current_day_lifetime_fans:,}, "
                f"monthly: {monthly_fans[-1]:,}"
            )
        
        return parsed_data
    
    def get_current_day(self) -> int:
        """Get the current day number"""
        return self.current_day_count