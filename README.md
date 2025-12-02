# Umamusume Club Quota Tracker

<div align="center">

**Discord bot for tracking and managing Umamusume club member quotas**

[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-5865F2?style=flat-square&logo=discord)](https://discordpy.readthedocs.io/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Ko-Fi](https://img.shields.io/badge/Support-Ko--fi-FF5E5B?style=flat-square&logo=ko-fi)](https://ko-fi.com/harukidev)

</div>

## Overview

Automated Discord bot that scrapes ChronoGenesis.net to track club member fan quotas, manages warning systems, and generates daily performance reports for Umamusume clubs.

## Key Features

- **Automated Daily Tracking**: Scrapes member data from ChronoGenesis.net at scheduled times
- **Dynamic Quota System**: Flexible daily quota requirements with admin controls
- **Bomb Warning System**: 3-strike countdown system for members falling behind
- **Smart Member Management**: Auto-detects when members leave or return to the club
- **Flexible Reporting**: Separate channels for daily reports and urgent alerts
- **Monthly Reset Handling**: Automatically detects and handles monthly game resets

## Installation

### Prerequisites
- Python 3.10 or higher
- PostgreSQL database (free tier from [Neon](https://neon.tech), [Supabase](https://supabase.com), etc.)
- Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- Chrome/Chromium for web scraping

### Setup

1. **Clone or download this repository**

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Create a PostgreSQL database** and get your connection string

4. **Create a Discord bot**:
   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Create an application and add a bot
   - Enable "Server Members Intent" and "Message Content Intent"
   - Copy the bot token
   - Invite bot with these permissions: Send Messages, Embed Links, Read Messages

5. **Configure environment variables** (create `.env` file):
   ```env
   DISCORD_TOKEN=your_bot_token_here
   CHANNEL_ID=your_channel_id_here
   DATABASE_URL=postgresql://user:password@host:5432/database_name
   LOG_LEVEL=INFO
   ```

6. **Run the bot**
   ```bash
   python main.py
   ```

## Quick Start

1. **Set up channels** (optional, uses CHANNEL_ID from .env as fallback):
   ```
   /set_report_channel #daily-reports
   /set_alert_channel #mod-alerts
   ```

2. **Configure quota** (optional, default is 1M fans/day):
   ```
   /quota 1000000
   ```

3. **Test the system**:
   ```
   /force_check
   ```

The bot will automatically run daily checks at 16:00 CEST.

## Commands

### For Everyone
- `/member_status <trainer_name>` - Check any member's quota status

### For Administrators
- `/set_report_channel <channel>` - Set where daily reports are posted
- `/set_alert_channel <channel>` - Set where alerts (bombs, kicks) are posted
- `/channel_settings` - View current channel configuration
- `/quota <amount>` - Set daily quota requirement
- `/quota_history` - View quota changes this month
- `/force_check` - Manually trigger daily check and report
- `/bomb_status` - View all active bomb warnings
- `/add_member <name> <join_date> [trainer_id]` - Manually add a member
- `/deactivate_member <trainer_name>` - Deactivate a member

## How It Works

### Daily Quota System
- Each member must earn **1,000,000 fans per day** (configurable)
- System tracks cumulative progress since joining
- Members can catch up from previous deficits

### Bomb Warning System
- **Activation**: 3 consecutive days behind quota
- **Countdown**: 7 days to get back on track
- **Deactivation**: Immediate when member catches up
- **Expiration**: Kick required if still behind after countdown

### Auto-Detection
- **New Members**: Automatically added when they appear in scraped data
- **Departed Members**: Auto-deactivated when missing from scraped data
- **Returning Members**: Auto-reactivated when they rejoin

## Deployment

### Local Development
```bash
python main.py
```

### Production (Linux VPS)
```bash
# Create systemd service
sudo nano /etc/systemd/system/umamusume-bot.service

# Start service
sudo systemctl enable umamusume-bot
sudo systemctl start umamusume-bot
```

### Docker
```bash
docker build -t umamusume-bot .
docker run -d --env-file .env umamusume-bot
```

### Cloud Hosting
Works with Railway, Render, Fly.io, etc. Add a `Procfile`:
```
worker: python main.py
```

## Configuration

Edit `config/settings.py` to customize:
- `DAILY_QUOTA` - Daily fan requirement (default: 1,000,000)
- `BOMB_TRIGGER_DAYS` - Days before bomb activation (default: 3)
- `BOMB_COUNTDOWN_DAYS` - Days until kick required (default: 7)
- `DAILY_REPORT_TIME` - When to run daily check (default: "16:00")
- `TIMEZONE` - Timezone for scheduling (default: "Europe/Amsterdam")

## Troubleshooting

**Bot doesn't start**
- Check logs in `bot.log`
- Verify `DISCORD_TOKEN` and `DATABASE_URL` in `.env`

**Scraping fails**
- Ensure Chrome/Chromium is installed
- Check if website is accessible
- Increase `SCRAPE_TIMEOUT` in settings

**Database errors**
- Bot auto-creates tables on first run
- Ensure database URL is correct and accessible

## Project Structure

```
umamusume-bot/
├── bot/              # Discord bot client and commands
├── config/           # Configuration and database
├── models/           # Data models (Member, Bomb, etc.)
├── scrapers/         # Web scraping logic
├── services/         # Business logic (quota calculations, reports)
├── utils/            # Utilities (logging, timezone)
└── main.py           # Entry point
```

## Support the Project

This bot is completely free and open source! If you find it useful, consider supporting development:

[![Ko-Fi Support](https://img.shields.io/badge/Buy%20me%20a%20coffee-Ko--fi-FF5E5B?style=for-the-badge&logo=ko-fi)](https://ko-fi.com/harukidev)

## License

MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

Built with Discord.py, Selenium, PostgreSQL, and asyncpg.