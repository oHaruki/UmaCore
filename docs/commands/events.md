# Event Feed

Announces new Uma Musume **Global** content in a channel as it goes live —
gacha banners, story events, Champions Meeting, mission campaigns and more —
with the banner art and the start/end times rendered in each reader's own
timezone.

Events come from the official Global notices, read through
[umapyoi.net](https://umapyoi.net). Banners and mission sets come from
[uma.moe](https://uma.moe), with their end times corrected from
[GameTora](https://gametora.com/umamusume). UmaCore polls every 15 minutes and
posts anything it hasn't posted before.

---

## /set_events_channel

Turn the feed on for this server. Requires **Manage Server**.

| Parameter | Required | Description |
|---|---|---|
| `channel` | Yes | Where announcements are posted |
| `ping_role` | No | Role mentioned with each announcement |

The bot posts everything currently running into the channel straight away —
one embed per banner or event, with its art — both as a starting point and as
proof it can actually post there. If it can't, nothing is saved and you're told
which permission is missing.

One channel per server.

---

## /disable_events_channel

Stop announcements for this server. Requires **Manage Server**.

---

## /events

What's running right now — banners, mission events, story events — one embed
each, with the banner art. Anyone can use it.

---

## /events_status

Where the feed posts, when it last checked its sources, how many events are on
record, and which source failed if one is down. Requires **Manage Server**.

---

## /events_preview

Shows you (and only you) the announcement embed for the most recent live event,
so you can see the format before switching the feed on. Requires **Manage
Server**.

---

## What gets announced

| Type | Source | Notes |
|---|---|---|
| Character, support card and paid banners | uma.moe | End time corrected from GameTora |
| Mission sets | uma.moe | End time corrected from GameTora; mission count shown |
| Story events | Official notices | Event SSR shown; ends when the event does, not when reward collection does |
| Champions Meeting | Official notices | Runs from league selection to the final; race conditions shown |
| Legend Race | Official notices | Covers both halves; race conditions shown |
| Events (Aim for the Stars! Dream Team, Trainer Aptitude Test, ...) | Official notices | |
| Campaigns (celebrations, Bonus Star Pieces, Transfer Requests, ...) | Official notices | |
| New Career scenarios | Official notices | No end date |

---

### Why three sources

The official notices are the only source whose dates are the game's own. Checked
against them over Aug–Oct 2026, uma.moe ended every Champions Meeting before its
final, ended story events and Legend Races early, and left several campaigns out
entirely. umapyoi mirrors the notices, so the feed reads the dates straight from
them.

The notices are prose, but every period is written in one fixed form under a
heading — `10:00 p.m., Sep 28–9:59 p.m., Oct 12, 2026 (UTC)` — so they can be
read reliably. A "coming soon" notice and the matching "is here" notice are
recognised as one event, as are Champions Meeting's league-selection and
race-day notices.

Banners stay on uma.moe because it carries the pickups and the banner ids the
art is built from. uma.moe derives their end times from a duration rather than
reading them, which has run one daily rollover early since April 2026, so the
end is taken from GameTora, joined on the start time. Mission sets are corrected
the same way, joined on the mission id.

Each source fails on its own. If umapyoi is down the banners still show; if
uma.moe is down the events still show; `/events_status` names whichever is
missing.

### Artwork and links

Events use the official English notice banner and link to the notice on
umapyoi. Banners and mission sets use GameTora's English art and link there,
with uma.moe's Japanese art as a fallback when GameTora doesn't have that id.

---

## What does *not* get announced

These rules keep the channel from being a firehose. All are deliberate:

- **The first run after enabling the feed posts nothing.** Everything already
  live is recorded silently, because "we have never checked before" is not the
  same as "this is new". Use `/events` to see the current state.
- **Entries backdated by more than 3 days** are recorded without posting. GameTora
  sometimes adds an event that started weeks ago; that's a data edit, not news.
- **Permanent mission sets older than 21 days** (the launch tutorials, the Tracen
  Specials) never count as live.
- **Unconfirmed banners.** uma.moe forecasts when JP content will reach Global.
  Those dates move and the same banner can appear several times at different
  dates, so only confirmed entries are announced.
- **Notices that aren't events** — scout announcements (banners come from
  uma.moe), known issues, maintenance, WebStore items and "has ended" posts.

Because the record of what's been announced is global to the bot, a server that
enables the feed today starts from the next new event rather than replaying
history into a fresh channel. Announcement keys are namespaced per source, so
changing data source seeds the new source silently instead of re-announcing
everything currently live.

---

## Configuration

Both are optional environment variables with working defaults.

| Variable | Default | Description |
|---|---|---|
| `EVENTS_POLL_MINUTES` | `15` | How often the sources are checked |
| `EVENTS_MAX_BACKFILL_SEC` | `259200` | How far past its start an entry can be and still be announced |
