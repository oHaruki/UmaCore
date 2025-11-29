"""
Quota calculation service
"""
from datetime import date, timedelta
from typing import Dict, List, Tuple
import logging

from models import Member, QuotaHistory
from config.settings import DAILY_QUOTA

logger = logging.getLogger(__name__)


class QuotaCalculator:
    """Handles all quota calculations and tracking"""
    
    @staticmethod
    def calculate_expected_fans(days_active_in_month: int) -> int:
        """
        Calculate expected cumulative fans based on days active this month
        
        Args:
            days_active_in_month: Number of days the member has been in the club this month
        
        Returns:
            Expected cumulative fan count
        """
        return days_active_in_month * DAILY_QUOTA
    
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
            # Member joined after today? Shouldn't happen
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
        new_members = 0
        updated_members = 0
        
        # Process each scraped member
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
                # Day 1 = November 1, Day 6 = November 6, etc.
                join_date = date(current_date.year, current_date.month, detected_join_day)
                
                member = await Member.create(trainer_name, join_date, trainer_id)
                new_members += 1
                logger.info(f"New member added: {trainer_name} (ID: {trainer_id}, "
                          f"joined Day {detected_join_day} = {join_date.strftime('%Y-%m-%d')})")
            else:
                # Existing member - update name if it changed
                if member.trainer_name != trainer_name:
                    await member.update_name(trainer_name)
            
            # Update last seen
            await member.update_last_seen(current_date)
            
            # Calculate days active this month based on their join date
            # If they joined 2024-11-06 and today is 2024-11-28, they've been active for 23 days
            if member.join_date.year == current_date.year and member.join_date.month == current_date.month:
                # They joined this month
                join_day_from_db = member.join_date.day
            else:
                # They joined in a previous month, so they've been active since Day 1
                join_day_from_db = 1
            
            days_active = self.calculate_days_active_in_month(join_day_from_db, current_day)
            expected_fans = self.calculate_expected_fans(days_active)
            
            # Calculate deficit/surplus
            deficit_surplus = self.calculate_deficit_surplus(cumulative_fans, expected_fans)
            
            # Count consecutive days behind
            days_behind = await self._calculate_days_behind(member.member_id, deficit_surplus)
            
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
    
    async def _calculate_days_behind(self, member_id, current_deficit_surplus: int) -> int:
        """Calculate how many consecutive days a member has been behind"""
        if current_deficit_surplus >= 0:
            return 0
        
        # Get recent history
        recent_history = await QuotaHistory.get_last_n_days(member_id, 10)
        
        if not recent_history:
            return 1
        
        # Count consecutive days with negative deficit (most recent first)
        consecutive_days = 1  # Include today
        for history in recent_history[1:]:  # Skip today (index 0)
            if history.deficit_surplus < 0:
                consecutive_days += 1
            else:
                break
        
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