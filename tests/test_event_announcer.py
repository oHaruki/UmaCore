"""Tests for the Uma Musume event announcer — mainly what it decides *not* to
announce (first run, backdated entries, stale permanents, broken channels).
GameTora is never contacted; the client is a fake returning fixed events.
"""
import asyncio
import time
from dataclasses import replace

import pytest

from events import announcer as announcer_mod
from events.announcer import EventAnnouncer
from events.client import GameEvent, PERMANENT_WINDOW

NOW = int(time.time())
HOUR = 3600
DAY = 24 * HOUR


def run(coro):
    return asyncio.run(coro)


def event(key, start=-HOUR, end=7 * DAY, kind="mission", image=None) -> GameEvent:
    return GameEvent(
        key=key,
        kind=kind,
        name=f"Event {key}",
        start=NOW + start,
        end=None if end is None else NOW + end,
        image=image,
        url="https://gametora.com/umamusume/missions",
    )


class FakeClient:
    def __init__(self, events, missing_images=()):
        self.events = events
        self.missing_images = set(missing_images)

    async def fetch_events(self):
        return list(self.events)

    async def image_exists(self, url):
        return url not in self.missing_images


class FakeChannel:
    def __init__(self, channel_id, raises=None):
        self.id = channel_id
        self.sent = []
        self.raises = raises

    async def send(self, **kwargs):
        if self.raises:
            raise self.raises
        self.sent.append(kwargs)


class FakeBot:
    def __init__(self, *channels):
        self.channels = {c.id: c for c in channels}

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)


class FakeStore:
    """Stands in for both DB-backed classes in events.store."""

    def __init__(self, targets=(), announced=()):
        self.targets = list(targets)
        self.announced = dict.fromkeys(announced)

    # AnnouncedEvents
    async def keys(self):
        return set(self.announced)

    async def mark(self, key, kind, name, start_ts, end_ts):
        self.announced.setdefault(key, (kind, name, start_ts, end_ts))

    async def mark_many(self, events):
        for e in events:
            await self.mark(e.key, e.kind, e.name, e.start, e.end)

    # EventFeedChannel
    async def all(self):
        return list(self.targets)


class Target:
    def __init__(self, guild_id, channel_id, ping_role_id=None):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.ping_role_id = ping_role_id


@pytest.fixture
def wire(monkeypatch):
    """Build an announcer with the store swapped for an in-memory one."""
    def _wire(events, targets=(), announced=(), missing_images=(), bot=None):
        store = FakeStore(targets, announced)
        monkeypatch.setattr(announcer_mod, "AnnouncedEvents", store)
        monkeypatch.setattr(announcer_mod, "EventFeedChannel", store)
        cog = EventAnnouncer(bot or FakeBot(), FakeClient(events, missing_images))
        return cog, store
    return _wire


def test_first_run_seeds_without_posting(wire):
    """An empty record means we have never looked, not that nothing has happened.

    Announcing on that would empty a month of live banners into the channel the
    moment the feature is switched on.
    """
    channel = FakeChannel(10)
    cog, store = wire(
        [event("mission:1"), event("gacha:2", kind="gacha_char")],
        targets=[Target(1, 10)],
        bot=FakeBot(channel),
    )

    posted = run(cog.run_once())

    assert posted == []
    assert channel.sent == []
    assert set(store.announced) == {"mission:1", "gacha:2"}


def test_new_event_is_announced_once(wire):
    channel = FakeChannel(10)
    events = [event("mission:1"), event("mission:2")]
    cog, store = wire(events, targets=[Target(1, 10)],
                      announced=["mission:1"], bot=FakeBot(channel))

    posted = run(cog.run_once())
    assert [e.key for e in posted] == ["mission:2"]
    assert len(channel.sent) == 1

    # Second poll over the same feed says nothing more.
    assert run(cog.run_once()) == []
    assert len(channel.sent) == 1


def test_upcoming_event_waits_until_it_starts(wire):
    channel = FakeChannel(10)
    upcoming = event("mission:2", start=+2 * DAY)
    cog, store = wire([event("mission:1"), upcoming], targets=[Target(1, 10)],
                      announced=["mission:1"], bot=FakeBot(channel))

    assert run(cog.run_once()) == []
    assert channel.sent == []
    assert "mission:2" not in store.announced

    # Once it goes live it is announced, from the same feed.
    cog.client.events = [event("mission:1"), replace(upcoming, start=NOW - 60)]
    posted = run(cog.run_once())
    assert [e.key for e in posted] == ["mission:2"]


def test_backdated_entry_is_recorded_not_announced(wire):
    """A GameTora edit that adds a month-old entry is not news."""
    channel = FakeChannel(10)
    cog, store = wire(
        [event("mission:1"), event("mission:old", start=-30 * DAY)],
        targets=[Target(1, 10)],
        announced=["mission:1"],
        bot=FakeBot(channel),
    )

    assert run(cog.run_once()) == []
    assert channel.sent == []
    assert "mission:old" in store.announced


def test_stale_permanent_missions_are_never_live(wire):
    """Launch tutorials carry an end date in 2049 and would otherwise run forever."""
    channel = FakeChannel(10)
    cog, store = wire(
        [event("mission:1"),
         event("mission:tutorial", start=-(PERMANENT_WINDOW + DAY), end=None)],
        targets=[Target(1, 10)],
        announced=["mission:1"],
        bot=FakeBot(channel),
    )

    assert run(cog.run_once()) == []
    assert channel.sent == []
    assert "mission:tutorial" not in store.announced


def test_missing_banner_art_drops_the_image(wire):
    """A 404 image renders as a blank strip, which is worse than no image."""
    channel = FakeChannel(10)
    art = "https://gametora.com/images/umamusume/en/gacha/img_bnr_gacha_9999.png"
    cog, _ = wire([event("gacha:9999", kind="gacha_char", image=art)],
                  targets=[Target(1, 10)], announced=["seed"],
                  missing_images=[art], bot=FakeBot(channel))

    run(cog.run_once())

    assert "image" not in channel.sent[0]["embed"].to_dict()


def test_ping_role_is_mentioned(wire):
    channel = FakeChannel(10)
    cog, _ = wire([event("mission:2")],
                  targets=[Target(1, 10, ping_role_id=777)],
                  announced=["seed"], bot=FakeBot(channel))

    run(cog.run_once())

    assert channel.sent[0]["content"] == "<@&777>"


def test_one_broken_guild_does_not_block_the_others(wire):
    """A guild that revoked Send Messages must not cost every other guild the
    announcement, nor leave the event unmarked — an unmarked event is re-posted
    on every poll for as long as it runs."""
    import discord

    forbidden = discord.Forbidden.__new__(discord.Forbidden)
    forbidden.status, forbidden.code, forbidden.text = 403, 50013, "Missing Permissions"
    Exception.__init__(forbidden, "Missing Permissions")

    broken = FakeChannel(10, raises=forbidden)
    working = FakeChannel(20)
    cog, store = wire([event("mission:2")],
                      targets=[Target(1, 10), Target(2, 20)],
                      announced=["seed"],
                      bot=FakeBot(broken, working))

    posted = run(cog.run_once())

    assert [e.key for e in posted] == ["mission:2"]
    assert len(working.sent) == 1
    assert "mission:2" in store.announced


def test_no_subscribers_still_records(wire):
    """Otherwise the first channel to subscribe gets everything since deploy."""
    cog, store = wire([event("mission:2")], targets=[], announced=["seed"])

    posted = run(cog.run_once())

    assert [e.key for e in posted] == ["mission:2"]
    assert "mission:2" in store.announced
