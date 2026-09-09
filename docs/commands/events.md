# Event Feed

Announces new Uma Musume **Global** content in a channel as it goes live —
gacha banners, mission events and story events — with the banner art and the
start/end times rendered in each reader's own timezone.

Data comes from [GameTora](https://gametora.com/umamusume). UmaCore polls it
every 15 minutes and posts anything it hasn't posted before.

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

Where the feed posts, when it last checked GameTora, and how many events are on
record. Requires **Manage Server**.

---

## /events_preview

Shows you (and only you) the announcement embed for the most recent live event,
so you can see the format before switching the feed on. Requires **Manage
Server**.

---

## What gets announced

| Type | Source | State |
|---|---|---|
| Character banners | `en/gacha/char-standard` | Current |
| Support card banners | `en/gacha/support-standard` | Current |
| Mission events | `en/missions/limited` | Current |
| Story events | `en/storyevents` | GameTora's Global data lags behind — see below |
| Champions Meeting | `en/events/champions-meeting` | Current |
| Legend Race | `en/events/legend-race` | Current |

Banners and mission events (celebration missions, anniversary missions, the
Tracen Specials) are the two feeds GameTora keeps up to date for Global, and
they're what you'll actually see day to day.

**Story events are wired up but quiet.** GameTora's `en/storyevents` file has not
been extended past February 2026, so Global story events currently produce no
announcements. Nothing needs changing on this end — if GameTora resumes updating
it, they start appearing.

---

## What does *not* get announced

Three rules keep the channel from being a firehose. All three are deliberate:

- **The first run after enabling the feed posts nothing.** Everything already
  live is recorded silently, because "we have never checked before" is not the
  same as "this is new". Use `/events` to see the current state.
- **Entries backdated by more than 3 days** are recorded without posting. GameTora
  sometimes adds an event that started weeks ago; that's a data edit, not news.
- **Permanent mission sets older than 21 days** (the launch tutorials, the Tracen
  Specials) never count as live. Same 21-day rule GameTora's own front page uses.

Because the record of what's been announced is global to the bot, a server that
enables the feed today starts from the next new event rather than replaying
history into a fresh channel.

---

## Configuration

Both are optional environment variables with working defaults.

| Variable | Default | Description |
|---|---|---|
| `EVENTS_POLL_MINUTES` | `15` | How often GameTora is checked |
| `EVENTS_MAX_BACKFILL_SEC` | `259200` | How far past its start an entry can be and still be announced |
