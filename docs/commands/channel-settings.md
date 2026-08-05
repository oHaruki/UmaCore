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

### Members who just joined

Uma.moe records one figure per member per day and nothing about *when* someone
joined your club. On a new member's first day that figure therefore mixes fans
they earned before arriving with fans they earned after, and there's no way to
separate the two.

So a member's first day isn't counted. They're listed under **🆕 Joined Today**
rather than among the members who haven't raced, and they contribute nothing to
the club's "Fans today" total. They start counting normally the next day.

After that their running total is measured from the day they arrived, and the
board says so — `+2.77M (4.66M since Aug 2)` instead of `(4.66M total)` — because
that figure covers a few days rather than the whole month and shouldn't be read
next to a full-month total as though the two were comparable.

### It pins the club's scrape time

Turning the live board on moves that club's **daily report** to the competition day
close (15:00 UTC), whatever `scrape_time` is set to. The board finalises the day at
that moment, so scraping earlier would leave the report a day behind the board
sitting next to it.

In local terms that's 17:00 in summer / 16:00 in winter for a European club,
00:00 for a Japanese one, 11:00 US Eastern. It follows DST automatically.

Turn the board off and the club goes back to its configured `scrape_time`.

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
