"""Tests for the uma.moe event source and the merged feed.

uma.moe supplies banners and the game's mission sets; everything else comes
from the official notices (see test_umapyoi_events). uma.moe derives end times
rather than reading them, so banners and mission sets are corrected from
GameTora, and most of what is pinned here is that division of labour.

No service is contacted: every client is a fake over fixed payloads.
"""
import asyncio
import time

import pytest

from events.client import GameEvent, PERMANENT_CUTOFF
from events.feed import EventFeed
from events.umamoe_client import UmaMoeEventsClient, _clean, image_url

NOW = int(time.time())
DAY = 24 * 3600


def run(coro):
    return asyncio.run(coro)


def iso(offset_days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(NOW + offset_days * DAY))


def raw(**over) -> dict:
    base = {
        "id": "char-banner-1",
        "type": "character_banner",
        "title": "Tamamo Cross + 1 more",
        "global_release_date": iso(-1),
        "estimated_end_date": iso(9),
        "is_confirmed": True,
        "image_path": "assets/images/character/banner/2022_30124.webp",
        "description": None,
        "related_characters": ["Tamamo Cross", "Inari One"],
    }
    base.update(over)
    return base


class FakeUmaMoe(UmaMoeEventsClient):
    """Real normalisation, fixed payload."""

    def __init__(self, events):
        super().__init__()
        self._payload = {"events": events}

    async def _fetch_timeline(self):
        return self._payload

    async def close(self):
        pass


class FakeGametora:
    def __init__(self, ends=None, boom=False, mission_ends=None):
        self.ends = ends or {}
        self.mission_ends = mission_ends or {}
        self.boom = boom

    async def gacha_end_times(self):
        if self.boom:
            raise RuntimeError("gametora down")
        return self.ends

    async def mission_end_times(self):
        if self.boom:
            raise RuntimeError("gametora down")
        return self.mission_ends

    async def image_exists(self, url):
        return True

    async def close(self):
        pass


class FakeNews:
    def __init__(self, events=(), boom=False):
        self.events = list(events)
        self.boom = boom

    async def fetch_events(self):
        if self.boom:
            raise RuntimeError("umapyoi down")
        return list(self.events)

    async def close(self):
        pass


class DownUmaMoe(FakeUmaMoe):
    async def _fetch_timeline(self):
        raise RuntimeError("uma.moe down")


def feed(events, ends=None, boom=False, mission_ends=None, news=(),
         news_down=False) -> EventFeed:
    return EventFeed(FakeUmaMoe(events), FakeGametora(ends, boom, mission_ends),
                     FakeNews(news, news_down))


def umamoe(events) -> list[GameEvent]:
    """uma.moe's own normalisation, before the feed picks from it."""
    return run(FakeUmaMoe(events).fetch_events())


def news_event(key="news:story:x:1", kind="story", start=-1, end=9) -> GameEvent:
    return GameEvent(key=key, kind=kind, name="From the notices",
                     start=NOW + start * DAY, end=NOW + end * DAY,
                     image=None, url=f"https://umapyoi.net/en/news/{key}")


# ---------------------------------------------------------------- normalising

def test_confirmed_events_are_kept():
    events = umamoe([raw()])

    assert [e.key for e in events] == ["umamoe:char-banner-1"]
    assert events[0].kind == "gacha_char"


def test_predicted_events_are_dropped():
    """uma.moe forecasts when JP content reaches Global. Those move, they
    duplicate, and announcing one as scheduled would be worse than silence."""
    events = umamoe([raw(is_confirmed=False)])

    assert events == []


def test_every_uma_moe_type_maps_to_a_kind():
    types = ["character_banner", "support_card_banner", "paid_banner", "campaign",
             "story_event", "champions_meeting", "legend_race", "scenario_release",
             "factor_research", "league_of_heroes", "masters_challenge",
             "racing_carnival", "strongest_team", "trainer_skills_test"]
    payload = [raw(id=f"e{i}", type=t) for i, t in enumerate(types)]

    kinds = [e.kind for e in umamoe(payload)]

    assert len(kinds) == len(types)
    assert "character_banner" not in kinds  # mapped, not passed through raw
    assert {"gacha_char", "story", "champions_meeting", "legend_race"} <= set(kinds)


def test_an_unmapped_type_is_still_announced():
    """A type uma.moe adds later should show up rather than vanish silently."""
    events = umamoe([raw(type="brand_new_event")])

    assert [e.kind for e in events] == ["brand_new_event"]


def test_permanent_content_has_no_end():
    far_future = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(PERMANENT_CUTOFF + DAY))
    events = umamoe([raw(estimated_end_date=far_future)])

    assert events[0].end is None


def test_finished_events_are_dropped():
    events = umamoe([raw(global_release_date=iso(-30),
                           estimated_end_date=iso(-2))])

    assert events == []


def test_pickups_become_the_detail_line_when_there_is_no_description():
    events = umamoe([raw()])

    assert events[0].detail == "Tamamo Cross, Inari One"


def test_description_html_is_flattened():
    """Champions Meeting puts the race conditions in the description as HTML."""
    assert _clean("Tokyo - Turf<br>1600m - Mile<br>Firm") == "Tokyo - Turf · 1600m - Mile · Firm"


def test_long_news_copy_is_truncated():
    body = _clean("word " * 200)

    assert len(body) <= 180
    assert body.endswith("…")


def test_image_paths_become_absolute_urls():
    assert image_url("assets/images/campaign/1.webp") == \
        "https://uma.moe/assets/images/campaign/1.webp"
    assert image_url(None) is None


# -------------------------------------------------------------------- merging

def test_gacha_end_times_are_corrected_from_gametora():
    """uma.moe derives the end from a duration field; since April 2026 that has
    run exactly one rollover early on every Global banner. GameTora has the real
    value, and the start time is what joins them."""
    event = raw()
    events = run(feed([event]).fetch_events())
    derived_end = events[0].end

    corrected = run(feed([event], ends={events[0].start: derived_end + DAY}).fetch_events())

    assert corrected[0].end == derived_end + DAY


def test_mission_sets_are_corrected_by_mission_id():
    """uma.moe stretches the first set of each campaign part to the end of the
    whole part. The mission id is in both sources, so the join is exact."""
    mission = raw(type="campaign", id="campaign-198", estimated_end_date=iso(30))

    events = run(feed([mission], mission_ends={198: NOW + 2 * DAY}).fetch_events())

    assert events[0].end == NOW + 2 * DAY


def test_a_mission_set_corrected_into_the_past_is_dropped():
    mission = raw(type="campaign", id="campaign-198", global_release_date=iso(-9),
                  estimated_end_date=iso(30))

    assert run(feed([mission], mission_ends={198: NOW - DAY}).fetch_events()) == []


def test_a_gacha_start_collision_does_not_rewrite_a_mission_set():
    mission = raw(type="campaign", id="campaign-200")
    events = run(feed([mission]).fetch_events())
    original_end = events[0].end

    merged = run(feed([mission], ends={events[0].start: original_end + DAY}).fetch_events())

    assert merged[0].end == original_end


def test_a_banner_on_its_last_day_survives_uma_moe_ending_it_early():
    """uma.moe's derived end can pass while the banner is still up; the entry
    must reach the correction rather than be dropped before it."""
    banner = raw(global_release_date=iso(-9), estimated_end_date=iso(-0.5))
    start = umamoe([raw(global_release_date=iso(-9))])[0].start

    events = run(feed([banner], ends={start: NOW + 0.5 * DAY}).fetch_events())

    assert [e.end for e in events] == [NOW + 0.5 * DAY]


def test_a_banner_gametora_does_not_know_keeps_its_own_end():
    events = run(feed([raw()], ends={NOW - 999: 1}).fetch_events())

    assert events[0].end is not None


def test_gametora_being_down_does_not_break_the_feed():
    """The correction is decoration. Losing it costs a day of accuracy on gacha
    countdowns; losing the feed costs every announcement."""
    events = run(feed([raw()], boom=True).fetch_events())

    assert [e.key for e in events] == ["umamoe:char-banner-1"]


def test_uma_moe_events_the_notices_cover_are_left_out():
    """Story events, Champions Meeting, Legend Races and news campaigns come
    from the official notices; uma.moe's copies drift from them."""
    payload = [raw(id="banner"),
               raw(id="campaign-201", type="campaign"),
               raw(id="story-event-11", type="story_event"),
               raw(id="champions-meeting-19", type="champions_meeting"),
               raw(id="legend-race-15", type="legend_race"),
               raw(id="news-event-campaign-1003", type="campaign")]

    keys = [e.key for e in run(feed(payload).fetch_events())]

    assert sorted(keys) == ["umamoe:banner", "umamoe:campaign-201"]


def test_notice_events_join_the_feed():
    events = run(feed([raw()], news=[news_event()]).fetch_events())

    assert {e.key for e in events} == {"umamoe:char-banner-1", "news:story:x:1"}


def test_umapyoi_being_down_still_leaves_the_banners():
    f = feed([raw()], news_down=True)

    events = run(f.fetch_events())

    assert [e.key for e in events] == ["umamoe:char-banner-1"]
    assert f.problems and "umapyoi" in f.problems[0]


def test_uma_moe_being_down_still_leaves_the_notices():
    f = EventFeed(DownUmaMoe([]), FakeGametora(), FakeNews([news_event()]))

    assert [e.key for e in run(f.fetch_events())] == ["news:story:x:1"]


def test_every_source_down_is_an_error():
    f = EventFeed(DownUmaMoe([]), FakeGametora(), FakeNews(boom=True))

    with pytest.raises(RuntimeError):
        run(f.fetch_events())


def test_a_recovered_source_clears_the_problem():
    news = FakeNews(boom=True)
    f = EventFeed(FakeUmaMoe([raw()]), FakeGametora(), news)
    run(f.fetch_events())

    news.boom = False
    run(f.fetch_events())

    assert f.problems == []


def test_events_come_back_in_start_order():
    payload = [raw(id="late", global_release_date=iso(-1)),
               raw(id="early", global_release_date=iso(-5))]

    events = run(feed(payload, news=[news_event(start=-3)]).fetch_events())

    assert [e.key for e in events] == ["umamoe:early", "news:story:x:1", "umamoe:late"]


def test_each_event_gets_a_distinct_url():
    """Discord merges same-message embeds sharing a url into one gallery."""
    payload = [raw(id="a"), raw(id="b"), raw(id="campaign-5", type="campaign")]

    urls = [e.url for e in run(feed(payload, news=[news_event()]).fetch_events())]

    assert len(set(urls)) == 4


# ------------------------------------------------------------------- artwork

def test_banners_use_gametora_english_art():
    """uma.moe hosts the Japanese banner; Global players see the English one.
    uma.moe's gacha_id is what lets us build the English URL."""
    e = umamoe([raw(gacha_id=30124)])[0]

    assert e.image == "https://gametora.com/images/umamusume/en/gacha/img_bnr_gacha_30124.png"
    assert "gametora.com" in e.url


def test_campaigns_use_gametora_english_art():
    e = umamoe([raw(id="campaign-198", type="campaign")])[0]

    assert e.image.endswith("/en/missions/tex_campaign_mission_logo_00198.png")
    assert "gametora.com" in e.url


def test_the_japanese_art_is_kept_as_a_fallback():
    """GameTora doesn't have every id; better the Japanese banner than none."""
    e = umamoe([raw(gacha_id=30124)])[0]

    assert e.image_alt == "https://uma.moe/assets/images/character/banner/2022_30124.webp"


def test_story_events_stay_on_uma_moe():
    """GameTora hosts English story art, but its ids don't map to uma.moe's
    slugs — guessing one would show a different event's banner."""
    e = umamoe([raw(id="story-event-10_intertwined", type="story_event",
                      image_path="assets/images/story/10_intertwined.webp")])[0]

    assert e.image.startswith("https://uma.moe/")
    assert e.url.startswith("https://uma.moe/timeline#")


def test_news_derived_campaigns_stay_on_uma_moe():
    """Only the ``campaign-<mission id>`` form maps onto GameTora."""
    e = umamoe([raw(id="news-event-campaign-994", type="campaign")])[0]

    assert e.image.startswith("https://uma.moe/")
    assert "uma.moe/timeline" in e.url


def test_art_and_link_never_come_from_different_sites():
    payload = [raw(gacha_id=1, id="b1"),
               raw(id="campaign-2", type="campaign"),
               raw(id="story-event-x", type="story_event")]

    for e in umamoe(payload):
        assert ("gametora.com" in e.image) == ("gametora.com" in e.url)
