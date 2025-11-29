# Umamusume Discord Bot - Quota Tracker

An automated Discord bot for tracking Umamusume club member quotas, managing warnings, and generating daily reports.

## Features

- **Automated Daily Scraping**: Scrapes ChronoGenesis.net daily at 16:00 CEST
- **Quota Tracking**: Monitors each member's progress against the 1M fans/day requirement
- **Cumulative System**: Members can catch up from previous deficits
- **Bomb Warning System**: 3 consecutive days behind triggers a 7-day countdown
- **Automatic Alerts**: Notifies when bombs activate, deactivate, or expire
- **Admin Commands**: Manual checks, member status lookups, and more
- **Monthly Reset Support**: Automatically handles monthly resets

## Requirements

- Python 3.10+
- PostgreSQL database (free tier from Neon, Supabase, etc.)
- Discord bot token
- Chrome/Chromium for Selenium

## Installation

### 1. Clone or Download the Project

```bash
cd /path/to/your/projects
# (copy the umamusume-bot directory here)
```

### 2. Install Dependencies

```bash
cd umamusume-bot
pip install -r requirements.txt
```

### 3. Set Up PostgreSQL Database

Create a free PostgreSQL database using one of these providers:

- **Neon**: https://neon.tech (Recommended)
- **Supabase**: https://supabase.com
- **ElephantSQL**: https://elephantsql.com

Get your connection string (should look like):
```
postgresql://user:password@host.region.provider.com:5432/database_name
```

### 4. Create Discord Bot

1. Go to https://discord.com/developers/applications
2. Click "New Application"
3. Go to "Bot" section and click "Add Bot"
4. Enable these **Privileged Gateway Intents**:
   - Server Members Intent
   - Message Content Intent
5. Copy the bot token
6. Go to OAuth2 > URL Generator:
   - Select scopes: `bot`, `applications.commands`
   - Select permissions: `Send Messages`, `Embed Links`, `Read Messages`
7. Use the generated URL to invite bot to your server

### 5. Configure Environment Variables

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
DISCORD_TOKEN=your_bot_token_here
CHANNEL_ID=your_channel_id_here
DATABASE_URL=postgresql://user:password@host:5432/database_name
LOG_LEVEL=INFO
```

**To get Channel ID:**
1. Enable Developer Mode in Discord (User Settings > Advanced)
2. Right-click the channel where you want reports
3. Click "Copy ID"

## Running the Bot

### Development (Local)

```bash
python main.py
```

### Production (Recommended: Always-On Server)

**Option 1: VPS (DigitalOcean, Linode, etc.)**

```bash
# Install as systemd service
sudo nano /etc/systemd/system/umamusume-bot.service
```

```ini
[Unit]
Description=Umamusume Discord Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/umamusume-bot
ExecStart=/usr/bin/python3 /path/to/umamusume-bot/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable umamusume-bot
sudo systemctl start umamusume-bot
sudo systemctl status umamusume-bot
```

**Option 2: Cloud Hosting (Railway, Render, Fly.io)**

These platforms can run the bot 24/7. Add a `Procfile`:

```
worker: python main.py
```

**Option 3: Docker**

```dockerfile
FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

```bash
docker build -t umamusume-bot .
docker run -d --env-file .env umamusume-bot
```

## Bot Commands

All commands require **Administrator** permissions.

### `/force_check`
Manually trigger a quota check and report (bypasses daily schedule).

**Usage:**
```
/force_check
```

### `/member_status`
View detailed status for a specific member.

**Usage:**
```
/member_status trainer_name:MemberName
```

**Example:**
```
/member_status trainer_name:TrainerABC
```

### `/bomb_status`
View all currently active bombs with countdown timers.

**Usage:**
```
/bomb_status
```

### `/add_member`
Manually add a new member (useful for members who joined before bot was set up).

**Usage:**
```
/add_member trainer_name:MemberName join_date:YYYY-MM-DD
```

**Example:**
```
/add_member trainer_name:NewTrainer join_date:2024-11-01
```

### `/deactivate_member`
Mark a member as inactive (e.g., they left the club).

**Usage:**
```
/deactivate_member trainer_name:MemberName
```

## How It Works

### Daily Schedule (16:00 CEST)

1. **Scrape Website**: Fetches latest fan counts from ChronoGenesis.net
2. **Process Data**: Updates database with cumulative fan counts
3. **Calculate Quotas**: Determines who's on track vs. behind
4. **Bomb Management**:
   - Activates bombs for members 3+ consecutive days behind
   - Deactivates bombs if members catch up
   - Decrements countdown timers
5. **Generate Report**: Posts Discord embed with:
   - Summary statistics
   - Members on track (sorted by surplus)
   - Members behind (sorted by days behind)
   - Active bombs (sorted by urgency)
6. **Send Alerts**:
   - Bomb activation notifications
   - Kick alerts for expired bombs

### Quota System

**Daily Requirement**: 1,000,000 fans per day per member

**Calculation**:
- Expected fans = (Days since join) × 1,000,000
- Actual fans = Latest cumulative count from website
- Deficit/Surplus = Actual - Expected

**Examples**:

**Member A joined Day 1:**
- Day 5: Has 5.2M fans
  - Expected: 5M (5 days × 1M)
  - Surplus: +200K ✅

**Member B joined Day 3:**
- Day 5: Has 2.5M fans
  - Expected: 3M (3 days × 1M)
  - Deficit: -500K ⚠️

### Bomb System

**Activation**: 3 consecutive days with negative deficit

**Countdown**: 7 days to get back on track

**Deactivation**: Immediate when deficit becomes positive

**Expiration**: After 7 days, if still behind → kick required

**Example**:
```
Day 1: -500K (1 day behind)
Day 2: -800K (2 days behind)
Day 3: -1M (3 days behind) → 💣 BOMB ACTIVATED (7 days remaining)
Day 4: -1.2M (6 days remaining)
Day 5: +200K → ✅ BOMB DEACTIVATED (back on track!)
```

### Monthly Reset

When the bot detects Day 1 with lower cumulative counts than the previous scrape:
1. Clears all quota history
2. Deactivates all bombs
3. Preserves member join dates
4. Starts fresh tracking

## Troubleshooting

### Bot doesn't start

**Check logs**: Look at `bot.log` for error messages

**Common issues**:
- Invalid `DISCORD_TOKEN`
- Invalid `DATABASE_URL`
- Database connection refused
- Chrome/Chromium not installed

### Scraping fails

**Error**: "Timeout while loading"

**Solutions**:
1. Check if website is accessible
2. Increase `SCRAPE_TIMEOUT` in `config/settings.py`
3. Check Chrome/Chromium installation

### Daily report not posting

**Check**:
1. Bot has "Send Messages" and "Embed Links" permissions in the channel
2. `CHANNEL_ID` is correct
3. Check logs for errors during scheduled task

### Database errors

**Error**: "relation does not exist"

**Solution**: Schema wasn't initialized properly
```bash
# Restart bot - it will auto-create tables
python main.py
```

## Project Structure

```
umamusume-bot/
├── config/              # Configuration and database setup
│   ├── settings.py      # Bot settings
│   └── database.py      # PostgreSQL connection
├── models/              # Data models
│   ├── member.py        # Member model
│   ├── quota_history.py # Quota tracking
│   └── bomb.py          # Bomb warnings
├── scrapers/            # Web scraping
│   ├── base_scraper.py
│   └── chronogenesis_scraper.py
├── services/            # Business logic
│   ├── quota_calculator.py
│   ├── bomb_manager.py
│   └── report_generator.py
├── bot/                 # Discord bot
│   ├── client.py        # Bot setup
│   ├── commands.py      # Slash commands
│   └── tasks.py         # Scheduled tasks
├── utils/               # Utilities
│   ├── logger.py
│   └── timezone_helper.py
├── main.py              # Entry point
├── requirements.txt     # Dependencies
└── .env.example         # Environment template
```

## Customization

### Change Daily Quota

Edit `config/settings.py`:
```python
DAILY_QUOTA = 1_500_000  # 1.5M instead of 1M
```

### Change Report Time

Edit `config/settings.py`:
```python
DAILY_REPORT_TIME = "18:00"  # 6 PM instead of 4 PM
```

### Change Bomb Parameters

Edit `config/settings.py`:
```python
BOMB_TRIGGER_DAYS = 5    # 5 days instead of 3
BOMB_COUNTDOWN_DAYS = 10  # 10 days instead of 7
```

### Change Timezone

Edit `config/settings.py`:
```python
TIMEZONE = "America/New_York"  # EST instead of CEST
```

## Support

For issues or questions:
1. Check the logs in `bot.log`
2. Review this README
3. Check Discord bot permissions
4. Verify database connection

## License

MIT License - Feel free to modify and use as needed.
