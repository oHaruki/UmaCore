"""
Timezone helper utilities
"""
from datetime import datetime, date, time
import pytz
from config.settings import TIMEZONE


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
