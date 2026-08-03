# Club Management

These commands are restricted to server admins.

---

## /add_club

Register a new club to track.

| Parameter | Required | Description |
|---|---|---|
| `club_name` | Yes | Name of the club |
| `circle_id` | Yes | Numeric ID from uma.moe — digits only, e.g. `860280110` |
| `daily_quota` | Yes | Fan goal **per quota period**, not necessarily per day |
| `quota_period` | No | `daily`, `weekly`, or `biweekly` (default: daily) |
| `timezone` | No | IANA timezone (default: Europe/Amsterdam) |
| `scrape_time` | No | Time to run daily check in HH:MM (default: 16:00) |
| `scrape_url` | No | ChronoGenesis URL — legacy fallback, only needed without a circle ID |

**Example:**
```
/add_club club_name:MyClub circle_id:860280110 daily_quota:1000000
```

!!! note
    `circle_id` must be numeric — the bot rejects anything else and tells you where to
    find it. With a circle ID set, the club uses the Uma.moe API. Without one it falls
    back to the slower ChronoGenesis scraper, which needs `scrape_url`.

Despite the name, `daily_quota` is the target for one `quota_period` — set
`quota_period:weekly daily_quota:7000000` for a weekly goal of 7M.

---

## /remove_club

Permanently delete a club and all its data. Asks for confirmation before proceeding.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Club to remove |

---

## /activate_club

Reactivate a previously deactivated club.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Club to reactivate |

---

## /list_clubs

View all clubs registered in this server, including their status and basic settings.

No parameters.

---

## /edit_club

Modify settings for an existing club.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Club to edit |
| `circle_id` | No | Update uma.moe circle ID |
| `daily_quota` | No | Update quota amount |
| `quota_period` | No | Update quota period (`daily`, `weekly`, `biweekly`) |
| `scrape_time` | No | Update daily check time (HH:MM) |
| `timezone` | No | Update timezone |
| `bomb_trigger_days` | No | Days behind before bomb activates (default: 3) |
| `bomb_countdown_days` | No | Days to get back on track (default: 7) |
| `bombs_enabled` | No | Enable or disable bomb system (`true`/`false`) |

Only include the parameters you want to change.


---

## /recalculate

Re-fetch the current month from Uma.moe and repair everything that derives from it.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Target club |

Use this whenever the numbers look wrong. Unlike a normal scrape, it treats Uma.moe
as the source of truth and **overwrites** what's stored:

- Rewrites each member's daily fan totals for the whole month
- Fills in any days that are missing
- Re-derives join dates from the fan history
- Recomputes expected fans, deficits and days-behind
- Clears every bomb and re-arms only the ones the corrected history justifies

The report tells you what was actually wrong — how many fan totals were rewritten,
days filled in, and join dates corrected. If nothing was wrong it says so.

Members that Uma.moe no longer returns (they left) are left untouched.

If Uma.moe can't be reached, it still recomputes deficits and bombs from stored
values and flags the result as partial rather than failing outright.

!!! note
    Run `/force_check` afterwards to post a fresh report with the corrected numbers.
