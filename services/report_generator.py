"""
Discord report generation service
"""
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
import discord
import logging

from config.settings import COLOR_ON_TRACK, COLOR_BOMB
from services import embed_style
from services.quota_calculator import PERIOD_DAYS, BehindCount, quota_days
from utils.jst_calendar import ROLLOVER_UTC_HOUR
from utils.timezone_helper import resolve_timezone

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

    @staticmethod
    def _competition_window(report_date: date, club_timezone: Optional[str] = None) -> str:
        """Describe the competition day this report covers, in the club's timezone.

        uma.moe runs on JST, so a competition day closes at 15:00 UTC and the day
        reported as ``report_date`` actually spans ``report_date 15:00 UTC`` to the
        following day at the same time.

        A fixed offset like "16:00 CEST" is wrong here — the wrong hour once DST
        shifts, and wrong entirely for a club outside central Europe — so the
        window is always computed from the club's own timezone.
        """
        start_utc = datetime(report_date.year, report_date.month, report_date.day,
                             ROLLOVER_UTC_HOUR, 0, tzinfo=timezone.utc)
        end_utc = start_utc + timedelta(days=1)

        try:
            tz = resolve_timezone(club_timezone) if club_timezone else timezone.utc
            start, end = start_utc.astimezone(tz), end_utc.astimezone(tz)
            label = start.strftime("%Z") or "UTC"
        except Exception:
            start, end, label = start_utc, end_utc, "UTC"

        return (f"{start.strftime('%B %d')}, {start.strftime('%H:%M')}"
                f" ~ {end.strftime('%B %d')}, {end.strftime('%H:%M')} {label}")

    def create_daily_report(self, club_name: str, daily_quota: int, status_summary: Dict,
                            bombs_data: List[Dict], report_date: date,
                            rank_data: Optional[Dict] = None,
                            quota_period: str = 'daily',
                            club_timezone: Optional[str] = None,
                            daily_gain: bool = False,
                            style: Optional[Dict] = None,
                            days_mode: str = 'streak') -> List[discord.Embed]:
        """
        Create the main daily report embeds.

        ``style`` is the club's resolved report style (see
        :func:`services.embed_style.resolve`). Without one the defaults apply,
        where ``daily_gain`` swaps each member's running surplus for the fans
        they gained that day. ``days_mode`` picks what ``{days_behind}`` counts.

        Returns a list of embeds (multiple if content is too long).
        """
        if style is None:
            style = embed_style.defaults(embed_style.REPORT, quota_period, daily_gain)
        counting = BehindCount(days_mode, daily_quota / PERIOD_DAYS.get(quota_period, 1))
        decimals = style['decimals']
        embeds = []

        period_info = status_summary.get('period_info')

        # Build description based on quota period
        period_labels = {'daily': 'day', 'weekly': 'week', 'biweekly': '2 weeks'}
        period_label = period_labels.get(quota_period, 'day')
        quota_line = f"**Quota:** {self.format_fans_short(daily_quota)} fans per {period_label}"

        date_range = self._competition_window(report_date, club_timezone)
        description = f"**Date:** {date_range}\n{quota_line}"

        if period_info:
            label = period_info['quota_label'].capitalize()
            p_start = period_info['period_start'].strftime('%b %d')
            p_end = period_info['period_end'].strftime('%b %d')
            if period_info.get('anchored'):
                description += (f"\n**Period:** {label} {p_start} – {p_end} "
                                f"(day {period_info['day_number']} of {period_info['period_days']})")
            else:
                p_num = period_info['period_number']
                p_total = period_info['total_periods']
                description += f"\n**Period:** {label} {p_num} of {p_total} ({p_start} – {p_end})"

        values = self._summary_values(club_name, daily_quota, status_summary, bombs_data,
                                      report_date, rank_data, quota_period, decimals)
        note = embed_style.render(style['note'], values).strip()
        if note:
            description = f"{description}\n\n{note}"[:4096]

        # Summary embed
        summary_embed = discord.Embed(
            title=self._title(style['title'], club_name, status_summary['total_members']),
            description=description,
            color=embed_style.color(style, 'color_report'),
            timestamp=discord.utils.utcnow()
        )
        if style['thumbnail']:
            summary_embed.set_thumbnail(url=style['thumbnail'])
        if style['image']:
            summary_embed.set_image(url=style['image'])

        total = status_summary['total_members']
        on_track_count = len(status_summary['on_track'])
        behind_count = len(status_summary['behind'])
        bombs_count = len(bombs_data)

        if style['show_summary']:
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

        if style['show_rankings'] and rank_data and rank_data.get('monthly_rank') is not None:
            rank_text = self._format_rank_section(rank_data)
            summary_embed.add_field(
                name="🏆 Club Rankings",
                value=rank_text,
                inline=False
            )

        for block in embed_style.blocks(style, 'field'):
            name = embed_style.render(block['title'], values).strip()[:256]
            value = embed_style.render(block['text'], values).strip()[:1024]
            if name and value:
                summary_embed.add_field(name=name, value=value, inline=block['inline'])

        footer = embed_style.render(style['footer'], {'club': club_name})
        if footer:
            summary_embed.set_footer(text=footer)
        embeds.append(summary_embed)

        sections = []
        if style['show_on_track']:
            sections.append((status_summary['on_track'], False, 'title_on_track', 'color_on_track',
                             'line_on_track', self._ON_TRACK_ORDER[style['sort_on_track']]))
        if style['show_behind']:
            sections.append((status_summary['behind'], True, 'title_behind', 'color_behind',
                             'line_behind', self._BEHIND_ORDER[style['sort_behind']]))

        # On Track, then Behind (each split if needed)
        for items, is_behind, title_key, color_key, line_key, order in sections:
            if not items:
                continue
            ordered = sorted(items, key=lambda i: order(i, days_mode))
            lines = [
                embed_style.render(style[line_key],
                                   self._member_values(item, pos, decimals, counting))
                for pos, item in enumerate(ordered, 1)
            ]
            base_title = self._title(style[title_key], club_name, len(items))
            for idx, section in enumerate(self._split_lines(lines)):
                embeds.append(discord.Embed(
                    title=self._continued(base_title, idx),
                    description=section,
                    color=embed_style.color(style, color_key),
                    timestamp=discord.utils.utcnow()
                ))

        # Bombs embed (if any)
        if bombs_data and style['show_bombs']:
            bombs_text = self._format_bombs_section(bombs_data, style, counting)
            bombs_embed = discord.Embed(
                title=self._title(style['title_bombs'], club_name, len(bombs_data)),
                description=bombs_text,
                color=embed_style.color(style, 'color_bombs'),
                timestamp=discord.utils.utcnow()
            )
            embeds.append(bombs_embed)

        everyone = status_summary['on_track'] + status_summary['behind']
        for block in embed_style.blocks(style, 'list'):
            keep = self._LIST_WHO[block['who']]
            members = [i for i in everyone if keep(i, counting, report_date)]
            members.sort(key=lambda i: self._LIST_ORDER[block['order']](i, days_mode))
            if block['limit']:
                members = members[:block['limit']]
            if not members:
                continue
            lines = [embed_style.render(block['line'],
                                        self._member_values(item, pos, decimals, counting))
                     for pos, item in enumerate(members, 1)]
            base_title = self._title(block['title'], club_name, len(members))
            for idx, section in enumerate(self._split_lines(lines)):
                embeds.append(discord.Embed(
                    title=self._continued(base_title, idx),
                    description=section,
                    color=embed_style.color(block, 'color'),
                    timestamp=discord.utils.utcnow()
                ))

        return embeds

    def daily_report_message(self, club_name: str, daily_quota: int, status_summary: Dict,
                             bombs_data: List[Dict], report_date: date,
                             rank_data: Optional[Dict] = None,
                             quota_period: str = 'daily',
                             style: Optional[Dict] = None) -> Optional[str]:
        """The text posted above the report, e.g. a role ping. None when the club set none."""
        if not style or not style.get('message'):
            return None
        values = self._summary_values(club_name, daily_quota, status_summary, bombs_data,
                                      report_date, rank_data, quota_period, style['decimals'])
        return embed_style.render(style['message'], values).strip()[:2000] or None

    def _summary_values(self, club_name: str, daily_quota: int, status_summary: Dict,
                        bombs_data: List[Dict], report_date: date, rank_data: Optional[Dict],
                        quota_period: str, decimals: int) -> Dict[str, str]:
        """Club-wide token values for the message, the note and extra fields."""
        everyone = status_summary['on_track'] + status_summary['behind']
        gains = [(i['member'].trainer_name, i.get('day_fans') or 0) for i in everyone]
        fans_today = sum(g for _, g in gains)
        top = max(gains, key=lambda g: g[1], default=None)
        has_top = top is not None and top[1] > 0
        rank = (rank_data or {}).get('monthly_rank')
        return {
            'club': club_name,
            'date': f"{report_date:%B} {report_date.day}",
            'quota': embed_style.compact(daily_quota, decimals),
            'period': {'daily': 'day', 'weekly': 'week', 'biweekly': '2 weeks'}.get(quota_period, 'day'),
            'members': str(status_summary['total_members']),
            'on_track': str(len(status_summary['on_track'])),
            'behind': str(len(status_summary['behind'])),
            'bombs': str(len(bombs_data)),
            'fans_today': embed_style.compact(fans_today, decimals),
            'club_total': embed_style.compact(
                sum(i['history'].cumulative_fans for i in everyone), decimals),
            'avg_gain': (embed_style.compact(round(fans_today / len(everyone)), decimals)
                         if everyone else embed_style.PLACEHOLDER),
            'top_name': top[0] if has_top else embed_style.PLACEHOLDER,
            'top_gain': embed_style.signed(top[1], decimals) if has_top else embed_style.PLACEHOLDER,
            'rank': f"#{rank:,}" if rank else embed_style.PLACEHOLDER,
        }

    # Who an extra list picks, from everyone in the report.
    _LIST_WHO = {
        'all': lambda i, c, d: True,
        'on_track': lambda i, c, d: i['history'].deficit_surplus >= 0,
        'behind': lambda i, c, d: i['history'].deficit_surplus < 0,
        'close': lambda i, c, d: 0 < -i['history'].deficit_surplus <= c.per_day_quota,
        'gained': lambda i, c, d: (i.get('day_fans') or 0) > 0,
        'no_gain': lambda i, c, d: i.get('day_fans') == 0,
        'joined': lambda i, c, d: (getattr(i['member'], 'join_date', None) is not None
                                   and (i['member'].join_date.year, i['member'].join_date.month)
                                   == (d.year, d.month)),
    }
    _LIST_ORDER = {
        'gain': lambda i, mode: -(i.get('day_fans') or 0),
        'total': lambda i, mode: -i['history'].cumulative_fans,
        'ahead': lambda i, mode: -i['history'].deficit_surplus,
        'behind': lambda i, mode: i['history'].deficit_surplus,
        'days': lambda i, mode: (-((i.get('quota_days') or 0) if mode == 'quota'
                                   else i.get('streak', i['history'].days_behind)),
                                 i['history'].deficit_surplus),
        'name': lambda i, mode: i['member'].trainer_name.lower(),
    }

    # Sort keys per order choice. Python's sort is stable and the status summary
    # arrives most-ahead / most-behind first, so the defaults keep that order.
    _ON_TRACK_ORDER = {
        'surplus': lambda i, mode: -i['history'].deficit_surplus,
        'gain': lambda i, mode: -(i.get('day_fans') or 0),
        'total': lambda i, mode: -i['history'].cumulative_fans,
        'name': lambda i, mode: i['member'].trainer_name.lower(),
    }
    _BEHIND_ORDER = {
        'deficit': lambda i, mode: i['history'].deficit_surplus,
        'deficit_asc': lambda i, mode: -i['history'].deficit_surplus,
        'days': lambda i, mode: (-((i.get('quota_days') or 0) if mode == 'quota'
                                   else i.get('streak', i['history'].days_behind)),
                                 i['history'].deficit_surplus),
        'gain': lambda i, mode: -(i.get('day_fans') or 0),
        'name': lambda i, mode: i['member'].trainer_name.lower(),
    }

    @staticmethod
    def _title(template: str, club_name: str, count: int) -> Optional[str]:
        return embed_style.render(template, {'club': club_name, 'count': str(count)}) or None

    @staticmethod
    def _continued(title: Optional[str], idx: int) -> Optional[str]:
        if title is None or idx == 0:
            return title
        return f"{title} (continued {idx + 1})"

    def _member_values(self, item: Dict, pos: int, decimals: int,
                       counting: BehindCount) -> Dict[str, str]:
        """Every member token's text for one report line."""
        member = item['member']
        history = item['history']
        surplus = history.deficit_surplus
        day_fans = item.get('day_fans')
        expected = getattr(history, 'expected_fans', None)
        streak = item.get('streak', history.days_behind)
        short = item.get('quota_days')
        if short is None:
            short = quota_days(surplus, counting.per_day_quota)
        info = item.get('period_info')
        period_fans = item.get('period_fans')
        period_quota = info['period_quota'] if info else None

        return {
            'name': member.trainer_name,
            'pos': str(pos),
            'gain': "joined today" if day_fans is None else embed_style.signed(day_fans, decimals),
            'gain_full': "joined today" if day_fans is None else embed_style.signed_full(day_fans),
            'balance': embed_style.signed(surplus, decimals),
            'balance_full': embed_style.signed_full(surplus),
            'total': embed_style.compact(history.cumulative_fans, decimals),
            'total_full': embed_style.full(history.cumulative_fans),
            'expected': embed_style.compact(expected, decimals),
            'expected_full': embed_style.full(expected),
            'progress': embed_style.percent(history.cumulative_fans, expected),
            'days_behind': embed_style.days(short if counting.mode == 'quota' else streak),
            'streak': embed_style.days(streak),
            'quota_days': embed_style.days(short),
            'period_fans': embed_style.compact(period_fans, decimals),
            'period_quota': embed_style.compact(period_quota, decimals),
            'period_left': (embed_style.compact(max(0, period_quota - period_fans), decimals)
                            if info and period_fans is not None else embed_style.PLACEHOLDER),
            'period': info['quota_label'] if info else embed_style.PLACEHOLDER,
        }

    def _format_member_line(self, item: Dict, is_behind: bool, quota_period: str = 'daily',
                            daily_gain: bool = False) -> str:
        """One member's line with the default style."""
        style = embed_style.defaults(embed_style.REPORT, quota_period, daily_gain)
        template = style['line_behind' if is_behind else 'line_on_track']
        return embed_style.render(template, self._member_values(item, 1, style['decimals'], BehindCount()))

    def _split_lines(self, lines: List[str], max_length: int = 1000) -> List[str]:
        return self._split_into_sections(lines, lambda line: line, max_length)

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

    def _format_bombs_section(self, bombs_data: List[Dict], style: Optional[Dict] = None,
                              counting: Optional[BehindCount] = None) -> str:
        """Format the active bombs section"""
        style = style or embed_style.defaults(embed_style.REPORT)
        counting = counting or BehindCount()
        decimals = style['decimals']
        lines = []

        for item in bombs_data:
            member = item['member']
            bomb = item['bomb']
            history = item['history']
            days_remaining = bomb.days_remaining

            if days_remaining <= 2:
                emoji = "🔴"
            elif days_remaining <= 4:
                emoji = "🟠"
            else:
                emoji = "🟡"

            # A bomb's member is behind by definition, so the deficit reads as
            # negative even on a row that has already crept back to zero.
            deficit = -abs(history.deficit_surplus) if history else None
            lines.append(embed_style.render(style['line_bomb'], {
                'marker': emoji,
                'name': member.trainer_name,
                'days_left': embed_style.days(days_remaining),
                'balance': embed_style.signed(deficit, decimals) if deficit else "-0",
                'balance_full': embed_style.signed_full(deficit) if deficit else "-0",
                'days_behind': embed_style.days(counting.of(history)) if history else embed_style.PLACEHOLDER,
            }))

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

        # Promotion progress toward the next milestone (best-effort, added by tasks.py)
        promotion = rank_data.get('promotion')
        if promotion and promotion.get('fans_needed'):
            target = promotion['target_rank']
            need = self.format_fans_short(promotion['fans_needed'])
            line = f"Next milestone: Top {target} — +{need} fans"
            extra = promotion.get('extra_per_day')
            if extra:
                line += f" (~+{self.format_fans_short(extra)}/day on top of current pace)"
            lines.append(line)

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

    def create_bomb_activation_alert(self, club_name: str, newly_activated: List[Dict],
                                     reason: str = "have been behind quota for 3+ consecutive days"
                                     ) -> List[discord.Embed]:
        """
        Create alert embeds for newly activated bombs.

        ``reason`` finishes "The following members ...", see
        :meth:`BehindCount.trigger_reason`.

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
                f"The following members {reason}:\n\n{section}"
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