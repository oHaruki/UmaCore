"""Previews of a club's report and live board, for the dashboard's style editor.

Rendered by the same code that posts the real ones, from the club's own newest
figures where it has any and from a made-up roster where it doesn't. Reads
only: nothing here writes history, sends a message or calls Discord.
"""
import logging
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from config.database import db
from models import QuotaRequirement
from scrapers.umamoe_api_scraper import LiveSnapshot, MemberGain, UmaMoeAPIScraper
from services import embed_style, live_board
from services.bomb_manager import BombManager
from services.quota_calculator import PERIOD_DAYS, QuotaCalculator, quota_days
from services.report_generator import ReportGenerator
from utils.rate_limiter import PRIORITY_INTERACTIVE

logger = logging.getLogger(__name__)

# Typing in the editor re-renders on every pause, so the club's figures are
# read once and reused for a minute rather than re-queried per keystroke.
CACHE_SECONDS = 60
# A live fetch costs an uma.moe call, so it's kept for longer.
LIVE_CACHE_SECONDS = 300

_cache: Dict[Tuple, Tuple[float, Any]] = {}

# The rank fields come from uma.moe at report time and aren't stored, so the
# preview shows these instead.
EXAMPLE_RANKS = {'monthly_rank': 87, 'yesterday_rank': 91, 'last_month_rank': 104}

SAMPLE_DAILY_QUOTA = 8_000_000

# name, share of the expected fans, days in a row behind, gained that day
_SAMPLE_MEMBERS = (
    ("Special Week", 1.26, 0, 9_800_000),
    ("Silence Suzuka", 1.13, 0, 7_200_000),
    ("Tokai Teio", 1.02, 0, 8_100_000),
    ("Gold Ship", 1.00, 0, 0),
    ("Mejiro McQueen", 0.97, 1, 3_300_000),
    ("Oguri Cap", 0.89, 4, 6_000_000),
    ("Rice Shower", 0.72, 9, 3_400_000),
    ("Haru Urara", 0.59, 14, 2_100_000),
)

MODES = [
    {'value': 'streak', 'label': "Days in a row under quota",
     'help': "Counts every day someone stays under the full quota, until they've caught up "
             "completely. 45M behind since the 6th reads 14 days on the 20th."},
    {'value': 'quota', 'label': "Days of quota missing",
     'help': "The deficit divided by the daily quota, rounded up. 45M behind at 8M a "
             "day reads 6 days, and drops as soon as they earn more than the quota."},
]


def _cached(key: Tuple, ttl: int):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    return None


def _store(key: Tuple, value: Any) -> Any:
    _cache[key] = (time.monotonic(), value)
    return value


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #

def _sample_report(club: Any, report_date: date) -> Tuple[Dict, List[Dict], int]:
    """A made-up roster, for a club with no history yet."""
    period = getattr(club, 'quota_period', 'daily') or 'daily'
    per_day = SAMPLE_DAILY_QUOTA
    expected = per_day * report_date.day
    period_info = QuotaCalculator.get_period_info(
        period, report_date, getattr(club, 'period_anchor_date', None))
    if period_info:
        period_info['period_quota'] = per_day * period_info['period_days']

    on_track, behind = [], []
    for name, share, streak, gained in _SAMPLE_MEMBERS:
        cumulative = round(expected * share)
        surplus = cumulative - expected
        history = SimpleNamespace(
            date=report_date, cumulative_fans=cumulative, expected_fans=expected,
            deficit_surplus=surplus, days_behind=streak,
        )
        item = {
            'member': SimpleNamespace(trainer_name=name, member_id=name),
            'history': history,
            'day_fans': gained,
            'streak': streak,
            'quota_days': quota_days(surplus, per_day),
        }
        if period_info:
            item['period_info'] = period_info
            item['period_fans'] = min(cumulative, round(period_info['period_quota'] * share
                                                        * period_info['day_number']
                                                        / period_info['period_days']))
        (on_track if surplus >= 0 else behind).append(item)

    on_track.sort(key=lambda i: i['history'].deficit_surplus, reverse=True)
    behind.sort(key=lambda i: i['history'].deficit_surplus)
    summary = {'on_track': on_track, 'behind': behind,
               'total_members': len(_SAMPLE_MEMBERS), 'period_info': period_info}

    bombs = []
    if getattr(club, 'bombs_enabled', True):
        for item, left in zip(behind[:2], (2, 5)):
            bombs.append({'member': item['member'], 'history': item['history'],
                          'bomb': SimpleNamespace(days_remaining=left)})
    return summary, bombs, per_day * PERIOD_DAYS.get(period, 1)


async def _report_data(club: Any) -> Dict[str, Any]:
    latest = await db.fetchval(
        "SELECT MAX(date) FROM quota_history WHERE club_id = $1", club.club_id)

    if latest is not None:
        key = ('report', club.club_id, latest, club.quota_period, club.period_anchor_date,
               club.bombs_enabled)
        hit = _cached(key, CACHE_SECONDS)
        if hit is not None:
            return hit

        summary = await QuotaCalculator().get_member_status_summary(
            club.club_id, latest, quota_period=club.quota_period,
            period_anchor=club.period_anchor_date,
        )
        if summary['on_track'] or summary['behind']:
            bombs = (await BombManager().get_active_bombs_with_members(club.club_id)
                     if club.bombs_enabled else [])
            quota = await QuotaRequirement.get_quota_for_date(club.club_id, latest)
            return _store(key, {'summary': summary, 'bombs': bombs, 'quota': quota,
                                'date': latest, 'source': 'history'})

    report_date = date.today() - timedelta(days=1)
    summary, bombs, quota = _sample_report(club, report_date)
    return {'summary': summary, 'bombs': bombs, 'quota': quota,
            'date': report_date, 'source': 'sample'}


async def report_preview(club: Any, override: Optional[Dict], days_mode: str) -> Dict[str, Any]:
    style = embed_style.resolve(embed_style.REPORT, club, override)
    data = await _report_data(club)
    generator = ReportGenerator()
    embeds = generator.create_daily_report(
        club.club_name, data['quota'], data['summary'], data['bombs'], data['date'],
        rank_data=EXAMPLE_RANKS, quota_period=club.quota_period,
        club_timezone=club.timezone, style=style, days_mode=days_mode,
    )
    content = generator.daily_report_message(
        club.club_name, data['quota'], data['summary'], data['bombs'], data['date'],
        rank_data=EXAMPLE_RANKS, quota_period=club.quota_period, style=style,
    )

    notes = []
    if data['source'] == 'sample':
        notes.append("No report data for this club yet, so the preview uses a made-up roster.")
    else:
        notes.append(f"Using this club's report for {data['date']:%B %d}.")
    if style['show_rankings'] or embed_style.uses(style, 'rank'):
        notes.append("Club rankings show example numbers here; the real report uses uma.moe's.")
    return {'embeds': [e.to_dict() for e in embeds], 'content': content,
            'source': data['source'], 'separate_messages': True, 'notes': notes}


# --------------------------------------------------------------------------- #
# live board
# --------------------------------------------------------------------------- #

def _sample_snapshot(today: date) -> LiveSnapshot:
    gains = [
        MemberGain("1", "Special Week", 9_800_000, 212_400_000),
        MemberGain("2", "Silence Suzuka", 7_200_000, 190_150_000),
        MemberGain("3", "Tokai Teio", 5_100_000, 171_900_000, joined_slot=4),
        MemberGain("4", "Mejiro McQueen", 3_300_000, 163_700_000),
        MemberGain("5", "Gold Ship", 0, 168_000_000),
        MemberGain("6", "Rice Shower", 0, 121_000_000),
        MemberGain("7", "Haru Urara", 0, 0, joined_slot=today.day, is_new=True),
    ]
    return LiveSnapshot(
        circle_id="0", jst_day=today,
        as_of=datetime.now(timezone.utc).replace(minute=5, second=0, microsecond=0),
        live_points=1_241_500_000, live_rank=84,
        monthly_point=1_216_100_000, monthly_rank=87, gains=gains,
    )


async def _live_snapshot(club: Any) -> Tuple[LiveSnapshot, str]:
    snap = live_board.last_snapshot(club.club_id)
    if snap is not None:
        return snap, 'live'

    key = ('live', club.club_id)
    hit = _cached(key, LIVE_CACHE_SECONDS)
    if hit is not None:
        return hit, 'live'

    if club.is_circle_id_valid():
        try:
            snap = await UmaMoeAPIScraper(club.circle_id, priority=PRIORITY_INTERACTIVE).fetch_live()
        except Exception as e:
            logger.warning(f"Live preview fetch failed for {club.club_name}: {e}")
            snap = None
        if snap is not None:
            return _store(key, snap), 'live'

    return _sample_snapshot(datetime.now(timezone.utc).date()), 'sample'


async def live_preview(club: Any, override: Optional[Dict], closed: bool = False) -> Dict[str, Any]:
    style = embed_style.resolve(embed_style.LIVE, club, override)
    snap, source = await _live_snapshot(club)
    quota = await live_board.daily_quota(club, snap.jst_day, style=style)
    embeds = live_board.build_embeds(club, snap, closed=closed, style=style, quota=quota)
    content = live_board.build_message(club, snap, style=style, quota=quota)

    notes = []
    if source == 'sample':
        notes.append("No live figures for this club right now, so the preview uses a made-up roster.")
    elif snap.as_of:
        notes.append(f"Using uma.moe's figures from {snap.as_of:%H:%M} UTC.")
    if live_board.needs_quota(style) and quota is None:
        notes.append("This club has no daily quota set, so the quota figures show a dash.")
    return {'embeds': [e.to_dict() for e in embeds], 'content': content, 'source': source,
            'separate_messages': False, 'notes': notes}


# --------------------------------------------------------------------------- #
# saving
# --------------------------------------------------------------------------- #

def describe(club: Any) -> Dict[str, Any]:
    """What the editor needs to start: the vocabulary and the club's current style."""
    period = getattr(club, 'quota_period', 'daily') or 'daily'
    return {
        'kinds': {kind: embed_style.schema(kind, period) for kind in embed_style.KINDS},
        'style': {kind: embed_style.resolve(kind, club) for kind in embed_style.KINDS},
        'days_behind_mode': getattr(club, 'days_behind_mode', 'streak') or 'streak',
        'modes': MODES,
        'quota_period': period,
        'bombs_enabled': getattr(club, 'bombs_enabled', False),
        'bomb_trigger_days': getattr(club, 'bomb_trigger_days', None),
    }


def to_store(club: Any, styles: Any) -> Tuple[Dict[str, Dict], Dict[str, Dict[str, str]]]:
    """Turn the editor's full styles into what gets stored, plus any errors.

    Only the changes from the defaults are kept, so a later change to a default
    still reaches every club that never touched that field.
    """
    period = getattr(club, 'quota_period', 'daily') or 'daily'
    stored: Dict[str, Dict] = {}
    errors: Dict[str, Dict[str, str]] = {}
    styles = styles if isinstance(styles, dict) else {}
    for kind in embed_style.KINDS:
        clean, problems = embed_style.validate(kind, styles.get(kind))
        if problems:
            errors[kind] = problems
        changed = embed_style.changes(kind, clean, period)
        if changed:
            stored[kind] = changed
    return stored, errors
