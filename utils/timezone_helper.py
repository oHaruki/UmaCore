"""
Timezone helper utilities
"""
from datetime import datetime, date, time
import logging
import pytz
from config.settings import TIMEZONE

logger = logging.getLogger(__name__)

# Common non-IANA abbreviations users sometimes store instead of a proper zone.
_TZ_ALIASES = {
    'JST': 'Asia/Tokyo', 'KST': 'Asia/Seoul',
    'PST': 'America/Los_Angeles', 'PDT': 'America/Los_Angeles',
    'EST': 'America/New_York', 'EDT': 'America/New_York',
    'CST': 'America/Chicago', 'CDT': 'America/Chicago',
    'MST': 'America/Denver', 'MDT': 'America/Denver',
    'CET': 'Europe/Paris', 'CEST': 'Europe/Paris', 'BST': 'Europe/London',
    'GMT': 'UTC', 'Z': 'UTC',
}

# Cache: raw stored name -> canonical IANA name (also ensures we log each fix once).
_resolve_cache = {}


def _resolve_name(name: str) -> str:
    """Best-effort map a possibly-invalid timezone string to a valid IANA name.

    Tries: exact match, abbreviation alias, case-insensitive match, then a match
    on the city part (so 'Southamerica/Lima' -> 'America/Lima'). Falls back to
    'UTC' if nothing matches.
    """
    if not name:
        return 'UTC'
    raw = name.strip()
    try:
        pytz.timezone(raw)
        return raw
    except Exception:
        pass

    alias = _TZ_ALIASES.get(raw.upper())
    if alias:
        return alias

    low = raw.lower()
    for z in pytz.all_timezones:
        if z.lower() == low:
            return z

    city = low.rsplit('/', 1)[-1].replace(' ', '_')
    matches = [z for z in pytz.all_timezones if z.lower().rsplit('/', 1)[-1] == city]
    if matches:
        return matches[0]

    return 'UTC'


def resolve_timezone(name: str):
    """Return a pytz tzinfo for a possibly-invalid stored timezone. Never raises.

    Logs once per unique bad input: an info line when it auto-corrects, a warning
    when it has to fall back to UTC (that club's timezone should be fixed).
    """
    if name not in _resolve_cache:
        resolved = _resolve_name(name)
        _resolve_cache[name] = resolved
        if resolved != name:
            if resolved == 'UTC' and (name or '').strip().upper() not in ('UTC', 'GMT', 'Z'):
                logger.warning(f"Unknown timezone '{name}' — falling back to UTC; fix this club's timezone.")
            else:
                logger.info(f"Normalized timezone '{name}' -> '{resolved}'")
    return pytz.timezone(_resolve_cache[name])


def get_timezone():
    """Get the configured timezone"""
    return pytz.timezone(TIMEZONE)


def get_current_datetime():
    """Get current datetime in the configured timezone"""
    tz = get_timezone()
    return datetime.now(tz)


def get_current_date():
    """Get current date in the configured timezone"""
    return get_current_datetime().date()


def parse_time_string(time_str: str):
    """
    Parse a time string (HH:MM) and return a time object with timezone
    
    Args:
        time_str: Time string in format "HH:MM"
    
    Returns:
        time object with timezone info
    """
    tz = get_timezone()
    hour, minute = map(int, time_str.split(':'))
    return time(hour=hour, minute=minute, tzinfo=tz)


def convert_to_utc(dt: datetime):
    """Convert a timezone-aware datetime to UTC"""
    return dt.astimezone(pytz.UTC)


def format_datetime(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S"):
    """Format a datetime with the configured timezone"""
    tz = get_timezone()
    if dt.tzinfo is None:
        dt = tz.localize(dt)
    else:
        dt = dt.astimezone(tz)
    return dt.strftime(fmt)
