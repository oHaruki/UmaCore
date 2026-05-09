"""
Monthly Info Board service for generating persistent information embed
"""
import discord
from datetime import date
from typing import Optional
from uuid import UUID
import logging
import calendar

from models import QuotaRequirement
from config.settings import DAILY_QUOTA, COLOR_INFO

logger = logging.getLogger(__name__)


class MonthlyInfoService:
    """Generates and updates the monthly information board"""
    
    @staticmethod
    async def create_monthly_info_embed(club_id: UUID, club_name: str, current_date: date,
                                        quota_period: str = 'daily') -> discord.Embed:
        """
        Create the monthly info embed with quota history and commands
        
        Args:
            club_id: Club ID for filtering quota requirements
            club_name: Club name for display
            current_date: Current date for determining month
        
        Returns:
            Discord Embed with monthly information
        """
        month_name = current_date.strftime('%B %Y')
        
        embed = discord.Embed(
            title=f"📋 Monthly Info Board - {club_name}",
            description=f"**{month_name}**\nCurrent quota requirements and available commands",
            color=COLOR_INFO,
            timestamp=discord.utils.utcnow()
        )
        
        # Get current quota
        current_quota = await QuotaRequirement.get_quota_for_date(club_id, current_date)
        
        # Format current quota
        if current_quota >= 1_000_000:
            quota_formatted = f"{current_quota / 1_000_000:.1f}M"
        elif current_quota >= 1_000:
            quota_formatted = f"{current_quota / 1_000:.1f}K"
        else:
            quota_formatted = str(current_quota)

        period_label = {'daily': 'day', 'weekly': 'week', 'biweekly': 'biweek'}.get(quota_period, 'day')
        quota_field_name = {'daily': 'Daily', 'weekly': 'Weekly', 'biweekly': 'Biweekly'}.get(quota_period, 'Daily')

        embed.add_field(
            name=f"📊 Current {quota_field_name} Quota",
            value=f"**{quota_formatted} fans/{period_label}** ({current_quota:,} fans)",
            inline=False
        )
        
        # Get quota history for this month
        quota_history = await QuotaRequirement.get_all_current_month(club_id, current_date)
        
        if quota_history:
            # Create visualization
            visualization = MonthlyInfoService._create_quota_visualization(
                current_date, quota_history, current_quota, quota_period
            )
            
            embed.add_field(
                name="📅 Quota History This Month",
                value=visualization,
                inline=False
            )
        else:
            embed.add_field(
                name="📅 Quota History This Month",
                value=f"No changes this month. Using default quota of {quota_formatted} fans/day.",
                inline=False
            )
        
        # User commands section
        user_commands = (
            "**Status & Tracking:**\n"
            "`/my_status` - View your linked trainer's status\n"
            f"`/member_status club:{club_name} trainer_name:<name>` - View any member\n"
            "\n"
            "**User Linking:**\n"
            f"`/link_trainer club:{club_name} trainer_name:<name>` - Link your Discord\n"
            "`/unlink` - Remove your trainer link\n"
            "`/notification_settings` - Manage DM notifications\n"
        )
        
        embed.add_field(
            name="🎮 Available Commands",
            value=user_commands,
            inline=False
        )
        
        embed.set_footer(text=f"{club_name} • This message auto-updates when quota changes • Last updated")
        
        return embed
    
    @staticmethod
    def _create_quota_visualization(current_date: date, quota_history: list, current_quota: int,
                                    quota_period: str = 'daily') -> str:
        """
        Create a visual representation of quota changes throughout the month
        
        Args:
            current_date: Current date
            quota_history: List of QuotaRequirement objects for this month
            current_quota: Current quota amount
        
        Returns:
            Formatted string with visualization
        """
        # Get month info
        year = current_date.year
        month = current_date.month
        _, last_day = calendar.monthrange(year, month)
        
        # Create a mapping of day -> quota
        day_quotas = {}
        
        # Start with default quota
        default_quota = DAILY_QUOTA
        
        # Apply quota changes
        for req in sorted(quota_history, key=lambda x: x.effective_date):
            day = req.effective_date.day
            # Set this quota from this day onwards
            for d in range(day, last_day + 1):
                day_quotas[d] = req.daily_quota
        
        # Fill in days before first change with default
        if quota_history:
            first_change_day = min(req.effective_date.day for req in quota_history)
            for d in range(1, first_change_day):
                if d not in day_quotas:
                    day_quotas[d] = default_quota
        
        # Build visualization
        lines = []
        
        # Group consecutive days with same quota
        current_quota_val = None
        start_day = None
        
        for day in range(1, last_day + 1):
            quota = day_quotas.get(day, current_quota)
            
            if quota != current_quota_val:
                # New quota value, close previous range if exists
                if current_quota_val is not None:
                    lines.append(MonthlyInfoService._format_quota_range(
                        start_day, day - 1, current_quota_val, current_date.day
                    ))
                
                current_quota_val = quota
                start_day = day
        
        # Close final range
        if current_quota_val is not None:
            lines.append(MonthlyInfoService._format_quota_range(
                start_day, last_day, current_quota_val, current_date.day
            ))
        
        period_label = {'daily': 'day', 'weekly': 'week', 'biweekly': 'biweek'}.get(quota_period, 'day')

        if not lines:
            return f"No quota changes. Default: {MonthlyInfoService._format_quota(default_quota)}"

        return "\n".join(
            line.replace('/day', f'/{period_label}') for line in lines
        )
    
    @staticmethod
    def _format_quota_range(start_day: int, end_day: int, quota: int, current_day: int) -> str:
        """Format a quota range for display"""
        quota_str = MonthlyInfoService._format_quota(quota)
        
        # Determine if this range includes today
        is_current = start_day <= current_day <= end_day
        indicator = "📍" if is_current else "  "
        
        if start_day == end_day:
            date_range = f"Day {start_day:2d}"
        else:
            date_range = f"Day {start_day:2d}-{end_day:2d}"
        
        return f"{indicator} `{date_range}` → **{quota_str}**/day"  # caller replaces /day with correct label
    
    @staticmethod
    def _format_quota(quota: int) -> str:
        """Format quota amount"""
        if quota >= 1_000_000:
            return f"{quota / 1_000_000:.1f}M"
        elif quota >= 1_000:
            return f"{quota / 1_000:.1f}K"
        else:
            return str(quota)