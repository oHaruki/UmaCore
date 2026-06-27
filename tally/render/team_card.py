"""Team Trials card renderer — a trainer's Team Stadium roster.

Renders the ``team_stadium`` roster (page 2 of the status carousel) grouped by
race category. uma.moe returns 15 umas, 3 per category, tagged with
``distance_type`` (1 Sprint · 2 Mile · 3 Medium · 4 Long · 5 Dirt). Each
category gets its own band with its three umas as tiles.

Per uma we show the portrait (reusing the leader-portrait cache via ``card_id``),
the five raw stats, the skill count, and — per the design — only the aptitude
grades that matter for that category:

* Sprint/Mile/Medium/Long → that distance grade + Turf
* Dirt                     → Dirt ground grade + Mile distance

Skills/factors/support cards are IDs only, so nothing beyond the skill *count* is
shown; the card stays self-contained (no name data, no extra network beyond
portrait art). Reuses the trainer card's theme/fonts so both pages match.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

from .base import load_font
from .themes import THEME
from .trainer_card import _short

SCALE = 2

# 1× logical-pixel layout constants
_W         = 768
_PAD       = 32
_GAP       = 18
_HEADER_H  = 66
_FOOTER_H  = 24

# Category column + tile geometry. Categories run left-to-right as columns; the
# umas in each category stack vertically beneath the column header.
_CAT_H     = 30      # column header strip
_COL_W     = 226     # width of one category column
_COL_GAP   = 16      # gap between category columns
_TILE_H    = 196
_TILE_GAP  = 14
_PORTRAIT  = 64

# Status accent colors (shared palette with the trainer card)
_GREEN  = (74, 201, 126)
_GOLD   = (240, 196, 64)
_ORANGE = (232, 150, 56)
_RED    = (224, 84, 84)
_BLUE   = (72, 150, 220)
_PINK   = (236, 108, 128)

# Aptitude grade letters. uma.moe sends proper_* as ints 1..8 (G..S).
_GRADE_LETTERS = {1: "G", 2: "F", 3: "E", 4: "D", 5: "C", 6: "B", 7: "A", 8: "S"}
_GRADE_COLORS = {
    "S": _GOLD,
    "A": _PINK,
    "B": _BLUE,
    "C": _GREEN,
    "D": (150, 160, 120),
}

# running_style: 1 Front (Nige), 2 Pace (Senko), 3 Late (Sashi), 4 End (Oikomi)
_RUNNING_STYLES = {1: "Front", 2: "Pace", 3: "Late", 4: "End"}

# Overall uma evaluation rank, derived from rank_score via the game's
# ``single_mode_rank`` table (98 tiers). ``_RANK_MIN[i]`` is the inclusive
# minimum score for tier index ``i`` (rank_id ``i + 1``), which also indexes the
# bundled badge art (``utx_txt_rank_{i:02d}`` → ``ranks/{i:02d}.png``).
_RANK_MIN = [
    0, 300, 600, 900, 1300, 1800, 2300, 2900, 3500, 4900,
    6500, 8200, 10000, 12100, 14500, 15900, 17500, 19200, 19600, 20000,
    20400, 20800, 21200, 21600, 22100, 22500, 23000, 23400, 23900, 24300,
    24800, 25300, 25800, 26300, 26800, 27300, 27800, 28300, 28800, 29400,
    29900, 30400, 31000, 31500, 32100, 32700, 33200, 33800, 34400, 35000,
    35600, 36200, 36800, 37500, 38100, 38700, 39400, 40000, 40700, 41300,
    42000, 42700, 43400, 44000, 44700, 45400, 46200, 46900, 47600, 48300,
    49000, 49800, 50500, 51300, 52000, 52800, 53600, 54400, 55200, 55900,
    56700, 57500, 58400, 59200, 60000, 60800, 61700, 62500, 63400, 64200,
    65100, 66000, 66800, 67700, 68600, 69500, 70400, 71400,
]

# Human-readable labels per tier index (fallback text only — the badge art is
# the primary display). Base tiers G→SS+, then ultra UG1→US10.
_RANK_LABELS = [
    "G", "G+", "F", "F+", "E", "E+", "D", "D+", "C", "C+",
    "B", "B+", "A", "A+", "S", "S+", "SS", "SS+",
] + [f"U{L}{n}" for L in ("G", "F", "E", "D", "C", "B", "A", "S") for n in range(1, 11)]

# Bundled rank-badge icons (extracted from the in-game rating atlas). Loaded
# lazily and cached by tier index; renders fall back to a drawn pill if missing.
_RANK_DIR = Path(__file__).parent.parent / "assets" / "ranks"
_rank_icon_cache: dict = {}

_RANK_BASE_COLORS = {
    "G": (120, 114, 140), "F": (120, 114, 140),
    "E": (150, 142, 170), "D": (150, 142, 170),
    "C": _GREEN, "B": _BLUE, "A": _PINK, "S": _GOLD,
    "SS": (255, 168, 64),
}


def _rank_index(score: Optional[int]) -> Optional[int]:
    """0-based tier index for a rank_score, or None when unknown/zero."""
    if not score or score <= 0:
        return None
    idx = 0
    for i, thr in enumerate(_RANK_MIN):
        if score >= thr:
            idx = i
        else:
            break
    return idx


def _rank_icon(index: int):
    if index in _rank_icon_cache:
        return _rank_icon_cache[index]
    icon = None
    p = _RANK_DIR / f"{index:02d}.png"
    if p.exists():
        try:
            icon = Image.open(p).convert("RGBA")
        except Exception:
            icon = None
    _rank_icon_cache[index] = icon
    return icon


def _rank_label(score: Optional[int]) -> Optional[str]:
    idx = _rank_index(score)
    return _RANK_LABELS[idx] if idx is not None else None


def _rank_color(label: str) -> tuple:
    base = label.rstrip("+")
    if base.startswith("U"):  # ultra tiers
        return (190, 130, 235)
    return _RANK_BASE_COLORS.get(base, (150, 142, 170))

# distance_type → (band label, distance-aptitude key, short label for the grade)
_CATEGORY_ORDER = [1, 2, 3, 4, 5]
_CATEGORY_NAMES = {1: "SPRINT", 2: "MILE", 3: "MEDIUM", 4: "LONG", 5: "DIRT"}
_DISTANCE_KEY = {1: "short", 2: "mile", 3: "middle", 4: "long"}
_DISTANCE_LABEL = {1: "SPRINT", 2: "MILE", 3: "MEDIUM", 4: "LONG"}


def _grade_letter(value: Optional[int]) -> str:
    if value is None:
        return "—"
    return _GRADE_LETTERS.get(int(value), "—")


def _grade_color(letter: str) -> tuple:
    return _GRADE_COLORS.get(letter, (138, 130, 158))


# ── data model ──────────────────────────────────────────────────────────────

@dataclass
class TeamMemberCard:
    """One uma in the team-stadium roster."""
    card_id: Optional[int] = None
    distance_type: Optional[int] = None
    member_id: Optional[int] = None
    skill_count: int = 0
    portrait_path: Optional[str] = None

    speed: int = 0
    stamina: int = 0
    power: int = 0
    guts: int = 0
    wiz: int = 0

    rarity: Optional[int] = None
    talent_level: Optional[int] = None
    running_style: Optional[int] = None
    rank_score: Optional[int] = None
    team_rating: Optional[int] = None
    fans: Optional[int] = None

    # aptitude grade ints, keyed by short code; missing keys render as "—"
    distance: dict = field(default_factory=dict)   # short/mile/middle/long
    ground: dict = field(default_factory=dict)      # turf/dirt
    style_apt: dict = field(default_factory=dict)   # front/pace/late/end

    def category_aptitudes(self) -> list:
        """The two grades to show for this uma's category: [(label, value), ...]."""
        if self.distance_type == 5:
            return [("DIRT", self.ground.get("dirt")), ("MILE", self.distance.get("mile"))]
        key = _DISTANCE_KEY.get(self.distance_type)
        label = _DISTANCE_LABEL.get(self.distance_type, "DIST")
        return [(label, self.distance.get(key) if key else None),
                ("TURF", self.ground.get("turf"))]


@dataclass
class TeamCardData:
    trainer_name: str
    trainer_id: Optional[str]
    club_name: str
    members: list = field(default_factory=list)

    team_evaluation_point: Optional[int] = None
    team_class: Optional[int] = None
    has_api_data: bool = False


# ── renderer ─────────────────────────────────────────────────────────────────

class _Renderer:
    def __init__(self, data: TeamCardData):
        self.d = data
        self.t = THEME
        self.S = SCALE

        # Group members into ordered category columns. Unknown distance_types
        # fall into a trailing "OTHER" column so nothing silently vanishes.
        self.bands = self._group(data.members)
        n_cols = max(1, len(self.bands))
        max_rows = max((len(m) for _, m in self.bands), default=1)

        # The canvas widens to fit one column per category.
        content_w_logical = n_cols * _COL_W + (n_cols - 1) * _COL_GAP
        self.content_x = _PAD * self.S
        self.content_w = content_w_logical * self.S
        self.img_w = (content_w_logical + 2 * _PAD) * self.S

        grid_h = _CAT_H + max_rows * _TILE_H + (max_rows - 1) * _TILE_GAP
        h = (
            _PAD + _HEADER_H + 2
            + _GAP + grid_h
            + _GAP + _FOOTER_H
            + _PAD
        )
        self.img_h = h * self.S

    def _group(self, members: list) -> list:
        buckets: dict = {}
        for m in members:
            buckets.setdefault(m.distance_type, []).append(m)
        for grp in buckets.values():
            grp.sort(key=lambda x: (x.member_id is None, x.member_id or 0))

        bands = []
        for dt in _CATEGORY_ORDER:
            if buckets.get(dt):
                bands.append((_CATEGORY_NAMES[dt], buckets[dt]))
        # Any leftover/unknown categories.
        extras = [m for dt, grp in buckets.items() if dt not in _CATEGORY_NAMES for m in grp]
        if extras:
            bands.append(("OTHER", extras))
        return bands

    # ── primitives ──────────────────────────────────────────────────────────────

    def _text(self, draw, xy, s, font, fill, anchor="lm"):
        draw.text(xy, s, font=font, fill=fill, anchor=anchor)

    def _truncate(self, draw, s, font, max_w):
        if draw.textlength(s, font=font) <= max_w:
            return s
        ell = "…"
        while s and draw.textlength(s + ell, font=font) > max_w:
            s = s[:-1]
        return s + ell

    # ── header ────────────────────────────────────────────────────────────────

    def _draw_header(self, draw, fonts, y):
        S, t, d = self.S, self.t, self.d
        x = self.content_x

        label = "TEAM TRIALS"
        pill_font = fonts["pill"]
        tb = draw.textbbox((0, 0), label, font=pill_font)
        pill_w = (tb[2] - tb[0]) + 28 * S
        pill_h = 30 * S
        pill_x1 = self.content_x + self.content_w
        pill_x0 = pill_x1 - pill_w
        draw.rounded_rectangle((pill_x0, y, pill_x1, y + pill_h), radius=pill_h // 2, fill=_BLUE)
        self._text(draw, ((pill_x0 + pill_x1) // 2, y + pill_h // 2),
                   label, pill_font, (16, 16, 20), anchor="mm")

        name = d.trainer_name or "Unknown Trainer"
        name = self._truncate(draw, name, fonts["name"], pill_x0 - x - 16 * S)
        self._text(draw, (x, y + 24 * S), name, fonts["name"], t["title"], anchor="lm")

        parts = []
        if d.team_evaluation_point:
            parts.append(f"Team Rating {_short(d.team_evaluation_point)}")
        if d.team_class:
            parts.append(f"Class {d.team_class}")
        parts.append(f"{len(d.members)} umas")
        sub = self._truncate(draw, "   ·   ".join(parts), fonts["sub"], self.content_w)
        self._text(draw, (x, y + 50 * S), sub, fonts["sub"], t["muted"], anchor="lm")

        dy = y + _HEADER_H * S
        draw.rectangle((self.content_x, dy, self.content_x + self.content_w, dy + 2 * S), fill=t["divider"])

    # ── category column header ──────────────────────────────────────────────────

    def _draw_col_header(self, draw, fonts, cx, y, col_w, label):
        S, t = self.S, self.t
        mid = cx + col_w // 2
        self._text(draw, (mid, y + 11 * S), label, fonts["band"], t["divider"], anchor="mm")
        # accent underline across the column
        ly = y + (_CAT_H - 6) * S
        draw.rectangle((cx, ly, cx + col_w, ly + max(1, S)),
                       fill=tuple(int(a + (b - a) * 0.4) for a, b in zip(t["panel"], t["divider"])))

    # ── portrait ────────────────────────────────────────────────────────────────

    def _paste_portrait(self, img, m: TeamMemberCard, cx, cy):
        S, t = self.S, self.t
        size = _PORTRAIT * S
        x0 = cx - size // 2
        y0 = cy - size // 2

        face = None
        if m.portrait_path and os.path.exists(m.portrait_path):
            try:
                src = Image.open(m.portrait_path).convert("RGBA")
                w, h = src.size
                side = int(min(w, h) * 0.60)
                px = w // 2
                top = int(h * 0.05)
                face = src.crop((px - side // 2, top, px + side // 2, top + side)).resize(
                    (size, size), Image.LANCZOS)
            except Exception:
                face = None

        tile = Image.new("RGBA", (size, size), tuple(t["panel"]) + (255,))
        comp = Image.alpha_composite(tile, face) if face is not None else tile
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        img.paste(comp.convert("RGB"), (x0, y0), mask)

        ring = ImageDraw.Draw(img)
        ring.ellipse((x0, y0, x0 + size - 1, y0 + size - 1), outline=t["divider"], width=max(2, S))
        if face is None:
            ring.text((cx, cy), "?", font=load_font(24 * S, bold=True, game=True),
                      fill=t["subtle"], anchor="mm")

    # ── uma tile (vertical) ──────────────────────────────────────────────────────

    def _draw_tile(self, img, draw, fonts, m: TeamMemberCard, tx, ty, tw):
        S, t = self.S, self.t
        th = _TILE_H * S
        draw.rounded_rectangle((tx, ty, tx + tw, ty + th), radius=12 * S, fill=t["panel"])

        cx = tx + tw // 2

        # Portrait
        self._paste_portrait(img, m, cx, ty + (12 + _PORTRAIT // 2) * S)

        # Overall uma rank badge (top-left corner) — real icon if bundled,
        # otherwise a drawn colored pill.
        ridx = _rank_index(m.rank_score)
        if ridx is not None:
            rank = _RANK_LABELS[ridx]
            icon = _rank_icon(ridx)
            if icon is not None:
                target_h = 44 * S
                w = max(1, int(icon.width * target_h / icon.height))
                ic = icon.resize((w, target_h), Image.LANCZOS)
                img.paste(ic, (tx + 8 * S, ty + 6 * S), ic)
            else:
                rfont = fonts["rank"]
                tb = draw.textbbox((0, 0), rank, font=rfont)
                tw_text = tb[2] - tb[0]
                ph = 22 * S
                pw = tw_text + 16 * S
                bx0 = tx + 8 * S
                by0 = ty + 8 * S
                draw.rounded_rectangle((bx0, by0, bx0 + pw, by0 + ph),
                                       radius=ph // 2, fill=_rank_color(rank))
                self._text(draw, (bx0 + pw // 2, by0 + ph // 2), rank, rfont, (16, 16, 20), anchor="mm")

        # Rarity stars + meta line
        if m.rarity:
            stars = "★" * max(1, min(5, int(m.rarity)))
            self._text(draw, (cx, ty + 84 * S), stars, fonts["stars"], _GOLD, anchor="mm")
        meta_bits = []
        style = _RUNNING_STYLES.get(m.running_style or 0)
        if style:
            meta_bits.append(style)
        if m.talent_level:
            meta_bits.append(f"Lv.{m.talent_level}")
        if meta_bits:
            self._text(draw, (cx, ty + 100 * S), "  ·  ".join(meta_bits),
                       fonts["meta"], t["muted"], anchor="mm")

        # Stats row (5 cells)
        inner = tw - 16 * S
        sx = tx + 8 * S
        stats = [("SPD", m.speed), ("STA", m.stamina), ("POW", m.power),
                 ("GUTS", m.guts), ("WIT", m.wiz)]
        cw = inner // len(stats)
        for i, (label, value) in enumerate(stats):
            ccx = sx + cw * i + cw // 2
            self._text(draw, (ccx, ty + 118 * S), label, fonts["stat_label"], t["muted"], anchor="mm")
            self._text(draw, (ccx, ty + 134 * S), str(int(value or 0)), fonts["stat_value"], t["title"], anchor="mm")

        # Matching aptitude pair (2 cells, centered)
        apt = m.category_aptitudes()
        acw = inner // 2
        for i, (label, val) in enumerate(apt):
            ccx = sx + acw * i + acw // 2
            letter = _grade_letter(val)
            self._text(draw, (ccx, ty + 156 * S), label, fonts["apt_label"], t["subtle"], anchor="mm")
            self._text(draw, (ccx, ty + 174 * S), letter, fonts["apt_grade"], _grade_color(letter), anchor="mm")

        # Footer: skill count (left) + score (right)
        fy = ty + th - 13 * S
        self._text(draw, (tx + 12 * S, fy), f"{m.skill_count} skills",
                   fonts["foot"], t["muted"], anchor="lm")
        score = m.team_rating or m.rank_score
        if score:
            self._text(draw, (tx + tw - 12 * S, fy), _short(score), fonts["foot"], _GOLD, anchor="rm")

    def _draw_empty(self, draw, fonts, y):
        S, t = self.S, self.t
        cx = self.content_x + self.content_w // 2
        self._text(draw, (cx, y + 40 * S), "No Team Trials data available",
                   fonts["sub"], t["muted"], anchor="mm")

    def _draw_footer(self, draw, fonts, y):
        S, t, d = self.S, self.t, self.d
        x = self.content_x
        self._text(draw, (x, y + 10 * S), "Team Stadium roster", fonts["footer"], t["subtle"], anchor="lm")
        src = "Data: uma.moe + UmaCore" if d.has_api_data else "Data: UmaCore"
        self._text(draw, (x + self.content_w, y + 10 * S), src, fonts["footer"], t["subtle"], anchor="rm")

    # ── entry point ───────────────────────────────────────────────────────────

    def draw(self, out_path: Path):
        S, t = self.S, self.t
        fonts = {
            "name":        load_font(30 * S, bold=True,  game=True),
            "sub":         load_font(14 * S, bold=False, game=True),
            "pill":        load_font(13 * S, bold=True,  game=False),
            "band":        load_font(14 * S, bold=True,  game=False),
            "rank":        load_font(12 * S, bold=True,  game=False),
            "stars":       load_font(12 * S, bold=True,  game=False),
            "meta":        load_font(11 * S, bold=True,  game=True),
            "stat_label":  load_font(9 * S,  bold=True,  game=False),
            "stat_value":  load_font(13 * S, bold=True,  game=True),
            "apt_label":   load_font(10 * S, bold=True,  game=False),
            "apt_grade":   load_font(16 * S, bold=True,  game=True),
            "foot":        load_font(11 * S, bold=True,  game=True),
            "footer":      load_font(12 * S, bold=False, game=True),
        }

        img = Image.new("RGB", (self.img_w, self.img_h), t["bg"])
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, self.img_w, (_PAD + _HEADER_H) * S), fill=t["panel"])

        y = _PAD * S
        self._draw_header(draw, fonts, y)
        y += (_HEADER_H + 2 + _GAP) * S

        if not self.bands:
            self._draw_empty(draw, fonts, y)
            y += 80 * S
        else:
            col_w = _COL_W * S
            tiles_y = y + _CAT_H * S
            for ci, (label, members) in enumerate(self.bands):
                col_x = self.content_x + ci * (_COL_W + _COL_GAP) * S
                self._draw_col_header(draw, fonts, col_x, y, col_w, label)
                for ri, m in enumerate(members):
                    tyy = tiles_y + ri * (_TILE_H + _TILE_GAP) * S
                    self._draw_tile(img, draw, fonts, m, col_x, tyy, col_w)
            max_rows = max((len(m) for _, m in self.bands), default=1)
            y = tiles_y + (max_rows * _TILE_H + (max_rows - 1) * _TILE_GAP) * S

        y += _GAP * S
        self._draw_footer(draw, fonts, y)
        img.save(out_path)


def render(data: TeamCardData, out_path: Path) -> None:
    """Render a team-trials card PNG to ``out_path``."""
    _Renderer(data).draw(out_path)
