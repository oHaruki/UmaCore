"""
Bomb warning system manager
"""
from datetime import date
from typing import List, Dict
import logging

from models import Member, Bomb, QuotaHistory
from config.settings import BOMB_TRIGGER_DAYS, BOMB_COUNTDOWN_DAYS

logger = logging.getLogger(__name__)


class BombManager:
    """Manages bomb activation, deactivation, and countdowns"""
    
    async def check_and_activate_bombs(self, current_date: date) -> List[Bomb]:
        """
        Check all members and activate bombs for those who meet the criteria
        
        Args:
            current_date: Current date
        
        Returns:
            List of newly activated bombs
        """
        members = await Member.get_all_active()
        newly_activated = []
        
        for member in members:
            # Check if member already has an active bomb
            existing_bomb = await Bomb.get_active_for_member(member.member_id)
            if existing_bomb:
                continue
            
            # Check consecutive days behind
            consecutive_days = await QuotaHistory.check_consecutive_behind_days(
                member.member_id, BOMB_TRIGGER_DAYS
            )
            
            if consecutive_days >= BOMB_TRIGGER_DAYS:
                # Activate bomb
                bomb = await Bomb.create(
                    member_id=member.member_id,
                    activation_date=current_date,
                    days_remaining=BOMB_COUNTDOWN_DAYS
                )
                newly_activated.append(bomb)
                logger.warning(f"💣 Bomb activated for {member.trainer_name} "
                             f"({consecutive_days} days behind)")
        
        return newly_activated
    
    async def check_and_deactivate_bombs(self, current_date: date) -> List[Bomb]:
        """
        Check active bombs and deactivate if member is back on track
        
        Args:
            current_date: Current date
        
        Returns:
            List of deactivated bombs
        """
        active_bombs = await Bomb.get_all_active()
        deactivated = []
        
        for bomb in active_bombs:
            # Get latest quota history
            latest_history = await QuotaHistory.get_latest_for_member(bomb.member_id)
            
            if latest_history and latest_history.deficit_surplus >= 0:
                # Member is back on track
                await bomb.deactivate(current_date)
                deactivated.append(bomb)
                
                member = await Member.get_by_id(bomb.member_id)
                logger.info(f"✅ Bomb deactivated for {member.trainer_name} "
                          f"(back on track with +{latest_history.deficit_surplus:,})")
        
        return deactivated
    
    async def update_bomb_countdowns(self, current_date: date) -> List[Bomb]:
        """
        Decrement countdown for all active bombs (only once per day)
        
        Args:
            current_date: Current date for checking if countdown should happen
        
        Returns:
            List of bombs with updated countdowns
        """
        active_bombs = await Bomb.get_all_active()
        updated = []
        
        for bomb in active_bombs:
            await bomb.decrement_days(current_date)
            updated.append(bomb)
        
        return updated
    
    async def check_expired_bombs(self) -> List[Member]:
        """
        Check for bombs that have reached 0 days remaining
        
        Returns:
            List of members who should be kicked
        """
        active_bombs = await Bomb.get_all_active()
        members_to_kick = []
        
        for bomb in active_bombs:
            if bomb.days_remaining <= 0:
                # Check if still behind quota
                latest_history = await QuotaHistory.get_latest_for_member(bomb.member_id)
                
                if latest_history and latest_history.deficit_surplus < 0:
                    member = await Member.get_by_id(bomb.member_id)
                    members_to_kick.append(member)
                    logger.critical(f"🚨 KICK REQUIRED: {member.trainer_name} "
                                  f"(bomb expired, still {latest_history.deficit_surplus:,} behind)")
        
        return members_to_kick
    
    async def get_active_bombs_with_members(self) -> List[Dict]:
        """
        Get all active bombs with associated member information
        
        Returns:
            List of dicts containing bomb and member data
        """
        active_bombs = await Bomb.get_all_active()
        result = []
        
        for bomb in active_bombs:
            member = await Member.get_by_id(bomb.member_id)
            latest_history = await QuotaHistory.get_latest_for_member(bomb.member_id)
            
            result.append({
                'bomb': bomb,
                'member': member,
                'history': latest_history
            })
        
        # Sort by days remaining (ascending)
        result.sort(key=lambda x: x['bomb'].days_remaining)
        
        return result