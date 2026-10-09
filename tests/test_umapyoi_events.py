"""Tests for the umapyoi news source — turning official Global notices into events.

The notices are prose. What makes them usable is that every period is written in
one fixed form under a heading, so most of what is pinned here is reading that
form, choosing the right periods, and recognising when two notices describe the
same event. The fixtures copy the markup of real notices.

umapyoi is never contacted.
"""
import asyncio
import time
from calendar import timegm
from datetime import datetime, timezone

import pytest

from events import umapyoi_client as mod
from events.umapyoi_client import (
    UmapyoiNewsClient, classify, events_from_posts, parse_range, post_to_event,
    _RANGE,
)

UTC = timezone.utc
POSTED = datetime(2026, 10, 7, 22, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


def ts(y, mo, d, h, mi, s=0) -> int:
    return timegm((y, mo, d, h, mi, s))


def rng(text, posted=POSTED):
    return parse_range(_RANGE.search(text), posted)


def body(*lines) -> str:
    """Notice markup: indented lines joined by <br>, as umapyoi serves them."""
    return "<br>\n".join(f"\xa0{line}" if line else "" for line in lines)


def post(title, *lines, at=POSTED, pid=1000, label="Game", image="thumb.png") -> dict:
    return {
        "announce_id": pid,
        "title": title,
        "message": body(*lines),
        "post_at": int(at.timestamp()),
        "label_name_en": label,
        "image": image,
    }


STORY = post(
    "The story event Illuminate the Heart is here!",
    "As of 10:00 p.m., Sep 28, 2026 (UTC), the story event Illuminate the Heart has begun!",
    "",
    "Event Availability Period",
    "10:00 p.m., Sep 28–9:59 p.m., Oct 12, 2026 (UTC)",
    "",
    "Reward Collection Period",
    "10:00 p.m., Oct 12–9:59 p.m., Oct 15, 2026 (UTC)",
    "",
    "• Event-Exclusive SSR Support Card: [Don't Stop Anymore!] Sakura Laurel",
    at=datetime(2026, 9, 28, 22, tzinfo=UTC), pid=1077,
)

CM_SELECTION = post(
    "The league selection period for the Champions Meeting: Sagittarius Cup has begun!",
    "Event Availability Period",
    "10:00 p.m., Oct 10–9:59 p.m., Oct 16, 2026 (UTC)",
    "League Selection Period",
    "10:00 p.m., Oct 7–9:59 p.m., Oct 14, 2026 (UTC)",
    "• If you have not entered the Champions Meeting by 9:59 p.m., Oct 14, 2026 (UTC), "
    "you will not be able to do so after that point.",
    "Final Round: Race Period",
    "10:00 p.m., Oct 15–9:59 p.m., Oct 16, 2026 (UTC)",
    "Race Conditions",
    "Nakayama / Turf / 2,500m (Long) / Right-Handed / Inner / Winter / Cloudy / Good",
    pid=1086,
)

CM_RACE_DAY = {**CM_SELECTION, "announce_id": 1099,
               "title": "The race event Champions Meeting: Sagittarius Cup is here!",
               "post_at": ts(2026, 10, 10, 22, 0)}

LEGEND = post(
    "A Legend Race is here!",
    "Race Availability Period",
    "10:00 p.m., Aug 13 - 2:59 p.m., Aug 16, 2026 (UTC)",
    "3:00 p.m., Aug 16 - 2:59 p.m., Aug 19, 2026 (UTC)",
    "Race Conditions",
    "Sprinters Stakes / Nakayama / Turf / 1,200m (Sprint) / Right-Handed / Outer / Fall",
    at=datetime(2026, 8, 13, 22, tzinfo=UTC), pid=954,
)


# ------------------------------------------------------------------- periods

def test_a_period_reads_as_utc_with_an_inclusive_end():
    """The end is the last second of the stated minute, so "9:59 p.m." reads
    back as 9:59 p.m. — the same convention as GameTora's ``…:59:59``."""
    assert rng("10:00 p.m., Sep 28–9:59 p.m., Oct 12, 2026 (UTC)") == \
        (ts(2026, 9, 28, 22, 0), ts(2026, 10, 12, 21, 59, 59))


def test_spaced_hyphens_read_like_en_dashes():
    assert rng("10:00 p.m., Aug 12 - 9:59 p.m., Aug 16, 2026 (UTC)") == \
        (ts(2026, 8, 12, 22, 0), ts(2026, 8, 16, 21, 59, 59))


def test_noon_and_midnight():
    assert rng("12:00 a.m., Oct 1–12:00 p.m., Oct 1, 2026 (UTC)") == \
        (ts(2026, 10, 1, 0, 0), ts(2026, 10, 1, 12, 0, 59))


def test_a_same_day_range_borrows_the_end_date():
    assert rng("10:00 a.m. - 9:59 p.m., May 19, 2026 (UTC)") == \
        (ts(2026, 5, 19, 10, 0), ts(2026, 5, 19, 21, 59, 59))


def test_full_month_names():
    assert rng("10:00 p.m., May 29 - 9:59 p.m., June 1, 2026 (UTC)") == \
        (ts(2026, 5, 29, 22, 0), ts(2026, 6, 1, 21, 59, 59))


def test_a_range_spanning_new_year():
    assert rng("10:00 p.m., Dec 28–9:59 p.m., Jan 4, 2027 (UTC)") == \
        (ts(2026, 12, 28, 22, 0), ts(2027, 1, 4, 21, 59, 59))


def test_a_missing_year_is_taken_from_the_post():
    posted = datetime(2026, 6, 24, 22, tzinfo=UTC)
    assert rng("10:00 p.m., Jun 25 - 9:59 p.m., Jul 3 (UTC)", posted) == \
        (ts(2026, 6, 25, 22, 0), ts(2026, 7, 3, 21, 59, 59))


def test_an_open_ended_time_is_not_a_period():
    assert _RANGE.search("League Selection: From 10:00 p.m., Oct 7, 2026 (UTC)") is None
    assert _RANGE.search("10:00 p.m., Jun 11 – present (UTC)") is None


# ------------------------------------------------------------------ notices

def test_a_story_event_ends_before_reward_collection():
    e = post_to_event(STORY)

    assert (e.kind, e.name) == ("story", "Illuminate the Heart")
    assert (e.start, e.end) == (ts(2026, 9, 28, 22, 0), ts(2026, 10, 12, 21, 59, 59))
    assert e.detail == "Event SSR: [Don't Stop Anymore!] Sakura Laurel"


def test_story_names_are_kept_exactly():
    """Quoted names keep their article and punctuation."""
    assert classify('The story event "The Promised Hour: Silks & Three Riddles" is here!') == \
        ("story", "The Promised Hour: Silks & Three Riddles")
    assert classify('The story event "Search! Solve! Summer!" is coming soon!') == \
        ("story", "Search! Solve! Summer!")


def test_champions_meeting_runs_from_league_selection_to_the_final():
    e = post_to_event(CM_SELECTION)

    assert (e.kind, e.name) == ("champions_meeting", "Sagittarius Cup")
    assert (e.start, e.end) == (ts(2026, 10, 7, 22, 0), ts(2026, 10, 16, 21, 59, 59))
    assert e.detail.startswith("Nakayama / Turf / 2,500m (Long)")


def test_champions_meeting_notices_are_one_event():
    """The league-selection and race-day notices describe the same meeting;
    announcing both would post it twice."""
    events = events_from_posts([CM_SELECTION, CM_RACE_DAY])

    assert len(events) == 1
    assert events[0].url.endswith("/1099")


def test_a_legend_race_is_named_from_its_conditions_and_spans_both_halves():
    e = post_to_event(LEGEND)

    assert e.name == "Sprinters Stakes Legend Race"
    assert e.detail.startswith("Nakayama / Turf / 1,200m")
    assert (e.start, e.end) == (ts(2026, 8, 13, 22, 0), ts(2026, 8, 19, 14, 59, 59))


def test_coming_soon_and_is_here_are_one_event_with_the_newer_details():
    soon = post("Transfer Requests coming soon!", "Availability Period",
                "10:00 p.m., Sep 24–9:59 p.m., Sep 28, 2026 (UTC)",
                at=datetime(2026, 9, 23, 22, tzinfo=UTC), pid=1057, image="soon.png")
    here = post("Transfer Requests now available!", "Availability Period",
                "10:00 p.m., Sep 24–9:59 p.m., Sep 28, 2026 (UTC)",
                at=datetime(2026, 9, 24, 22, tzinfo=UTC), pid=1058, image="here.png")

    events = events_from_posts([here, soon])

    assert [(e.name, e.image) for e in events] == [("Transfer Requests", "here.png")]
    assert post_to_event(soon).key == post_to_event(here).key


def test_a_recurring_event_is_a_new_event_each_time():
    first = post("Transfer Requests now available!", "Availability Period",
                 "10:00 p.m., Sep 1 - 9:59 p.m., Sep 5, 2026 (UTC)", pid=1)
    second = post("Transfer Requests now available!", "Availability Period",
                  "10:00 p.m., Sep 24–9:59 p.m., Sep 28, 2026 (UTC)", pid=2)

    keys = {e.key for e in events_from_posts([first, second])}

    assert len(keys) == 2


def test_bonus_star_pieces_name_their_race_and_keep_their_start():
    """These notices land hours after the bonus opens; the bonus still opened
    when the notice says."""
    p = post("Bonus Star Piece rewards in Career!", "Race / Period / Rewards", "Japan Cup",
             "3:00 p.m., Oct 6–2:59 p.m., Oct 8, 2026 (UTC)",
             at=datetime(2026, 10, 6, 22, tzinfo=UTC))

    e = post_to_event(p)

    assert (e.kind, e.name, e.detail) == ("campaign", "Bonus Star Piece rewards in Career", "Japan Cup")
    assert e.start == ts(2026, 10, 6, 15, 0)


def test_a_later_part_starts_with_its_notice_not_its_leftovers():
    """Part 2 notices repeat periods still running from Part 1. Taking the
    earliest would date Part 2 a week back, and the announcer would skip it as
    backdated."""
    p = post("1.5-Year Anniversary Celebration Part 2 now available!",
             "Login Bonus", "10:00 p.m., Jul 14 - 9:59 p.m., Aug 27, 2026 (UTC)",
             "Missions", "10:00 p.m., Jul 22 - 2:59 p.m., Aug 28, 2026 (UTC)",
             at=datetime(2026, 7, 22, 22, tzinfo=UTC))

    e = post_to_event(p)

    assert e.start == ts(2026, 7, 22, 22, 0)
    assert e.end == ts(2026, 8, 28, 14, 59, 59)


def test_campaign_headlines_become_names():
    assert classify("A special Umayuru Celebration has begun!") == ("campaign", "Umayuru Celebration")
    assert classify("Increased Daily Race Tickets event coming soon!") == \
        ("campaign", "Increased Daily Race Tickets")
    assert classify("Let's Go! Uma Outing now available!") == ("campaign", "Let's Go! Uma Outing")
    assert classify("The event Aim for the Stars! Dream Team is here!") == \
        ("event", "Aim for the Stars! Dream Team")


@pytest.mark.parametrize("title", [
    "Spotlight Pretty Derby and Spotlight Support Card Scouts out now!",
    "New Spotlight Pretty Derby and Spotlight Support Card Scouts coming soon!",
    "Issue with Obtaining Scout Points in the Aim for the Stars! Dream Team Event",
    "Career Maintenance Announcement",
    "1.5-Year Anniversary Celebration items now available on the Cygames WebStore!",
    'The story event "Hark Back, Run Forward" has ended!',
    "1.5-Year Anniversary Celebrations ending soon!",
    "The race event Champions Meeting: Sagittarius Cup is coming in early October!",
])
def test_notices_that_are_not_events(title):
    assert classify(title) is None


def test_free_scouts_are_a_campaign_not_a_banner():
    assert classify("Up to 100 free scouts! Daily Free 10x Scout has begun!")[0] == "campaign"


def test_media_posts_and_undated_notices_are_skipped():
    dated = ("Availability Period", "10:00 p.m., Sep 1 - 9:59 p.m., Sep 5, 2026 (UTC)")

    assert post_to_event(post("Check out all the latest updates!", *dated, label="Media")) is None
    assert post_to_event(post("Bond Level Uncaps coming soon!", "Stay tuned!")) is None


def test_a_new_scenario_is_announced_without_an_end():
    p = post('The new Career scenario "Brighter Together! Our Grand Concert" is here!',
             "As of 10:00 p.m., Jul 22, 2026 (UTC), a new Career scenario is available!",
             at=datetime(2026, 7, 22, 22, tzinfo=UTC))

    e = post_to_event(p)

    assert (e.kind, e.name, e.end) == ("scenario", "Brighter Together! Our Grand Concert", None)
    assert e.start == p["post_at"]


def test_the_thumbnail_is_the_art_and_the_header_its_fallback():
    p = {**STORY, "message": STORY["message"] + '<br><img src="https://cdn/header.png">'}

    e = post_to_event(p)

    assert (e.image, e.image_alt) == ("thumb.png", "https://cdn/header.png")


def test_each_event_links_to_its_own_notice():
    assert post_to_event(STORY).url == "https://umapyoi.net/en/news/1077"


def test_a_notice_that_breaks_the_parser_does_not_take_the_rest_down(monkeypatch):
    real = mod.post_to_event

    def flaky(p):
        if p["announce_id"] == 954:
            raise ValueError("bad notice")
        return real(p)

    monkeypatch.setattr(mod, "post_to_event", flaky)

    assert [e.kind for e in events_from_posts([LEGEND, STORY])] == ["story"]


# -------------------------------------------------------------------- client

class FakeNews(UmapyoiNewsClient):
    """Real caching and parsing, pages served from memory."""

    def __init__(self, pages):
        super().__init__()
        self.pages = pages
        self.requested = []
        self.down = False

    async def _page(self, offset):
        self.requested.append(offset)
        if self.down:
            raise RuntimeError("umapyoi down")
        return self.pages.get(offset, [])


def live_post(pid, start_days, end_days, title="Transfer Requests now available!"):
    now = time.time()
    fmt = lambda t: time.strftime("%I:%M %p, %b %d, %Y", time.gmtime(t)) \
        .replace("AM", "a.m.").replace("PM", "p.m.").lstrip("0")
    start, end = fmt(now + start_days * 86400), fmt(now + end_days * 86400)
    start = start.rsplit(",", 1)[0]  # the start's year is left off, as in real notices
    return {"announce_id": pid, "title": title, "post_at": int(now + start_days * 86400),
            "label_name_en": "Game", "image": "",
            "message": body("Availability Period", f"{start}–{end} (UTC)")}


def test_the_first_fetch_reads_the_history_and_later_ones_only_the_newest_page():
    client = FakeNews({0: [live_post(1, -1, 3)]})

    run(client.fetch_posts())
    first = list(client.requested)
    run(client.fetch_posts())

    assert first == [i * mod.PAGE_SIZE for i in range(mod.DEEP_PAGES)]
    assert client.requested[len(first):] == [0]


def test_a_failed_refresh_keeps_the_notices_already_held():
    client = FakeNews({0: [live_post(1, -1, 3)]})
    run(client.fetch_events())

    client.down = True

    assert [e.name for e in run(client.fetch_events())] == ["Transfer Requests"]


def test_a_failed_first_fetch_is_an_error():
    client = FakeNews({})
    client.down = True

    with pytest.raises(RuntimeError):
        run(client.fetch_events())


def test_finished_events_are_dropped():
    client = FakeNews({0: [live_post(1, -9, -2), live_post(2, -1, 3, "Let's Go! Uma Outing now available!")]})

    assert [e.name for e in run(client.fetch_events())] == ["Let's Go! Uma Outing"]
