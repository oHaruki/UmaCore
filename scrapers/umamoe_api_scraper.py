"""
Uma.moe API scraper for club data fetching
"""
from typing import Dict, Optional, List
import logging
import calendar
import aiohttp
from datetime import datetime, date

from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class UmaMoeAPIScraper(BaseScraper):
    """Scraper using Uma.moe API for fast data retrieval"""
    
    def __init__(self, circle_id: str):
        self.circle_id = circle_id
        self.base_url = "https://uma.moe/api/v4/circles"
        self.current_day_count = 1
        # Track which year/month was actually fetched (differs from now() on Day 1)
        self._fetched_year = None
        self._fetched_month = None
        # Set to a date object when the scraper fell back to the previous month;
        # None when the fetched data matches the current calendar date.
        self._data_date: Optional[date] = None
        super().__init__(self.base_url)
    
    async def _fetch_month(self, session: aiohttp.ClientSession, year: int, month: int) -> Optional[dict]:
        """Fetch API data for a specific year/month. Returns parsed JSON or None on failure."""
        params = {
            "circle_id": self.circle_id,
            "year": year,
            "month": month
        }
        async with session.get(self.base_url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Uma.moe API returned status {response.status} for {year}-{month:02d}: {error_text[:200]}")
                return None
            return await response.json()
    
    async def scrape(self) -> Dict[str, Dict]:
        """
        Scrape club data from Uma.moe API.
        
        On Day 1 the new month hasn't populated yet, so we fetch the previous
        month as the primary data source. We also fetch the current month and
        use its index 0 as the true endpoint per member — this captures fans
        earned between the previous month's last snapshot (~15:10 UTC on the
        last day) and the reset. Without this correction, up to ~24h of fans
        at end-of-month are invisible.
        
        Returns:
            Dict mapping viewer_id -> member data
        """
        try:
            now = datetime.now()
            year = now.year
            month = now.month
            
            # Determine which month to use as primary data source
            if now.day == 1:
                if month == 1:
                    year -= 1
                    month = 12
                else:
                    month -= 1
                # Record the actual date the data belongs to so callers can use it
                # instead of today's date for reports, quota calculations, etc.
                last_day = calendar.monthrange(year, month)[1]
                self._data_date = date(year, month, last_day)
                logger.info(f"Day 1 detected: fetching previous month ({year}-{month:02d}) as primary source, data date: {self._data_date}")
            
            self._fetched_year = year
            self._fetched_month = month
            
            logger.info(f"Fetching data from Uma.moe API for circle {self.circle_id}...")
            
            async with aiohttp.ClientSession() as session:
                # Primary fetch: the month we're actually reporting on
                primary_data = await self._fetch_month(session, year, month)
                if not primary_data:
                    raise ValueError(f"Primary API request failed for {year}-{month:02d}")
                
                # On Day 1, also fetch current month for endpoint correction
                endpoint_members = None
                if now.day == 1:
                    endpoint_data = await self._fetch_month(session, now.year, now.month)
                    if endpoint_data and "members" in endpoint_data:
                        endpoint_members = endpoint_data.get("members", [])
                        logger.info(f"Fetched {len(endpoint_members)} members from {now.year}-{now.month:02d} for endpoint correction")
                    else:
                        logger.warning("Could not fetch current month for endpoint correction — using previous month's last snapshot")
            
            if not primary_data or "members" not in primary_data:
                logger.error("API response missing 'members' field")
                raise ValueError("Invalid API response structure")
            
            members = primary_data.get("members", [])
            logger.info(f"API returned {len(members)} members")
            
            if not members:
                logger.warning("No members found in API response")
                return {}
            
            parsed_data = self._parse_api_data(members, endpoint_members=endpoint_members)
            logger.info(f"Successfully parsed {len(parsed_data)} active members from API")
            
            return parsed_data
            
        except aiohttp.ClientError as e:
            logger.error(f"Network error while fetching from Uma.moe API: {e}")
            raise
        except Exception as e:
            logger.error(f"Error during Uma.moe API scraping: {e}")
            raise
    
    def _parse_api_data(self, members: list, endpoint_members: Optional[List] = None) -> Dict[str, Dict]:
        """
        Parse API member data into scraper format.
        
        Uma.moe returns LIFETIME cumulative fans. Converts to monthly by
        subtracting each member's starting lifetime fans (fans at join).
        
        Uma.moe updates around 15:10 UTC with yesterday's data, so:
        - Calendar Day 2 → Use Day 1 data
        - Calendar Day 3 → Use Day 2 data
        
        When endpoint_members is provided (Day 1 only), the final fan count
        per member is corrected using the current month's index 0 value.
        
        Args:
            members: List of member dicts from the primary (previous) month
            endpoint_members: Member list from current month (Day 1 only)
        
        Returns:
            Dict mapping viewer_id -> member data
        """
        parsed_data = {}
        
        now = datetime.now()
        
        if now.day == 1:
            # Day 1: Fetched previous month, use last day of that month
            current_day = calendar.monthrange(self._fetched_year, self._fetched_month)[1]
            logger.info(f"Day 1 fallback: using day {current_day} (last day of {self._fetched_year}-{self._fetched_month:02d})")
        else:
            # Day 2+: Competition is one day behind calendar
            # Use calendar day for array indexing (to read latest data)
            # But report as yesterday for quota calculations (actual competition day)
            current_day = now.day
            self._data_date = date(now.year, now.month, now.day - 1)
            logger.info(f"Calendar day {now.day} → reading index {current_day-1}, reporting as Day {now.day-1} (data_date: {self._data_date})")
        
        self.current_day_count = current_day
        
        # Build endpoint lookup: viewer_id -> lifetime fans at current month index 0.
        # This is the true month boundary snapshot for end-of-month correction.
        endpoint_totals = {}
        if endpoint_members:
            for m in endpoint_members:
                vid = m.get("viewer_id")
                fans = m.get("daily_fans", [])
                if vid and fans and len(fans) > 0 and fans[0] > 0:
                    endpoint_totals[str(vid)] = fans[0]
            logger.info(f"Endpoint correction available for {len(endpoint_totals)} members")
        
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
            monthly_fans = []
            for day_idx in range(current_day):
                lifetime_total = lifetime_fans[day_idx]
                
                if lifetime_total == 0:
                    fans_this_month = 0
                else:
                    fans_this_month = lifetime_total - starting_lifetime_fans
                
                monthly_fans.append(fans_this_month)
            
            # Day 1 endpoint correction: replace the final monthly total with the
            # value derived from current month's index 0. This captures fans earned
            # between the previous month's last snapshot and the actual reset.
            if endpoint_totals and viewer_id_str in endpoint_totals:
                endpoint_lifetime = endpoint_totals[viewer_id_str]
                if endpoint_lifetime >= starting_lifetime_fans:
                    corrected_monthly = endpoint_lifetime - starting_lifetime_fans
                    if corrected_monthly > monthly_fans[-1]:
                        logger.debug(
                            f"Endpoint correction for {trainer_name}: "
                            f"{monthly_fans[-1]:,} → {corrected_monthly:,} "
                            f"(+{corrected_monthly - monthly_fans[-1]:,} recovered)"
                        )
                        monthly_fans[-1] = corrected_monthly
                else:
                    logger.warning(
                        f"Endpoint correction skipped for {trainer_name}: "
                        f"endpoint lifetime ({endpoint_lifetime:,}) < starting ({starting_lifetime_fans:,})"
                    )
            
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
    
    def get_data_date(self) -> Optional[date]:
        """
        Returns the date the scraped data belongs to when the previous-month
        fallback was used (Day 1), or None when the data matches today.
        
        Callers should use this to override current_date for reports and
        quota calculations instead of blindly using datetime.now().
        """
        return self._data_date