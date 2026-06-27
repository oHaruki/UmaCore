# UmaCore

<div align="center">

**Uma Musume club quota tracker for Discord**

[![Website](https://img.shields.io/badge/Website-umacore.app-5865F2?style=flat-square&logo=globe)](https://umacore.app)
[![Discord](https://img.shields.io/badge/Discord-Join%20Server-5865F2?style=flat-square&logo=discord)](https://discord.gg/f4QZNag9Hv)
[![Invite Bot](https://img.shields.io/badge/Invite-Add%20to%20Server-57F287?style=flat-square&logo=discord)](https://discord.com/oauth2/authorize?client_id=1467295225184784488&permissions=83968&integration_type=0&scope=bot+applications.commands)
[![Ko-Fi](https://img.shields.io/badge/Support-Ko--fi-FF5E5B?style=flat-square&logo=ko-fi)](https://ko-fi.com/harukidev)
[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

</div>

---

## What is UmaCore?

UmaCore is a Discord bot that automatically tracks fan quota progress for Uma Musume club members. It pulls each member's fan count daily via the Uma.moe API, calculates whether they're on pace to meet their quota, and sends warnings to members who are falling behind — so club leaders don't have to chase people manually.

## Add UmaCore to Your Server

**[Invite the bot](https://discord.com/oauth2/authorize?client_id=1467295225184784488&permissions=83968&integration_type=0&scope=bot+applications.commands)**

After inviting, use `/add_club` to register your club and `/set_report_channel` to configure where reports are posted. Need help getting set up? Join the **[support server](https://discord.gg/f4QZNag9Hv)** or check the full guide at **[umacore.app](https://umacore.app)**.

---

## Features

- **Daily fan tracking** — pulls each member's fan count automatically on a configurable schedule
- **Quota progress** — tracks cumulative progress against daily targets and shows surplus/deficit per member
- **Bomb warning system** — 3-strike countdown that activates after 3 consecutive days behind quota, with a 7-day window to recover
- **Discord notifications** — daily reports and at-risk member alerts posted to configured channels
- **DM notifications** — members can link their Discord accounts to get personal bomb and deficit alerts
- **Mid-month quota changes** — supports changing the quota requirement partway through a month with automatic recalculation
- **Monthly reset detection** — automatically handles Uma Musume's monthly game resets
- **Multi-club support** — track multiple clubs independently with separate quotas, schedules, and channels
- **Visual image reports** — optional PNG quota report with progress bars, rank movement, and bomb indicators (opt-in per club)
- **Web dashboard** — pair with [UmaCore Web](https://github.com/oHaruki/UmaCore-web) for a visual management interface

---

## Commands

### Club Management
| Command | Description |
|---|---|
| `/add_club` | Register a new club to track |
| `/remove_club` | Deactivate a club |
| `/activate_club` | Reactivate a deactivated club |
| `/list_clubs` | View all registered clubs |
| `/edit_club` | Edit club settings (quota, schedule, circle_id, etc.) |

### Channel Settings
| Command | Description |
|---|---|
| `/set_report_channel` | Set where daily reports are posted |
| `/set_alert_channel` | Set where alerts are posted |
| `/channel_settings` | View current channel configuration |
| `/post_monthly_info` | Post the monthly info board |

### Quota Management
| Command | Description |
|---|---|
| `/quota` | Set daily quota requirement for a club |
| `/quota_history` | View quota changes this month |
| `/force_check` | Manually trigger a daily check and report |
| `/bomb_status` | View all active bomb warnings |

### Member Commands
| Command | Description |
|---|---|
| `/link_trainer` | Link your Discord account to your trainer name |
| `/unlink` | Remove your trainer link |
| `/my_status` | View your own quota status |
| `/member_status` | View any member's quota status |
| `/notification_settings` | Manage DM notification preferences |

---

## Self-Hosting

> Most users should just [invite the bot](#add-umacore-to-your-server). Self-hosting is only needed if you want to run a private instance.

### Prerequisites
- Python 3.10+
- PostgreSQL database ([Neon](https://neon.tech), [Supabase](https://supabase.com), or self-hosted)
- Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))

### Installation

1. Clone the repo and install dependencies:
   ```bash
   git clone https://github.com/oHaruki/UmaCore.git
   cd UmaCore
   pip install -r requirements.txt
   ```

2. Create a `.env` file:
   ```env
   DISCORD_TOKEN=your_bot_token_here
   DATABASE_URL=postgresql://user:password@host:5432/database_name
   LOG_LEVEL=INFO
   USE_UMAMOE_API=true
   BOT_API_SECRET=your_random_secret_here
   ```
   Generate `BOT_API_SECRET` with `openssl rand -hex 32` and use the same value in the web app's `.env.local`.

3. Run the bot:
   ```bash
   python main.py
   ```
   The bot creates database tables automatically on first run.

4. In Discord, add your first club:
   ```
   /add_club club_name:YourClubName circle_id:860280110 daily_quota:1000000
   ```
   Find your `circle_id` at [uma.moe/circles](https://uma.moe/circles/) — it's the number at the end of the URL.

### Deployment

**Docker:**
```bash
docker-compose up -d --build
```

**PM2 / Linux:**
```bash
pm2 start python --name umacore -- main.py
```

Cloud platforms (Railway, Render, Fly.io) also work — use a `Procfile` with `worker: python main.py`.

---

## Support the Project

UmaCore is free and open source. If it saves your club leadership time, consider supporting development:

[![Ko-Fi](https://img.shields.io/badge/Buy%20me%20a%20coffee-Ko--fi-FF5E5B?style=for-the-badge&logo=ko-fi)](https://ko-fi.com/harukidev)

## Credits

- **[rat (rattenschwe1f)](https://github.com/rattenschwe1f)** — visual image report renderer, originally built as [uma-fan-tally-tool](https://github.com/rattenschwe1f/uma-fan-tally-tool). The PNG generation code in `tally/` is adapted from his work.

## License

MIT — see [LICENSE](LICENSE) for details.
