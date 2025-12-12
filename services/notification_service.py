"""
Notification service for sending DMs to linked users
"""
import discord
from typing import List, Dict
import logging

from models import UserLink, Member, QuotaHistory, Bomb
from config.settings import COLOR_BOMB, COLOR_BEHIND

logger = logging.getLogger(__name__)


class NotificationService:
    """Handles sending DM notifications to linked users"""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def send_bomb_notifications(self, club_name: str, newly_activated_bombs: List):
        """Send DM notifications to users whose bombs were just activated"""
        user_links = await UserLink.get_all_with_bomb_notifications()
        
        for bomb in newly_activated_bombs:
            member = await Member.get_by_id(bomb.member_id)
            
            # Find if this member is linked to a Discord user
            user_link = None
            for link in user_links:
                if link.member_id == member.member_id:
                    user_link = link
                    break
            
            if not user_link:
                continue
            
            try:
                user = await self.bot.fetch_user(user_link.discord_user_id)
                
                embed = discord.Embed(
                    title=f"💣 Bomb Activated - {club_name}",
                    description=f"Your trainer **{member.trainer_name}** has been behind quota for 3 consecutive days.",
                    color=COLOR_BOMB,
                    timestamp=discord.utils.utcnow()
                )
                
                latest_history = await QuotaHistory.get_latest_for_member(member.member_id)
                if latest_history:
                    deficit = abs(latest_history.deficit_surplus)
                    embed.add_field(
                        name="⚠️ Current Status",
                        value=f"**Deficit:** -{deficit:,} fans\n"
                              f"**Days Behind:** {latest_history.days_behind} consecutive days",
                        inline=False
                    )
                
                embed.add_field(
                    name="⏰ Countdown",
                    value=f"**{bomb.days_remaining} days** to get back on track or face removal from the club.",
                    inline=False
                )
                
                embed.add_field(
                    name="💡 What to do",
                    value=f"Earn **{deficit + (latest_history.expected_fans // latest_history.days_behind):,}+ fans per day** to catch up and deactivate the bomb!",
                    inline=False
                )
                
                embed.set_footer(text=f"Use /my_status to check your progress • {club_name}")
                
                await user.send(embed=embed)
                logger.info(f"Sent bomb notification to Discord user {user_link.discord_user_id} for {member.trainer_name} in {club_name}")
                
            except discord.Forbidden:
                logger.warning(f"Cannot send DM to user {user_link.discord_user_id} (DMs disabled)")
            except Exception as e:
                logger.error(f"Error sending bomb notification to {user_link.discord_user_id}: {e}")
    
    async def send_deficit_notifications(self, club_name: str, members_data: List[Dict]):
        """Send DM notifications to users who are behind quota (once per day)"""
        user_links = await UserLink.get_all_with_deficit_notifications()
        
        for item in members_data:
            member = item['member']
            history = item['history']
            
            # Only notify if actually behind
            if history.deficit_surplus >= 0:
                continue
            
            # Find if this member is linked to a Discord user
            user_link = None
            for link in user_links:
                if link.member_id == member.member_id:
                    user_link = link
                    break
            
            if not user_link:
                continue
            
            try:
                user = await self.bot.fetch_user(user_link.discord_user_id)
                
                deficit = abs(history.deficit_surplus)
                
                embed = discord.Embed(
                    title=f"⚠️ Behind Quota - {club_name}",
                    description=f"Your trainer **{member.trainer_name}** is currently behind quota.",
                    color=COLOR_BEHIND,
                    timestamp=discord.utils.utcnow()
                )
                
                # Progress bar
                if history.expected_fans > 0:
                    progress_pct = min(100, int((history.cumulative_fans / history.expected_fans) * 100))
                    filled = int(progress_pct / 5)
                    empty = 20 - filled
                    bar = "█" * filled + "░" * empty
                    
                    embed.add_field(
                        name="📉 Current Progress",
                        value=f"```\nCurrent:  {history.cumulative_fans:,} 👥\n"
                              f"Expected: {history.expected_fans:,} 👥\n"
                              f"━━━━━━━━━━━━━━━━━━━━\n"
                              f"Progress: {bar} {progress_pct}%\n```",
                        inline=False
                    )
                
                embed.add_field(
                    name="⚠️ Status",
                    value=f"**Deficit:** -{deficit:,} fans\n"
                          f"**Days Behind:** {history.days_behind} consecutive days",
                    inline=False
                )
                
                # Bomb warning if close
                if history.days_behind == 2:
                    embed.add_field(
                        name="🚨 Bomb Warning",
                        value="**1 more day behind** and a bomb will be activated!\nGet back on track today to avoid this.",
                        inline=False
                    )
                
                embed.set_footer(text=f"Use /my_status to check progress • {club_name}")
                
                await user.send(embed=embed)
                logger.info(f"Sent deficit notification to Discord user {user_link.discord_user_id} for {member.trainer_name} in {club_name}")
                
            except discord.Forbidden:
                logger.warning(f"Cannot send DM to user {user_link.discord_user_id} (DMs disabled)")
            except Exception as e:
                logger.error(f"Error sending deficit notification to {user_link.discord_user_id}: {e}")
    
    async def send_bomb_deactivation_notification(self, club_name: str, member: Member):
        """Send notification when a bomb is deactivated"""
        user_link = await UserLink.get_by_member_id(member.member_id)
        
        if not user_link or not user_link.notify_on_bombs:
            return
        
        try:
            user = await self.bot.fetch_user(user_link.discord_user_id)
            
            embed = discord.Embed(
                title=f"✅ Bomb Deactivated - {club_name}",
                description=f"Congratulations! Your trainer **{member.trainer_name}** is back on track!",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            
            latest_history = await QuotaHistory.get_latest_for_member(member.member_id)
            if latest_history:
                surplus = latest_history.deficit_surplus
                embed.add_field(
                    name="🎉 Great Job!",
                    value=f"**Surplus:** +{surplus:,} fans\n"
                          f"**Current Fans:** {latest_history.cumulative_fans:,}",
                    inline=False
                )
            
            embed.add_field(
                name="💡 Keep it up!",
                value="Stay on track to avoid future bombs. Keep earning those fans! 🏆",
                inline=False
            )
            
            embed.set_footer(text=f"Use /my_status to check your progress • {club_name}")
            
            await user.send(embed=embed)
            logger.info(f"Sent bomb deactivation notification to Discord user {user_link.discord_user_id} for {club_name}")
            
        except discord.Forbidden:
            logger.warning(f"Cannot send DM to user {user_link.discord_user_id} (DMs disabled)")
        except Exception as e:
            logger.error(f"Error sending deactivation notification to {user_link.discord_user_id}: {e}")
