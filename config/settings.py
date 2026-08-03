"""
Configuration settings for the Umamusume Discord Bot
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Discord Configuration
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL")

# Scraping Configuration
SCRAPE_TIMEOUT = 90  # seconds
SCRAPE_RETRY_ATTEMPTS = 3
SCRAPE_RETRY_DELAY = 1  # seconds

# Uma.moe API Configuration
USE_UMAMOE_API = os.getenv("USE_UMAMOE_API", "true").lower() == "true"
UMAMOE_API_KEY = os.getenv("UMAMOE_API_KEY")

# Uma.moe rate limiting (shared across ALL outbound API calls).
# Kept under the API's 120/min circle-data limit with margin; raise once the
# API owner bumps the cap.
UMAMOE_RATE_PER_MIN = int(os.getenv("UMAMOE_RATE_PER_MIN", "100"))   # tokens per minute
UMAMOE_RATE_BURST = int(os.getenv("UMAMOE_RATE_BURST", "10"))       # bucket capacity (max burst)

# Guard against reading the wrong daily_fans slot: the parsed club total is
# compared against uma.moe's own monthly_point/live_points.
#
# Scaled against ONE DAY of fans rather than a percentage of the month, because
# that is what the failure actually looks like — an off-by-one slot is wrong by
# about a full day's gain. The leftover noise from member churn is a few million
# fans in absolute terms regardless of club size (measured 2-7M across six clubs
# spanning 125M-1.66B monthly points), so a flat percentage flags small clubs for
# ordinary churn while missing nothing on large ones.
UMAMOE_SLOT_ERROR_FRACTION = float(os.getenv("UMAMOE_SLOT_ERROR_FRACTION", "0.5"))

# Fallback when a full-day reference isn't available (missing yesterday_points):
# relative tolerance, plus an absolute floor so small clubs aren't flagged for
# a few million fans of churn.
UMAMOE_CHECKSUM_TOLERANCE = float(os.getenv("UMAMOE_CHECKSUM_TOLERANCE", "0.02"))
UMAMOE_CHECKSUM_MIN_ABS = int(os.getenv("UMAMOE_CHECKSUM_MIN_ABS", "25000000"))

# Grace period after the 15:00 UTC daily finalize before we trust a fetch.
# A club scheduled in the minutes right after rollover can beat uma.moe's write
# for its circle, so those are held this long. Anything scheduled later (or
# before rollover, reading the previous JST day) fires on time.
#
# This replaced a rank-based delay model that assumed a ~20 circles/s rollout.
# Measured 2026-07-25: the top 100 circles all finalized within ~3s, and the
# scraper now detects staleness exactly via circle.last_updated, so predicting
# readiness from rank is neither accurate nor necessary.
SCRAPE_ROLLOVER_GRACE_SEC = int(os.getenv("SCRAPE_ROLLOVER_GRACE_SEC", "120"))

# Freshness re-queue: if a club's target day still isn't finalized on fetch,
# re-queue instead of trusting it or spamming an error. Bounded to avoid storms.
SCRAPE_MAX_FRESHNESS_RETRIES = int(os.getenv("SCRAPE_MAX_FRESHNESS_RETRIES", "4"))
SCRAPE_FRESHNESS_RETRY_DELAY_SEC = int(os.getenv("SCRAPE_FRESHNESS_RETRY_DELAY_SEC", "60"))
SCRAPE_MAX_CONCURRENCY = int(os.getenv("SCRAPE_MAX_CONCURRENCY", "8"))    # clubs processed in parallel

# Database backups (run by the bot itself — no cron entry needed).
# Requires the postgresql client on the host for pg_dump; set PG_DUMP_PATH if it
# isn't on PATH. Dumps land in DB_BACKUP_DIR and only the newest
# DB_BACKUP_KEEP are retained.
#
# Every value below has a working default, so backups run with nothing set in
# .env. The numeric ones are parsed tolerantly on purpose: a typo'd backup
# setting must not stop the bot from reporting quota.
def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"[settings] {name}='{raw}' is not a number, using {default}")
        return default
    if value < minimum:
        print(f"[settings] {name}={value} is below the minimum {minimum}, using {default}")
        return default
    return value


DB_BACKUP_ENABLED = os.getenv("DB_BACKUP_ENABLED", "true").lower() == "true"
DB_BACKUP_DIR = os.getenv("DB_BACKUP_DIR", "backups")   # relative to the bot's working dir
DB_BACKUP_KEEP = _int_env("DB_BACKUP_KEEP", 7)          # how many dumps to keep
DB_BACKUP_UTC_TIME = os.getenv("DB_BACKUP_UTC_TIME", "03:30")   # HH:MM UTC, quiet hour
DB_BACKUP_TIMEOUT_SEC = _int_env("DB_BACKUP_TIMEOUT_SEC", 600)

# Timezone Configuration
TIMEZONE = "Europe/Amsterdam"  # CEST
DAILY_REPORT_TIME = "16:00"

# Quota Rules
DAILY_QUOTA = 1_000_000
BOMB_TRIGGER_DAYS = 3
BOMB_COUNTDOWN_DAYS = 7

# Rank-promotion milestones (club position ranks). The /promotion command and the
# daily report use these as the default "next target" a club is climbing toward: the
# best milestone strictly above the club's current rank. Editable — order doesn't matter.
PROMOTION_MILESTONES = [10, 50, 100, 500, 3000]

# Internal API server (web UI integration)
BOT_API_PORT = int(os.getenv("BOT_API_PORT", "7890"))
# Shared secret for the localhost-only HTTP API (must match umacore-web BOT_API_SECRET).
BOT_API_SECRET = os.getenv("BOT_API_SECRET")

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = "bot.log"

# Discord Embed Colors
COLOR_ON_TRACK = 0x00FF00  # Green
COLOR_BEHIND = 0xFFA500     # Orange
COLOR_BOMB = 0xFF0000       # Red
COLOR_INFO = 0x3498db       # Blue