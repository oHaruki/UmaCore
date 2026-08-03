# Getting Started

## Invite the Bot

[Click here to invite UmaCore to your server](https://discord.com/oauth2/authorize?client_id=1467295225184784488&permissions=83968&integration_type=0&scope=bot+applications.commands)

Alternatively, you can self-host it — the bot is open source.

---

## First-Time Setup (Admin)

**1. Add your club**

```
/add_club club_name:YourClub circle_id:860280110 daily_quota:1000000
```

All three are required. See [Finding Your Circle ID](#finding-your-circle-id) below if you're
not sure what to put there.

`daily_quota` is the fan goal per quota period — add `quota_period:weekly` (or `biweekly`)
if your club doesn't run on daily targets.

**2. Set your report and alert channels**

```
/set_report_channel club:YourClub channel:#daily-reports
/set_alert_channel club:YourClub channel:#mod-alerts
```

**3. Test it**

Either wait for the automatic daily scrape, or trigger it manually:

```
/force_check club:YourClub
```

That's it — the bot will handle everything else automatically from here.

---

## Finding Your Circle ID

**Uma.moe:** Go to [uma.moe/circles](https://uma.moe/circles/), search for your club, and copy the number from the URL.
- Example: `https://uma.moe/circles/860280110` → use `860280110`

**ChronoGenesis:** The circle ID is shown directly under your club name on the site. It looks something like `690001342`.

---

## For Members

Once your admin has set up the bot, you can link your Discord account to your trainer name to get personal DM notifications:

```
/link_trainer trainer_name:YourName club:YourClub
```

After linking you can use `/my_status` to check your own quota progress at any time.
