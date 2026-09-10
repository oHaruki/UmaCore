# Event Feed

Announces new Uma Musume **Global** content in a channel as it goes live —
gacha banners, story events, Champions Meeting, mission campaigns and more —
with the banner art and the start/end times rendered in each reader's own
timezone.

Data comes from [uma.moe](https://uma.moe), with gacha end times corrected from
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

Where the feed posts, when it last checked GameTora, and how many events are on
record. Requires **Manage Server**.

---

## /events_preview

Shows you (and only you) the announcement embed for the most recent live event,
so you can see the format before switching the feed on. Requires **Manage
Server**.

---

## What gets announced

Every confirmed Global event uma.moe tracks:

| Type | Notes |
|---|---|
| Character banners | End time corrected from GameTora |
| Support card banners | End time corrected from GameTora |
| Paid banners | End time corrected from GameTora |
| Story events | |
| Champions Meeting | Race conditions shown in the embed |
| Legend Race | |
| Mission campaigns | Mission count shown in the embed |
| New scenarios | |
| Racing Carnival, League of Heroes, Masters Challenge, Strongest Team, Trainer Aptitude Test, Factor Research | Announced when they reach Global |

A type uma.moe adds later is announced too, labelled from its own name, rather
than being dropped for being unrecognised.

---

### Why two sources

uma.moe covers Global content GameTora has no data for at all — Champions
Meeting, story events, scenario releases and the smaller recurring events.

Its one weak spot is gacha end times, which it derives from a duration field
rather than reading from the game. Since April 2026 that has run exactly one
daily rollover early on every Global banner (23 of 61 matched historically).
GameTora carries the real value and the start times agree across both sources
on all 61, so the start is used to join them and the end is taken from
GameTora. Campaign end times need no such help — those match on 49 of 52.

If GameTora is unreachable the feed still works; banners just carry uma.moe's
derived end, which runs about a day early.

### Artwork and links

uma.moe hosts the Japanese artwork for everything. GameTora hosts the English
artwork Global players actually see in game, but only for banners and mission
campaigns — and uma.moe supplies the ids needed to build those URLs.

So each announcement takes its art and its link from the same site: GameTora for
banners and campaigns, uma.moe for everything else. The Japanese art is kept as a
fallback in case GameTora doesn't have that particular id.

Story events stay on uma.moe's art even though GameTora hosts English story
banners, because GameTora's story ids don't map onto uma.moe's — guessing one
would show a different event's banner.

---

## What does *not* get announced

Three rules keep the channel from being a firehose. All three are deliberate:

- **The first run after enabling the feed posts nothing.** Everything already
  live is recorded silently, because "we have never checked before" is not the
  same as "this is new". Use `/events` to see the current state.
- **Entries backdated by more than 3 days** are recorded without posting. GameTora
  sometimes adds an event that started weeks ago; that's a data edit, not news.
- **Permanent mission sets older than 21 days** (the launch tutorials, the Tracen
  Specials) never count as live.
- **Unconfirmed entries.** uma.moe forecasts when JP content will reach Global.
  Those dates move and the same campaign can appear several times at different
  dates, so only confirmed entries are announced.

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
| `EVENTS_POLL_MINUTES` | `15` | How often GameTora is checked |
| `EVENTS_MAX_BACKFILL_SEC` | `259200` | How far past its start an entry can be and still be announced |
