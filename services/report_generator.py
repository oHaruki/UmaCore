"""
Discord report generation service
"""
from datetime import date
from typing import Dict, List
import discord
import logging

from config.settings import COLOR_ON_TRACK, COLOR_BEHIND, COLOR_BOMB, COLOR_INFO, DAILY_QUOTA

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates Discord embed reports"""
    
    @staticmethod
    def format_number(num: int) -> str:
        """Format number with commas"""
        return f"{num:,}"
    
    @staticmethod
    def format_fans_short(num: int) -> str:
        """Format fan count in short form (e.g., 1.5M)"""
        if abs(num) >= 1_000_000:
            return f"{num / 1_000_000:.1f}M"
        elif abs(num) >= 1_000:
            return f"{num / 1_000:.1f}K"
        return str(num)
    
    def create_daily_report(self, status_summary: Dict, bombs_data: List[Dict], 
                           report_date: date) -> List[discord.Embed]:
        """
        Create the main daily report embed(s)
        
        Args:
            status_summary: Dict from QuotaCalculator.get_member_status_summary()
            bombs_data: List from BombManager.get_active_bombs_with_members()
            report_date: Date of the report
        
        Returns:
            List of Discord Embed objects (multiple if content is too long)
        """
        embeds = []
        
        # Summary embed
        summary_embed = discord.Embed(
            title="📊 Daily Quota Report",
            description=f"**Date:** {report_date.strftime('%B %d, %Y')}\n"
                       f"**Daily Quota:** {self.format_fans_short(DAILY_QUOTA)} fans per member",
            color=COLOR_INFO,
            timestamp=discord.utils.utcnow()
        )
        
        # Summary stats
        total = status_summary['total_members']
        on_track_count = len(status_summary['on_track'])
        behind_count = len(status_summary['behind'])
        bombs_count = len(bombs_data)
        
        summary_embed.add_field(
            name="📈 Summary",
            value=f"**Total Members:** {total}\n"
                  f"✅ On Track: {on_track_count}\n"
                  f"⚠️ Behind: {behind_count}\n"
                  f"💣 Bombs Active: {bombs_count}",
            inline=False
        )
        
        summary_embed.set_footer(text="Umamusume Quota Tracker")
        embeds.append(summary_embed)
        
        # On Track embed (separate if needed)
        if status_summary['on_track']:
            on_track_sections = self._split_into_sections(
                status_summary['on_track'],
                lambda item: self._format_member_line(item, is_behind=False),
                max_length=1000
            )
            
            for idx, section in enumerate(on_track_sections):
                title = "✅ On Track" if idx == 0 else f"✅ On Track (continued {idx+1})"
                on_track_embed = discord.Embed(
                    title=title,
                    description=section,
                    color=COLOR_ON_TRACK,
                    timestamp=discord.utils.utcnow()
                )
                embeds.append(on_track_embed)
        
        # Behind embed (separate if needed)
        if status_summary['behind']:
            behind_sections = self._split_into_sections(
                status_summary['behind'],
                lambda item: self._format_member_line(item, is_behind=True),
                max_length=1000
            )
            
            for idx, section in enumerate(behind_sections):
                title = "⚠️ Behind Quota" if idx == 0 else f"⚠️ Behind Quota (continued {idx+1})"
                behind_embed = discord.Embed(
                    title=title,
                    description=section,
                    color=COLOR_BEHIND,
                    timestamp=discord.utils.utcnow()
                )
                embeds.append(behind_embed)
        
        # Bombs embed (if any)
        if bombs_data:
            bombs_text = self._format_bombs_section(bombs_data)
            bombs_embed = discord.Embed(
                title="💣 Active Bombs",
                description=bombs_text,
                color=COLOR_BOMB,
                timestamp=discord.utils.utcnow()
            )
            embeds.append(bombs_embed)
        
        return embeds
    
    def _format_member_line(self, item: Dict, is_behind: bool) -> str:
        """Format a single member line"""
        member = item['member']
        history = item['history']
        
        if is_behind:
            deficit = abs(history.deficit_surplus)
            days_behind = history.days_behind
            days_text = f"{days_behind} day{'s' if days_behind != 1 else ''}"
            return f"**{member.trainer_name}**: -{self.format_fans_short(deficit)} ({days_text} behind)"
        else:
            surplus = history.deficit_surplus
            return f"**{member.trainer_name}**: +{self.format_fans_short(surplus)} ({self.format_number(history.cumulative_fans)} total)"
    
    def _split_into_sections(self, items: List[Dict], formatter, max_length: int = 1000) -> List[str]:
        """Split a list of items into multiple sections that fit within Discord's limits"""
        sections = []
        current_section = []
        current_length = 0
        
        for item in items:
            line = formatter(item)
            line_length = len(line) + 1  # +1 for newline
            
            if current_length + line_length > max_length and current_section:
                # Current section is full, start a new one
                sections.append("\n".join(current_section))
                current_section = [line]
                current_length = line_length
            else:
                current_section.append(line)
                current_length += line_length
        
        # Add the last section
        if current_section:
            sections.append("\n".join(current_section))
        
        return sections if sections else ["*No members*"]
    
    def _format_bombs_section(self, bombs_data: List[Dict]) -> str:
        """Format the active bombs section"""
        lines = []
        
        for item in bombs_data:
            member = item['member']
            bomb = item['bomb']
            history = item['history']
            
            deficit = abs(history.deficit_surplus)
            days_remaining = bomb.days_remaining
            
            # Different emoji based on urgency
            if days_remaining <= 2:
                emoji = "🔴"
            elif days_remaining <= 4:
                emoji = "🟠"
            else:
                emoji = "🟡"
            
            lines.append(
                f"{emoji} **{member.trainer_name}**: {days_remaining} day{'s' if days_remaining != 1 else ''} remaining "
                f"(#{self.format_fans_short(deficit)} behind)"
            )
        
        return "\n".join(lines) if lines else "*No active bombs*"
    
    def create_kick_alert(self, members_to_kick: List) -> discord.Embed:
        """
        Create an alert embed for members who need to be kicked
        
        Args:
            members_to_kick: List of Member objects
        
        Returns:
            Discord Embed object
        """
        embed = discord.Embed(
            title="🚨 KICK ALERT",
            description="The following members have failed to meet quota after bomb expiration:",
            color=COLOR_BOMB,
            timestamp=discord.utils.utcnow()
        )
        
        for member in members_to_kick:
            embed.add_field(
                name=f"❌ {member.trainer_name}",
                value=f"Joined: {member.join_date.strftime('%Y-%m-%d')}\n"
                      f"Bomb expired - still behind quota",
                inline=False
            )
        
        embed.set_footer(text="Manual kick required")
        return embed
    
    def create_bomb_activation_alert(self, newly_activated: List[Dict]) -> discord.Embed:
        """
        Create an alert embed for newly activated bombs
        
        Args:
            newly_activated: List of dicts with 'bomb' and 'member' keys
        
        Returns:
            Discord Embed object
        """
        embed = discord.Embed(
            title="💣 Bomb Activation Alert",
            description=f"The following members have been behind quota for 3+ consecutive days:",
            color=COLOR_BOMB,
            timestamp=discord.utils.utcnow()
        )
        
        for item in newly_activated:
            member = item['member']
            bomb = item['bomb']
            
            embed.add_field(
                name=f"💣 {member.trainer_name}",
                value=f"**{bomb.days_remaining} days** to get back on track",
                inline=False
            )
        
        embed.set_footer(text="Get back on track to deactivate the bomb!")
        return embed
    
    def create_bomb_deactivation_report(self, deactivated: List[Dict]) -> discord.Embed:
        """
        Create an embed for bombs that were deactivated (back on track)
        
        Args:
            deactivated: List of dicts with 'bomb', 'member', and 'history' keys
        
        Returns:
            Discord Embed object
        """
        embed = discord.Embed(
            title="✅ Bombs Defused",
            description=f"{len(deactivated)} member{'s' if len(deactivated) != 1 else ''} got back on track!",
            color=COLOR_ON_TRACK,
            timestamp=discord.utils.utcnow()
        )
        
        for item in deactivated:
            member = item['member']
            history = item['history']
            
            surplus = history.deficit_surplus
            
            embed.add_field(
                name=f"🎉 {member.trainer_name}",
                value=f"**+{surplus:,} fans** surplus\n"
                      f"Total: {history.cumulative_fans:,} fans",
                inline=True
            )
        
        embed.set_footer(text="Great job getting back on track!")
        return embed
    
    def create_error_report(self, error_message: str) -> discord.Embed:
        """Create an error report embed"""
        embed = discord.Embed(
            title="❌ Error During Daily Check",
            description=error_message,
            color=0xFF0000,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Please check logs for details")
        return embed