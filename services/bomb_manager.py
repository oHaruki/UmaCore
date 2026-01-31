"""
Bomb warning system manager - FIXED VERSION
This version skips kick alerts for already-deactivated members
"""
from datetime import date
from typing import List, Dict
from uuid import UUID
import logging

from models import Member, Bomb, QuotaHistory, Club

logger = logging.getLogger(__name__)


class BombManager:
    """Manages bomb activation, deactivation, and countdowns per club"""
    
    async def check_and_activate_bombs(self, club: Club, current_date: date) -> List[Bomb]:
        """
        Check all members and activate bombs for those who meet the criteria
        
        Args:
            club: Club object
            current_date: Current date
        
        Returns:
            List of newly activated bombs
        """
        members = await Member.get_all_active(club.club_id)
        newly_activated = []
        
        for member in members:
            existing_bomb = await Bomb.get_active_for_member(member.member_id)
            if existing_bomb:
                continue
            
            consecutive_days = await QuotaHistory.check_consecutive_behind_days(
                member.member_id, club.bomb_trigger_days
            )
            
            if consecutive_days >= club.bomb_trigger_days:
                bomb = await Bomb.create(
                    member_id=member.member_id,
                    club_id=club.club_id,
                    activation_date=current_date,
                    days_remaining=club.bomb_countdown_days
                )
                newly_activated.append(bomb)
                logger.warning(f"💣 Bomb activated for {member.trainer_name} in {club.club_name} "
                             f"({consecutive_days} days behind)")
        
        return newly_activated
    
    async def check_and_deactivate_bombs(self, club_id: UUID, current_date: date) -> List[Dict]:
        """
        Check active bombs and deactivate if member is back on track
        
        Args:
            club_id: Club UUID
            current_date: Current date
        
        Returns:
            List of dicts with 'bomb', 'member', and 'history' keys
        """
        active_bombs = await Bomb.get_all_active(club_id)
        deactivated = []
        
        for bomb in active_bombs:
            latest_history = await QuotaHistory.get_latest_for_member(bomb.member_id)
            
            if latest_history and latest_history.deficit_surplus >= 0:
                member = await Member.get_by_id(bomb.member_id)
                
                await bomb.deactivate(current_date)
                
                deactivated.append({
                    'bomb': bomb,
                    'member': member,
                    'history': latest_history
                })
                
                logger.info(f"✅ Bomb deactivated for {member.trainer_name} "
                          f"(back on track with +{latest_history.deficit_surplus:,})")
        
        return deactivated
    
    async def update_bomb_countdowns(self, club_id: UUID, current_date: date) -> List[Bomb]:
        """
        Decrement countdown for all active bombs (only once per day)
        
        Args:
            club_id: Club UUID
            current_date: Current date for checking if countdown should happen
        
        Returns:
            List of bombs with updated countdowns
        """
        active_bombs = await Bomb.get_all_active(club_id)
        updated = []
        
        for bomb in active_bombs:
            await bomb.decrement_days(current_date)
            updated.append(bomb)
        
        return updated
    
    async def check_expired_bombs(self, club_id: UUID) -> List[Member]:
        """
        Check for bombs that have reached 0 days remaining
        FIXED: Now skips members who are already deactivated
        
        Args:
            club_id: Club UUID
        
        Returns:
            List of members who should be kicked (only active members)
        """
        active_bombs = await Bomb.get_all_active(club_id)
        members_to_kick = []
        
        for bomb in active_bombs:
            if bomb.days_remaining <= 0:
                # Get member info
                member = await Member.get_by_id(bomb.member_id)
                
                # FIXED: Skip if member is already deactivated
                if not member or not member.is_active:
                    logger.info(f"Skipping kick alert for already-deactivated member: "
                              f"{member.trainer_name if member else 'Unknown'}")
                    continue
                
                # Check if still behind quota
                latest_history = await QuotaHistory.get_latest_for_member(bomb.member_id)
                
                if latest_history and latest_history.deficit_surplus < 0:
                    members_to_kick.append(member)
                    logger.critical(f"🚨 KICK REQUIRED: {member.trainer_name} "
                                  f"(bomb expired, still {latest_history.deficit_surplus:,} behind)")
        
        return members_to_kick
    
    async def get_active_bombs_with_members(self, club_id: UUID) -> List[Dict]:
        """
        Get all active bombs with associated member information
        Only includes bombs for currently active members
        
        Args:
            club_id: Club UUID
        
        Returns:
            List of dicts containing bomb, member, and history data
        """
        active_bombs = await Bomb.get_all_active(club_id)
        result = []
        
        for bomb in active_bombs:
            member = await Member.get_by_id(bomb.member_id)
            
            # Skip if member is deactivated
            if not member or not member.is_active:
                logger.debug(f"Skipping bomb for deactivated member: {member.trainer_name if member else 'Unknown'}")
                continue
            
            latest_history = await QuotaHistory.get_latest_for_member(bomb.member_id)
            
            result.append({
                'bomb': bomb,
                'member': member,
                'history': latest_history
            })
        
        # Sort by days remaining (ascending)
        result.sort(key=lambda x: x['bomb'].days_remaining)
        
        return result
