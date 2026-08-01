# Channel Settings

These commands are restricted to server admins.

---

## /set_report_channel

Set the channel where daily quota reports are posted.

| Parameter | Required | Description |
|---|---|---|
| `channel` | Yes | Discord text channel |
| `club` | Yes | Target club |

---

## /set_alert_channel

Set the channel where alerts (bombs, kicks) are posted.

| Parameter | Required | Description |
|---|---|---|
| `channel` | Yes | Discord text channel |
| `club` | Yes | Target club |

---

## /channel_settings

View the current report and alert channel configuration for a club.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Target club |

---

## /post_monthly_info

Post the monthly info board to a channel. This embed automatically updates whenever quota changes are made.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Target club |
| `channel` | No | Channel to post in (defaults to report channel) |

---

## /update_monthly_info

Manually refresh the monthly info board embed.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Target club |

---

## /live_board

Enable or disable the **live board** for a club — a single self-editing message
that tracks the competition day as it happens.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Target club |
| `channel` | No | Channel for the board. **Leave empty to turn it off.** |

**Off by default.** A club is only polled once you set a channel, so this costs
nothing for clubs that don't use it.

### How it works

The board follows Uma.moe's clock, not your report time. A competition day opens
at **15:00 UTC** (00:00 JST):

1. A new message is posted when the day opens
2. It's edited through the day as fresh figures arrive (roughly hourly)
3. When the day closes, it gets one **final edit** with the finished numbers
4. A new message is posted for the next day

So each message ends up as an exact record of one competition day. Edits don't
notify anyone, so the board updating all day won't spam the channel.

### What it shows

Fans earned so far today, live club rank and how it's moved, month total, and the
members contributing today.

### Important

The live board is **display only**. Numbers on it are not final — the day is still
running. Your **daily report is unchanged**: it still posts at the club's own
scrape time, still uses finalized data, and remains the only thing that drives
quota tracking, bombs and DMs.


---

## /live_refresh

Force a club's live board to update immediately, instead of waiting for its slot.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | Target club |

Boards normally refresh on a fixed minute of the hour, chosen per club so that many
clubs spread their API calls across the hour rather than firing together. That means
a change can take up to an hour to show. This bypasses the wait for one club.

The reply reports which array slot was read and which competition day it maps to,
which is the quickest way to confirm the boundary logic is behaving:

```
Reading
2026-07 slot 31
JST day 2026-08-01 → competition day 2026-07-31
```

Handy when verifying a change or after a restart. It costs one Uma.moe call.
