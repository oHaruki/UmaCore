"""Tests for the uma.moe event source and the merged feed.

uma.moe is the source of record because it carries Global content GameTora has
no data for at all. It has one weak spot — it derives gacha end times from a
duration rather than reading them — so those are corrected from GameTora, and
most of what is pinned here is that division of labour.

Neither service is contacted: both clients are fakes over fixed payloads.
"""
import asyncio
import time

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
    def __init__(self, ends=None, boom=False):
        self.ends = ends or {}
        self.boom = boom

    async def gacha_end_times(self):
        if self.boom:
            raise RuntimeError("gametora down")
        return self.ends

    async def image_exists(self, url):
        return True

    async def close(self):
        pass


def feed(events, ends=None, boom=False) -> EventFeed:
    return EventFeed(FakeUmaMoe(events), FakeGametora(ends, boom))


# ---------------------------------------------------------------- normalising

def test_confirmed_events_are_kept():
    events = run(feed([raw()]).fetch_events())

    assert [e.key for e in events] == ["umamoe:char-banner-1"]
    assert events[0].kind == "gacha_char"


def test_predicted_events_are_dropped():
    """uma.moe forecasts when JP content reaches Global. Those move, they
    duplicate, and announcing one as scheduled would be worse than silence."""
    events = run(feed([raw(is_confirmed=False)]).fetch_events())

    assert events == []


def test_every_uma_moe_type_maps_to_a_kind():
    types = ["character_banner", "support_card_banner", "paid_banner", "campaign",
             "story_event", "champions_meeting", "legend_race", "scenario_release",
             "factor_research", "league_of_heroes", "masters_challenge",
             "racing_carnival", "strongest_team", "trainer_skills_test"]
    payload = [raw(id=f"e{i}", type=t) for i, t in enumerate(types)]

    kinds = [e.kind for e in run(feed(payload).fetch_events())]

    assert len(kinds) == len(types)
    assert "character_banner" not in kinds  # mapped, not passed through raw
    assert {"gacha_char", "story", "champions_meeting", "legend_race"} <= set(kinds)


def test_an_unmapped_type_is_still_announced():
    """A type uma.moe adds later should show up rather than vanish silently."""
    events = run(feed([raw(type="brand_new_event")]).fetch_events())

    assert [e.kind for e in events] == ["brand_new_event"]


def test_permanent_content_has_no_end():
    far_future = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                               time.gmtime(PERMANENT_CUTOFF + DAY))
    events = run(feed([raw(estimated_end_date=far_future)]).fetch_events())

    assert events[0].end is None


def test_finished_events_are_dropped():
    events = run(feed([raw(global_release_date=iso(-30),
                           estimated_end_date=iso(-2))]).fetch_events())

    assert events == []


def test_pickups_become_the_detail_line_when_there_is_no_description():
    events = run(feed([raw()]).fetch_events())

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


def test_only_gacha_is_corrected():
    """uma.moe's campaign end times match GameTora on 49 of 52, so nothing else
    is second-guessed — a start-time collision must not rewrite a campaign."""
    campaign = raw(type="campaign", id="c1")
    events = run(feed([campaign]).fetch_events())
    original_end = events[0].end

    merged = run(feed([campaign], ends={events[0].start: original_end + DAY}).fetch_events())

    assert merged[0].end == original_end


def test_a_banner_gametora_does_not_know_keeps_its_own_end():
    events = run(feed([raw()], ends={NOW - 999: 1}).fetch_events())

    assert events[0].end is not None


def test_gametora_being_down_does_not_break_the_feed():
    """The correction is decoration. Losing it costs a day of accuracy on gacha
    countdowns; losing the feed costs every announcement."""
    events = run(feed([raw()], boom=True).fetch_events())

    assert [e.key for e in events] == ["umamoe:char-banner-1"]


def test_events_come_back_in_start_order():
    payload = [raw(id="late", global_release_date=iso(-1)),
               raw(id="early", global_release_date=iso(-5))]

    events = run(feed(payload).fetch_events())

    assert [e.key for e in events] == ["umamoe:early", "umamoe:late"]


def test_each_event_gets_a_distinct_url():
    """Discord merges same-message embeds sharing a url into one gallery."""
    payload = [raw(id="a"), raw(id="b"), raw(id="c", type="campaign")]

    urls = [e.url for e in run(feed(payload).fetch_events())]

    assert len(set(urls)) == 3


# ------------------------------------------------------------------- artwork

def test_banners_use_gametora_english_art():
    """uma.moe hosts the Japanese banner; Global players see the English one.
    uma.moe's gacha_id is what lets us build the English URL."""
    e = run(feed([raw(gacha_id=30124)]).fetch_events())[0]

    assert e.image == "https://gametora.com/images/umamusume/en/gacha/img_bnr_gacha_30124.png"
    assert "gametora.com" in e.url


def test_campaigns_use_gametora_english_art():
    e = run(feed([raw(id="campaign-198", type="campaign")]).fetch_events())[0]

    assert e.image.endswith("/en/missions/tex_campaign_mission_logo_00198.png")
    assert "gametora.com" in e.url


def test_the_japanese_art_is_kept_as_a_fallback():
    """GameTora doesn't have every id; better the Japanese banner than none."""
    e = run(feed([raw(gacha_id=30124)]).fetch_events())[0]

    assert e.image_alt == "https://uma.moe/assets/images/character/banner/2022_30124.webp"


def test_story_events_stay_on_uma_moe():
    """GameTora hosts English story art, but its ids don't map to uma.moe's
    slugs — guessing one would show a different event's banner."""
    e = run(feed([raw(id="story-event-10_intertwined", type="story_event",
                      image_path="assets/images/story/10_intertwined.webp")]).fetch_events())[0]

    assert e.image.startswith("https://uma.moe/")
    assert e.url.startswith("https://uma.moe/timeline#")


def test_news_derived_campaigns_stay_on_uma_moe():
    """Only the ``campaign-<mission id>`` form maps onto GameTora."""
    e = run(feed([raw(id="news-event-campaign-994", type="campaign")]).fetch_events())[0]

    assert e.image.startswith("https://uma.moe/")
    assert "uma.moe/timeline" in e.url


def test_art_and_link_never_come_from_different_sites():
    payload = [raw(gacha_id=1, id="b1"),
               raw(id="campaign-2", type="campaign"),
               raw(id="story-event-x", type="story_event")]

    for e in run(feed(payload).fetch_events()):
        assert ("gametora.com" in e.image) == ("gametora.com" in e.url)
