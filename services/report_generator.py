"""
Discord report generation service
"""
from datetime import date, timedelta
from typing import Dict, List, Optional
import discord
import logging

from config.settings import COLOR_ON_TRACK, COLOR_BEHIND, COLOR_BOMB, COLOR_INFO

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

    def create_daily_report(self, club_name: str, daily_quota: int, status_summary: Dict,
                            bombs_data: List[Dict], report_date: date,
                            rank_data: Optional[Dict] = None,
                            quota_period: str = 'daily') -> List[discord.Embed]:
        """
        Create the main daily report embeds.

        Returns a list of embeds (multiple if content is too long).
        """
        embeds = []

        period_info = status_summary.get('period_info')

        # Build description based on quota period
        period_labels = {'daily': 'day', 'weekly': 'week', 'biweekly': '2 weeks'}
        period_label = period_labels.get(quota_period, 'day')
        quota_line = f"**Quota:** {self.format_fans_short(daily_quota)} fans per {period_label}"

        next_date = report_date + timedelta(days=1)
        date_range = (f"{report_date.strftime('%B %d')}, 16:00 CEST"
                      f" ~ {next_date.strftime('%B %d')}, 16:00 CEST")
        description = f"**Date:** {date_range}\n{quota_line}"

        if period_info:
            p_num = period_info['period_number']
            p_total = period_info['total_periods']
            p_start = period_info['period_start'].strftime('%b %d')
            p_end = period_info['period_end'].strftime('%b %d')
            description += f"\n**Period:** {period_info['quota_label'].capitalize()} {p_num} of {p_total} ({p_start} – {p_end})"

        # Summary embed
        summary_embed = discord.Embed(
            title=f"📊 Daily Quota Report - {club_name}",
            description=description,
            color=COLOR_INFO,
            timestamp=discord.utils.utcnow()
        )

        total = status_summary['total_members']
        on_track_count = len(status_summary['on_track'])
        behind_count = len(status_summary['behind'])
        bombs_count = len(bombs_data)

        # Build summary text
        summary_text = (
            f"**Total Members:** {total}\n"
            f"✅ On Track: {on_track_count}\n"
            f"⚠️ Behind: {behind_count}"
        )

        # Only show bomb count if there are active bombs
        # When bombs are disabled, bombs_data will be empty and this won't show
        if bombs_count > 0:
            summary_text += f"\n💣 Bombs Active: {bombs_count}"

        summary_embed.add_field(
            name="📈 Summary",
            value=summary_text,
            inline=False
        )

        if rank_data and rank_data.get('monthly_rank') is not None:
            rank_text = self._format_rank_section(rank_data)
            summary_embed.add_field(
                name="🏆 Club Rankings",
                value=rank_text,
                inline=False
            )

        summary_embed.set_footer(text=f"Umamusume Quota Tracker - {club_name}")
        embeds.append(summary_embed)

        # On Track embed (split if needed)
        if status_summary['on_track']:
            on_track_sections = self._split_into_sections(
                status_summary['on_track'],
                lambda item: self._format_member_line(item, is_behind=False, quota_period=quota_period),
                max_length=1000
            )

            for idx, section in enumerate(on_track_sections):
                title = "✅ On Track" if idx == 0 else f"✅ On Track (continued {idx + 1})"
                on_track_embed = discord.Embed(
                    title=title,
                    description=section,
                    color=COLOR_ON_TRACK,
                    timestamp=discord.utils.utcnow()
                )
                embeds.append(on_track_embed)

        # Behind embed (split if needed)
        if status_summary['behind']:
            behind_sections = self._split_into_sections(
                status_summary['behind'],
                lambda item: self._format_member_line(item, is_behind=True, quota_period=quota_period),
                max_length=1000
            )

            for idx, section in enumerate(behind_sections):
                title = "⚠️ Behind Quota" if idx == 0 else f"⚠️ Behind Quota (continued {idx + 1})"
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

    def _format_member_line(self, item: Dict, is_behind: bool, quota_period: str = 'daily') -> str:
        """Format a single member line for on-track or behind sections"""
        member = item['member']
        history = item['history']

        if quota_period != 'daily' and 'period_start_fans' in item and 'period_info' in item:
            period_info = item['period_info']
            period_fans = history.cumulative_fans - item['period_start_fans']
            period_quota = period_info['period_quota']
            period_label = period_info['quota_label']  # 'week' or 'biweek'

            if is_behind:
                deficit = abs(history.deficit_surplus)
                return (f"**{member.trainer_name}**: "
                        f"{self.format_fans_short(period_fans)}/{self.format_fans_short(period_quota)} this {period_label} "
                        f"(-{self.format_fans_short(deficit)} overall)")
            else:
                surplus = history.deficit_surplus
                return (f"**{member.trainer_name}**: "
                        f"{self.format_fans_short(period_fans)}/{self.format_fans_short(period_quota)} this {period_label} "
                        f"(+{self.format_fans_short(surplus)} overall)")

        # Default daily format
        if is_behind:
            deficit = abs(history.deficit_surplus)
            days_behind = history.days_behind
            days_text = f"{days_behind} day{'s' if days_behind != 1 else ''}"
            return f"**{member.trainer_name}**: -{self.format_fans_short(deficit)} ({days_text} behind)"
        else:
            surplus = history.deficit_surplus
            return f"**{member.trainer_name}**: +{self.format_fans_short(surplus)} ({self.format_number(history.cumulative_fans)} total)"

    def _split_into_sections(self, items: List[Dict], formatter, max_length: int = 1000) -> List[str]:
        """Split a list of items into text sections that fit within Discord's character limits"""
        sections = []
        current_section = []
        current_length = 0

        for item in items:
            line = formatter(item)
            line_length = len(line) + 1  # +1 for newline

            if current_length + line_length > max_length and current_section:
                sections.append("\n".join(current_section))
                current_section = [line]
                current_length = line_length
            else:
                current_section.append(line)
                current_length += line_length

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

            if days_remaining <= 2:
                emoji = "🔴"
            elif days_remaining <= 4:
                emoji = "🟠"
            else:
                emoji = "🟡"

            lines.append(
                f"{emoji} **{member.trainer_name}**: {days_remaining} day{'s' if days_remaining != 1 else ''} remaining "
                f"(-{self.format_fans_short(deficit)} behind)"
            )

        return "\n".join(lines) if lines else "*No active bombs*"

    def _format_rank_section(self, rank_data: Dict) -> str:
        """Format the club/monthly rank lines for the summary embed."""
        monthly_rank = rank_data.get('monthly_rank')
        last_month_rank = rank_data.get('last_month_rank')
        yesterday_rank = rank_data.get('yesterday_rank')

        lines = []

        # Club Rank line — delta vs yesterday (available directly from API)
        if monthly_rank is not None:
            if yesterday_rank is not None:
                delta = yesterday_rank - monthly_rank  # positive = improved (lower number = better)
                if delta > 0:
                    change = f"(↑{delta} since yesterday)"
                elif delta < 0:
                    change = f"(↓{abs(delta)} since yesterday)"
                else:
                    change = "(no change)"
                lines.append(f"Club Rank: #{monthly_rank} {change}")
            else:
                lines.append(f"Club Rank: #{monthly_rank}")

        # Monthly Rank line — last month comparison comes directly from the API
        if monthly_rank is not None:
            monthly_line = f"Monthly Rank: #{monthly_rank}"
            if last_month_rank is not None:
                monthly_line += f" | Last Month: #{last_month_rank}"
            lines.append(monthly_line)

        return "\n".join(lines) if lines else "*No rank data available*"

    def create_kick_alert(self, club_name: str, members_to_kick: List) -> List[discord.Embed]:
        """
        Create alert embeds for members who need to be kicked.

        Returns a list of embeds, split across multiple if needed.
        """
        items = [{"member": m} for m in members_to_kick]
        sections = self._split_into_sections(
            items,
            lambda item: (
                f"❌ **{item['member'].trainer_name}**: "
                f"Joined {item['member'].join_date.strftime('%Y-%m-%d')} — "
                f"bomb expired, still behind quota"
            ),
            max_length=1000
        )

        embeds = []
        for idx, section in enumerate(sections):
            title = f"🚨 KICK ALERT - {club_name}" if idx == 0 else f"🚨 KICK ALERT - {club_name} (continued {idx + 1})"
            description = (
                f"The following members have failed to meet quota after bomb expiration:\n\n{section}"
                if idx == 0 else section
            )
            embed = discord.Embed(
                title=title,
                description=description,
                color=COLOR_BOMB,
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text=f"Manual kick required - {club_name}")
            embeds.append(embed)

        return embeds

    def create_bomb_activation_alert(self, club_name: str, newly_activated: List[Dict]) -> List[discord.Embed]:
        """
        Create alert embeds for newly activated bombs.

        Returns a list of embeds, split across multiple if needed.
        """
        sections = self._split_into_sections(
            newly_activated,
            lambda item: (
                f"💣 **{item['member'].trainer_name}**: "
                f"**{item['bomb'].days_remaining} day{'s' if item['bomb'].days_remaining != 1 else ''}** to get back on track"
            ),
            max_length=1000
        )

        embeds = []
        for idx, section in enumerate(sections):
            title = f"💣 Bomb Activation Alert - {club_name}" if idx == 0 else f"💣 Bomb Activation Alert - {club_name} (continued {idx + 1})"
            description = (
                f"The following members have been behind quota for 3+ consecutive days:\n\n{section}"
                if idx == 0 else section
            )
            embed = discord.Embed(
                title=title,
                description=description,
                color=COLOR_BOMB,
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text=f"Get back on track to deactivate the bomb! - {club_name}")
            embeds.append(embed)

        return embeds

    def create_bomb_deactivation_report(self, club_name: str, deactivated: List[Dict]) -> List[discord.Embed]:
        """
        Create report embeds for deactivated bombs.

        Returns a list of embeds, split across multiple if needed.
        """
        sections = self._split_into_sections(
            deactivated,
            lambda item: (
                f"🎉 **{item['member'].trainer_name}**: "
                f"+{item['history'].deficit_surplus:,} fans surplus "
                f"({item['history'].cumulative_fans:,} total)"
            ),
            max_length=1000
        )

        count = len(deactivated)
        embeds = []
        for idx, section in enumerate(sections):
            title = f"✅ Bombs Defused - {club_name}" if idx == 0 else f"✅ Bombs Defused - {club_name} (continued {idx + 1})"
            description = (
                f"{count} member{'s' if count != 1 else ''} got back on track!\n\n{section}"
                if idx == 0 else section
            )
            embed = discord.Embed(
                title=title,
                description=description,
                color=COLOR_ON_TRACK,
                timestamp=discord.utils.utcnow()
            )
            embed.set_footer(text=f"Great job getting back on track! - {club_name}")
            embeds.append(embed)

        return embeds

    def create_error_report(self, club_name: str, error_message: str) -> discord.Embed:
        """Create an error report embed"""
        embed = discord.Embed(
            title=f"❌ Error During Daily Check - {club_name}",
            description=error_message,
            color=0xFF0000,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Please check logs for details - {club_name}")
        return embed