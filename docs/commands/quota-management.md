# Quota Management

These commands are restricted to server admins.

---

## /quota

Set a club's quota for one quota period: per day for daily clubs, per week for weekly ones, and per 2 weeks for biweekly ones. Takes effect from today onwards.

A biweekly club that wants about 650K a day sets `9000000`, not `650000`.

| Parameter | Required | Description |
|---|---|---|
| `amount` | Yes | Fans for the whole quota period (integer) |
| `club` | Yes | Target club |

**Example:**
```
/quota amount:1500000 club:MyClub
```

---

## /quota_history

View all quota changes made this month for a club.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Target club |

---

## /delete_quota

Remove a specific quota entry from history. Useful for correcting mistakes.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Target club |
| `date` | Yes | Date of the entry (YYYY-MM-DD) |
| `amount` | Yes | Quota amount to remove |

---

## /force_check

Manually trigger a daily check immediately. Scrapes current data and posts a report to the report channel.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Club to check |

---

## /recalculate

Recalculate days-behind counts and bomb statuses based on existing data, without clearing anything.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Club to recalculate |

---

## /reset_month

Manually trigger a monthly reset. Clears quota history, bombs, and tracking data for the new month.

| Parameter | Required | Description |
|---|---|---|
| `club` | No | Specific club to reset (omit to reset all) |
| `skip_confirmation` | No | Skip the confirmation prompt |

> The bot normally handles monthly resets automatically when it detects fan counts have dropped significantly. Only use this if something went wrong.
