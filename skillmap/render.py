"""
Pillow renderer for skill maps, styled after umalator's course chart in a dark
theme: the elevation strip, slopes, corners and straights, race phases and the
game's 24 sections, each row labelled with the distance at every boundary.

Above the course sits the reference uma's speed, with the stretches where she is
still accelerating shaded green. Skills are drawn on the chart the way umalator
shows activations: a named box over the stretch where the skill is active,
stacked so boxes never overlap. Below the chart each skill also gets a lane
showing every spot it can fire, coloured by what a firing there wins (green: the
acceleration lands while she is still speeding up, blue: only the speed boost
helps, grey: nothing).
"""
from __future__ import annotations

import io
from typing import Optional

from PIL import Image, ImageDraw

from tally.render.base import load_font

from .analysis import KIND_RECOVERY, USEFUL_LENGTHS, SkillResult, TrackAnalysis, heal_amount
from .gamedata import STRATEGIES, CourseChoice
from .sim import Build, RaceModel, phase_start
from .text import effect_text, fires_text

SCALE = 2
BIN_HALF_WIDTH = 1.0        # metres; firing distributions are binned every 2 m
MAX_ROWS = 20
MAX_BOXES = 10          # skills drawn on the chart itself; the list below has the rest

W = 1200
PAD = 20
GUTTER = 330
X0 = PAD + GUTTER
X1 = W - PAD - 16
HEAD_H = 62
CHART_TITLE_H = 40
SPEED_H = 86
ELEV_H = 36
ROW_H_COURSE = 40
RULER_H = 32
AXIS_H = 26
ROW_H = 24
SKILL_H = 104
LINE_H = 15
BOX_H = 17

# page
BG = (22, 23, 26)
CARD = (34, 36, 40)
BORDER = (78, 128, 48)
LIME = (118, 190, 56)
CHART_TITLE = (240, 178, 108)
TEXT = (238, 240, 244)
MUTED = (168, 174, 184)
FAINT = (118, 124, 136)
# course chart
SPEED_BG = (28, 30, 34)
GRID = (46, 49, 55)
ELEV_FILL = (160, 198, 50)
ELEV_BASE = (112, 146, 24)
ELEV_TEXT = (36, 44, 10)
SLOPE_FLAT = (58, 50, 68)
DOWNHILL = (36, 150, 138)
UPHILL = (170, 132, 66)
SLOPE_LINE = (156, 104, 200)
STRAIGHT = (48, 84, 118)
STRAIGHT_LINE = (64, 158, 246)
CORNERS = ((148, 94, 56), (126, 80, 48))
CORNER_LINE = (246, 142, 32)
GAP = (44, 47, 53)
PHASE_FILL = ((10, 124, 86), (150, 134, 34), (162, 82, 122), (140, 62, 108))
PHASE_LINE = ((30, 196, 136), (230, 210, 64), (236, 126, 176), (214, 100, 164))
PHASE_NAMES = ("Early-race", "Mid-race", "Late-race", "Last spurt")
SEG_TEXT = (244, 240, 232)
EDGE_TEXT = (226, 228, 232)
RULER = (40, 44, 51)
RULER_LINE = (56, 61, 70)
RULER_TEXT = (122, 131, 146)
AXIS = (160, 166, 176)
SPEED_LINE = (244, 72, 92)
WINDOW = (46, 204, 113)
# firing colours
GREEN = (52, 196, 116)
BLUE = (78, 148, 234)
IDLE = (88, 93, 104)
BOX = {"green": (40, 160, 92), "blue": (54, 116, 204), "idle": (84, 89, 100)}
LANE = (28, 30, 34)
RARITY = {"White": (200, 204, 214), "Gold": (240, 190, 50), "Unique": (232, 110, 190),
          "Inherited unique": (170, 130, 240)}

_WINDOW_LABELS = {"Race start": "Start", "Last spurt": "Last-spurt kick", "Mid-race pace": "Pace change",
                  "Uphill ends": "Top of hill", "Hill eases": "Hill eases", "Speed change": "Speed change"}


def _blend(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class _Canvas:
    def __init__(self, height: float, model: RaceModel):
        self.S = SCALE
        self.img = Image.new("RGB", (W * SCALE, int(height) * SCALE), BG)
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.model = model
        self.dist = model.distance
        self.f = {
            "head": load_font(24 * SCALE, bold=True, game=True),
            "sub": load_font(13 * SCALE, game=True),
            "chart": load_font(21 * SCALE, bold=True, game=True),
            "seg": load_font(12 * SCALE, bold=True, game=True),
            "seg_arrow": load_font(12 * SCALE, bold=True),
            "edge": load_font(10 * SCALE, bold=True, game=True),
            "box": load_font(10 * SCALE, bold=True, game=True),
            "label": load_font(12 * SCALE, game=True),
            "label_b": load_font(12 * SCALE, bold=True, game=True),
            "small": load_font(10 * SCALE, game=True),
            "small_b": load_font(10 * SCALE, bold=True, game=True),
            "num": load_font(17 * SCALE, bold=True, game=True),
        }

    def px(self, metres: float) -> float:
        return X0 + (X1 - X0) * metres / self.dist

    def rect(self, x0, y0, x1, y1, fill, radius=0, outline=None, width=1):
        S = self.S
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            return
        box = [round(x0 * S), round(y0 * S), round(x1 * S) - 1, round(y1 * S) - 1]
        if radius:
            self.d.rounded_rectangle(box, radius=radius * S, fill=fill, outline=outline, width=width * S)
        else:
            self.d.rectangle(box, fill=fill, outline=outline, width=width * S if outline else 0)

    def line(self, pts, fill, width=1):
        S = self.S
        self.d.line([(x * S, y * S) for x, y in pts], fill=fill, width=max(1, round(width * S)), joint="curve")

    def poly(self, pts, fill):
        self.d.polygon([(x * self.S, y * self.S) for x, y in pts], fill=fill)

    def text(self, x, y, s, font, fill, anchor="lm"):
        self.d.text((x * self.S, y * self.S), s, font=self.f[font], fill=fill, anchor=anchor)

    def width(self, s, font) -> float:
        return self.d.textlength(s, font=self.f[font]) / self.S

    def runs(self, x, y, parts, fill, anchor="m"):
        """Draw [(text, font), ...] as one line; anchor l/m/r on x."""
        total = sum(self.width(t, f) for t, f in parts)
        cx = x if anchor == "l" else x - total / 2 if anchor == "m" else x - total
        for t, f in parts:
            self.text(cx, y, t, f, fill, "lm")
            cx += self.width(t, f)
        return total

    def fit(self, s, font, max_w) -> str:
        if self.width(s, font) <= max_w:
            return s
        while s and self.width(s + "…", font) > max_w:
            s = s[:-1]
        return s.rstrip() + "…" if s else ""

    def clip(self, s, font, max_w) -> str:
        """Cut text to fit without an ellipsis, the way umalator labels its skill boxes."""
        while s and self.width(s, font) > max_w:
            s = s[:-1]
        return s.rstrip()

    def chip(self, x_right, y, s, fg, bg) -> float:
        w = self.width(s, "label_b") + 22
        self.rect(x_right - w, y - 13, x_right, y + 13, bg, radius=13)
        self.text(x_right - w / 2, y, s, "label_b", fg, "mm")
        return w

    def png(self) -> bytes:
        buf = io.BytesIO()
        self.img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


# ── header and course chart ────────────────────────────────────────────────

def _header(c: _Canvas, title: str, subtitle: str, chips: list) -> float:
    c.rect(0, 0, W, HEAD_H, CARD)
    c.rect(0, HEAD_H - 3, W, HEAD_H, LIME)
    right = W - PAD
    for text in reversed(chips):
        right -= c.chip(right, HEAD_H / 2 - 2, text, (255, 255, 255), LIME) + 8
    c.text(PAD, 24, c.fit(title, "head", right - PAD - 10), "head", TEXT)
    c.text(PAD, 46, c.fit(subtitle, "sub", right - PAD - 10), "sub", MUTED)
    return HEAD_H + 14


def _course_title(course) -> str:
    turn = {1: "right-handed", 2: "left-handed", 4: "straight"}.get(course.turn, "")
    side = {2: ", inner", 3: ", outer", 4: ", outer→inner"}.get(course.inout, "")
    surface = " dirt" if course.surface == 2 else ""
    return f"{course.track} {course.distance}m{surface} ({turn}{side})"


def _elevation(course, x: float) -> float:
    """Metres above the start line at distance ``x``."""
    h = 0.0
    for s, e, pct in course.slopes:
        if x > s:
            h += pct / 100 * (min(x, e) - s)
    return h


def _segment_label(c: _Canvas, x0, x1, y0, y1, options, fill) -> None:
    """Centre the first label option that fits; options are lists of (text, font) runs."""
    for parts in options:
        if x1 - x0 >= sum(c.width(t, f) for t, f in parts) + 8:
            c.runs((x0 + x1) / 2, (y0 + y1) / 2 - 2, parts, fill)
            return


def _edges(c: _Canvas, xs: list, y: float) -> None:
    """Distance labels at segment boundaries, umalator-style: right-aligned on the edge."""
    last_right = -1e9
    for metres in sorted(set(xs)):
        if metres <= 0 or metres >= c.dist:
            continue
        label = f"{metres:,.0f}m"
        x = c.px(metres) - 3
        w = c.width(label, "edge")
        if x - w < last_right + 4 or x - w < X0 + 2:
            continue
        c.text(x, y, label, "edge", EDGE_TEXT, "rm")
        last_right = x


def _boxes(c: _Canvas, boxes: list, top: float, bottom: float) -> None:
    """Umalator-style skill boxes, stacked upward from ``bottom`` so none overlap."""
    lanes: list = []
    capacity = int((bottom - top) // (BOX_H + 2))
    for start, end, label, colour in boxes:
        x0, x1 = c.px(start), max(c.px(min(end, c.dist)), c.px(start) + 6)
        lane = next((i for i, taken in enumerate(lanes) if all(x0 >= b + 3 or x1 <= a - 3 for a, b in taken)), None)
        if lane is None:
            if len(lanes) >= capacity:
                continue
            lanes.append([])
            lane = len(lanes) - 1
        lanes[lane].append((x0, x1))
        y1 = bottom - 2 - lane * (BOX_H + 2)
        c.rect(x0, y1 - BOX_H, x1, y1, (*colour, 240), radius=3, outline=_blend(colour, (0, 0, 0), 0.45), width=1)
        name = c.clip(label, "box", x1 - x0 - 5)
        if name:
            c.text(x0 + 3, y1 - BOX_H / 2, name, "box", (255, 255, 255), "lm")


def _chart(c: _Canvas, choice: CourseChoice, y: float, boxes: Optional[list] = None) -> float:
    m, course, D = c.model, choice.course, c.dist
    rows_h = SPEED_H + ELEV_H + 3 * ROW_H_COURSE + RULER_H + AXIS_H
    card_top, card_bottom = y, y + CHART_TITLE_H + rows_h + 14
    c.rect(PAD, card_top, W - PAD, card_bottom, CARD, radius=12, outline=BORDER, width=2)
    c.text((X0 + X1) / 2, y + 22, _course_title(course), "chart", CHART_TITLE, "mm")
    y += CHART_TITLE_H

    # gutter: two facts that matter for timing, and the colour key
    gx = PAD + 16
    facts = [f"Last spurt from {m.spurt_start:,.0f}m"]
    if course.straights:
        s, e, _ = course.straights[-1]
        facts.append(f"Final straight {e - s:,.0f}m")
    for i, fact in enumerate(facts):
        c.text(gx, y + 10 + i * 18, fact, "label_b", TEXT)
    key = [("line", SPEED_LINE, "speed"), ("box", (*WINDOW, 150), "still speeding up"),
           ("box", BOX["green"], "acceleration lands"), ("box", BOX["blue"], "only speed helps"),
           ("box", BOX["idle"], "no effect")]
    for i, (kind, colour, label) in enumerate(key):
        ky = y + 58 + i * 17
        if kind == "line":
            c.line([(gx, ky), (gx + 14, ky)], colour, 2)
        else:
            c.rect(gx + 2, ky - 5, gx + 12, ky + 5, colour, radius=2)
        c.text(gx + 22, ky, label, "small", MUTED)

    # speed band
    top = y
    c.rect(X0, top, X1, top + SPEED_H, SPEED_BG)
    vmin, vmax = m.dash_cap - 0.6, m.spurt_speed + 0.6

    def vy(v):
        return top + SPEED_H - 6 - (SPEED_H - 26) * (max(vmin, min(vmax, v)) - vmin) / (vmax - vmin)
    for v in range(int(vmin) + 1, int(vmax) + 1):
        if v % 2 == 0:
            c.line([(X0, vy(v)), (X1, vy(v))], GRID, 1)
            c.text(X0 - 6, vy(v), str(v), "small", FAINT, "rm")
    c.text(X0 - 6, top + 8, "m/s", "small", FAINT, "rm")
    for w in m.windows:
        c.rect(c.px(w.start), top, max(c.px(w.end), c.px(w.start) + 2), top + SPEED_H, (*WINDOW, 46))
    labelled_to = X0 - 10
    for w in sorted(m.windows, key=lambda w: w.start):
        label = _WINDOW_LABELS.get(w.cause, w.cause)
        x = max(c.px(w.start) + 2, labelled_to + 4)
        lw = c.width(label, "small_b") + 10
        if x + lw <= X1:
            c.rect(x, top + 3, x + lw, top + 17, (*WINDOW, 220), radius=7)
            c.text(x + lw / 2, top + 10, label, "small_b", (10, 40, 22), "mm")
            labelled_to = x + lw
    steps = int((X1 - X0) * 2)
    c.line([(c.px(D * i / steps), vy(m.speed_at(D * i / steps))) for i in range(steps + 1)], SPEED_LINE, 2)
    y += SPEED_H

    # elevation strip
    heights = [_elevation(course, D * i / steps) for i in range(steps + 1)]
    lo, hi = min(heights), max(heights)
    room = ELEV_H - 12
    scale = room / (hi - lo) if hi > lo else 0.0
    base = y + ELEV_H - 5

    def ey(h):
        return base - 3 - (h - lo) * scale if scale else base - room / 2
    c.poly([(c.px(D * i / steps), ey(heights[i])) for i in range(steps + 1)] + [(X1, base), (X0, base)], ELEV_FILL)
    c.rect(X0, base, X1, base + 5, ELEV_BASE)
    for s, e, pct in course.slopes:
        label = f"{abs(pct):g}%"
        if c.px(e) - c.px(s) >= c.width(label, "edge") + 4:
            c.text(c.px((s + e) / 2), base - 8, label, "edge", ELEV_TEXT, "mm")
    y += ELEV_H
    rows_top = y

    # slopes
    row_top, row_bottom = y, y + ROW_H_COURSE
    c.rect(X0, row_top, X1, row_bottom, SLOPE_FLAT)
    for s, e, pct in course.slopes:
        x0, x1 = c.px(s), c.px(e)
        c.rect(x0, row_top, x1, row_bottom, UPHILL if pct > 0 else DOWNHILL)
        word, arrow = ("Uphill", "↗") if pct > 0 else ("Downhill", "↘")
        _segment_label(c, x0, x1, row_top, row_bottom, [[(f"{word} {arrow}", "seg")], [(arrow, "seg")]], SEG_TEXT)
    c.rect(X0, row_bottom - 3, X1, row_bottom, SLOPE_LINE)
    _edges(c, [v for s, e, _ in course.slopes for v in (s, e)], row_bottom - 10)
    y = row_bottom

    # corners and straights
    row_top, row_bottom = y, y + ROW_H_COURSE
    c.rect(X0, row_top, X1, row_bottom, GAP)
    for s, e, _ in course.straights:
        c.rect(c.px(s), row_top, c.px(e), row_bottom, STRAIGHT)
        c.rect(c.px(s), row_bottom - 3, c.px(e), row_bottom, STRAIGHT_LINE)
        _segment_label(c, c.px(s), c.px(e), row_top, row_bottom, [[("Straight →", "seg")], [("→", "seg")]],
                       SEG_TEXT)
    arrow = "↻" if course.turn == 1 else "↺"
    for i, (s, e) in enumerate(course.corners):
        number = 4 - (len(course.corners) - 1 - i) % 4
        c.rect(c.px(s), row_top, c.px(e), row_bottom, CORNERS[i % 2])
        c.rect(c.px(s), row_bottom - 3, c.px(e), row_bottom, CORNER_LINE)
        _segment_label(c, c.px(s), c.px(e), row_top, row_bottom,
                       [[("Corner ", "seg"), (arrow, "seg_arrow"), (str(number), "seg")],
                        [(arrow, "seg_arrow"), (str(number), "seg")]], SEG_TEXT)
    edges = [v for s, e, _ in course.straights for v in (s, e)] + [v for s, e in course.corners for v in (s, e)]
    _edges(c, edges, row_bottom - 10)
    y = row_bottom

    # race phases
    row_top, row_bottom = y, y + ROW_H_COURSE
    bounds = [phase_start(D, p) for p in range(4)] + [float(D)]
    for p in range(4):
        x0, x1 = c.px(bounds[p]), c.px(bounds[p + 1])
        c.rect(x0, row_top, x1, row_bottom, PHASE_FILL[p])
        c.rect(x0, row_bottom - 3, x1, row_bottom, PHASE_LINE[p])
        _segment_label(c, x0, x1, row_top, row_bottom, [[(PHASE_NAMES[p], "seg")], [(PHASE_NAMES[p][:4], "seg")]],
                       SEG_TEXT)
    _edges(c, bounds[1:4], row_bottom - 10)
    y = row_bottom

    # the game's 24 sections
    c.rect(X0, y, X1, y + RULER_H, RULER)
    section = (X1 - X0) / 24
    for i in range(24):
        x = X0 + i * section
        if i:
            c.line([(x, y), (x, y + RULER_H)], RULER_LINE, 1)
        c.text(x + section / 2, y + RULER_H / 2, str(i + 1), "small", RULER_TEXT, "mm")
    y += RULER_H
    if boxes:
        _boxes(c, boxes, rows_top, y)

    # distance axis
    c.line([(X0, y + 1), (X1, y + 1)], AXIS, 2)
    major = 100 if D <= 1600 else 200
    minor = major / 5
    mark = 0.0
    while mark <= D + 0.1:
        x = c.px(mark)
        big = abs(mark / major - round(mark / major)) < 1e-6
        c.line([(x, y + 1), (x, y + (8 if big else 5))], AXIS, 1)
        if big and (mark == D or D - mark > major * 0.6 or D % major == 0):
            c.text(x, y + 17, f"{mark:,.0f}", "small_b", TEXT, "mm")
        mark += minor
    if D % major:
        c.text(X1, y + 17, f"{D:,}", "small_b", TEXT, "rm")

    c.text(X0 - 8, top + SPEED_H + ELEV_H / 2, "height", "small_b", FAINT, "rm")
    for i, name in enumerate(("slope", "track", "phase")):
        c.text(X0 - 8, rows_top + (i + 0.5) * ROW_H_COURSE, name, "small_b", FAINT, "rm")
    c.text(X0 - 8, rows_top + 3 * ROW_H_COURSE + RULER_H / 2, "section", "small_b", FAINT, "rm")
    return card_bottom + 12


# ── skills ─────────────────────────────────────────────────────────────────

def _columns(c: _Canvas, r: SkillResult) -> list:
    """Per-pixel [mass, total gain, accel gain, coverage] of the firing distribution."""
    plot_w = int(X1 - X0) + 1
    cols = [[0.0, 0.0, 0.0, 0.0] for _ in range(plot_w)]
    point = len(r.dist) == 1
    scale = (X1 - X0) / c.dist
    for i, (x, p) in enumerate(r.dist):
        g = r.gains[i] if r.gains else 0.0
        ga = r.accel_gains[i] if r.accel_gains else 0.0
        hw = 0.0 if point else BIN_HALF_WIDTH
        a, b = (x - hw) * scale, (x + hw) * scale
        for col in range(max(0, int(a)), min(plot_w - 1, int(b)) + 1):
            overlap = 1.0 if b <= a else max(0.0, min(b, col + 1) - max(a, col))
            share = 1.0 if b <= a else overlap / (b - a)
            if share:
                cell = cols[col]
                cell[0] += p * share
                cell[1] += p * share * g
                cell[2] += p * share * ga
                cell[3] += overlap
    return cols


def _colour(r: SkillResult, x_m: float, gain: float, accel_gain: float, scale: float, spurt_m: float) -> tuple:
    if r.kind == KIND_RECOVERY:
        return BLUE if x_m < spurt_m else IDLE
    if accel_gain >= USEFUL_LENGTHS:
        return _blend(_blend(IDLE, GREEN, 0.55), GREEN, gain / max(scale, 0.01))
    if gain >= USEFUL_LENGTHS:
        return _blend(_blend(IDLE, BLUE, 0.55), BLUE, gain / max(scale, 0.01))
    return IDLE


def _box_colour(r: SkillResult, x_m: float, gain: float, accel_gain: float, spurt_m: float) -> tuple:
    if r.kind == KIND_RECOVERY:
        return BOX["blue"] if x_m < spurt_m else BOX["idle"]
    if accel_gain >= USEFUL_LENGTHS:
        return BOX["green"]
    return BOX["blue"] if gain >= USEFUL_LENGTHS else BOX["idle"]


def _skill_box(r: SkillResult, model: RaceModel, best: bool) -> Optional[tuple]:
    """(start, end, name, colour) for the skill fired at its best spot, or its typical (median) one."""
    if not r.possible or not r.dist:
        return None
    if best and r.gains and r.best_gain >= USEFUL_LENGTHS:
        i = max(range(len(r.gains)), key=lambda k: r.gains[k])
    else:
        acc, i = 0.0, len(r.dist) - 1
        for k, (_, p) in enumerate(r.dist):
            acc += p
            if acc >= 0.5:
                i = k
                break
    x_m = r.dist[i][0]
    if r.duration:
        end = model.position_at_time(model.time_at(x_m) + r.duration)
    else:
        end = x_m + model.distance / 60
    gain = r.gains[i] if r.gains else 0.0
    accel_gain = r.accel_gains[i] if r.accel_gains else 0.0
    colour = _box_colour(r, x_m, gain, accel_gain, model.spurt_start)
    return x_m, end, r.skill.name.replace(" (inherited)", ""), colour


def _lane(c: _Canvas, y0: float, y1: float) -> None:
    c.rect(X0, y0, X1, y1, LANE, radius=4)
    for w in c.model.windows:
        c.rect(c.px(w.start), y0, max(c.px(w.end), c.px(w.start) + 2), y1, (*WINDOW, 30))
    for p in (1, 2, 3):
        x = c.px(phase_start(c.dist, p))
        c.line([(x, y0), (x, y1)], (255, 255, 255, 22), 1)


def _firing(c: _Canvas, r: SkillResult, y0: float, h: float, scale_gain: float) -> None:
    """Where the skill fires: a density curve, or a pin for a fixed spot."""
    for s, e in r.placement.regions:
        c.rect(c.px(s), y0, max(c.px(e), c.px(s) + 1), y0 + h, (255, 255, 255, 18), radius=3)
    if not r.dist:
        return
    spurt_m = c.model.spurt_start
    metres_per_px = c.dist / (X1 - X0)
    cols = _columns(c, r)
    if len(r.dist) == 1:
        x_m = r.dist[0][0]
        x = c.px(x_m)
        colour = _colour(r, x_m, r.gains[0] if r.gains else 0, r.accel_gains[0] if r.accel_gains else 0,
                         scale_gain, spurt_m)
        c.line([(x, y0 + 4), (x, y0 + h)], colour, 2)
        c.d.ellipse([(x - 4) * SCALE, (y0 + 1) * SCALE, (x + 4) * SCALE, (y0 + 9) * SCALE], fill=colour)
        return
    # Mass per covered pixel, so a pixel the distribution only half covers isn't drawn half as tall.
    density = [cell[0] / min(cell[3], 1.0) if cell[3] > 0 else 0.0 for cell in cols]
    smooth = []
    for i, value in enumerate(density):
        near = [v for v in density[max(0, i - 1):i + 2] if v > 0]
        smooth.append(sum(near) / len(near) if value > 0 else 0.0)
    peak = max(smooth) or 1.0
    for col, (mass, gain, accel_gain, _) in enumerate(cols):
        if mass <= 1e-9:
            continue
        x = X0 + col
        bh = max(2.0, (h - 2) * (smooth[col] / peak) ** 0.7)
        colour = _colour(r, col * metres_per_px, gain / mass, accel_gain / mass, scale_gain, spurt_m)
        c.rect(x, y0 + h - bh, x + 1, y0 + h, colour)


def _numbers(r: SkillResult) -> tuple:
    if r.kind == KIND_RECOVERY:
        return f"+{heal_amount(r) * 100:.1f}% HP", f"{r.before_spurt * 100:.0f}%"
    if r.sig and r.sig.moves:
        share = r.accel_hits if r.sig.accel > 0 else r.useful
        return f"{r.mean_gain:+.2f} L", f"{share * 100:.0f}%"
    return "", ""


def _footer(c: _Canvas, y: float, build: Build) -> None:
    note = (f"L = horse lengths gained at the finish · * also needs a race position (assumed) · "
            f"reference uma {build.label}, A aptitudes, running alone")
    c.text(PAD, y + 8, c.fit(note, "small", W - 2 * PAD), "small", FAINT)


def _subtitle(choice: CourseChoice) -> str:
    if choice.key.startswith("cm:"):
        name = choice.label.split(" - ")[0].replace("CM ", "")
        return f"Champions Meeting · {name} · {choice.env.label}"
    return choice.label


# ── public ─────────────────────────────────────────────────────────────────

def _chart_height() -> float:
    return CHART_TITLE_H + SPEED_H + ELEV_H + 3 * ROW_H_COURSE + RULER_H + AXIS_H + 14 + 12


def render_skill(r: SkillResult, choice: CourseChoice, build: Build, model: RaceModel) -> bytes:
    height = HEAD_H + 14 + _chart_height() + SKILL_H + 12 + LINE_H + 12
    c = _Canvas(height, model)
    y = _header(c, r.skill.name, _subtitle(choice), [STRATEGIES[build.strategy]])
    box = _skill_box(r, model, best=True)
    y = _chart(c, choice, y, [box] if box else None)

    top = y
    c.rect(PAD, top, W - PAD, top + SKILL_H, CARD, radius=12, outline=BORDER, width=2)
    gx = PAD + 16
    c.rect(gx, top + 17, gx + 9, top + 26, RARITY.get(r.skill.rarity_label, TEXT), radius=2)
    c.text(gx + 16, top + 22, c.fit(r.skill.name, "label_b", GUTTER - 40), "label_b", TEXT)
    meta = r.skill.rarity_label + (f" · {r.skill.sp} SP" if r.skill.sp else "")
    if r.duration:
        meta += f" · {r.duration:.1f}s here"
    c.text(gx + 16, top + 40, meta, "small", MUTED)
    big, small = _numbers(r)
    if r.possible and big:
        what = "fire early" if r.kind == KIND_RECOVERY else "hits" if (r.sig and r.sig.accel > 0) else "pays"
        c.text(gx + 16, top + 66, big, "num", TEXT, "lm")
        c.text(gx + 26 + c.width(big, "num"), top + 67, f"{small} {what}", "small", MUTED, "lm")
    if box and r.gains and r.best_gain >= USEFUL_LENGTHS:
        where = "fires at" if len(r.dist) == 1 else "best spot"
        colour = GREEN if box[3] == BOX["green"] else BLUE
        c.text(gx + 16, top + 88, f"{where} {box[0]:,.0f}m: {r.best_gain:+.1f} L", "small_b", colour, "lm")

    lane_top, lane_bottom = top + 10, top + SKILL_H - 10
    _lane(c, lane_top, lane_bottom)
    room = X1 - X0 - 20
    c.text(X0 + 10, lane_top + 12, c.fit("Fires: " + fires_text(r.skill, r.alt_index), "small", room), "small", MUTED)
    c.text(X0 + 10, lane_top + 27, c.fit("Effect: " + effect_text(r.skill.alts[r.alt_index]), "small", room),
           "small", MUTED)
    if r.possible:
        _firing(c, r, lane_top + 36, lane_bottom - lane_top - 38, r.best_gain)
    else:
        c.text(X0 + 10, lane_top + 56, c.fit(f"Can't fire here: {r.blocked}", "label_b", room), "label_b", MUTED)
    _footer(c, top + SKILL_H + 12, build)
    return c.png()


def render_track(t: TrackAnalysis, kind: str) -> bytes:
    rows = t.results[:MAX_ROWS]
    list_h = 30 + max(len(rows), 1) * ROW_H + 10
    height = HEAD_H + 14 + _chart_height() + list_h + 12 + LINE_H + 12
    c = _Canvas(height, t.model)
    title = {"accel": "Acceleration skills", "speed": "Speed skills", "recovery": "Recovery skills"}[kind]
    y = _header(c, title, _subtitle(t.choice), [STRATEGIES[t.build.strategy]])
    boxes = [b for b in (_skill_box(r, t.model, best=False) for r in rows[:MAX_BOXES]) if b]
    y = _chart(c, t.choice, y, boxes)

    top = y
    c.rect(PAD, top, W - PAD, top + list_h, CARD, radius=12, outline=BORDER, width=2)
    head_y = top + 16
    share_head = {"accel": "Hits", "speed": "Pays", "recovery": "Early"}[kind]
    c.text(PAD + 16, head_y, "Skill", "small_b", FAINT)
    c.text(X0 - 76, head_y, "HP" if kind == "recovery" else "Avg", "small_b", FAINT, "rm")
    c.text(X0 - 16, head_y, share_head, "small_b", FAINT, "rm")
    c.text(X0 + 4, head_y, "where it can fire", "small_b", FAINT)
    kx = X1
    for label, colour in reversed((("white", RARITY["White"]), ("gold", RARITY["Gold"]),
                                   ("inherited unique", RARITY["Inherited unique"]))):
        kx -= c.width(label, "small") + 4
        c.text(kx, head_y, label, "small", FAINT)
        kx -= 14
        c.rect(kx, head_y - 4, kx + 8, head_y + 4, colour, radius=2)
        kx -= 12
    y = top + 30
    _lane(c, y, y + max(len(rows), 1) * ROW_H)
    scale = max((r.best_gain for r in rows), default=1.0)
    for i, r in enumerate(rows):
        ry = y + i * ROW_H
        if i % 2:
            c.rect(PAD + 6, ry, X0 - 4, ry + ROW_H, (255, 255, 255, 6), radius=4)
            c.rect(X0, ry, X1, ry + ROW_H, (255, 255, 255, 5))
        c.rect(PAD + 16, ry + ROW_H / 2 - 4, PAD + 24, ry + ROW_H / 2 + 4,
               RARITY.get(r.skill.rarity_label, TEXT), radius=2)
        flagged = any(not n.startswith(("HP", "first: HP")) for n in r.placement.needs)
        label = r.skill.name.replace(" (inherited)", "")
        room = (X0 - 76 - 62) - (PAD + 30) - (c.width(" *", "label") if flagged else 0)
        name = c.fit(label, "label", room) + (" *" if flagged else "")
        c.text(PAD + 30, ry + ROW_H / 2, name, "label", TEXT)
        big, small = _numbers(r)
        c.text(X0 - 76, ry + ROW_H / 2, big, "label_b", TEXT, "rm")
        c.text(X0 - 16, ry + ROW_H / 2, small, "small", MUTED, "rm")
        _firing(c, r, ry + 3, ROW_H - 6, scale)
    if not rows:
        c.text(X0 + 12, y + ROW_H / 2, "Nothing of this type can fire on this course for this running style.",
               "label", MUTED)
    _footer(c, top + list_h + 12, t.build)
    return c.png()
