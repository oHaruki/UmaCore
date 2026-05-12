"""Classic spreadsheet-style report. Renders at 2× and saves at 2× for retina output.
Adapted from rattenschwe1f/uma-fan-tally-tool (credit: rat [TTA]).
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw

from ..types import Config, MemberReport, Circle
from .base import fmt_blank_if_zero, fmt_int, load_font
from .themes import THEME

DISPLAY_ROWS = 30


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _severity_color(stops: list, severity: float) -> tuple:
    severity = max(0.0, min(1.0, severity))
    n = len(stops) - 1
    idx = min(int(severity * n + 1e-9), n)
    return stops[idx]


def _row_color(off_by: int, quota_per_day: int, theme: dict) -> tuple:
    stops = theme["severity_steps"]
    if off_by <= 0 or quota_per_day <= 0:
        return stops[0]
    days_behind = off_by / quota_per_day
    if days_behind < 1:
        return stops[0]
    if days_behind < 3:
        return stops[1]
    return stops[2]


def _low_days_color(low_days: int, days_elapsed: int, theme: dict) -> tuple:
    if low_days <= 0 or days_elapsed <= 0:
        return theme["subtle"]
    severity = min(1.0, (low_days / days_elapsed) / 0.3)
    return _severity_color(theme["severity_steps"], severity)


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    width: int
    align: str


def _short_amount(n: int, *, trim_zero: bool = True) -> str:
    if n >= 1_000_000:
        v = n / 1_000_000
        if trim_zero and v == int(v):
            return f"{v:.0f}M"
        return f"{v:.1f}M"
    if n >= 1000:
        v = n / 1000
        if trim_zero and v == int(v):
            return f"{v:.0f}K"
        return f"{v:.1f}K"
    return str(n)


def _columns(latest_day: int, low_day_threshold: int, config: Config) -> list:
    day_label = f"Day {latest_day}" if latest_day else "Day 1"
    low_label = f"Days < {_short_amount(low_day_threshold)}"
    optional = [
        ("daily_avg",    "Daily Avg",   180, "right",  config.show_daily_avg),
        ("on_pace",      "On Pace?",    150, "center", config.show_on_pace),
        ("needed_per_day", "Needed/Day", 180, "right", config.show_needed_per_day),
        ("low_days",     low_label,     160, "right",  config.show_days_below_threshold),
        ("latest_day",   day_label,     170, "right",  config.show_latest_day),
    ]
    columns = [Column("rank", "#", 110, "left"), Column("trainer", "Trainer", 260, "left")]
    if config.expected_fans_style == "bar":
        hidden_width = sum(w for _, _, w, _, shown in optional if not shown)
        columns.append(Column("progress", "Progress", 380 + hidden_width, "left"))
        columns.append(Column("quota", "Quota", 130, "right"))
        columns.append(Column("current_quota", "Today", 140, "right"))
    else:
        columns.extend([
            Column("expected", "Expected", 180, "right"),
            Column("total",    "Total Fans", 180, "right"),
            Column("behind",   "Behind By",  170, "right"),
        ])
    columns.extend(Column(k, l, w, a) for k, l, w, a, shown in optional if shown)
    return columns


PAD_X = 32
PAD_Y = 24
ROW_H = 54
HEADER_H = 62
TITLE_H = 108
SCALE = 2


def _draw_text_aligned(draw, cell_xywh, text, font, fill, align):
    x, y, w, h = cell_xywh
    pad = 14 * SCALE
    descent = font.getmetrics()[1]
    cy = y + h / 2 - descent / 2
    if align == "left":
        draw.text((x + pad, cy), text, font=font, fill=fill, anchor="lm")
    elif align == "right":
        draw.text((x + w - pad, cy), text, font=font, fill=fill, anchor="rm")
    else:
        draw.text((x + w / 2, cy), text, font=font, fill=fill, anchor="mm")


def _draw_header_banner(img, xy, theme):
    x0, y0, x1, y1 = xy
    w, h = x1 - x0, y1 - y0
    radius = 14 * SCALE
    stripe = 4 * SCALE
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rectangle((0, 0, w, h), fill=theme["header_bg"])
    ld.rectangle((0, 0, w, stripe), fill=theme["header_top"])
    ld.rectangle((0, h - stripe, w, h), fill=theme["header_bottom"])
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    md.rectangle((0, h - radius, w, h), fill=255)
    img.paste(layer, (x0, y0), mask)


def _pill(draw, img, xy, w, h, text, font, fill, fg):
    x, y = xy
    ss = 4
    W, H = w * ss, h * ss
    ring = max(1, H // 22)
    outer = _lerp(fill, (0, 0, 0), 0.35)
    inner = _lerp(fill, (255, 255, 255), 0.25)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2, fill=outer + (255,))
    ld.rounded_rectangle((ring, ring, W - ring - 1, H - ring - 1), radius=(H - ring * 2) // 2, fill=inner + (255,))
    ld.rounded_rectangle((ring * 2, ring * 2, W - ring * 2 - 1, H - ring * 2 - 1), radius=(H - ring * 4) // 2, fill=fill + (255,))
    layer = layer.resize((w, h), Image.LANCZOS)
    img.paste(layer, (x, y), layer)
    bx0, by0, bx1, by1 = font.getbbox(text)
    px = x + (w - (bx1 - bx0)) / 2 - bx0
    py = y + (h - (by1 - by0)) / 2 - by0
    draw.text((px, py), text, font=font, fill=fg)


def _quota_ratio(row: MemberReport) -> float:
    if row.quota_total <= 0:
        return 0.0
    return row.total / row.quota_total


def _previous_quota_ratio(row: MemberReport) -> float:
    if row.quota_total <= 0:
        return 0.0
    return row.previous_total / row.quota_total


def _status_color(total, quota, progress_ratio, expected_ratio, config: Config) -> tuple:
    if quota > 0 and progress_ratio >= 1:
        return config.finished_color
    return config.on_pace_color if progress_ratio >= expected_ratio else config.off_pace_color


def _draw_progress(draw, cell_xywh, row: MemberReport, expected_ratio: float, font, theme, config: Config):
    x, y, w, h = cell_xywh
    pad = 14 * SCALE
    gap = 12 * SCALE
    bar_h = 18 * SCALE
    progress_ratio = _quota_ratio(row)
    label = f"{round(progress_ratio * 100)}%"
    label_bbox = draw.textbbox((0, 0), label, font=font)
    label_w = label_bbox[2] - label_bbox[0]
    label_h = label_bbox[3] - label_bbox[1]
    bar_x = x + pad
    bar_y = y + (h - bar_h) // 2
    label_x = x + w - pad
    bar_w = max(80 * SCALE, w - pad * 2 - gap - label_w)
    bar_w = min(bar_w, max(0, label_x - gap - bar_x))
    radius = bar_h // 2
    pct = max(0.0, min(1.0, progress_ratio))
    fill_w = int(round(bar_w * pct))
    track = _lerp(theme["panel"], theme["subtle"], 0.25)
    outline = _lerp(theme["subtle"], theme["text"], 0.35)
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=radius, fill=track, outline=outline)
    if fill_w > 0:
        fill = _status_color(row.total, row.quota_total, progress_ratio, expected_ratio, config)
        draw.rounded_rectangle((bar_x, bar_y, bar_x + fill_w, bar_y + bar_h), radius=radius, fill=fill)
    label_y = y + h / 2 + label_h / 2
    draw.text((label_x, label_y), label, font=font, fill=theme["text"], anchor="rs")


def _previous_ranks(rows: list) -> dict:
    ranked = sorted(rows, key=lambda r: (-_previous_quota_ratio(r), r.trainer_name.lower(), r.viewer_id))
    return {r.viewer_id: i + 1 for i, r in enumerate(ranked)}


def _leader_sort_group(row: MemberReport, pin_leader: bool) -> int:
    if not pin_leader:
        return 2
    if row.staff_role == "leader":
        return 0
    return 1


def _sort_rows(rows: list, config: Config) -> list:
    return sorted(
        rows,
        key=lambda r: (_leader_sort_group(r, config.pin_leader), -_quota_ratio(r), r.trainer_name.lower(), r.viewer_id),
    )


def _podium_color(rank: int, theme: dict):
    return theme["podium"].get(rank)


def _draw_rank(draw, cell_xywh, rank: int, movement, font, theme):
    x, y, w, h = cell_xywh
    pad = 14 * SCALE
    descent = font.getmetrics()[1]
    cy = y + h / 2 - descent / 2
    podium = _podium_color(rank, theme)
    if podium:
        text, fill = f"#{rank}", podium
    elif movement is None:
        text, fill = str(rank), theme["subtle"]
    elif movement > 0:
        text, fill = f"{rank} ↑{movement}", theme["rank_up"]
    elif movement < 0:
        text, fill = f"{rank} ↓{abs(movement)}", theme["rank_down"]
    else:
        text, fill = f"{rank} -", theme["rank_flat"]
    draw.text((x + pad, cy), text, font=font, fill=fill, anchor="lm")


def _draw_trainer(draw, cell_xywh, name: str, leader: bool, rank: int, font, tag_font, theme):
    x, y, w, h = cell_xywh
    pad = 14 * SCALE
    descent = font.getmetrics()[1]
    cy = y + h / 2 - descent / 2
    name_x = x + pad
    draw.text((name_x, cy), name, font=font, fill=_podium_color(rank, theme) or theme["text"], anchor="lm")
    if not leader:
        return
    bbox = draw.textbbox((name_x, cy), name, font=font, anchor="lm")
    draw.text((bbox[2] + 8 * SCALE, cy), "leader", font=tag_font, fill=theme["rank_flat"], anchor="lm")


def render(
    circle: Circle,
    reports: list,
    today: date,
    config: Config,
    out_path: Path,
    *,
    rank_icon: Path | None = None,
) -> None:
    theme = THEME
    use_display_font = theme["use_display_font"]

    title_font      = load_font(44 * SCALE, bold=True, game=use_display_font)
    sub_font        = load_font(17 * SCALE, bold=True, game=use_display_font)
    header_font     = load_font(16 * SCALE, bold=True, game=use_display_font)
    cell_font       = load_font(18 * SCALE, bold=True, game=use_display_font)
    trainer_font    = load_font(18 * SCALE, bold=True, game=use_display_font)
    rank_font       = load_font(16 * SCALE, bold=True)
    leader_tag_font = load_font(11 * SCALE, bold=True)
    pill_font       = load_font(15 * SCALE, bold=True, game=use_display_font)

    rows = _sort_rows(reports, config)
    shown_rows = rows[:DISPLAY_ROWS]
    previous_ranks = _previous_ranks(rows)
    latest_day = max((r.days_elapsed for r in rows), default=0)
    columns = _columns(latest_day, config.low_day_threshold, config)
    width_1x = sum(c.width for c in columns) + PAD_X * 2
    height_1x = TITLE_H + HEADER_H + ROW_H * DISPLAY_ROWS + PAD_Y * 2

    width  = width_1x * SCALE
    height = height_1x * SCALE
    pad_x  = PAD_X * SCALE
    pad_y  = PAD_Y * SCALE
    row_h  = ROW_H * SCALE
    header_h = HEADER_H * SCALE
    title_h  = TITLE_H * SCALE

    img  = Image.new("RGB", (width, height), theme["bg"])
    draw = ImageDraw.Draw(img)

    name_x = pad_x
    name_y = pad_y
    block_h = (40 + 22) * SCALE
    logo_bottom = pad_y

    if config.club_logo and config.club_logo.exists():
        with Image.open(config.club_logo) as raw_logo:
            logo = raw_logo.convert("RGBA")
        logo_size = 96 * SCALE
        squeezed_w = int(logo.size[0] * 0.93)
        logo = logo.resize((squeezed_w, logo.size[1]), Image.LANCZOS)
        scale = logo_size / logo.size[1]
        final_w = int(logo.size[0] * scale)
        logo = logo.resize((final_w, logo_size), Image.LANCZOS)
        canvas = Image.new("RGBA", (logo_size, logo_size), (0, 0, 0, 0))
        canvas.paste(logo, ((logo_size - final_w) // 2, 0), logo)
        logo = canvas
        logo_y = pad_y + (block_h - logo_size) // 2
        img.paste(logo, (pad_x, logo_y), logo)
        name_x = pad_x + logo_size + 18 * SCALE
        name_y = pad_y + 6 * SCALE
        logo_bottom = logo_y + logo_size
    sub_x = name_x

    if rank_icon and rank_icon.exists():
        with Image.open(rank_icon) as raw_rank:
            rank_img = raw_rank.convert("RGBA")
        rank_size = 56 * SCALE
        rw, rh = rank_img.size
        scale = rank_size / max(rw, rh)
        rank_img = rank_img.resize((int(rw * scale), int(rh * scale)), Image.LANCZOS)
        cap_bbox = title_font.getbbox("X")
        cap_center = name_y + (cap_bbox[1] + cap_bbox[3]) / 2
        rank_y = int(cap_center - rank_img.size[1] / 2)
        img.paste(rank_img, (name_x, rank_y), rank_img)
        name_x += rank_img.size[0] + 12 * SCALE

    draw.text((name_x, name_y), circle.name, font=title_font, fill=theme["title"])
    rank_str = f"Rank #{circle.monthly_rank}" if circle.monthly_rank else ""
    monthly_quota  = config.monthly_quota
    days_in_month  = monthrange(today.year, today.month)[1]
    quota_per_day  = round(monthly_quota / days_in_month)
    report_expected_ratio = 0 if days_in_month <= 0 else min(1.0, max(0.0, latest_day / days_in_month))
    quota_str = f"Quota {fmt_int(monthly_quota)}/month  ({_short_amount(quota_per_day)}/day pace)"
    sub = (
        f"{today.strftime('%B %d, %Y')}  ·  {rank_str}  ·  {len(rows)} active members"
        f"  ·  {quota_str}"
    )
    sub_bbox = draw.textbbox((sub_x, 0), sub, font=sub_font)
    sub_y = logo_bottom - (sub_bbox[3] - sub_bbox[1])
    draw.text((sub_x, sub_y), sub, font=sub_font, fill=theme["muted"])

    y = title_h + pad_y
    _draw_header_banner(img, (pad_x, y, width - pad_x, y + header_h), theme)
    x = pad_x
    for column in columns:
        _draw_text_aligned(draw, (x, y, column.width * SCALE, header_h), column.label, header_font, theme["text"], column.align)
        x += column.width * SCALE

    y += header_h
    for i in range(DISPLAY_ROWS):
        r = shown_rows[i] if i < len(shown_rows) else None
        bg = theme["panel"] if i % 2 == 0 else theme["row_alt"]
        draw.rectangle((pad_x, y, width - pad_x, y + row_h), fill=bg)
        x = pad_x

        cells = {}
        if r is not None:
            cells = {
                "trainer":   (r.trainer_name, "left",  theme["text"]),
                "expected":  (fmt_int(r.expected_so_far), "right", theme["subtle"]),
                "total":     (fmt_int(r.total), "right", theme["text"]),
                "daily_avg": (fmt_int(r.daily_avg), "right", theme["text"]),
                "behind": (
                    fmt_blank_if_zero(r.off_by), "right",
                    _row_color(r.off_by, quota_per_day, theme),
                ),
                "needed_per_day": (
                    fmt_blank_if_zero(int(r.needed_per_day)) if not r.on_target else "", "right",
                    _row_color(r.off_by, quota_per_day, theme),
                ),
                "low_days": (
                    str(r.low_days) if r.low_days else "0", "right",
                    _low_days_color(r.low_days, r.days_elapsed, theme),
                ),
                "latest_day": (fmt_int(r.latest_day_delta), "right", theme["text"]),
                "quota":         (_quota_label(r.total, r.quota_total), "right", theme["text"]),
                "current_quota": (_quota_label(r.total, r.expected_so_far) if r.expected_so_far > 0 else "", "right", theme["subtle"]),
            }

        for column in columns:
            wpx = column.width * SCALE
            if column.key == "rank":
                prev_rank = previous_ranks.get(r.viewer_id) if r is not None else None
                movement = None if r is None or prev_rank is None else prev_rank - (i + 1)
                _draw_rank(draw, (x, y, wpx, row_h), i + 1, movement, rank_font, theme)
            elif r is None:
                pass
            elif column.key == "trainer":
                show_leader = bool(config.highlight_leader and r.staff_role == "leader")
                _draw_trainer(draw, (x, y, wpx, row_h), r.trainer_name, show_leader, i + 1, trainer_font, leader_tag_font, theme)
            elif column.key == "progress":
                _draw_progress(draw, (x, y, wpx, row_h), r, report_expected_ratio, cell_font, theme, config)
            elif column.key == "on_pace":
                pill_w, pill_h = 80 * SCALE, 26 * SCALE
                px = x + (wpx - pill_w) // 2
                py = y + (row_h - pill_h) // 2
                tier_label = theme["pill_label"][r.pill_tier]
                tier_color = _status_color(r.total, r.quota_total, _quota_ratio(r), report_expected_ratio, config)
                _pill(draw, img, (px, py), pill_w, pill_h, tier_label, pill_font, tier_color, theme["pill_fg"])
            else:
                text, cell_align, color = cells[column.key]
                _draw_text_aligned(draw, (x, y, wpx, row_h), text or "", cell_font, color or theme["text"], cell_align)
            x += wpx
        y += row_h

    img.save(out_path)


def _quota_label(total: int, quota: int) -> str:
    def millions(n: int) -> str:
        v = n / 1_000_000
        return f"{v:.0f}" if v == int(v) else f"{v:.1f}"
    return f"{millions(total)}/{millions(quota)}M"
