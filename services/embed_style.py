"""How a club's daily report and live board look.

A club stores only what it changed (``clubs.embed_style``, one dict per kind).
Everything else comes from the defaults here, which reproduce the embeds as they
were before any of this was configurable.

The bot owns this vocabulary. The dashboard reads the schema and renders its
previews through the bot API, so tokens and defaults can't drift between the two.
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import discord

from config.settings import COLOR_BEHIND, COLOR_BOMB, COLOR_INFO, COLOR_ON_TRACK

REPORT = 'report'
LIVE = 'live'
KINDS = (REPORT, LIVE)

PLACEHOLDER = "—"

# What a club's own message may ping. Roles and people, never @everyone or
# @here, since club editors aren't necessarily server admins.
MENTIONS = discord.AllowedMentions(everyone=False, roles=True, users=True)

# A line repeats once per member and ~30 of them share one live board message,
# which Discord caps at 6000 characters, so lines stay short.
LINE_MAX = 120
# Discord allows 256 for a title; this leaves room for "(continued 2)".
TITLE_MAX = 100
FOOTER_MAX = 200
# Free text a club adds: the message above the embeds, the header note and
# extra fields. Discord's own limits (2000 / 4096 / 1024) leave room for tokens.
TEXT_MAX = 600
URL_MAX = 500
# Extra fields and lists one embed kind can carry.
MAX_BLOCKS = 8

# {token}, or {token:>2} with an alignment and width for columns in code spans.
TOKEN_RE = re.compile(r"\{([a-z_]+)(?::([<>^]?)(\d{1,2}))?\}")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_URL_RE = re.compile(r"^https://[^\s<>\"']+$")


# --------------------------------------------------------------------------- #
# tokens
# --------------------------------------------------------------------------- #

TITLE_TOKENS = {
    'club': "Club name",
    'count': "How many members the section lists",
}

FOOTER_TOKENS = {
    'club': "Club name",
}

MEMBER_TOKENS = {
    'name': "Trainer name",
    'pos': "Position in the list: 1, 2, 3…",
    'gain': "Fans gained that day, e.g. +3.4M",
    'gain_full': "Fans gained that day, with separators",
    'balance': "Surplus or deficit for the month, e.g. +12.3M or -47.0M",
    'balance_full': "Surplus or deficit, with separators",
    'total': "Fans this month, e.g. 330.7M",
    'total_full': "Fans this month, with separators",
    'expected': "Fans the quota expects by now",
    'expected_full': "Fans the quota expects by now, with separators",
    'progress': "Fans this month as a share of what's expected, e.g. 94%",
    'days_behind': "Days behind, counted the way the club chose",
    'streak': "Days in a row under quota",
    'quota_days': "Days of quota missing: deficit ÷ daily quota",
    'period_fans': "Fans this week or biweek (weekly and biweekly clubs)",
    'period_quota': "Quota for the whole week or biweek",
    'period_left': "Fans still needed this week or biweek",
    'period': "\"week\" or \"biweek\"",
}

BOMB_TOKENS = {
    'marker': "🔴 2 days left or less, 🟠 4 or less, 🟡 otherwise",
    'name': "Trainer name",
    'days_left': "Days left on the bomb, e.g. 3 days",
    'balance': "Deficit for the month, e.g. -47.0M",
    'balance_full': "Deficit, with separators",
    'days_behind': "Days behind, counted the way the club chose",
}

LIVE_TITLE_TOKENS = TITLE_TOKENS

LIVE_FOOTER_TOKENS = {
    'club': "Club name",
    'as_of': "When uma.moe last updated, e.g. 14:05 UTC",
}

LIVE_MEMBER_TOKENS = {
    'name': "Trainer name",
    'pos': "Position in the list: 1, 2, 3…",
    'gain': "Fans gained so far today, e.g. +5.00M",
    'gain_full': "Fans gained so far today, with separators",
    'total': "Fans this month, e.g. 60.00M",
    'total_full': "Fans this month, with separators",
    'span': "What the total covers: \"total\", \"this month\", or \"since Oct 3\" for a mid-month joiner",
    'quota': "The club's quota for one day",
    'quota_left': "Fans still needed today to reach the daily quota",
    'quota_pct': "Today's fans as a share of the daily quota, e.g. 85%",
}

LIVE_JOINED_TOKENS = {
    'name': "Trainer name",
}

# For the club's own text: the message above the report, its note and extra fields.
SUMMARY_TOKENS = {
    'club': "Club name",
    'date': "The day the report covers, e.g. October 21",
    'quota': "The club's quota, e.g. 8.0M",
    'period': "\"day\", \"week\" or \"2 weeks\"",
    'members': "How many members the report covers",
    'on_track': "How many are on track",
    'behind': "How many are behind",
    'bombs': "How many bombs are active",
    'fans_today': "Fans the whole club gained that day",
    'club_total': "Fans the whole club has this month",
    'avg_gain': "Average gain per member that day",
    'top_name': "Who gained the most that day",
    'top_gain': "How much they gained",
    'rank': "Club rank on uma.moe, e.g. #87",
}

LIVE_SUMMARY_TOKENS = {
    'club': "Club name",
    'date': "The competition day, e.g. October 21",
    'fans_today': "Fans the club has gained so far today",
    'month_total': "The club's fans this month",
    'rank': "Live rank, e.g. #84",
    'members': "How many members are listed",
    'raced': "How many have raced today",
    'idle': "How many have no fans yet today",
    'joined': "How many joined today",
    'quota': "The club's quota for one day",
    'top_name': "Who has gained the most so far today",
    'top_gain': "How much they gained",
    'as_of': "When uma.moe last updated, e.g. 14:05 UTC",
}


# --------------------------------------------------------------------------- #
# fields
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Field:
    key: str
    kind: str                       # template | choice | bool | color | url | blocks
    label: str
    default: Any = None
    tokens: Optional[Dict[str, str]] = None
    choices: Tuple[Tuple[Any, str], ...] = ()
    max_length: int = 0
    required: bool = False          # a template that may not be empty
    multiline: bool = False

    def describe(self) -> Dict[str, Any]:
        return {
            'key': self.key, 'kind': self.kind, 'label': self.label, 'default': self.default,
            'tokens': self.tokens, 'max_length': self.max_length or None,
            'required': self.required, 'multiline': self.multiline,
            'choices': [{'value': v, 'label': l} for v, l in self.choices] or None,
        }


def _hex(color: int) -> str:
    return f"#{color:06x}"


def _title(key: str, label: str, default: str, tokens=TITLE_TOKENS) -> Field:
    return Field(key, 'template', label, default, tokens, max_length=TITLE_MAX)


def _line(key: str, label: str, default: Optional[str], tokens: Dict[str, str]) -> Field:
    return Field(key, 'template', label, default, tokens, max_length=LINE_MAX, required=True)


def _text(key: str, label: str, tokens: Dict[str, str]) -> Field:
    return Field(key, 'template', label, "", tokens, max_length=TEXT_MAX, multiline=True)


def _extras(tokens: Dict[str, str]) -> Tuple[Field, ...]:
    """What every kind lets a club add on top of the built-in sections."""
    return (
        _text('message', "Message above the embed", tokens),
        _text('note', "Note under the header", tokens),
        Field('thumbnail', 'url', "Thumbnail link", "", max_length=URL_MAX),
        Field('image', 'url', "Banner image link", "", max_length=URL_MAX),
        Field('extras', 'blocks', "Extra fields and lists", []),
    )


# ---- extra blocks ----------------------------------------------------------
# A club adds any number (up to MAX_BLOCKS) of these: a field on the header
# embed, or a member list of its own after the built-in ones.

LIMITS = ((0, "Everyone"), (1, "1"), (3, "3"), (5, "5"), (10, "10"), (20, "20"))


def _field_block(tokens: Dict[str, str]) -> Tuple[Field, ...]:
    return (
        Field('title', 'template', "Name", "", tokens, max_length=TITLE_MAX, required=True),
        Field('text', 'template', "Text", "", tokens, max_length=TEXT_MAX, required=True,
              multiline=True),
        Field('inline', 'bool', "Side by side", False),
    )


def _list_block(who, order, line_tokens, line_default: str, colour: int) -> Tuple[Field, ...]:
    return (
        Field('title', 'template', "Title", "", TITLE_TOKENS, max_length=TITLE_MAX),
        Field('who', 'choice', "Who", who[0][0], choices=who),
        Field('order', 'choice', "Order", order[0][0], choices=order),
        Field('limit', 'choice', "How many", 0, choices=LIMITS),
        Field('line', 'template', "Line", line_default, line_tokens,
              max_length=LINE_MAX, required=True),
        Field('color', 'color', "Colour", _hex(colour)),
    )


DECIMALS = Field('decimals', 'choice', "Decimals", 1,
                 choices=((0, "47M"), (1, "47.0M"), (2, "47.03M")))

REPORT_FIELDS: Tuple[Field, ...] = (
    _title('title', "Report title", "📊 Daily Quota Report - {club}"),
    _title('title_on_track', "On-track title", "✅ On Track"),
    _title('title_behind', "Behind title", "⚠️ Behind Quota"),
    _title('title_bombs', "Bombs title", "💣 Active Bombs"),
    # Line defaults depend on the quota period, see _report_lines.
    _line('line_on_track', "On-track line", None, MEMBER_TOKENS),
    _line('line_behind', "Behind line", None, MEMBER_TOKENS),
    _line('line_bomb', "Bomb line",
          "{marker} **{name}**: {days_left} remaining ({balance} behind)", BOMB_TOKENS),
    Field('footer', 'template', "Footer", "Umamusume Quota Tracker - {club}",
          FOOTER_TOKENS, max_length=FOOTER_MAX),
    DECIMALS,
    Field('sort_on_track', 'choice', "On-track order", 'surplus', choices=(
        ('surplus', "Most ahead first"),
        ('gain', "Most gained that day"),
        ('total', "Most fans this month"),
        ('name', "Name A–Z"),
    )),
    Field('sort_behind', 'choice', "Behind order", 'deficit', choices=(
        ('deficit', "Most behind first"),
        ('deficit_asc', "Least behind first"),
        ('days', "Most days behind first"),
        ('gain', "Most gained that day"),
        ('name', "Name A–Z"),
    )),
    Field('show_summary', 'bool', "Summary counts", True),
    Field('show_rankings', 'bool', "Club rankings", True),
    Field('show_on_track', 'bool', "On-track list", True),
    Field('show_behind', 'bool', "Behind list", True),
    Field('show_bombs', 'bool', "Bomb list", True),
    Field('color_report', 'color', "Report colour", _hex(COLOR_INFO)),
    Field('color_on_track', 'color', "On-track colour", _hex(COLOR_ON_TRACK)),
    Field('color_behind', 'color', "Behind colour", _hex(COLOR_BEHIND)),
    Field('color_bombs', 'color', "Bombs colour", _hex(COLOR_BOMB)),
    *_extras(SUMMARY_TOKENS),
)

LIVE_FIELDS: Tuple[Field, ...] = (
    _title('title', "Board title", "📊 Live Board - {club}", LIVE_TITLE_TOKENS),
    _title('title_raced', "Raced title", "✅ Raced Today", LIVE_TITLE_TOKENS),
    _title('title_idle', "Not-yet title", "⚠️ No Fans Yet Today", LIVE_TITLE_TOKENS),
    _title('title_joined', "Joined title", "🆕 Joined Today", LIVE_TITLE_TOKENS),
    _line('line_raced', "Raced line", "`{pos:>2}` **{name}**: {gain} ({total} {span})",
          LIVE_MEMBER_TOKENS),
    _line('line_idle', "Not-yet line", "**{name}**: {total} {span}", LIVE_MEMBER_TOKENS),
    _line('line_joined', "Joined line", "**{name}**", LIVE_JOINED_TOKENS),
    Field('footer', 'template', "Footer",
          "Umamusume Quota Tracker - {club} · uma.moe as of {as_of}",
          LIVE_FOOTER_TOKENS, max_length=FOOTER_MAX),
    Field('decimals', 'choice', "Decimals", 2, choices=DECIMALS.choices),
    Field('sort_raced', 'choice', "Raced order", 'gain', choices=(
        ('gain', "Most gained today"),
        ('total', "Most fans this month"),
        ('name', "Name A–Z"),
    )),
    Field('sort_idle', 'choice', "Not-yet order", 'total', choices=(
        ('total', "Most fans this month"),
        ('name', "Name A–Z"),
    )),
    Field('show_summary', 'bool', "Summary counts", True),
    Field('show_rank', 'bool', "Rank", True),
    Field('show_month_total', 'bool', "Month total", True),
    Field('show_idle', 'bool', "Not-yet list", True),
    Field('show_joined', 'bool', "Joined list", True),
    Field('color_open', 'color', "Colour while live", _hex(COLOR_INFO)),
    Field('color_closed', 'color', "Colour once closed", _hex(COLOR_ON_TRACK)),
    Field('color_raced', 'color', "Raced colour", _hex(COLOR_ON_TRACK)),
    Field('color_idle', 'color', "Not-yet colour", _hex(COLOR_BEHIND)),
    Field('color_joined', 'color', "Joined colour", _hex(COLOR_INFO)),
    *_extras(LIVE_SUMMARY_TOKENS),
)

FIELDS: Dict[str, Tuple[Field, ...]] = {REPORT: REPORT_FIELDS, LIVE: LIVE_FIELDS}

REPORT_WHO = (
    ('all', "Everyone"),
    ('on_track', "On track"),
    ('behind', "Behind"),
    ('close', "Within a day of quota"),
    ('gained', "Gained fans"),
    ('no_gain', "No fans that day"),
    ('joined', "Joined this month"),
)
REPORT_ORDER = (
    ('gain', "Most gained"),
    ('total', "Most fans"),
    ('ahead', "Most ahead first"),
    ('behind', "Most behind first"),
    ('days', "Most days behind first"),
    ('name', "Name A–Z"),
)
LIVE_WHO = (
    ('raced', "Raced today"),
    ('idle', "No fans yet today"),
    ('all', "Everyone"),
    ('under_quota', "Under today's quota"),
    ('met_quota', "Met today's quota"),
)
LIVE_ORDER = (
    ('gain', "Most gained"),
    ('total', "Most fans"),
    ('name', "Name A–Z"),
)

BLOCK_LABELS = {'field': "Field", 'list': "Member list"}

BLOCKS: Dict[str, Dict[str, Tuple[Field, ...]]] = {
    REPORT: {
        'field': _field_block(SUMMARY_TOKENS),
        'list': _list_block(REPORT_WHO, REPORT_ORDER, MEMBER_TOKENS,
                            "**{name}**: {balance}", COLOR_INFO),
    },
    LIVE: {
        'field': _field_block(LIVE_SUMMARY_TOKENS),
        'list': _list_block(LIVE_WHO, LIVE_ORDER, LIVE_MEMBER_TOKENS,
                            "**{name}**: {gain}", COLOR_INFO),
    },
}

# Filters that only work when the club's daily quota is known.
QUOTA_FILTERS = ('under_quota', 'met_quota')


# --------------------------------------------------------------------------- #
# defaults and presets
# --------------------------------------------------------------------------- #

_DAILY_LINES = {
    False: {'line_on_track': "**{name}**: {balance} ({total_full} total)",
            'line_behind': "**{name}**: {balance} ({days_behind} behind)"},
    True: {'line_on_track': "**{name}**: {gain} ({total_full} total)",
           'line_behind': "**{name}**: {gain} ({balance}, {days_behind} behind)"},
}

_PERIOD_LINES = {
    False: "**{name}**: {period_fans}/{period_quota} this {period} ({balance} overall)",
    True: "**{name}**: {period_fans}/{period_quota} this {period} ({gain} today)",
}


def _report_lines(quota_period: str, daily_gain: bool) -> Dict[str, str]:
    if quota_period != 'daily':
        line = _PERIOD_LINES[bool(daily_gain)]
        return {'line_on_track': line, 'line_behind': line}
    return dict(_DAILY_LINES[bool(daily_gain)])


def defaults(kind: str, quota_period: str = 'daily', daily_gain: bool = False) -> Dict[str, Any]:
    """Every field's default. ``daily_gain`` is the older on/off switch for lines."""
    out = {f.key: f.default for f in FIELDS[kind]}
    if kind == REPORT:
        out.update(_report_lines(quota_period, daily_gain))
    return out


def presets(kind: str, quota_period: str = 'daily') -> List[Dict[str, Any]]:
    """Ready-made line sets, so nobody has to start from a blank template."""
    if kind == LIVE:
        return [
            {'label': "Default", 'values': {
                'line_raced': defaults(LIVE)['line_raced'],
                'line_idle': defaults(LIVE)['line_idle']}},
            {'label': "Daily quota", 'values': {
                'line_raced': "`{pos:>2}` **{name}**: {gain} ({quota_pct} of today's quota)",
                'line_idle': "**{name}**: {quota_left} to go today"}},
            {'label': "Plain", 'values': {
                'line_raced': "{pos}. {name}: {gain}",
                'line_idle': "{name}"}},
        ]

    if quota_period != 'daily':
        return [
            {'label': "Overall balance", 'values': _report_lines(quota_period, False)},
            {'label': "Daily gain", 'values': _report_lines(quota_period, True)},
            {'label': "Left this period", 'values': {
                'line_on_track': "**{name}**: {period_fans}/{period_quota} this {period} ({balance} overall)",
                'line_behind': "**{name}**: {period_left} left this {period} ({balance} overall)"}},
        ]
    return [
        {'label': "Surplus", 'values': _report_lines('daily', False)},
        {'label': "Daily gain", 'values': _report_lines('daily', True)},
        {'label': "Progress", 'values': {
            'line_on_track': "**{name}**: {progress} of quota ({total} fans)",
            'line_behind': "**{name}**: {progress} of quota ({balance}, {days_behind} behind)"}},
        {'label': "Numbered", 'values': {
            'line_on_track': "`{pos:>2}` **{name}**: {balance} ({total_full} total)",
            'line_behind': "`{pos:>2}` **{name}**: {balance} ({days_behind} behind)"}},
        {'label': "Compact", 'values': {
            'line_on_track': "{name} {balance}",
            'line_behind': "{name} {balance} · {days_behind}"}},
    ]


def block_presets(kind: str) -> List[Dict[str, Any]]:
    """Starting points for the "add" menu: a filled-in block per common ask."""
    def block(type_: str, **values) -> Dict[str, Any]:
        out = {f.key: f.default for f in BLOCKS[kind][type_]}
        out.update(values, type=type_)
        return out

    if kind == LIVE:
        return [
            {'label': "Reminder", 'block': block(
                'field', title="Reminder", text="Race before the day closes at 15:00 UTC!")},
            {'label': "Top 3 today", 'block': block(
                'list', title="Top 3 Today", who='raced', order='gain', limit=3,
                line="{pos}. **{name}**: {gain}", color=_hex(COLOR_ON_TRACK))},
            {'label': "Under today's quota", 'block': block(
                'list', title="Still Under Today's Quota ({count})", who='under_quota',
                order='gain', line="**{name}**: {quota_left} to go", color=_hex(COLOR_BEHIND))},
            {'label': "Met today's quota", 'block': block(
                'list', title="Met Today's Quota ({count})", who='met_quota', order='gain',
                line="**{name}**: {gain}", color=_hex(COLOR_ON_TRACK))},
        ]
    return [
        {'label': "Reminder", 'block': block(
            'field', title="Reminder", text="Do your daily races before reset!")},
        {'label': "Club stats", 'block': block(
            'field', title="Today",
            text="**{fans_today}** fans gained, **{avg_gain}** on average\n"
                 "Top gainer: **{top_name}** ({top_gain})")},
        {'label': "Top gainers", 'block': block(
            'list', title="Top Gainers Today", who='gained', order='gain', limit=3,
            line="{pos}. **{name}**: {gain}", color=_hex(COLOR_ON_TRACK))},
        {'label': "Almost there", 'block': block(
            'list', title="Almost There ({count})", who='close', order='ahead',
            line="**{name}**: {balance}", color=_hex(COLOR_INFO))},
        {'label': "No fans that day", 'block': block(
            'list', title="No Fans That Day ({count})", who='no_gain', order='name',
            line="**{name}**", color=_hex(COLOR_BEHIND))},
        {'label': "Everyone in one list", 'block': block(
            'list', title="All Members", who='all', order='ahead',
            line="`{pos:>2}` **{name}**: {balance} ({days_behind} behind)", color=_hex(COLOR_INFO))},
    ]


def schema(kind: str, quota_period: str = 'daily') -> Dict[str, Any]:
    """Everything the dashboard needs to build its editor for one kind."""
    return {
        'fields': [f.describe() for f in FIELDS[kind]],
        'defaults': defaults(kind, quota_period),
        'presets': presets(kind, quota_period),
        'blocks': {
            type_: {'label': BLOCK_LABELS[type_], 'fields': [f.describe() for f in spec]}
            for type_, spec in BLOCKS[kind].items()
        },
        'block_presets': block_presets(kind),
        'max_blocks': MAX_BLOCKS,
    }


# --------------------------------------------------------------------------- #
# validation and resolution
# --------------------------------------------------------------------------- #

def unknown_tokens(template: str, tokens: Dict[str, str]) -> List[str]:
    return sorted({m.group(1) for m in TOKEN_RE.finditer(template) if m.group(1) not in tokens})


def _problem(f: Field, value: Any) -> Optional[str]:
    """Why ``value`` can't be stored for ``f``, or None if it can."""
    if f.kind == 'template':
        if not isinstance(value, str):
            return "Must be text"
        text = value.strip()
        if f.required and not text:
            return "Can't be empty"
        if len(text) > f.max_length:
            return f"Longer than {f.max_length} characters"
        bad = unknown_tokens(text, f.tokens or {})
        if bad:
            return "Unknown: " + ", ".join(f"{{{t}}}" for t in bad)
        return None
    if f.kind == 'choice':
        # type() rather than isinstance, so True doesn't pass for 1
        if not any(type(value) is type(v) and value == v for v, _ in f.choices):
            return "Not one of the options"
        return None
    if f.kind == 'bool':
        return None if isinstance(value, bool) else "Must be on or off"
    if f.kind == 'color':
        return None if isinstance(value, str) and _COLOR_RE.match(value) else "Must be a colour like #3498db"
    if f.kind == 'url':
        if not isinstance(value, str):
            return "Must be a link"
        link = value.strip()
        if len(link) > f.max_length:
            return f"Longer than {f.max_length} characters"
        if link and not _URL_RE.match(link):
            return "Must be an https:// link"
        return None
    return "Unknown field"


def _normalise(f: Field, value: Any) -> Any:
    if f.kind in ('template', 'url'):
        return value.strip()
    if f.kind == 'color':
        return value.lower()
    return value


def _validate_blocks(kind: str, value: Any) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Keep the blocks that are fully valid; name each problem as extras.N.field.

    A block with a problem is left out rather than half-rendered, so the
    preview shows everything else while the dashboard points at the fix.
    """
    if not isinstance(value, list):
        return [], {'extras': "Must be a list"}
    errors: Dict[str, str] = {}
    if len(value) > MAX_BLOCKS:
        errors['extras'] = f"At most {MAX_BLOCKS}"
        value = value[:MAX_BLOCKS]

    kept = []
    for i, raw in enumerate(value):
        spec = BLOCKS[kind].get(raw.get('type')) if isinstance(raw, dict) else None
        if spec is None:
            errors[f'extras.{i}'] = "Unknown kind of block"
            continue
        block: Dict[str, Any] = {'type': raw['type']}
        for f in spec:
            v = raw.get(f.key, f.default)
            problem = _problem(f, v)
            if problem:
                errors[f'extras.{i}.{f.key}'] = problem
            else:
                block[f.key] = _normalise(f, v)
        if not any(k.startswith(f'extras.{i}.') for k in errors):
            kept.append(block)
    return kept, errors


def validate(kind: str, raw: Any) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Split ``raw`` into the values that can be stored and errors for the rest.

    Keys that aren't fields are dropped quietly, so a stale dashboard tab can't
    wedge anything.
    """
    clean: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return clean, errors
    for f in FIELDS[kind]:
        if f.key not in raw:
            continue
        value = raw[f.key]
        if f.kind == 'blocks':
            clean[f.key], problems = _validate_blocks(kind, value)
            errors.update(problems)
            continue
        problem = _problem(f, value)
        if problem:
            errors[f.key] = problem
        else:
            clean[f.key] = _normalise(f, value)
    return clean, errors


def resolve(kind: str, club: Any, override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A club's effective style: defaults, overlaid with what it changed.

    ``override`` stands in for the stored changes, which is how the dashboard
    previews a style before saving it. Stored values that no longer validate
    fall back to the default rather than break a report.
    """
    style = defaults(kind, getattr(club, 'quota_period', 'daily') or 'daily',
                     getattr(club, 'report_daily_gain', False))
    if override is None:
        override = (getattr(club, 'embed_style', None) or {}).get(kind) or {}
    clean, _ = validate(kind, override)
    style.update(clean)
    return style


def changes(kind: str, style: Dict[str, Any], quota_period: str = 'daily') -> Dict[str, Any]:
    """What ``style`` changes from the plain defaults, which is all a club stores.

    Compared against the defaults with daily gain off: saving from the
    dashboard turns the old switch off and keeps its lines here instead.
    """
    base = defaults(kind, quota_period)
    return {k: v for k, v in style.items() if k in base and base[k] != v}


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def render(template: str, values: Dict[str, str]) -> str:
    """Fill in tokens. Unknown ones stay as written, so a typo shows up visibly."""
    def sub(m: re.Match) -> str:
        name, align, width = m.groups()
        if name not in values:
            return m.group(0)
        text = values[name]
        return format(text, f"{align or '<'}{width}") if width else text
    return TOKEN_RE.sub(sub, template)


def color(style: Dict[str, Any], key: str) -> int:
    return int(style[key][1:], 16)


def uses(style: Any, *tokens: str) -> bool:
    """Whether any template in ``style``, extra blocks included, references one of ``tokens``."""
    if isinstance(style, str):
        return any(m.group(1) in tokens for m in TOKEN_RE.finditer(style))
    if isinstance(style, dict):
        return any(uses(v, *tokens) for v in style.values())
    if isinstance(style, list):
        return any(uses(v, *tokens) for v in style)
    return False


def blocks(style: Dict[str, Any], type_: str) -> List[Dict[str, Any]]:
    """The club's extra blocks of one type, in the order it added them."""
    return [b for b in style.get('extras') or () if b.get('type') == type_]


def compact(n: Optional[int], decimals: int = 1) -> str:
    """47.0M, 3.4K. K never gets more than one decimal."""
    if n is None:
        return PLACEHOLDER
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.{decimals}f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.{min(decimals, 1)}f}K"
    return str(n)


def signed(n: Optional[int], decimals: int = 1) -> str:
    """+12.3M or -47.0M. Zero counts as on track, so it gets a plus."""
    if n is None:
        return PLACEHOLDER
    return f"{'+' if n >= 0 else '-'}{compact(abs(n), decimals)}"


def full(n: Optional[int]) -> str:
    return PLACEHOLDER if n is None else f"{n:,}"


def signed_full(n: Optional[int]) -> str:
    return PLACEHOLDER if n is None else f"{n:+,}"


def days(n: Optional[int]) -> str:
    return PLACEHOLDER if n is None else f"{n} day{'s' if n != 1 else ''}"


def percent(part: Optional[int], whole: Optional[float]) -> str:
    if part is None or not whole or whole <= 0:
        return PLACEHOLDER
    return f"{part / whole * 100:.0f}%"
