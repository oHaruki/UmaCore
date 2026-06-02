"""Tally image renderer — compact leaderboard layout."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

from ..types import Config, MemberReport, Circle
from .base import fmt_blank_if_zero, fmt_int, load_font
from .themes import THEME

DISPLAY_ROWS = 30
SCALE = 2

# 1× logical pixel constants
_PAD_X     = 28
_PAD_Y     = 16
_TITLE_H   = 76
_COL_H     = 34    # column-header row height
_DIVIDER   = 2     # accent line below headers
_ROW_H     = 34    # data row height
_SUMMARY_H = 34    # totals row at the bottom
_DOT_R     = 6     # status dot radius


@dataclass(frozen=True)
class _Col:
    key:   str
    label: str
    width: int    # logical pixels
    align: str    # "left" | "right" | "center"


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_short(n: int) -> str:
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.0f}M" if v == int(v) else f"{v:.1f}M"
    if n >= 1_000:
        v = n / 1_000
        return f"{v:.0f}K" if v == int(v) else f"{v:.1f}K"
    return str(n)


def _quota_label(total: int, quota: int) -> str:
    def M(n: int) -> str:
        v = n / 1_000_000
        return f"{v:.0f}" if v == int(v) else f"{v:.1f}"
    return f"{M(total)}/{M(quota)}M"


def _blend(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _quota_ratio(row: MemberReport) -> float:
    return row.total / row.quota_total if row.quota_total > 0 else 0.0


def _prev_ratio(row: MemberReport) -> float:
    return row.previous_total / row.quota_total if row.quota_total > 0 else 0.0


def _status_color(row: MemberReport, config: Config) -> tuple:
    if row.pill_tier == "done":
        return config.finished_color
    if row.pill_tier == "yes":
        return config.on_pace_color
    return config.off_pace_color


def _deficit_color(row: MemberReport, quota_per_day: int, theme: dict) -> tuple:
    stops = theme["severity_steps"]
    if row.off_by <= 0 or quota_per_day <= 0:
        return stops[0]
    d = row.off_by / quota_per_day
    return stops[0] if d < 1 else (stops[1] if d < 3 else stops[2])


def _low_days_color(low_days: int, days_elapsed: int, theme: dict) -> tuple:
    stops = theme["severity_steps"]
    if low_days <= 0 or days_elapsed <= 0:
        return theme["subtle"]
    sev = min(1.0, (low_days / days_elapsed) / 0.3)
    idx = min(int(sev * (len(stops) - 1) + 1e-9), len(stops) - 1)
    return stops[idx]


def _build_columns(latest_day: int, threshold: int, config: Config) -> list:
    day_label = f"Day {latest_day or 1}"
    thr_label = f"<{_fmt_short(threshold)}"
    optional = [
        ("daily_avg",      "Avg/Day",  116, "right", config.show_daily_avg),
        ("needed_per_day", "Need/Day", 116, "right", config.show_needed_per_day),
        ("low_days",       thr_label,   96, "right", config.show_days_below_threshold),
        ("latest_day",     day_label,  106, "right", config.show_latest_day),
    ]
    hidden_w = sum(w for _, _, w, _, shown in optional if not shown)

    cols = [_Col("rank", "#", 82, "left"), _Col("name", "Trainer", 200, "left")]
    if config.expected_fans_style == "bar":
        cols.append(_Col("bar", "Progress", 260 + hidden_w, "left"))
        cols.append(_Col("quota", "Quota", 108, "right"))
    else:
        cols += [
            _Col("total",    "Total",    134, "right"),
            _Col("expected", "Expected", 134, "right"),
            _Col("deficit",  "Behind",   114, "right"),
        ]
    cols += [_Col(k, l, w, a) for k, l, w, a, shown in optional if shown]
    if config.show_on_pace:
        cols.append(_Col("dot", "", 38, "center"))
    return cols


# ── renderer ──────────────────────────────────────────────────────────────────

class _Renderer:
    """Builds layout once in __init__, renders in a single pass via draw()."""

    def __init__(self, circle: Circle, reports: list, today: date, config: Config):
        self.circle = circle
        self.config = config
        self.today  = today
        self.theme  = THEME
        S = self.S  = SCALE

        self.rows  = self._sorted(reports)
        self.shown = self.rows[:DISPLAY_ROWS]
        self.prev_ranks  = self._compute_prev_ranks(self.rows)
        self.latest_day  = max((r.days_elapsed for r in self.rows), default=0)
        self.total_fans  = sum(r.total for r in self.rows)

        dim = monthrange(today.year, today.month)[1]
        self.days_in_month  = dim
        self.quota_per_day  = round(config.monthly_quota / dim) if dim else 0
        self.expected_ratio = min(1.0, max(0.0, self.latest_day / dim)) if dim else 0.0
        self.cols = _build_columns(self.latest_day, config.low_day_threshold, config)

        self.px        = _PAD_X   * S
        self.py        = _PAD_Y   * S
        self.row_h     = _ROW_H   * S
        self.col_h     = _COL_H   * S
        self.title_h   = _TITLE_H * S
        self.summary_h = _SUMMARY_H * S
        self.divider_h = _DIVIDER * S

        col_total  = sum(c.width for c in self.cols)
        self.img_w = (col_total + _PAD_X * 2) * S
        self.img_h = (
            _TITLE_H + _COL_H + _DIVIDER +
            _ROW_H * DISPLAY_ROWS +
            _SUMMARY_H + _PAD_Y * 2
        ) * S

    # ── sorting ───────────────────────────────────────────────────────────────

    def _sorted(self, rows: list) -> list:
        pin = self.config.pin_leader
        return sorted(
            rows,
            key=lambda r: (
                0 if (pin and r.staff_role == "leader") else 1,
                -_quota_ratio(r),
                r.trainer_name.lower(),
                r.viewer_id,
            ),
        )

    def _compute_prev_ranks(self, rows: list) -> dict:
        ranked = sorted(rows, key=lambda r: (-_prev_ratio(r), r.trainer_name.lower(), r.viewer_id))
        return {r.viewer_id: i + 1 for i, r in enumerate(ranked)}

    # ── draw primitives ───────────────────────────────────────────────────────

    def _cell(self, draw, x, mid_y, col_w, text, font, fill, align):
        S, pad = self.S, 10 * self.S
        if align == "left":
            draw.text((x + pad, mid_y), text, font=font, fill=fill, anchor="lm")
        elif align == "right":
            draw.text((x + col_w - pad, mid_y), text, font=font, fill=fill, anchor="rm")
        else:
            draw.text((x + col_w / 2, mid_y), text, font=font, fill=fill, anchor="mm")

    def _draw_col_headers(self, draw, y: int, fonts: dict):
        theme = self.theme
        S     = self.S
        px    = self.px
        h     = self.col_h

        x   = px
        mid = y + h // 2
        for col in self.cols:
            cw = col.width * S
            if col.label:
                self._cell(draw, x, mid, cw, col.label, fonts["header"], theme["muted"], col.align)
            x += cw

        # accent divider line
        dy = y + h
        draw.rectangle((px, dy, self.img_w - px, dy + self.divider_h), fill=theme["divider"])

    def _draw_rank_cell(self, draw, x, y, col_w, rank: int, row: Optional[MemberReport], font):
        S, theme = self.S, self.theme
        pad = 10 * S
        cy  = y + self.row_h // 2

        podium = theme["podium"].get(rank) if rank <= 3 else None

        movement = None
        if row is not None:
            prev = self.prev_ranks.get(row.viewer_id)
            if prev is not None:
                movement = prev - rank

        if podium:
            rank_text = f"#{rank}"
            fill = podium
        else:
            rank_text = str(rank)
            fill = theme["subtle"]

        draw.text((x + pad, cy), rank_text, font=font, fill=fill, anchor="lm")

        if not podium and movement is not None and movement != 0:
            rb = draw.textbbox((x + pad, cy), rank_text, font=font, anchor="lm")
            arrow = f"↑{movement}" if movement > 0 else f"↓{abs(movement)}"
            arrow_color = theme["rank_up"] if movement > 0 else theme["rank_down"]
            draw.text((rb[2] + 4 * S, cy), arrow, font=fonts["tiny"], fill=arrow_color, anchor="lm")

    def _draw_name_cell(self, draw, x, y, col_w, row: MemberReport, rank: int, font, tag_font):
        S, theme = self.S, self.theme
        pad  = 10 * S
        cy   = y + self.row_h // 2
        color = theme["podium"].get(rank) or theme["text"]
        nx = x + pad
        draw.text((nx, cy), row.trainer_name, font=font, fill=color, anchor="lm")
        if self.config.highlight_leader and row.staff_role == "leader":
            bbox = draw.textbbox((nx, cy), row.trainer_name, font=font, anchor="lm")
            draw.text((bbox[2] + 6 * S, cy), "ldr", font=tag_font, fill=theme["rank_flat"], anchor="lm")

    def _draw_progress_bar(self, draw, x, y, col_w, row: MemberReport, fonts: dict):
        """Thin compact bar with percentage label."""
        S, theme, config = self.S, self.theme, self.config
        pad   = 10 * S
        gap   = 8  * S
        bar_h = 12 * S

        ratio   = _quota_ratio(row)
        label   = f"{round(ratio * 100)}%"
        lb      = draw.textbbox((0, 0), label, font=fonts["cell"])
        label_w = lb[2] - lb[0]
        label_h = lb[3] - lb[1]

        bar_x = x + pad
        bar_y = y + (self.row_h - bar_h) // 2
        label_x = x + col_w - pad
        bar_w = max(40 * S, col_w - pad * 2 - gap - label_w)
        bar_w = min(bar_w, max(0, label_x - gap - bar_x))
        r     = bar_h // 2

        track = _blend(theme["panel"], theme["subtle"], 0.18)
        draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=r, fill=track)

        fill_w = int(round(bar_w * max(0.0, min(1.0, ratio))))
        if fill_w > 0:
            color = _status_color(row, config)
            draw.rounded_rectangle((bar_x, bar_y, bar_x + fill_w, bar_y + bar_h), radius=r, fill=color)

        # expected-position tick
        if 0 < self.expected_ratio < 1:
            tx = bar_x + int(bar_w * self.expected_ratio)
            tick = _blend(theme["subtle"], theme["text"], 0.45)
            draw.rectangle((tx - S, bar_y - S, tx + S, bar_y + bar_h + S), fill=tick)

        label_y = y + self.row_h / 2 + label_h / 2
        draw.text((label_x, label_y), label, font=fonts["cell"], fill=theme["text"], anchor="rs")

    def _draw_status_dot(self, draw, x, y, col_w, row: MemberReport):
        S   = self.S
        cx  = x + col_w * S // 2
        cy  = y + self.row_h // 2
        r   = _DOT_R * S
        color = _status_color(row, self.config)
        rim   = _blend(color, (0, 0, 0), 0.4)
        draw.ellipse((cx - r - S, cy - r - S, cx + r + S, cy + r + S), fill=rim)
        draw.ellipse((cx - r,     cy - r,     cx + r,     cy + r    ), fill=color)

    def _cell_value(self, key: str, row: MemberReport) -> tuple:
        theme = self.theme
        dc = _deficit_color(row, self.quota_per_day, theme)
        match key:
            case "total":
                return fmt_int(row.total), theme["text"]
            case "expected":
                return fmt_int(row.expected_so_far), theme["subtle"]
            case "deficit":
                return fmt_blank_if_zero(row.off_by), dc
            case "quota":
                return _quota_label(row.total, row.quota_total), theme["text"]
            case "daily_avg":
                return fmt_int(int(row.daily_avg)), theme["subtle"]
            case "needed_per_day":
                v = "" if row.on_target else fmt_blank_if_zero(int(row.needed_per_day))
                return v, dc
            case "low_days":
                lc = _low_days_color(row.low_days, row.days_elapsed, theme)
                return (str(row.low_days) if row.low_days else "0"), lc
            case "latest_day":
                return fmt_int(row.latest_day_delta), theme["subtle"]
            case _:
                return "", theme["subtle"]

    def _draw_summary(self, draw, y: int, fonts: dict):
        """Club-wide totals row."""
        S, theme, config = self.S, self.theme, self.config
        px = self.px
        h  = self.summary_h
        mid = y + h // 2

        draw.rectangle((px, y, self.img_w - px, y + h), fill=theme["summary_bg"])
        # thin top line to separate from data rows
        draw.rectangle((px, y, self.img_w - px, y + S), fill=theme["divider"])

        x = px
        for col in self.cols:
            cw = col.width * S
            match col.key:
                case "rank":
                    self._cell(draw, x, mid, cw, "Σ", fonts["cell"], theme["muted"], "left")
                case "name":
                    self._cell(draw, x, mid, cw, f"{len(self.rows)} members", fonts["cell"], theme["muted"], "left")
                case "total":
                    self._cell(draw, x, mid, cw, _fmt_short(self.total_fans), fonts["cell"], theme["text"], "right")
                case "bar":
                    club_ratio = (self.total_fans / (config.monthly_quota * len(self.rows))) if self.rows else 0
                    self._cell(draw, x, mid, cw, f"{round(club_ratio * 100)}% avg", fonts["cell"], theme["muted"], "left")
            x += cw

    # ── public entry point ────────────────────────────────────────────────────

    def draw(self, out_path: Path, *, rank_icon: Optional[Path] = None):
        S, theme, config = self.S, self.theme, self.config
        game = theme["use_display_font"]

        fonts = {
            "title":      load_font(36 * S, bold=True,  game=game),
            "sub":        load_font(14 * S, bold=False, game=game),
            "header":     load_font(13 * S, bold=True,  game=game),
            "cell":       load_font(14 * S, bold=True,  game=game),
            "name":       load_font(15 * S, bold=True,  game=game),
            "rank":       load_font(14 * S, bold=True),
            "tiny":       load_font(11 * S, bold=True),
            "leader_tag": load_font(10 * S, bold=True),
        }

        img  = Image.new("RGB", (self.img_w, self.img_h), theme["bg"])
        draw = ImageDraw.Draw(img)

        # ── title area ───────────────────────────────────────────────────────
        px, py   = self.px, self.py
        name_x   = px
        name_y   = py
        block_h  = _TITLE_H * S
        logo_btm = py + block_h

        if config.club_logo and config.club_logo.exists():
            with Image.open(config.club_logo) as raw:
                logo = raw.convert("RGBA")
            logo_sz    = 72 * S
            squeezed_w = int(logo.size[0] * 0.93)
            logo       = logo.resize((squeezed_w, logo.size[1]), Image.LANCZOS)
            sc         = logo_sz / logo.size[1]
            lw         = int(logo.size[0] * sc)
            logo       = logo.resize((lw, logo_sz), Image.LANCZOS)
            canvas     = Image.new("RGBA", (logo_sz, logo_sz), (0, 0, 0, 0))
            canvas.paste(logo, ((logo_sz - lw) // 2, 0), logo)
            logo_y     = py + (block_h - logo_sz) // 2
            img.paste(canvas, (px, logo_y), canvas)
            name_x = px + logo_sz + 14 * S
            logo_btm = logo_y + logo_sz

        if rank_icon and rank_icon.exists():
            with Image.open(rank_icon) as raw:
                ri = raw.convert("RGBA")
            ri_sz = 44 * S
            rw, rh = ri.size
            sc = ri_sz / max(rw, rh)
            ri = ri.resize((int(rw * sc), int(rh * sc)), Image.LANCZOS)
            cap    = fonts["title"].getbbox("X")
            cap_cy = name_y + (cap[1] + cap[3]) / 2
            ri_y   = int(cap_cy - ri.size[1] / 2)
            img.paste(ri, (name_x, ri_y), ri)
            name_x += ri.size[0] + 10 * S

        draw.text((name_x, name_y), self.circle.name, font=fonts["title"], fill=theme["title"])

        rank_part = f"Rank #{self.circle.monthly_rank}  ·  " if self.circle.monthly_rank else ""
        sub = (
            f"{self.today.strftime('%B %d, %Y')}  ·  "
            f"{rank_part}"
            f"{len(self.rows)} members  ·  "
            f"Quota {_fmt_short(config.monthly_quota)}  ({_fmt_short(self.quota_per_day)}/day)"
        )
        sb    = draw.textbbox((name_x, 0), sub, font=fonts["sub"])
        sub_y = logo_btm - (sb[3] - sb[1])
        draw.text((name_x, sub_y), sub, font=fonts["sub"], fill=theme["muted"])

        # ── column headers + divider ─────────────────────────────────────────
        y = self.title_h + py
        self._draw_col_headers(draw, y, fonts)
        y += self.col_h + self.divider_h

        # ── data rows ────────────────────────────────────────────────────────
        for i in range(DISPLAY_ROWS):
            row = self.shown[i] if i < len(self.shown) else None
            bg  = theme["panel"] if i % 2 == 0 else theme["row_alt"]
            draw.rectangle((px, y, self.img_w - px, y + self.row_h), fill=bg)

            x    = px
            rank = i + 1
            mid  = y + self.row_h // 2

            for col in self.cols:
                cw = col.width * S

                if col.key == "rank":
                    self._draw_rank_cell(draw, x, y, cw, rank, row, fonts["rank"])

                elif row is None:
                    pass

                elif col.key == "name":
                    self._draw_name_cell(draw, x, y, cw, row, rank, fonts["name"], fonts["leader_tag"])

                elif col.key == "bar":
                    self._draw_progress_bar(draw, x, y, cw, row, fonts)

                elif col.key == "dot":
                    self._draw_status_dot(draw, x, y, col.width, row)

                else:
                    text, fill = self._cell_value(col.key, row)
                    self._cell(draw, x, mid, cw, text, fonts["cell"], fill, col.align)

                x += cw
            y += self.row_h

        # ── summary row ──────────────────────────────────────────────────────
        self._draw_summary(draw, y, fonts)

        img.save(out_path)


# ── public API ────────────────────────────────────────────────────────────────

def render(
    circle: Circle,
    reports: list,
    today: date,
    config: Config,
    out_path: Path,
    *,
    rank_icon: Optional[Path] = None,
) -> None:
    _Renderer(circle, reports, today, config).draw(out_path, rank_icon=rank_icon)
