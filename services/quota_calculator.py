"""
Quota calculation service
"""
from datetime import date, timedelta
from typing import Dict, List, Tuple, Set
import logging

from models import Member, QuotaHistory, QuotaRequirement, Bomb
from config.settings import DAILY_QUOTA
from config.database import db

logger = logging.getLogger(__name__)


class QuotaCalculator:
    """Handles all quota calculations and tracking"""
    
    @staticmethod
    async def calculate_expected_fans(join_day: int, current_day: int, current_date: date) -> int:
        """
        Calculate expected cumulative fans based on days active and quota history
        
        Args:
            join_day: What day of the month they joined (1-31)
            current_day: Current day of the month (1-31)
            current_date: Current date object
        
        Returns:
            Expected cumulative fan count based on quota requirements
        """
        if join_day > current_day:
            return 0
        
        total_expected = 0
        
        # Calculate for each day the member was active
        for day in range(join_day, current_day + 1):
            # Get the date for this day
            day_date = date(current_date.year, current_date.month, day)
            
            # Get the quota that was in effect on that day
            daily_quota = await QuotaRequirement.get_quota_for_date(day_date)
            total_expected += daily_quota
        
        return total_expected
    
    @staticmethod
    def calculate_days_active_in_month(join_day: int, current_day: int) -> int:
        """
        Calculate how many days a member has been active this month
        
        Args:
            join_day: What day of the month they joined (1-31)
            current_day: Current day of the month (1-31)
        
        Returns:
            Number of days active (inclusive of both join and current day)
        """
        if join_day > current_day:
            return 0
        
        return current_day - join_day + 1
    
    @staticmethod
    def calculate_deficit_surplus(actual_fans: int, expected_fans: int) -> int:
        """
        Calculate deficit or surplus
        
        Returns:
            Positive = surplus, Negative = deficit
        """
        return actual_fans - expected_fans
    
    async def _get_previous_cumulative_totals(self) -> Dict[str, int]:
        """
        Get the latest cumulative fan counts from database for monthly reset detection
        
        Returns:
            Dict mapping trainer_id/name -> cumulative_fans
        """
        query = """
            SELECT m.trainer_id, m.trainer_name, qh.cumulative_fans
            FROM members m
            JOIN quota_history qh ON m.member_id = qh.member_id
            WHERE qh.date = (SELECT MAX(date) FROM quota_history)
        """
        rows = await db.fetch(query)
        
        result = {}
        for row in rows:
            key = row['trainer_id'] if row['trainer_id'] else row['trainer_name']
            result[key] = row['cumulative_fans']
        
        return result
    
    def _detect_monthly_reset_from_scraped(self, scraped_data: Dict[str, Dict], 
                                           previous_totals: Dict[str, int]) -> bool:
        """
        Detect if a monthly reset has occurred by comparing scraped data to previous totals
        
        Args:
            scraped_data: Dict of trainer_id -> {name, trainer_id, fans[], join_day}
            previous_totals: Dict of trainer_id/name -> previous cumulative fans
        
        Returns:
            True if monthly reset detected, False otherwise
        """
        if not previous_totals:
            logger.info("No previous data found, skipping reset detection")
            return False
        
        if not scraped_data:
            logger.warning("No scraped data, cannot detect reset")
            return False
        
        # Check if any member has significantly lower fans than before
        for key, member_data in scraped_data.items():
            current_fans = member_data["fans"][-1] if member_data["fans"] else 0
            
            if key in previous_totals:
                previous_fans = previous_totals[key]
                
                # If current count is less than 50% of previous, it's a reset
                if current_fans > 0 and current_fans < previous_fans * 0.5:
                    logger.warning(
                        f"Monthly reset detected: {member_data['name']} went from "
                        f"{previous_fans:,} to {current_fans:,} fans"
                    )
                    return True
        
        return False
    
    async def _auto_deactivate_missing_members(self, scraped_trainer_ids: Set[str]):
        """
        Auto-deactivate members who are no longer in the scraped data
        
        Args:
            scraped_trainer_ids: Set of trainer_ids (or names) from scraped data
        """
        # Get all currently active members
        active_members = await Member.get_all_active()
        
        deactivated_count = 0
        for member in active_members:
            # Use trainer_id if available, otherwise use name
            member_key = member.trainer_id if member.trainer_id else member.trainer_name
            
            # If this member is not in the scraped data, they've left the club
            if member_key not in scraped_trainer_ids:
                await member.deactivate()
                deactivated_count += 1
                logger.info(f"Auto-deactivated member (no longer in club): {member.trainer_name}")
        
        if deactivated_count > 0:
            logger.info(f"Auto-deactivated {deactivated_count} member(s) who left the club")
    
    async def process_scraped_data(self, scraped_data: Dict[str, Dict], 
                                   current_date: date, current_day: int) -> Tuple[int, int]:
        """
        Process scraped data and update database
        
        Args:
            scraped_data: Dict of trainer_id -> {name, trainer_id, fans[], join_day}
            current_date: Current date
            current_day: Current day number in the month (from scraper)
        
        Returns:
            Tuple of (new_members_count, updated_members_count)
        """
        # STEP 1: Check for monthly reset FIRST
        logger.info("Checking for monthly reset...")
        previous_totals = await self._get_previous_cumulative_totals()
        
        if self._detect_monthly_reset_from_scraped(scraped_data, previous_totals):
            logger.warning("Monthly reset detected! Clearing all history...")
            await QuotaHistory.clear_all()
            await Bomb.clear_all()
            await QuotaRequirement.clear_all()
            logger.info("Monthly reset complete - starting fresh")
        
        # STEP 2: Auto-deactivate members who are no longer in the scraped data
        scraped_trainer_ids = set(scraped_data.keys())
        await self._auto_deactivate_missing_members(scraped_trainer_ids)
        
        # STEP 3: Process scraped data normally
        new_members = 0
        updated_members = 0
        
        for key, member_data in scraped_data.items():
            trainer_id = member_data.get("trainer_id")
            trainer_name = member_data["name"]
            daily_fans = member_data["fans"]
            detected_join_day = member_data["join_day"]
            
            # Get the latest cumulative count (last day in the array)
            if not daily_fans:
                logger.warning(f"No fan data for {trainer_name}")
                continue
            
            cumulative_fans = daily_fans[-1]  # Last day's count
            
            # Look up member by trainer_id first, then by name
            if trainer_id:
                member = await Member.get_by_trainer_id(trainer_id)
            else:
                member = await Member.get_by_name(trainer_name)
            
            if not member:
                # New member - calculate their join date based on detected_join_day
                join_date = date(current_date.year, current_date.month, detected_join_day)
                
                member = await Member.create(trainer_name, join_date, trainer_id)
                new_members += 1
                logger.info(f"New member added: {trainer_name} (ID: {trainer_id}, "
                          f"joined Day {detected_join_day} = {join_date.strftime('%Y-%m-%d')})")
            else:
                # Existing member - update name if it changed
                if member.trainer_name != trainer_name:
                    await member.update_name(trainer_name)
                
                # Reactivate if they were previously deactivated
                if not member.is_active:
                    await member.activate()
                    logger.info(f"Reactivated returning member: {trainer_name}")
            
            # Update last seen
            await member.update_last_seen(current_date)
            
            # Calculate days active this month based on their join date
            if member.join_date.year == current_date.year and member.join_date.month == current_date.month:
                join_day_from_db = member.join_date.day
            else:
                join_day_from_db = 1
            
            days_active = self.calculate_days_active_in_month(join_day_from_db, current_day)
            
            # Calculate expected fans using dynamic quota system
            expected_fans = await self.calculate_expected_fans(join_day_from_db, current_day, current_date)
            
            # Calculate deficit/surplus
            deficit_surplus = self.calculate_deficit_surplus(cumulative_fans, expected_fans)
            
            # Count consecutive days behind
            days_behind = await self._calculate_days_behind(member.member_id, deficit_surplus, current_date)
            
            # Create or update quota history
            await QuotaHistory.create(
                member_id=member.member_id,
                date=current_date,
                cumulative_fans=cumulative_fans,
                expected_fans=expected_fans,
                deficit_surplus=deficit_surplus,
                days_behind=days_behind
            )
            
            updated_members += 1
            
            logger.debug(f"{trainer_name}: {cumulative_fans:,} fans "
                        f"(expected: {expected_fans:,} for {days_active} days, {deficit_surplus:+,})")
        
        logger.info(f"Processed {updated_members} members ({new_members} new)")
        return new_members, updated_members
    
    async def _calculate_days_behind(self, member_id, current_deficit_surplus: int, current_date: date) -> int:
        """
        Calculate how many consecutive days a member has been behind
        
        FIXED: 
        1. Filter out today's date from history (in case /force_check runs multiple times)
        2. Then count consecutive days before today that were behind
        """
        if current_deficit_surplus >= 0:
            return 0
        
        # Get recent history
        recent_history = await QuotaHistory.get_last_n_days(member_id, 10)
        
        if not recent_history:
            # First day being behind
            return 1
        
        # CRITICAL: Filter out any records from TODAY
        # (This happens when /force_check is run multiple times on the same day)
        recent_history = [h for h in recent_history if h.date < current_date]
        
        # Count consecutive days with negative deficit BEFORE today
        consecutive_days = 1  # Count today
        
        # Now loop through past days (yesterday, 2 days ago, etc.)
        for history in recent_history:
            if history.deficit_surplus < 0:
                consecutive_days += 1
            else:
                # Found a day they were on track, stop counting
                break
        
        logger.debug(f"Member {member_id}: {consecutive_days} consecutive days behind")
        return consecutive_days
    
    async def get_member_status_summary(self, current_date: date) -> Dict:
        """
        Get summary of all members' status
        
        Returns:
            Dict with categorized member data
        """
        members = await Member.get_all_active()
        
        on_track = []
        behind = []
        
        for member in members:
            latest_history = await QuotaHistory.get_latest_for_member(member.member_id)
            
            if not latest_history:
                continue
            
            member_status = {
                'member': member,
                'history': latest_history
            }
            
            if latest_history.deficit_surplus >= 0:
                on_track.append(member_status)
            else:
                behind.append(member_status)
        
        # Sort on_track by surplus (descending)
        on_track.sort(key=lambda x: x['history'].deficit_surplus, reverse=True)
        
        # Sort behind by deficit (most behind first)
        behind.sort(key=lambda x: x['history'].deficit_surplus)
        
        return {
            'on_track': on_track,
            'behind': behind,
            'total_members': len(members)
        }