"""
Quota calculation service with multi-club support
"""
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple, Set
from uuid import UUID
import logging
import calendar
import math

from models import Member, QuotaHistory, QuotaRequirement, Bomb, Club
from models.quota_requirement import QuotaSchedule
from config.database import db

logger = logging.getLogger(__name__)


class QuotaCalculator:
    """Handles all quota calculations and tracking per club"""
    
    @staticmethod
    async def calculate_expected_fans(club_id: UUID, member_join_date: date,
                                     current_date: date, quota_period: str = 'daily',
                                     schedule: Optional[QuotaSchedule] = None) -> int:
        """
        Calculate expected cumulative fans based on days active in current month.

        For weekly/biweekly periods the stored quota is the period amount (e.g. 700 000 for
        weekly), so we divide by the period length to get the per-day contribution.

        Args:
            club_id: Club UUID
            member_join_date: When the member joined the club
            current_date: The date the data belongs to (not necessarily today)
            quota_period: 'daily', 'weekly', or 'biweekly'
            schedule: Pre-loaded quota timeline. **Pass this when calling in a
                loop** — otherwise every call reloads it, which is what made this
                function issue a query per day per member. Omitting it is safe but
                costs two queries.

        Returns:
            Expected cumulative fan count for this month only
        """
        if schedule is None:
            schedule = await QuotaSchedule.load(club_id)

        # Determine the effective start date for this month
        if member_join_date.year == current_date.year and member_join_date.month == current_date.month:
            start_date = member_join_date
        else:
            start_date = date(current_date.year, current_date.month, 1)

        period_days = {'daily': 1, 'weekly': 7, 'biweekly': 14}.get(quota_period, 1)

        total_expected = 0.0

        day_count = (current_date - start_date).days + 1
        current_day = start_date
        for _ in range(day_count):
            # A member's join day establishes their baseline (actual gain is always
            # recorded as +0 that day), so it carries no quota requirement either.
            if current_day == member_join_date:
                current_day += timedelta(days=1)
                continue

            total_expected += schedule.for_date(current_day) / period_days
            current_day += timedelta(days=1)

        result = round(total_expected)
        logger.debug(f"Expected fans calculation: {start_date} to {current_date} = {day_count} days "
                     f"(period={quota_period}) = {result:,}")
        return result
    
    @staticmethod
    def calculate_days_active_in_month(member_join_date: date, current_date: date) -> int:
        """Calculate how many days a member has been active this month"""
        # Determine the effective start date for this month
        if member_join_date.year == current_date.year and member_join_date.month == current_date.month:
            # Joined this month
            start_date = member_join_date
        else:
            # Joined in previous month(s) - active since first day of current month
            start_date = date(current_date.year, current_date.month, 1)
        
        return (current_date - start_date).days + 1
    
    @staticmethod
    def calculate_deficit_surplus(actual_fans: int, expected_fans: int) -> int:
        """Calculate deficit or surplus (positive = surplus, negative = deficit)"""
        return actual_fans - expected_fans
    
    async def _latest_history_date(self, club_id: UUID) -> Optional[date]:
        """Date of the newest quota_history row for a club, or None if it has none."""
        return await db.fetchval(
            "SELECT MAX(date) FROM quota_history WHERE club_id = $1", club_id
        )

    async def handle_month_rollover(self, club_id: UUID, data_date: date) -> bool:
        """Carry a club into a new competition month. Returns True if it rolled over.

        Replaces the old "did anyone's fans drop by >50%?" detector, which drove an
        irreversible ``DELETE`` of quota_history, bombs and quota_requirements off a
        fuzzy threshold — one transferring member with a large lifetime total was
        enough to wipe a club's month.

        The month is not something to infer: the scraper fetches an explicit
        ``(year, month)`` and ``data_date`` names the day. So compare it to the
        newest row we already have and act only on a real boundary crossing.

        Deliberately does NOT delete anything:

        * ``quota_history`` is keyed by date, so a new month simply writes new rows
          and the old ones stay as history for the dashboard.
        * ``quota_requirements`` resolves via ``effective_date <= date``, so it is
          already month-correct; the old delete silently wiped an admin's
          configured quota schedule at every rollover.
        * ``bombs`` are the one exception — see :meth:`Bomb.expire_before`.
        """
        latest = await self._latest_history_date(club_id)
        if latest is None:
            return False
        if (latest.year, latest.month) >= (data_date.year, data_date.month):
            return False

        month_start = date(data_date.year, data_date.month, 1)
        logger.info(
            f"📅 Club {club_id} entering {data_date.year}-{data_date.month:02d} "
            f"(previous data ends {latest}) — expiring last month's bombs"
        )

        await Bomb.expire_before(club_id, month_start)

        # A new month is a clean slate for manual deactivations.
        await db.execute(
            "UPDATE members SET manually_deactivated = FALSE "
            "WHERE club_id = $1 AND manually_deactivated = TRUE",
            club_id,
        )
        return True


    async def _auto_deactivate_missing_members(self, club_id: UUID, scraped_trainer_ids: Set[str]):
        """Auto-deactivate members who are no longer in the scraped data"""
        active_members = await Member.get_all_active(club_id)
        
        deactivated_count = 0
        for member in active_members:
            member_key = member.trainer_id if member.trainer_id else member.trainer_name
            
            if member_key not in scraped_trainer_ids:
                await member.deactivate(manual=False)
                deactivated_count += 1
                logger.info(f"Auto-deactivated member (no longer in club): {member.trainer_name}")
        
        if deactivated_count > 0:
            logger.info(f"Auto-deactivated {deactivated_count} member(s) who left the club")
    
    async def process_scraped_data(self, club_id: UUID, scraped_data: Dict[str, Dict],
                                   current_date: date, current_day: int,
                                   quota_period: str = 'daily') -> Tuple[int, int]:
        """
        Process scraped data and update database for a specific club.
        
        Args:
            club_id: Club UUID
            scraped_data: Dict of trainer_id -> {name, trainer_id, fans[], join_day}
            current_date: Date the data represents (calculated by scraper/tasks)
            current_day: Day number in the array (used for array indexing)
        
        Returns:
            Tuple of (new_members_count, updated_members_count)
        """
        # Use the date already calculated by the scraper and tasks.py
        data_date = current_date
        logger.info(f"Processing scraped data for club {club_id}: data_date = {data_date}, current_day = {current_day}")
        
        # Competition month boundary — deterministic, no heuristics, no deletes.
        await self.handle_month_rollover(club_id, data_date)

        # Auto-deactivate members who are no longer in the scraped data
        scraped_trainer_ids = set(scraped_data.keys())
        await self._auto_deactivate_missing_members(club_id, scraped_trainer_ids)

        # Load the quota timeline once for the whole batch. Resolving it per day
        # per member is what made this O(members x days) queries.
        schedule = await QuotaSchedule.load(club_id)

        # Process each member
        new_members = 0
        updated_members = 0

        for key, member_data in scraped_data.items():
            trainer_id = member_data.get("trainer_id")
            trainer_name = member_data["name"]
            daily_fans = member_data["fans"]
            detected_join_day = member_data["join_day"]
            
            if not daily_fans:
                logger.warning(f"No fan data for {trainer_name}")
                continue
            
            # Use the last value in the fans array
            cumulative_fans = daily_fans[-1]
            
            # Look up member by trainer_id first, then by name
            if trainer_id:
                member = await Member.get_by_trainer_id(club_id, trainer_id)
            else:
                member = await Member.get_by_name(club_id, trainer_name)
            
            if not member:
                # New member - resolve their join day into a full date
                # detected_join_day is the day number in the scraped month (data_date.month)
                # Check if it's a valid day in that month
                last_day_of_month = calendar.monthrange(data_date.year, data_date.month)[1]
                
                if 1 <= detected_join_day <= last_day_of_month:
                    # Join day is within the current month being processed
                    join_date = date(data_date.year, data_date.month, detected_join_day)
                else:
                    # Join day exceeds current month, must be from previous month
                    if data_date.month == 1:
                        join_date = date(data_date.year - 1, 12, detected_join_day)
                    else:
                        join_date = date(data_date.year, data_date.month - 1, detected_join_day)
                
                member = await Member.create(club_id, trainer_name, join_date, trainer_id)
                new_members += 1
                logger.info(f"New member added: {trainer_name} (ID: {trainer_id}, joined {join_date.strftime('%Y-%m-%d')})")
            else:
                # Existing member
                if member.trainer_name != trainer_name:
                    await member.update_name(trainer_name)
                
                # Reactivate if previously auto-deactivated
                if not member.is_active:
                    if member.manually_deactivated:
                        logger.info(f"Skipping reactivation of manually deactivated member: {trainer_name}")
                        continue
                    else:
                        await member.activate()
                        await member.update_join_date(data_date)
                        logger.info(f"Reactivated returning member: {trainer_name} (join_date reset to {data_date})")
            
            # last_seen tracks when we actually observed them (wall-clock date)
            await member.update_last_seen(current_date)
            
            # All quota calculations use data_date
            days_active = self.calculate_days_active_in_month(member.join_date, data_date)
            
            expected_fans = await self.calculate_expected_fans(
                club_id, member.join_date, data_date, quota_period, schedule=schedule
            )
            
            deficit_surplus = self.calculate_deficit_surplus(cumulative_fans, expected_fans)
            
            days_behind = await self._calculate_days_behind(member.member_id, deficit_surplus, data_date)
            
            # Store history keyed to data_date
            await QuotaHistory.create(
                member_id=member.member_id,
                club_id=club_id,
                date=data_date,
                cumulative_fans=cumulative_fans,
                expected_fans=expected_fans,
                deficit_surplus=deficit_surplus,
                days_behind=days_behind
            )
            
            updated_members += 1
            
            logger.debug(f"{trainer_name}: {cumulative_fans:,} fans "
                        f"(expected: {expected_fans:,}, {deficit_surplus:+,}, days active: {days_active})")
        
        logger.info(f"Processed {updated_members} members ({new_members} new) for club {club_id}")
        return new_members, updated_members
    
    async def _calculate_days_behind(self, member_id: UUID, current_deficit_surplus: int,
                                    data_date: date) -> int:
        """Calculate how many consecutive days a member has been behind"""
        if current_deficit_surplus >= 0:
            return 0

        recent_history = await QuotaHistory.get_last_n_days(member_id, 10)

        if not recent_history:
            return 1

        # Exclude records from data_date or later, and from a different month
        recent_history = [
            h for h in recent_history
            if h.date < data_date
            and h.date.year == data_date.year
            and h.date.month == data_date.month
        ]

        # Count consecutive days with negative deficit before data_date
        consecutive_days = 1  # Count the current day

        for history in recent_history:
            if history.deficit_surplus < 0:
                consecutive_days += 1
            else:
                break

        logger.debug(f"Member {member_id}: {consecutive_days} consecutive days behind")
        return consecutive_days
    
    @staticmethod
    def get_period_info(quota_period: str, current_date: date) -> Optional[Dict]:
        """
        Return period metadata for the current date under weekly/biweekly quota.
        Returns None for daily quota.
        """
        if quota_period == 'daily':
            return None

        period_days = {'weekly': 7, 'biweekly': 14}[quota_period]
        days_in_month = calendar.monthrange(current_date.year, current_date.month)[1]

        day_of_month = current_date.day  # 1-indexed
        period_number = (day_of_month - 1) // period_days + 1

        period_start_day = (period_number - 1) * period_days + 1
        period_end_day = min(period_start_day + period_days - 1, days_in_month)

        period_start = date(current_date.year, current_date.month, period_start_day)
        period_end = date(current_date.year, current_date.month, period_end_day)

        total_periods = math.ceil(days_in_month / period_days)
        quota_label = 'week' if quota_period == 'weekly' else 'biweek'

        return {
            'period_number': period_number,
            'total_periods': total_periods,
            'period_start': period_start,
            'period_end': period_end,
            'period_days': period_days,
            'quota_label': quota_label,
        }

    async def get_member_status_summary(self, club_id: UUID, current_date: date,
                                        quota_period: str = 'daily') -> Dict:
        """
        Get summary of all members' status for a club.

        For weekly/biweekly quotas, each member_status entry will additionally
        contain 'period_start_fans' and 'period_info'.

        Returns:
            Dict with categorized member data
        """
        members = await Member.get_all_active(club_id)

        period_info = self.get_period_info(quota_period, current_date)

        # Pre-compute period_quota for the current period when not daily
        if period_info:
            actual_period_length = (period_info['period_end'] - period_info['period_start']).days + 1
            stored_quota = await QuotaRequirement.get_quota_for_date(club_id, period_info['period_start'])
            period_quota = round(stored_quota / period_info['period_days'] * actual_period_length)
            period_info['period_quota'] = period_quota

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

            if period_info:
                # Fans earned before this period started
                if period_info['period_start'].day == 1:
                    period_start_fans = 0
                else:
                    day_before_period = period_info['period_start'] - timedelta(days=1)
                    prev_record = await QuotaHistory.get_for_member_date(member.member_id, day_before_period)
                    period_start_fans = prev_record.cumulative_fans if prev_record else 0

                member_status['period_start_fans'] = period_start_fans
                member_status['period_info'] = period_info

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
            'total_members': len(members),
            'period_info': period_info,
        }