# Setup Guide

## Prerequisites

- Python 3.10+
- PostgreSQL database (Neon, Supabase, or local)
- Discord bot token
- Chrome/Chromium — only needed if using ChronoGenesis scraper

## Installation

### 1. Clone the repository

```bash
git clone <repository-url>
cd UmaCore
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create a PostgreSQL database

Use any PostgreSQL provider (Neon, Supabase, local). Copy the connection string — it looks like:

```
postgresql://user:password@host:5432/database_name
```

### 4. Set up the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and add a bot
3. Under **Bot settings**, enable:
   - Server Members Intent
   - Message Content Intent
4. Copy the bot token
5. Invite the bot to your server with permissions: Send Messages, Embed Links, Read Messages/History

### 5. Create a `.env` file

```env
DISCORD_TOKEN=your_bot_token_here
DATABASE_URL=postgresql://user:password@host:5432/database_name
LOG_LEVEL=INFO
USE_UMAMOE_API=true
UMAMOE_API_KEY=uma_k_your_key_here
```

Set `USE_UMAMOE_API=false` to use ChronoGenesis scraping instead of Uma.moe API.

`UMAMOE_API_KEY` is required for all Uma.moe API endpoints. Pass it via the `X-API-Key` request header. Contact the uma.moe admin to obtain a key.

### 6. Run the bot

```bash
python main.py
```

On first run, the bot automatically creates all database tables, syncs slash commands, and starts the scheduler.

---

## Quick Start

Once the bot is running, do this to get started:

**1. Add your club**
```
/add_club club_name:YourClub scrape_url:... circle_id:860280110
```

**2. Set up channels**
```
/set_report_channel club:YourClub channel:#daily-reports
/set_alert_channel club:YourClub channel:#mod-alerts
```

**3. Test it**
```
/force_check club:YourClub
```

**4. (Optional) Post the monthly info board**
```
/post_monthly_info club:YourClub channel:#info
```

---

## Finding Your Circle ID

If you want to use the Uma.moe API (recommended):

1. Go to [uma.moe/circles](https://uma.moe/circles/)
2. Search for your club
3. Copy the numeric ID from the URL — e.g. `https://uma.moe/circles/860280110` → use `860280110`

---

## Deployment

### Docker

```bash
docker-compose up -d --build
```

### Railway / Render / Fly.io

Create a `Procfile`:

```
worker: python main.py
```

### Linux (systemd)

```bash
sudo nano /etc/systemd/system/umacore.service
sudo systemctl enable umacore
sudo systemctl start umacore
```

---

## Database Tables

All tables are created automatically on first run:

| Table | Purpose |
|---|---|
| `clubs` | Club configurations and settings |
| `members` | Club member data |
| `quota_history` | Daily quota tracking per member |
| `quota_requirements` | Quota change history |
| `bombs` | Active bomb warnings |
| `user_links` | Discord ID to trainer mappings |
| `bot_settings` | Monthly info board locations |
| `club_rank_history` | Club ranking over time |
