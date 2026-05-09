# Quota System

## How Quotas Work

Each club has a quota — a fan-earning goal that members need to meet. The bot checks progress daily and compares each member's actual fans against the expected cumulative total.

### Deficit & Surplus

```
deficit_surplus = cumulative_fans - expected_fans
```

- **Positive** → member is ahead of quota
- **Negative** → member is behind quota
- Surplus from previous days can cover future deficits

### Quota Periods

| Period | Description |
|---|---|
| `daily` | Quota applies per day (e.g. 1M/day = 30M/month) |
| `weekly` | Quota applies per 7 days (e.g. 5M/week) |
| `biweekly` | Quota applies per 14 days (e.g. 10M/2 weeks) |

Set or change the period with `/edit_club`.

### Mid-Month Quota Changes

You can change the quota at any time with `/quota`. The new quota applies from that day forward — historical data is unaffected and expected fans are recalculated automatically. The monthly info board also updates.

---

## Bomb System

The bomb system warns members who consistently fall behind quota.

### How It Works

1. A member falls behind quota for `bomb_trigger_days` consecutive days (default: 3)
2. A bomb is activated — the member is notified via DM (if linked)
3. The member has `bomb_countdown_days` days (default: 7) to get back on track
4. If they catch up within the countdown, the bomb is defused
5. If they don't, an alert is posted in the alert channel

### Configuration

Configured per club via `/edit_club`:

| Setting | Default | Description |
|---|---|---|
| `bomb_trigger_days` | 3 | Days behind before bomb activates |
| `bomb_countdown_days` | 7 | Days to recover before alert |
| `bombs_enabled` | true | Toggle bomb system on/off |

Use `/bomb_status` to see all active bombs for a club.

---

## Monthly Reset

At the start of each month, the bot automatically detects when fan counts drop significantly (below 50% of the previous total) and triggers a reset, which:

- Clears quota history
- Clears active bombs
- Clears tracking data
- Starts fresh for the new month

You can also trigger it manually with `/reset_month` if something went wrong.

---

## Edge Cases

### Member Lifecycle

#### New Trainer Joins Mid-Month

When a trainer joins mid-month, the bot only knows their current total fan count — it has no data on how many fans they earned before joining. Because of this, **their first day is recorded as `+0`** (no daily gain) and they are not held to any quota requirement for that day.

From the next day onward, the bot can calculate their actual daily gain and quota tracking begins normally.

This appears in the daily report embed as:

```
TrainerName    +0    [total fans]
```

No bomb strike is issued on the first day regardless of their deficit, since the baseline is being established.

#### Trainer Leaves the Club

When a trainer no longer appears in the scraped data, the bot automatically deactivates them. Their quota history is preserved in the database but they are removed from all daily reports and their active bombs are cleared.

If they rejoin later in the same month, the bot reactivates them and treats it as a fresh join — the same first-day `+0` behaviour applies, and their quota expectations are calculated from the return date, not the original join date.

#### Manually Deactivated vs Auto-Deactivated

There are two kinds of deactivation:

- **Auto-deactivated** — triggered when a trainer disappears from scrape data. They will be reactivated automatically if they reappear.
- **Manually deactivated** — triggered via `/deactivate_member`. The bot will **not** reactivate them automatically, even if they show up in future scrapes. Use `/activate_member` to restore them.

---

### Bomb Edge Cases

#### Bomb Countdown Only Decrements Once Per Day

Even if the daily check runs more than once (e.g. after a `/force_check`), the bomb countdown only decrements once per calendar day. There is no risk of a bomb expiring faster than intended from repeated checks.

#### Bomb Urgency Colours

The bomb emoji colour in the daily report reflects how much time is left:

| Colour | Days Remaining |
|---|---|
| 🟡 Yellow | 5 or more |
| 🟠 Orange | 3–4 |
| 🔴 Red | 0–2 |

#### Consecutive Days Resets Each Month

The bomb trigger counts consecutive days behind quota **within the current month only**. If a member ends the previous month behind quota, that does not carry over — they start the new month with a clean streak count.

#### Bombs Are Cleared on Monthly Reset

When the bot detects a monthly reset, all active bombs for the club are deleted. Members must accumulate new consecutive behind-days in the new month before a bomb is re-issued.

#### Bomb Alerts for Already-Removed Members

If a member is manually deactivated while a bomb is still active, the bot will not send a kick alert for them. The bomb record is kept in the database for history but is otherwise ignored.

---

### Quota Period Edge Cases

#### Weekly / Biweekly Periods

When a club uses `weekly` or `biweekly` quota periods, the daily report shows progress within the current period rather than a running monthly total:

```
TrainerName    500K / 700K this week  (-200K overall)
```

The "overall" figure is the full month deficit/surplus. The per-period figure resets at the start of each new period.

#### Multiple Quota Changes on the Same Day

If `/quota` is run multiple times in a single day, the most recently set value takes effect. The monthly info board reflects the final value for that day.

---

### Monthly Reset Edge Cases

#### Automatic Reset Detection

The bot detects a reset when a member's fan count drops to less than 50% of their previous recorded total. This handles the in-game monthly reset without any manual intervention. If a reset is incorrectly triggered, use `/reset_month` to force a clean state.

#### No Previous Data (First Ever Scrape)

On the very first scrape for a new club, there is no prior history to compare against. The bot skips reset detection entirely and adds all members as new. No reset is triggered even if fan counts appear low.

---

### Notifications

#### DMs Disabled or User Not Linked

If a member hasn't linked their Discord account via `/link`, or has Discord DMs disabled, bomb notifications and deficit alerts are simply not delivered to them. The report channel and alert channel still receive all notifications as normal — DM failures do not affect channel output.
