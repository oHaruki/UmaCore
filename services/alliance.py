"""
Alliance leaderboard: sister clubs ranked on one board.

The web dashboard creates alliances and moves clubs in and out; the bot only
reads them. Standings are the alliance's newest month in quota_history, since
cumulative fans restart on the 1st and a club that missed today's scrape
should still count with what it has.
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional
from uuid import UUID

import discord

from config.database import db

logger = logging.getLogger(__name__)

SITE_URL = "https://umacore.app"
EMBED_COLOR = 0x8B5CF6
DESC_MAX = 4000
TOP_TRAINERS = 15


@dataclass
class TrainerRow:
    trainer_name: str
    club_name: str
    fans: int
    day_gain: Optional[int]


@dataclass
class ClubRow:
    club_name: str
    members: int = 0
    fans: int = 0
    day_gain: int = 0
    club_rank: Optional[int] = None

    @property
    def avg(self) -> int:
        return self.fans // self.members if self.members else 0


@dataclass
class Standings:
    alliance_id: UUID
    name: str
    slug: str
    is_public: bool
    month_start: Optional[date]
    latest_date: Optional[date]
    clubs: List[ClubRow] = field(default_factory=list)
    trainers: List[TrainerRow] = field(default_factory=list)


async def alliance_id_for_club(club_id: UUID) -> Optional[UUID]:
    return await db.fetchval(
        "SELECT alliance_id FROM alliance_clubs WHERE club_id = $1", club_id
    )


async def load_standings(alliance_id: UUID) -> Optional[Standings]:
    head = await db.fetchrow(
        "SELECT alliance_id, name, slug, is_public FROM alliances WHERE alliance_id = $1",
        alliance_id,
    )
    if not head:
        return None

    clubs = await db.fetch(
        """
        SELECT c.club_id, c.club_name,
               (SELECT club_rank FROM club_rank_history r
                 WHERE r.club_id = c.club_id ORDER BY r.date DESC LIMIT 1) AS club_rank
        FROM alliance_clubs a
        JOIN clubs c ON c.club_id = a.club_id
        WHERE a.alliance_id = $1 AND c.is_active
        ORDER BY a.joined_at
        """,
        alliance_id,
    )
    standings = Standings(
        alliance_id=head["alliance_id"], name=head["name"], slug=head["slug"],
        is_public=head["is_public"], month_start=None, latest_date=None,
    )
    if not clubs:
        return standings

    club_ids = [r["club_id"] for r in clubs]
    latest = await db.fetchval(
        "SELECT MAX(date) FROM quota_history WHERE club_id = ANY($1::uuid[])", club_ids
    )
    by_name = {r["club_name"]: ClubRow(r["club_name"], club_rank=r["club_rank"]) for r in clubs}
    standings.clubs = list(by_name.values())
    if latest is None:
        return standings

    month_start = latest.replace(day=1)
    standings.month_start = month_start
    standings.latest_date = latest

    rows = await db.fetch(
        """
        SELECT m.trainer_name, c.club_name, lat.cumulative_fans, lat.date,
               prev.cumulative_fans AS prev_fans, prev.date AS prev_date
        FROM members m
        JOIN clubs c ON c.club_id = m.club_id
        JOIN LATERAL (
            SELECT cumulative_fans, date FROM quota_history
            WHERE member_id = m.member_id AND club_id = m.club_id
              AND date >= $2 AND date < ($2::date + INTERVAL '1 month')
            ORDER BY date DESC LIMIT 1
        ) lat ON true
        LEFT JOIN LATERAL (
            SELECT cumulative_fans, date FROM quota_history
            WHERE member_id = m.member_id AND club_id = m.club_id
              AND date >= $2 AND date < lat.date
            ORDER BY date DESC LIMIT 1
        ) prev ON true
        WHERE m.club_id = ANY($1::uuid[]) AND m.is_active
        ORDER BY lat.cumulative_fans DESC, m.trainer_name
        """,
        club_ids, month_start,
    )
    for r in rows:
        gain = day_gain(r["cumulative_fans"], r["prev_fans"], r["prev_date"], r["date"])
        standings.trainers.append(
            TrainerRow(r["trainer_name"], r["club_name"], int(r["cumulative_fans"]), gain)
        )
        club = by_name[r["club_name"]]
        club.members += 1
        club.fans += int(r["cumulative_fans"])
        if r["date"] == latest:
            club.day_gain += gain or 0

    standings.clubs.sort(key=lambda c: (-c.fans, c.club_name))
    return standings


def day_gain(fans: int, prev_fans: Optional[int], prev_date: Optional[date],
             on: date) -> Optional[int]:
    """Fans earned on the member's newest day. Cumulative fans start from zero
    on the 1st, so the total is that day's gain. Without the row from the day
    before (joined mid month, missed scrapes) the gain is unknown."""
    if on.day == 1:
        return int(fans)
    if prev_fans is None or prev_date is None or (on - prev_date).days != 1:
        return None
    return max(0, int(fans) - int(prev_fans))


def fmt_fans(n: int) -> str:
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000_000:
        return f"{sign}{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{sign}{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{sign}{n / 1_000:.0f}K"
    return f"{sign}{n}"


def public_url(s: Standings) -> Optional[str]:
    return f"{SITE_URL}/alliance/{s.slug}" if s.is_public else None


def build_embed(s: Standings) -> discord.Embed:
    month = s.month_start.strftime("%B %Y") if s.month_start else "No data yet"
    total = sum(c.fans for c in s.clubs)
    members = sum(c.members for c in s.clubs)

    lines = [f"{month} · {len(s.clubs)} clubs · {members} trainers · {fmt_fans(total)} fans", ""]
    lines.append("**Clubs**")
    for i, c in enumerate(s.clubs, 1):
        rank = f" · #{c.club_rank}" if c.club_rank else ""
        gain = f" · +{fmt_fans(c.day_gain)} today" if c.day_gain else ""
        lines.append(
            f"`{i}.` **{c.club_name}** {fmt_fans(c.fans)} · avg {fmt_fans(c.avg)}{gain}{rank}"
        )

    if s.trainers:
        lines += ["", "**Top trainers**"]
        multi = len(s.clubs) > 1
        for i, t in enumerate(s.trainers[:TOP_TRAINERS], 1):
            club = f" ({t.club_name})" if multi else ""
            line = f"`{i:>2}.` {discord.utils.escape_markdown(t.trainer_name)}{club} · {fmt_fans(t.fans)}"
            if len("\n".join(lines)) + len(line) > DESC_MAX:
                break
            lines.append(line)

    embed = discord.Embed(
        title=f"🏆 {s.name}", description="\n".join(lines), color=EMBED_COLOR, url=public_url(s)
    )
    if s.latest_date:
        embed.set_footer(text=f"Data through {s.latest_date:%b %d}")
    return embed
