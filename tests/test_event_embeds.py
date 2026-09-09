"""Tests for how an event is rendered: one embed per event, each with its own
banner, each with a distinct url so Discord doesn't merge them together.
"""
import time

from events.client import GameEvent, GametoraClient, page_url
from events.embeds import (
    MAX_EMBEDS_PER_MESSAGE, chunk, event_embed, event_embeds, window_line,
)

NOW = int(time.time())
DAY = 24 * 3600


def event(key="gacha:1", kind="gacha_char", start=-DAY, end=7 * DAY,
          image="https://gametora.com/banner.png", detail="") -> GameEvent:
    return GameEvent(
        key=key, kind=kind, name=f"Event {key}",
        start=NOW + start,
        end=None if end is None else NOW + end,
        image=image,
        url="https://gametora.com/umamusume/gacha",
        detail=detail,
    )


def test_every_event_gets_its_own_embed_with_its_banner():
    events = [event("gacha:1", image="https://gametora.com/a.png"),
              event("mission:2", kind="mission", image="https://gametora.com/b.png")]

    embeds = [e.to_dict() for e in event_embeds(events, NOW)]

    assert len(embeds) == 2
    assert [e["image"]["url"] for e in embeds] == ["https://gametora.com/a.png",
                                                   "https://gametora.com/b.png"]


def test_the_source_line_appears_once_per_listing():
    """Five identical footers down one message is noise, not attribution."""
    embeds = [e.to_dict() for e in event_embeds([event("a"), event("b"), event("c")], NOW)]

    assert [("footer" in e) for e in embeds] == [False, False, True]


def test_a_single_announcement_keeps_its_footer():
    assert "footer" in event_embed(event(), NOW).to_dict()


def test_banners_are_listed_before_mission_events():
    events = [event("mission:1", kind="mission"),
              event("gacha:1", kind="gacha_char"),
              event("gacha:2", kind="gacha_support")]

    titles = [e.to_dict()["author"]["name"] for e in event_embeds(events, NOW)]

    assert titles == ["Character Banner", "Support Card Banner", "Mission Event"]


def test_the_kind_is_not_repeated_as_a_detail_line():
    """``detail`` carries the label for gacha, and the author line already says it."""
    body = event_embed(event(detail="Character Banner"), NOW).to_dict()["description"]

    assert body.count("Character Banner") == 0


def test_a_real_detail_is_kept():
    body = event_embed(event(kind="mission", detail="7 missions"), NOW).to_dict()["description"]

    assert body.startswith("7 missions\n")


def test_times_are_discord_timestamps_not_formatted_text():
    """A global-server announcement can't pick one timezone and be right."""
    e = event(start=-DAY, end=2 * DAY)

    line = window_line(e, NOW)

    assert f"<t:{e.start}:f>" in line
    assert f"Ends <t:{e.end}:R>" in line


def test_an_upcoming_event_counts_down_to_its_start():
    line = window_line(event(start=2 * DAY), NOW)

    assert "Starts <t:" in line
    assert "Ends" not in line


def test_permanent_content_shows_no_end_time():
    line = window_line(event(end=None), NOW)

    assert "No end date" in line
    assert "→" not in line


def test_a_long_listing_is_split_into_sendable_messages():
    embeds = event_embeds([event(f"gacha:{i}") for i in range(23)], NOW)

    batches = chunk(embeds)

    assert [len(b) for b in batches] == [MAX_EMBEDS_PER_MESSAGE, MAX_EMBEDS_PER_MESSAGE, 3]


def test_an_empty_listing_produces_nothing():
    assert event_embeds([], NOW) == []


def test_every_embed_in_a_listing_has_its_own_url():
    """Discord folds same-message embeds that share a `url` into one gallery
    embed, and GameTora has one gacha page for every banner — so each event
    needs its own url to stay in its own embed."""
    client = GametoraClient()
    rows = [{"id": 30124, "start": NOW - DAY, "end": NOW + DAY, "pickups": [[102102, 7500, True]]},
            {"id": 30125, "start": NOW - DAY, "end": NOW + DAY, "pickups": [[103402, 7500, True]]}]

    events = client._gacha_events(rows, "gacha_char", {})
    urls = [e.to_dict()["url"] for e in event_embeds(events, NOW)]

    assert len(set(urls)) == len(urls)


def test_the_uniqueness_is_only_a_fragment():
    """It must not change where the link actually goes."""
    assert page_url("gacha", "gacha:30124").split("#")[0] == (
        "https://gametora.com/umamusume/gacha"
    )
