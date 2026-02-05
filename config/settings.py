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

# Timezone Configuration
TIMEZONE = "Europe/Amsterdam"  # CEST
DAILY_REPORT_TIME = "16:00"

# Quota Rules
DAILY_QUOTA = 1_000_000
BOMB_TRIGGER_DAYS = 3
BOMB_COUNTDOWN_DAYS = 7

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = "bot.log"

# Discord Embed Colors
COLOR_ON_TRACK = 0x00FF00  # Green
COLOR_BEHIND = 0xFFA500     # Orange
COLOR_BOMB = 0xFF0000       # Red
COLOR_INFO = 0x3498db       # Blue