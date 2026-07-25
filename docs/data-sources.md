# Data Sources

UmaCore supports two data sources for fetching member fan counts.

---

## Uma.moe API (Recommended)

The primary data source. Fast and reliable.

**Requires:** `circle_id` set on the club (see below)

**What it provides:**
- Full month history per member
- Daily cumulative fan counts
- Club ranking data

**Finding your Circle ID:**
1. Go to [uma.moe/circles](https://uma.moe/circles/)
2. Search for your club
3. Copy the numeric ID from the URL
   - Example: `https://uma.moe/circles/860280110` → use `860280110`
4. Set it with `/add_club circle_id:860280110` or `/edit_club circle_id:860280110`

**Timing notes:**
- Uma.moe runs on JST. Each competition day finalizes at **15:00 UTC** (00:00 JST)
- Between finalizes, the in-progress day's total updates roughly hourly (`live_points`)
- The bot always reports the **last fully-closed** day — never the in-progress one
- Month boundaries are handled automatically (the closing day may live in the previous month's data)

---

## ChronoGenesis Scraper (Fallback)

Used when a club has no `circle_id`, or when `USE_UMAMOE_API=false` is set in `.env`.

**Requires:** Chrome/Chromium installed on the host machine

**Limitations:**
- Slower than the API
- Dependent on ChronoGenesis page structure (may break if the site changes)
- Less reliable overall

The bot automatically falls back to ChronoGenesis if the Uma.moe API fails.

---

## Switching Data Sources

To use Uma.moe API globally (default):
```env
USE_UMAMOE_API=true
```

To force ChronoGenesis globally:
```env
USE_UMAMOE_API=false
```

Per-club: if a club has no `circle_id` set, it always uses ChronoGenesis regardless of the global setting.

---

## Edge Cases

### Uma.moe API

#### Live vs Finalized Data

Uma.moe's per-member `daily_fans` array is indexed by **JST competition day**, and the
slot for the day currently being raced updates roughly every hour. Only the last
fully-closed day is used for quota accounting — reading the in-progress slot would
under-report every member by however much of the day is left.

Which slot that is comes from the clock (see `utils/jst_calendar.py`), and is confirmed
against the circle's `last_updated` timestamp before use. If Uma.moe hasn't finalized a
given circle yet, the scrape is re-queued rather than trusted.

As a safety net, the bot cross-checks its parsed club total against Uma.moe's own
`monthly_point`. A one-slot error would show up as roughly a full day of fans (~9% of the
month), so any drift beyond 2% is logged as an error.

#### Month Boundaries

Around the start of a month the last-closed day can belong to the previous month, so the
bot fetches whichever month contains that day. Reports are dated accordingly — no special
Day 1 handling is needed.

#### End-of-Month Competition Rollover

The API's rank fields always describe the *current* JST competition month. When the target
day belongs to a period that has already ended, those ranks describe a different (nearly
empty) period, so the bot drops rank display from the report rather than showing
incorrect values.

#### Invalid or Missing Circle ID

If a club's `circle_id` is missing or contains non-numeric characters, the bot sends an error embed to the report channel with instructions on how to find and set the correct ID. The daily check is skipped for that club until it's fixed.

#### Lifetime vs Monthly Fan Counts

The Uma.moe API returns **lifetime cumulative** fan counts. The bot converts these to monthly values automatically by detecting each member's first non-zero entry (their join day) and subtracting it. Members who joined before the current month start from 0 as expected.

---

### ChronoGenesis Scraper

#### Cookie Consent Popup

Some sessions trigger a cookie consent banner that can block the page from rendering. The bot attempts to click through it automatically, and falls back to removing it via JavaScript if clicking fails. If neither works, the scrape will fail and an error is reported.

#### Page Load Failures

If the chart container doesn't load within the timeout period, the bot saves a debug screenshot (`debug_no_chart.png`) and reports a scrape failure. This usually means the website is slow or down — retrying later via `/force_check` typically resolves it.

#### Missing or Incomplete Table Data

If a member's row has fewer columns than expected (e.g. they joined partway through the month and some day columns are empty), the bot uses `0` for those days rather than crashing. Members with a dash (`-`) for a day are also treated as 0 for that day.

---

### Scrape Failures (Both Sources)

#### Retry Behaviour

When a scrape fails, the bot retries up to **3 times** with exponential backoff (10s → 20s → 40s). If all retries fail, an error embed is posted to the report channel explaining the most likely cause. No reports, bombs, or notifications are sent for that run.

#### Concurrent Scrape Prevention

The bot uses a per-club scrape lock to prevent two scrapes from running at the same time (e.g. a scheduled check overlapping with a `/force_check`). If a lock is already held, the second attempt is silently skipped. Locks automatically expire after **30 minutes** to prevent deadlocks if the bot crashes mid-scrape.
