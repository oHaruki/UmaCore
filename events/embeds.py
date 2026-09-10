"""
Embed rendering for Uma Musume event announcements.

Everything is one embed per event, banner art included — the banner *is* the
announcement, and a list of names with no art tells you less than the picture
does. Discord takes up to 10 embeds per message, so a listing is chunked rather
than collapsed into fields.

Times go out as Discord timestamp markup (``<t:unix:f>``) rather than formatted
text, so every reader sees the window in their own timezone — the one detail a
global-server announcement can't get right any other way.
"""
import time

import discord

from .client import GameEvent

MAX_EMBEDS_PER_MESSAGE = 10

KIND_LABELS = {
    "gacha_char": "Character Banner",
    "gacha_support": "Support Card Banner",
    "gacha_paid": "Paid Banner",
    "mission": "Mission Event",
    "story": "Story Event",
    "champions_meeting": "Champions Meeting",
    "legend_race": "Legend Race",
    "scenario": "New Scenario",
    "factor_research": "Factor Research",
    "league_of_heroes": "League of Heroes",
    "masters_challenge": "Masters Challenge",
    "racing_carnival": "Racing Carnival",
    "strongest_team": "Strongest Team",
    "trainer_skills_test": "Trainer Aptitude Test",
}

KIND_COLOURS = {
    "gacha_char": 0xe84393,
    "gacha_support": 0x4a7dff,
    "gacha_paid": 0x8e44ad,
    "mission": 0xf39c12,
    "story": 0x9b59b6,
    "champions_meeting": 0xf1c40f,
    "legend_race": 0x1abc9c,
    "scenario": 0x16a085,
    "factor_research": 0x27ae60,
    "league_of_heroes": 0xe67e22,
    "masters_challenge": 0xc0392b,
    "racing_carnival": 0xff7675,
    "strongest_team": 0x2980b9,
    "trainer_skills_test": 0x7f8c8d,
}

#: Listing order: what people pull on first, then what they play, then the rest.
KIND_ORDER = [
    "gacha_char", "gacha_support", "gacha_paid",
    "story", "champions_meeting", "legend_race",
    "scenario", "mission",
    "racing_carnival", "league_of_heroes", "masters_challenge",
    "strongest_team", "trainer_skills_test", "factor_research",
]

FOOTER = "Uma Musume Global · data from uma.moe"


def kind_label(kind: str) -> str:
    """Readable label, falling back to the raw kind so a newly added uma.moe
    event type still shows something sensible instead of just "Event"."""
    return KIND_LABELS.get(kind) or kind.replace("_", " ").title()


def ts(unix: int, style: str = "f") -> str:
    return f"<t:{int(unix)}:{style}>"


def window_line(event: GameEvent, now: int = None) -> str:
    """The one line that says when this runs, and how long is left."""
    now = now or int(time.time())

    if event.end is None:
        return f"Started {ts(event.start)}\nNo end date"

    span = f"{ts(event.start)} → {ts(event.end)}"
    if event.start > now:
        return f"{span}\nStarts {ts(event.start, 'R')}"
    return f"{span}\nEnds {ts(event.end, 'R')}"


def sort_key(event: GameEvent):
    """Banners first, then dated events, soonest deadline first within a kind."""
    return (KIND_ORDER.index(event.kind) if event.kind in KIND_ORDER else 99,
            event.end or 2 ** 62)


def event_embed(event: GameEvent, now: int = None, *, footer: bool = True) -> discord.Embed:
    """One event: label, name, window, banner.

    ``footer`` is off for all but the last embed of a listing — repeating the
    same source line five times down a message is noise, not attribution.
    """
    label = kind_label(event.kind)

    lines = []
    if event.detail and event.detail != label:
        lines.append(event.detail)
    lines.append(window_line(event, now))

    embed = discord.Embed(
        title=event.name,
        url=event.url,
        description="\n".join(lines),
        colour=KIND_COLOURS.get(event.kind, 0x95a5a6),
    )
    embed.set_author(name=label)
    if event.image:
        embed.set_image(url=event.image)
    if footer:
        embed.set_footer(text=FOOTER)
        embed.timestamp = discord.utils.utcnow()
    return embed


def event_embeds(events: list[GameEvent], now: int = None) -> list[discord.Embed]:
    """A full listing, ordered, with the source line on the last embed only."""
    ordered = sorted(events, key=sort_key)
    embeds = [event_embed(e, now, footer=False) for e in ordered]
    if embeds:
        embeds[-1].set_footer(text=FOOTER)
        embeds[-1].timestamp = discord.utils.utcnow()
    return embeds


def chunk(embeds: list[discord.Embed]) -> list[list[discord.Embed]]:
    """Split a listing into messages Discord will accept."""
    return [embeds[i:i + MAX_EMBEDS_PER_MESSAGE]
            for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE)]
