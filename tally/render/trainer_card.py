"""Trainer card renderer — a single-member status card.

Renders one trainer's quota/standing into a clean PNG, reusing the same theme
and fonts as the club tally so the bot's imagery stays consistent. Local DB
values drive the card; optional uma.moe profile data (team rating, global fan
ranks, follower count) is layered on when present.

No emoji are drawn — the bundled fonts have no color-emoji glyphs — so status is
conveyed through colored pills, bars and accents instead.
"""
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

from .base import load_font
from .themes import THEME

SCALE = 2

# 1× logical-pixel layout constants
_W          = 768     # card width
_PAD        = 32
_GAP        = 18      # vertical gap between sections
_HEADER_H   = 66
_TILE_H     = 78
_QUOTA_H    = 96
_KV_HEADER  = 26      # column sub-header height
_KV_ROW     = 30      # one label/value row
_STRIP_H    = 40      # bottom meta strip
_FOOTER_H   = 24
_PILL_H     = 30
_BAR_H      = 16
_CHART_H    = 132     # fan-progression chart section (header + plot)
_CHART_PLOT = 92      # plot area height inside the chart section

# Status accent colors
_GREEN  = (74, 201, 126)
_GOLD   = (240, 196, 64)
_ORANGE = (232, 150, 56)
_RED    = (224, 84, 84)
_BLUE   = (72, 150, 220)


# ── data model ──────────────────────────────────────────────────────────────

@dataclass
class TrainerCardData:
    """Everything the card needs. API fields are optional best-effort extras."""
    # identity
    trainer_name: str
    trainer_id: Optional[str]
    club_name: str
    is_active: bool
    manually_deactivated: bool
    join_date: date
    last_updated: date

    # quota / progress (local DB)
    cumulative_fans: int
    expected_fans: int
    deficit_surplus: int
    days_behind: int
    daily_quota: int
    progress_pct: int

    # statistics (local DB)
    avg_daily: float
    best_day: int
    streak_days: int
    days_active: int
    club_rank: int
    club_total: int
    percentile: int

    # bomb state
    bomb_days_remaining: Optional[int] = None
    bomb_warning: bool = False

    # daily fan progression (chronological, local DB) for the sparkline
    daily_cumulative: list = field(default_factory=list)
    daily_expected: list = field(default_factory=list)

    # leader character portrait (resolved local PNG path, cached by card id)
    leader_chara_dress_id: Optional[int] = None
    portrait_path: Optional[str] = None

    # uma.moe enrichment (all optional)
    has_api_data: bool = False
    team_evaluation_point: Optional[int] = None
    team_class: Optional[int] = None
    follower_num: Optional[int] = None
    rank_score: Optional[int] = None
    comment: Optional[str] = None
    club_monthly_rank: Optional[int] = None
    monthly_fan_rank: Optional[int] = None
    gain_30d: Optional[int] = None
    gain_30d_rank: Optional[int] = None
    alltime_rank: Optional[int] = None
    alltime_total_fans: Optional[int] = None


# ── formatting helpers ───────────────────────────────────────────────────────

def _fmt(n) -> str:
    return f"{int(n):,}"


def _short(n) -> str:
    n = int(n)
    a = abs(n)
    if a >= 1_000_000_000:
        v = n / 1_000_000_000
        return (f"{v:.0f}B" if v == int(v) else f"{v:.2f}B")
    if a >= 1_000_000:
        v = n / 1_000_000
        return (f"{v:.0f}M" if v == int(v) else f"{v:.1f}M")
    if a >= 1_000:
        v = n / 1_000
        return (f"{v:.0f}K" if v == int(v) else f"{v:.1f}K")
    return str(n)


def _rank(n: Optional[int]) -> str:
    return f"#{_fmt(n)}" if n else "—"


def _blend(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ── renderer ─────────────────────────────────────────────────────────────────

class _Renderer:
    def __init__(self, data: TrainerCardData):
        self.d = data
        self.t = THEME
        self.S = SCALE

        # Build the dynamic content first so we can size the canvas to fit.
        self.tiles = self._build_tiles()
        self.left_rows = self._build_left_rows()
        self.right_rows = self._build_right_rows()
        self.kv_rows = max(len(self.left_rows), len(self.right_rows))

        self.kv_h = _KV_HEADER + self.kv_rows * _KV_ROW
        self.has_chart = len(data.daily_cumulative) >= 2
        self.has_portrait = bool(data.portrait_path) and os.path.exists(data.portrait_path)

        h = (
            _PAD + _HEADER_H + 2          # header + divider
            + _GAP + _TILE_H
            + _GAP + _QUOTA_H
            + (_GAP + _CHART_H if self.has_chart else 0)
            + _GAP + self.kv_h
            + _GAP + _STRIP_H
            + _GAP + _FOOTER_H
            + _PAD
        )
        self.img_w = _W * self.S
        self.img_h = h * self.S
        self.content_x = _PAD * self.S
        self.content_w = (_W - 2 * _PAD) * self.S

    # ── status helpers ────────────────────────────────────────────────────────

    def _status(self) -> tuple:
        """Return (label, color) for the header status pill."""
        d = self.d
        if not d.is_active:
            return ("INACTIVE", self.t["subtle"])
        if d.bomb_days_remaining is not None:
            return (f"BOMB · {d.bomb_days_remaining}D LEFT", _RED)
        if d.deficit_surplus < 0:
            return ("BEHIND", _ORANGE)
        if d.bomb_warning:
            return ("1 DAY TO BOMB", _GOLD)
        if d.progress_pct >= 100:
            return ("QUOTA MET", _GOLD)
        return ("ON TRACK", _GREEN)

    def _bar_color(self) -> tuple:
        d = self.d
        if d.bomb_days_remaining is not None:
            return _RED
        if d.deficit_surplus < 0:
            return _ORANGE
        if d.progress_pct >= 100:
            return _GOLD
        return _GREEN

    # ── content builders ──────────────────────────────────────────────────────

    def _build_tiles(self) -> list:
        d = self.d
        if d.has_api_data:
            return [
                ("TEAM RATING", _short(d.team_evaluation_point) if d.team_evaluation_point else "—"),
                ("FOLLOWERS", _fmt(d.follower_num) if d.follower_num is not None else "—"),
                ("RANK SCORE", _short(d.rank_score) if d.rank_score else "—"),
            ]
        return [
            ("MONTHLY FANS", _short(d.cumulative_fans)),
            ("CLUB RANK", f"#{d.club_rank}/{d.club_total}" if d.club_total else "—"),
            ("STREAK", f"{d.streak_days}d"),
        ]

    def _build_left_rows(self) -> list:
        """PERFORMANCE column: (label, value, color)."""
        d = self.t
        dd = self.d
        if dd.deficit_surplus >= 0:
            deficit_label, deficit_val, deficit_color = (
                "Surplus", f"+{_short(dd.deficit_surplus)}", _GREEN
            )
        else:
            deficit_label, deficit_val, deficit_color = (
                "Deficit", f"-{_short(abs(dd.deficit_surplus))}", _ORANGE
            )
        behind_color = _RED if dd.days_behind >= 2 else (_ORANGE if dd.days_behind == 1 else d["text"])
        return [
            ("Daily Quota", _short(dd.daily_quota), d["text"]),
            (deficit_label, deficit_val, deficit_color),
            ("Days Behind", str(dd.days_behind), behind_color),
            ("Avg / Day", _short(dd.avg_daily), d["text"]),
        ]

    def _build_right_rows(self) -> list:
        """STANDINGS column: priority pool, take first 4 available."""
        d = self.d
        text = self.t["text"]
        pool = []
        if d.has_api_data:
            pool.append(("Monthly Rank", _rank(d.monthly_fan_rank), text))
            if d.gain_30d is not None:
                pool.append(("30d Gain", _short(d.gain_30d), text))
            pool.append(("All-Time Rank", _rank(d.alltime_rank), text))
            if d.club_monthly_rank:
                pool.append(("Circle Rank", _rank(d.club_monthly_rank), text))
        pool.append(("Club Rank", f"#{d.club_rank}/{d.club_total}" if d.club_total else "—", text))
        pct = max(1, 100 - d.percentile)
        pool.append(("Percentile", f"Top {pct}%", text))
        return pool[:4]

    # ── primitive draws ───────────────────────────────────────────────────────

    def _text(self, draw, xy, s, font, fill, anchor="lm"):
        draw.text(xy, s, font=font, fill=fill, anchor=anchor)

    def _paste_avatar(self, img, y) -> int:
        """Crop the leader portrait to a circular avatar; return text x-offset (px)."""
        S, t, d = self.S, self.t, self.d
        size = 62 * S
        try:
            src = Image.open(d.portrait_path).convert("RGBA")
        except Exception:
            return 0

        w, h = src.size
        side = int(min(w, h) * 0.60)
        cx = w // 2
        top = int(h * 0.05)
        face = src.crop((cx - side // 2, top, cx + side // 2, top + side)).resize(
            (size, size), Image.LANCZOS
        )

        # Composite over a panel-colored tile so transparent areas read as a chip.
        tile = Image.new("RGBA", (size, size), tuple(t["panel"]) + (255,))
        comp = Image.alpha_composite(tile, face)

        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)

        ax = self.content_x
        ay = y + 2 * S
        img.paste(comp.convert("RGB"), (ax, ay), mask)

        # subtle ring
        ring = ImageDraw.Draw(img)
        ring.ellipse((ax, ay, ax + size - 1, ay + size - 1), outline=t["divider"], width=max(2, S))

        return size + 14 * S

    def _draw_header(self, draw, fonts, y, name_x_offset=0):
        S, t, d = self.S, self.t, self.d
        x = self.content_x + name_x_offset
        mid_name = y + 24 * S

        # status pill (right-aligned)
        label, color = self._status()
        pill_font = fonts["pill"]
        tb = draw.textbbox((0, 0), label, font=pill_font)
        tw = tb[2] - tb[0]
        pill_w = tw + 28 * S
        pill_h = _PILL_H * S
        pill_x1 = self.content_x + self.content_w
        pill_x0 = pill_x1 - pill_w
        pill_y0 = y
        pill_y1 = y + pill_h
        draw.rounded_rectangle(
            (pill_x0, pill_y0, pill_x1, pill_y1),
            radius=pill_h // 2, fill=color,
        )
        self._text(
            draw, ((pill_x0 + pill_x1) // 2, (pill_y0 + pill_y1) // 2),
            label, pill_font, (16, 16, 20), anchor="mm",
        )

        # trainer name (truncate so it never collides with the pill)
        name = d.trainer_name or "Unknown Trainer"
        name_font = fonts["name"]
        max_name_w = pill_x0 - x - 16 * S
        name = self._truncate(draw, name, name_font, max_name_w)
        self._text(draw, (x, mid_name), name, name_font, t["title"], anchor="lm")

        # subline: ID · Club · (circle rank)
        parts = []
        if d.trainer_id:
            parts.append(f"ID {d.trainer_id}")
        parts.append(d.club_name or "No Club")
        if d.has_api_data and d.club_monthly_rank:
            parts.append(f"Circle #{d.club_monthly_rank}")
        sub = "   ·   ".join(parts)
        sub = self._truncate(draw, sub, fonts["sub"], self.content_w)
        self._text(draw, (x, y + 50 * S), sub, fonts["sub"], t["muted"], anchor="lm")

        # accent divider (always full width)
        dy = y + _HEADER_H * S
        draw.rectangle(
            (self.content_x, dy, self.content_x + self.content_w, dy + 2 * S),
            fill=t["divider"],
        )

    def _truncate(self, draw, s, font, max_w):
        if draw.textlength(s, font=font) <= max_w:
            return s
        ell = "…"
        while s and draw.textlength(s + ell, font=font) > max_w:
            s = s[:-1]
        return s + ell

    def _draw_tiles(self, draw, fonts, y):
        S, t = self.S, self.t
        n = len(self.tiles)
        gap = 14 * S
        tile_w = (self.content_w - gap * (n - 1)) // n
        tile_h = _TILE_H * S
        x = self.content_x
        for label, value in self.tiles:
            draw.rounded_rectangle(
                (x, y, x + tile_w, y + tile_h),
                radius=10 * S, fill=t["panel"],
            )
            cx = x + tile_w // 2
            self._text(draw, (cx, y + 24 * S), label, fonts["tile_label"], t["muted"], anchor="mm")
            self._text(draw, (cx, y + 52 * S), value, fonts["tile_value"], t["title"], anchor="mm")
            x += tile_w + gap

    def _draw_quota(self, draw, fonts, y):
        S, t, d = self.S, self.t, self.d
        x = self.content_x
        w = self.content_w

        self._text(draw, (x, y + 12 * S), "MONTHLY QUOTA", fonts["section"], t["muted"], anchor="lm")
        pct_txt = f"{d.progress_pct}%"
        self._text(draw, (x + w, y + 12 * S), pct_txt, fonts["section_val"], self._bar_color(), anchor="rm")

        # progress bar
        bar_y = y + 32 * S
        bar_h = _BAR_H * S
        r = bar_h // 2
        track = tuple(int(a + (b - a) * 0.18) for a, b in zip(t["panel"], t["subtle"]))
        draw.rounded_rectangle((x, bar_y, x + w, bar_y + bar_h), radius=r, fill=track)
        ratio = max(0.0, min(1.0, d.progress_pct / 100.0))
        fill_w = int(w * ratio)
        if fill_w > r * 2:
            draw.rounded_rectangle((x, bar_y, x + fill_w, bar_y + bar_h), radius=r, fill=self._bar_color())
        elif fill_w > 0:
            draw.rectangle((x, bar_y, x + fill_w, bar_y + bar_h), fill=self._bar_color())

        # under-bar labels
        lbl_y = bar_y + bar_h + 16 * S
        cur_exp = f"{_short(d.cumulative_fans)} / {_short(d.expected_fans)}"
        self._text(draw, (x, lbl_y), cur_exp, fonts["small"], t["text"], anchor="lm")
        if d.deficit_surplus >= 0:
            self._text(draw, (x + w, lbl_y), f"+{_short(d.deficit_surplus)}", fonts["small"], _GREEN, anchor="rm")
        else:
            self._text(draw, (x + w, lbl_y), f"-{_short(abs(d.deficit_surplus))}", fonts["small"], _ORANGE, anchor="rm")

    def _draw_chart(self, draw, fonts, y):
        """Fan progression sparkline: actual cumulative vs expected over the month."""
        S, t, d = self.S, self.t, self.d
        x = self.content_x
        w = self.content_w

        self._text(draw, (x, y + 12 * S), "FAN PROGRESSION", fonts["section"], t["divider"], anchor="lm")
        last = d.daily_cumulative[-1]
        self._text(
            draw, (x + w, y + 12 * S),
            f"{_short(last)} · {d.last_updated.strftime('%b %d')}",
            fonts["chart_caption"], t["muted"], anchor="rm",
        )

        plot_top = y + _KV_HEADER * S + 6 * S
        plot_bot = y + _CHART_H * S - 6 * S
        plot_h = plot_bot - plot_top
        plot_w = w

        actual = d.daily_cumulative
        expected = d.daily_expected if len(d.daily_expected) == len(actual) else []
        n = len(actual)
        max_v = max([*actual, *(expected or [0])]) or 1

        def px(i):
            return x + (plot_w * i // (n - 1)) if n > 1 else x

        def py(v):
            return int(plot_bot - (v / max_v) * plot_h)

        # faint baseline + top gridline
        grid = _blend(t["panel"], t["subtle"], 0.35)
        draw.line((x, plot_bot, x + plot_w, plot_bot), fill=grid, width=S)
        draw.line((x, plot_top, x + plot_w, plot_top), fill=_blend(t["bg"], t["subtle"], 0.25), width=S)

        color = self._bar_color()

        # expected reference line (muted)
        if expected:
            exp_pts = [(px(i), py(expected[i])) for i in range(n)]
            if len(exp_pts) >= 2:
                draw.line(exp_pts, fill=t["subtle"], width=max(1, S))

        # actual: filled area + line + end dot
        act_pts = [(px(i), py(actual[i])) for i in range(n)]
        if len(act_pts) >= 2:
            poly = [(x, plot_bot)] + act_pts + [(x + plot_w, plot_bot)]
            draw.polygon(poly, fill=_blend(t["bg"], color, 0.22))
            draw.line(act_pts, fill=color, width=2 * S, joint="curve")
            ex, ey = act_pts[-1]
            r = 4 * S
            draw.ellipse((ex - r, ey - r, ex + r, ey + r), fill=color)

    def _draw_kv(self, draw, fonts, y):
        S, t = self.S, self.t
        gap = 28 * S
        col_w = (self.content_w - gap) // 2
        left_x = self.content_x
        right_x = self.content_x + col_w + gap

        self._draw_kv_col(draw, fonts, left_x, col_w, y, "PERFORMANCE", self.left_rows)
        self._draw_kv_col(draw, fonts, right_x, col_w, y, "STANDINGS", self.right_rows)

    def _draw_kv_col(self, draw, fonts, x, w, y, header, rows):
        S, t = self.S, self.t
        self._text(draw, (x, y + 12 * S), header, fonts["section"], t["divider"], anchor="lm")
        ry = y + _KV_HEADER * S
        for i, (label, value, color) in enumerate(rows):
            row_mid = ry + _KV_ROW * S // 2
            if i % 2 == 1:
                draw.rectangle((x - 6 * S, ry, x + w + 6 * S, ry + _KV_ROW * S), fill=t["row_alt"])
            self._text(draw, (x, row_mid), label, fonts["kv_label"], t["muted"], anchor="lm")
            self._text(draw, (x + w, row_mid), value, fonts["kv_value"], color, anchor="rm")
            ry += _KV_ROW * S

    def _draw_strip(self, draw, fonts, y):
        S, t, d = self.S, self.t, self.d
        x = self.content_x
        w = self.content_w
        h = _STRIP_H * S
        draw.rounded_rectangle((x, y, x + w, y + h), radius=10 * S, fill=t["summary_bg"])
        mid = y + h // 2
        items = [
            ("Best Day", f"+{_short(d.best_day)}"),
            ("Streak", f"{d.streak_days}d"),
            ("Days Active", str(d.days_active)),
            ("Joined", d.join_date.strftime("%b %d, %Y")),
        ]
        seg = w // len(items)
        for i, (label, value) in enumerate(items):
            cx = x + seg * i + seg // 2
            self._text(draw, (cx, mid - 8 * S), label.upper(), fonts["strip_label"], t["muted"], anchor="mm")
            self._text(draw, (cx, mid + 9 * S), value, fonts["strip_value"], t["text"], anchor="mm")
            if i > 0:
                lx = x + seg * i
                draw.rectangle((lx, y + 9 * S, lx + S, y + h - 9 * S), fill=t["subtle"])

    def _draw_footer(self, draw, fonts, y):
        S, t, d = self.S, self.t, self.d
        x = self.content_x
        left = f"Last updated {d.last_updated.strftime('%b %d, %Y')}"
        self._text(draw, (x, y + 10 * S), left, fonts["footer"], t["subtle"], anchor="lm")
        src = "Data: uma.moe + UmaCore" if d.has_api_data else "Data: UmaCore"
        self._text(draw, (x + self.content_w, y + 10 * S), src, fonts["footer"], t["subtle"], anchor="rm")

    # ── entry point ───────────────────────────────────────────────────────────

    def draw(self, out_path: Path):
        S, t = self.S, self.t
        fonts = {
            "name":        load_font(30 * S, bold=True,  game=True),
            "sub":         load_font(14 * S, bold=False, game=True),
            "pill":        load_font(13 * S, bold=True,  game=False),
            "tile_label":  load_font(12 * S, bold=True,  game=False),
            "tile_value":  load_font(24 * S, bold=True,  game=True),
            "section":     load_font(13 * S, bold=True,  game=False),
            "section_val": load_font(20 * S, bold=True,  game=True),
            "chart_caption": load_font(13 * S, bold=True, game=True),
            "small":       load_font(15 * S, bold=True,  game=True),
            "kv_label":    load_font(14 * S, bold=False, game=True),
            "kv_value":    load_font(15 * S, bold=True,  game=True),
            "strip_label": load_font(11 * S, bold=True,  game=False),
            "strip_value": load_font(15 * S, bold=True,  game=True),
            "footer":      load_font(12 * S, bold=False, game=True),
        }

        img = Image.new("RGB", (self.img_w, self.img_h), t["bg"])
        draw = ImageDraw.Draw(img)

        # Faint panel band behind the header so the name/ID read as a title bar.
        draw.rectangle((0, 0, self.img_w, (_PAD + _HEADER_H) * S), fill=t["panel"])

        y = _PAD * S
        name_offset = self._paste_avatar(img, y) if self.has_portrait else 0
        self._draw_header(draw, fonts, y, name_offset)
        y += (_HEADER_H + 2 + _GAP) * S

        self._draw_tiles(draw, fonts, y)
        y += (_TILE_H + _GAP) * S

        self._draw_quota(draw, fonts, y)
        y += (_QUOTA_H + _GAP) * S

        if self.has_chart:
            self._draw_chart(draw, fonts, y)
            y += (_CHART_H + _GAP) * S

        self._draw_kv(draw, fonts, y)
        y += (self.kv_h + _GAP) * S

        self._draw_strip(draw, fonts, y)
        y += (_STRIP_H + _GAP) * S

        self._draw_footer(draw, fonts, y)

        img.save(out_path)


def render(data: TrainerCardData, out_path: Path) -> None:
    """Render a trainer card PNG to ``out_path``."""
    _Renderer(data).draw(out_path)
