# UmaCore Club Quota Tracker

<div align="center">

**Discord bot for tracking and managing Umamusume club member quotas**

[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.3+-5865F2?style=flat-square&logo=discord)](https://discordpy.readthedocs.io/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Ko-Fi](https://img.shields.io/badge/Support-Ko--fi-FF5E5B?style=flat-square&logo=ko-fi)](https://ko-fi.com/harukidev)

</div>

## Overview

Automated Discord bot that tracks club member fan quotas, manages warning systems, and generates daily performance reports. Supports multiple clubs with independent tracking and customizable settings. Data can be fetched via the Uma.moe API or by scraping ChronoGenesis.net.

## Key Features

- **Multi-Club Support**: Track multiple clubs independently with separate quotas and schedules
- **Uma.moe API**: Fast, reliable club data fetching via the Uma.moe API (default)
- **ChronoGenesis Scraping**: Fallback option using Selenium-based web scraping
- **Dynamic Quota System**: Flexible daily quota requirements with mid-month changes
- **Bomb Warning System**: 3-strike countdown system for members falling behind
- **User Linking**: Members can link their Discord accounts for DM notifications
- **Smart Member Management**: Auto-detects when members leave or return
- **Monthly Reset Handling**: Automatically detects and handles monthly game resets
- **Scrape Locking**: Prevents concurrent scraping conflicts

## Setup

### Prerequisites
- Python 3.10 or higher
- PostgreSQL database ([Neon](https://neon.tech), [Supabase](https://supabase.com), etc.)
- Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- Chrome/Chromium browser (only required if using ChronoGenesis scraper)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd umamusume-bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up PostgreSQL database**
   - Create a database on your preferred PostgreSQL provider
   - Copy the connection string

4. **Configure Discord bot**
   - Visit [Discord Developer Portal](https://discord.com/developers/applications)
   - Create a new application and bot
   - Enable "Server Members Intent" and "Message Content Intent" under Bot settings
   - Copy the bot token
   - Invite the bot with permissions: Send Messages, Embed Links, Read Messages

5. **Create `.env` file**
   ```env
   DISCORD_TOKEN=your_bot_token_here
   DATABASE_URL=postgresql://user:password@host:5432/database_name
   LOG_LEVEL=INFO
   USE_UMAMOE_API=true
   ```

6. **Run the bot**
   ```bash
   python main.py
   ```

The bot will automatically create database tables on first run.

## Quick Start

1. **Add your club**
   ```
   /add_club club_name:YourClubName circle_id:your_circle_id
   ```

   The `circle_id` is a numeric ID from Uma.moe. To find it:
   1. Go to https://uma.moe/circles/
   2. Search for your club
   3. Copy the number at the end of the URL (e.g., `https://uma.moe/circles/860280110` → `860280110`)

2. **Set up channels**
   ```
   /set_report_channel club:YourClubName channel:#daily-reports
   /set_alert_channel club:YourClubName channel:#mod-alerts
   ```

3. **Test the system**
   ```
   /force_check club:YourClubName
   ```

The bot will run daily checks automatically at the scheduled time (default: 16:00 CEST).

## Data Sources

### Uma.moe API (default)
The bot fetches club data directly from the Uma.moe API. This is faster and more reliable than scraping. Requires a numeric `circle_id` per club.

- Enabled by default (`USE_UMAMOE_API=true`)
- Set to `false` in `.env` to switch all clubs to ChronoGenesis
- Each club needs a valid numeric `circle_id` set via `/edit_club`
- If the API is enabled but a club is missing its `circle_id`, the bot will report an error instead of silently falling back

### ChronoGenesis Scraper
Selenium-based scraper for ChronoGenesis.net. Used when `USE_UMAMOE_API=false`.

- Requires Chrome/Chromium installed
- Slower and more fragile than the API (depends on page structure and cookie consent)
- Can be useful as a manual verification source

## Commands

### Club Management (Admin)
- `/add_club` - Register a new club to track
- `/remove_club` - Deactivate a club
- `/activate_club` - Reactivate a deactivated club
- `/list_clubs` - View all registered clubs
- `/edit_club` - Edit club settings (quota, schedule, circle_id, etc.)

### Channel Settings (Admin)
- `/set_report_channel` - Set where daily reports are posted
- `/set_alert_channel` - Set where alerts are posted
- `/channel_settings` - View current channel configuration
- `/post_monthly_info` - Post the monthly info board

### Quota Management (Admin)
- `/quota` - Set daily quota requirement for a club
- `/quota_history` - View quota changes this month
- `/force_check` - Manually trigger daily check and report

### Member Management (Admin)
- `/add_member` - Manually add a member
- `/deactivate_member` - Deactivate a member
- `/activate_member` - Reactivate a member
- `/bomb_status` - View all active bomb warnings

### User Commands
- `/link_trainer` - Link your Discord account to your trainer
- `/unlink` - Remove your trainer link
- `/my_status` - View your own quota status
- `/member_status` - View any member's quota status
- `/notification_settings` - Manage DM notification preferences

## How It Works

### Daily Quota System
- Configurable daily fan requirement (default: 1,000,000 fans/day)
- Tracks cumulative progress since joining
- Mid-month quota changes supported
- Members can catch up from previous deficits

### Bomb Warning System
- **Activation**: 3 consecutive days behind quota
- **Countdown**: 7 days to get back on track
- **Deactivation**: Immediate when member catches up
- **Expiration**: Manual kick required if still behind after countdown

### Member Auto-Detection
- **New Members**: Automatically added when they appear in scraped data
- **Departed Members**: Auto-deactivated when missing from scraped data
- **Returning Members**: Auto-reactivated when they rejoin (unless manually deactivated)

### DM Notifications
- Users can link their Discord accounts to trainers
- Receive DMs for bomb activations
- Optional daily deficit notifications
- Bomb deactivation celebrations

## Deployment

### Docker
```bash
docker-compose up -d --build
```

### Cloud Hosting
The bot works with Railway, Render, Fly.io, etc.

Create a `Procfile`:
```
worker: python main.py
```

### Systemd Service (Linux)
```bash
sudo nano /etc/systemd/system/umamusume-bot.service
sudo systemctl enable umamusume-bot
sudo systemctl start umamusume-bot
```

## Configuration

Each club can be configured independently:
- Daily quota requirement
- Scrape time and timezone
- Bomb trigger days (default: 3)
- Bomb countdown days (default: 7)
- Circle ID (numeric, required when Uma.moe API is enabled)

Use `/edit_club` to modify settings after creation.

## Troubleshooting

**Bot doesn't start**
- Check `bot.log` for errors
- Verify `DISCORD_TOKEN` and `DATABASE_URL` in `.env`
- Ensure PostgreSQL database is accessible

**"Missing Circle ID" error**
- Uma.moe API is enabled but the club has no `circle_id` set
- Run `/edit_club club:YourClub circle_id:<numeric_id>` to fix
- Find your circle_id at https://uma.moe/circles/

**Uma.moe API errors**
- Verify the `circle_id` is correct and numeric
- Check if uma.moe is accessible
- Set `USE_UMAMOE_API=false` in `.env` to temporarily switch to ChronoGenesis

**ChronoGenesis scraping fails**
- Verify Chrome/Chromium is installed (`chromium-browser --version`)
- Check if ChronoGenesis.net is accessible
- Cookie consent popup issues may require manual intervention

**Database errors**
- Bot creates tables automatically on first run
- Verify database connection string format
- Check database permissions

## Project Structure

```
umamusume-bot/
├── bot/
│   ├── client.py          # Bot client and setup
│   ├── tasks.py           # Scheduled tasks
│   └── commands/          # Command handlers
│       ├── admin.py       # Admin commands
│       ├── member.py      # User commands
│       ├── settings.py    # Channel settings
│       └── club_management.py
├── config/
│   ├── database.py        # Database connection
│   └── settings.py        # Configuration
├── models/                # Data models
│   ├── club.py
│   ├── member.py
│   ├── quota_history.py
│   ├── bomb.py
│   └── user_link.py
├── scrapers/              # Data fetching
│   ├── base_scraper.py
│   ├── chronogenesis_scraper.py
│   └── umamoe_api_scraper.py
├── services/              # Business logic
│   ├── quota_calculator.py
│   ├── bomb_manager.py
│   ├── report_generator.py
│   └── notification_service.py
├── utils/                 # Utilities
│   ├── logger.py
│   └── timezone_helper.py
└── main.py                # Entry point
```

## Support the Project

This bot is completely free and open source! If you find it useful, consider supporting development:

[![Ko-Fi Support](https://img.shields.io/badge/Buy%20me%20a%20coffee-Ko--fi-FF5E5B?style=for-the-badge&logo=ko-fi)](https://ko-fi.com/harukidev)

## License

MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

Built with Discord.py, aiohttp, Selenium, PostgreSQL, and asyncpg.
