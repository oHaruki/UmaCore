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
# API owner bumps the cap. Day 1 makes 2 calls/club, which also draws from this.
UMAMOE_RATE_PER_MIN = int(os.getenv("UMAMOE_RATE_PER_MIN", "100"))   # tokens per minute
UMAMOE_RATE_BURST = int(os.getenv("UMAMOE_RATE_BURST", "10"))       # bucket capacity (max burst)

# Rolling-update aware dispatch for the default-time clump.
# uma.moe publishes circle data gradually after daily rollover (~20 circles/s),
# so a club at rank R isn't fresh until ~R/rate seconds in. Only clubs whose
# scrape time resolves to the shared default below get this rank-based delay;
# clubs on custom times already self-stagger and fire immediately.
SCRAPE_DEFAULT_UTC_TIME = os.getenv("SCRAPE_DEFAULT_UTC_TIME", "17:00")   # HH:MM UTC rollover starts (default clump)
# Any club scheduled between rollover start and start+window is subject to the
# rolling update and gets a rank-aware delay — not just the exact default minute.
# Clubs outside the window (e.g. mornings) read settled data and fire on time.
SCRAPE_ROLLOVER_WINDOW_MIN = int(os.getenv("SCRAPE_ROLLOVER_WINDOW_MIN", "15"))
SCRAPE_ROLLOUT_PER_SEC = float(os.getenv("SCRAPE_ROLLOUT_PER_SEC", "20")) # circles/s uma.moe publishes
SCRAPE_RANK_BUFFER_SEC = int(os.getenv("SCRAPE_RANK_BUFFER_SEC", "30"))   # safety margin on top of rank/rate
SCRAPE_MAX_RANK_DELAY_SEC = int(os.getenv("SCRAPE_MAX_RANK_DELAY_SEC", "600"))     # cap for very low ranks
SCRAPE_UNKNOWN_RANK_DELAY_SEC = int(os.getenv("SCRAPE_UNKNOWN_RANK_DELAY_SEC", "180"))  # clubs with no rank history yet

# Freshness re-queue: if a default club's data is still stale on fetch, re-queue
# instead of trusting it or spamming an error. Bounded to avoid re-fetch storms.
SCRAPE_MAX_FRESHNESS_RETRIES = int(os.getenv("SCRAPE_MAX_FRESHNESS_RETRIES", "4"))
SCRAPE_FRESHNESS_RETRY_DELAY_SEC = int(os.getenv("SCRAPE_FRESHNESS_RETRY_DELAY_SEC", "60"))
SCRAPE_MAX_CONCURRENCY = int(os.getenv("SCRAPE_MAX_CONCURRENCY", "8"))    # clubs processed in parallel

# Timezone Configuration
TIMEZONE = "Europe/Amsterdam"  # CEST
DAILY_REPORT_TIME = "16:00"

# Quota Rules
DAILY_QUOTA = 1_000_000
BOMB_TRIGGER_DAYS = 3
BOMB_COUNTDOWN_DAYS = 7

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