"""
Pillow renderer for skill maps, styled after umalator's course chart in a dark
theme: the elevation strip, slopes, corners and straights and race phases, each
row labelled with the distance at every boundary, under the reference uma's
speed with the stretches where she is still accelerating shaded green.

Two layouts share those rows:

- ``render_skill``: a compact strip for the /skill card. The stretch where the
  skill can fire is highlighted on the course, coloured by what a firing there
  wins (green: the acceleration lands while she is still speeding up, blue:
  only the speed boost helps, grey: nothing), with its best spot drawn as an
  umalator-style activation box.
- ``render_track``: the /track overview. The top skills sit on the chart as
  stacked activation boxes, and below it every skill gets a lane showing each
  spot it can fire.
"""
from __future__ import annotations

import io
from typing import Optional

from PIL import Image, ImageDraw

from tally.render.base import load_font

from .analysis import KIND_RECOVERY, USEFUL_LENGTHS, SkillResult, TrackAnalysis, heal_amount
from .conditions import IMMEDIATE, TRAFFIC
from .gamedata import STRATEGIES, CourseChoice
from .sim import Build, RaceModel, phase_start

SCALE = 2
BIN_HALF_WIDTH = 1.0        # metres; firing distributions are binned every 2 m
MAX_ROWS = 20
MAX_BOXES = 10              # skills drawn on the /track chart itself; the list below has the rest

W = 1200
PAD = 20
GUTTER = 330
X0 = PAD + GUTTER
X1 = W - PAD - 16
HEAD_H = 62
LINE_H = 15
ROW_H = 24
BOX_H = 17

# page
BG = (22, 23, 26)
CARD = (34, 36, 40)
EMBED_BG = (43, 45, 49)     # Discord's embed background, so the /skill strip blends in
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
    def __init__(self, height: float, model: RaceModel, x0: float = X0, x1: float = X1, bg: tuple = BG):
        self.S = SCALE
        self.img = Image.new("RGB", (W * SCALE, int(height) * SCALE), bg)
        self.d = ImageDraw.Draw(self.img, "RGBA")
        self.model = model
        self.dist = model.distance
        self.x0, self.x1 = x0, x1
        self.f = {
            "head": load_font(24 * SCALE, bold=True, game=True),
            "sub": load_font(13 * SCALE, game=True),
            "chart": load_font(21 * SCALE, bold=True, game=True),
            "chart_s": load_font(17 * SCALE, bold=True, game=True),
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
        return self.x0 + (self.x1 - self.x0) * metres / self.dist

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


# ── course rows ────────────────────────────────────────────────────────────

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
        if x - w < last_right + 4 or x - w < c.x0 + 2:
            continue
        c.text(x, y, label, "edge", EDGE_TEXT, "rm")
        last_right = x


def _speed_band(c: _Canvas, top: float, h: float, axis: bool = True) -> None:
    m, D = c.model, c.dist
    c.rect(c.x0, top, c.x1, top + h, SPEED_BG)
    vmin, vmax = m.dash_cap - 0.6, m.spurt_speed + 0.6

    def vy(v):
        return top + h - 6 - (h - 24) * (max(vmin, min(vmax, v)) - vmin) / (vmax - vmin)
    for v in range(int(vmin) + 1, int(vmax) + 1):
        if v % 2 == 0:
            c.line([(c.x0, vy(v)), (c.x1, vy(v))], GRID, 1)
            if axis:
                c.text(c.x0 - 6, vy(v), str(v), "small", FAINT, "rm")
    if axis:
        c.text(c.x0 - 6, top + 8, "m/s", "small", FAINT, "rm")
    for w in m.windows:
        c.rect(c.px(w.start), top, max(c.px(w.end), c.px(w.start) + 2), top + h, (*WINDOW, 46))
    labelled_to = c.x0 - 10
    for w in sorted(m.windows, key=lambda w: w.start):
        label = _WINDOW_LABELS.get(w.cause, w.cause)
        x = max(c.px(w.start) + 2, labelled_to + 4)
        lw = c.width(label, "small_b") + 10
        if x + lw <= c.x1:
            c.rect(x, top + 3, x + lw, top + 17, (*WINDOW, 220), radius=7)
            c.text(x + lw / 2, top + 10, label, "small_b", (10, 40, 22), "mm")
            labelled_to = x + lw
    steps = int((c.x1 - c.x0) * 2)
    c.line([(c.px(D * i / steps), vy(m.speed_at(D * i / steps))) for i in range(steps + 1)], SPEED_LINE, 2)


def _height_strip(c: _Canvas, top: float, h: float) -> None:
    course, D = c.model.course, c.dist
    steps = int((c.x1 - c.x0) * 2)
    heights = [_elevation(course, D * i / steps) for i in range(steps + 1)]
    lo, hi = min(heights), max(heights)
    room = h - 12
    scale = room / (hi - lo) if hi > lo else 0.0
    base = top + h - 5

    def ey(v):
        return base - 3 - (v - lo) * scale if scale else base - room / 2
    c.poly([(c.px(D * i / steps), ey(heights[i])) for i in range(steps + 1)] + [(c.x1, base), (c.x0, base)],
           ELEV_FILL)
    c.rect(c.x0, base, c.x1, base + 5, ELEV_BASE)
    for s, e, pct in course.slopes:
        label = f"{abs(pct):g}%"
        if c.px(e) - c.px(s) >= c.width(label, "edge") + 4:
            c.text(c.px((s + e) / 2), base - 8, label, "edge", ELEV_TEXT, "mm")


def _slope_row(c: _Canvas, top: float, h: float) -> None:
    course = c.model.course
    bottom = top + h
    c.rect(c.x0, top, c.x1, bottom, SLOPE_FLAT)
    for s, e, pct in course.slopes:
        x0, x1 = c.px(s), c.px(e)
        c.rect(x0, top, x1, bottom, UPHILL if pct > 0 else DOWNHILL)
        word, arrow = ("Uphill", "↗") if pct > 0 else ("Downhill", "↘")
        _segment_label(c, x0, x1, top, bottom, [[(f"{word} {arrow}", "seg")], [(arrow, "seg")]], SEG_TEXT)
    c.rect(c.x0, bottom - 3, c.x1, bottom, SLOPE_LINE)
    _edges(c, [v for s, e, _ in course.slopes for v in (s, e)], bottom - 10)


def _track_row(c: _Canvas, top: float, h: float) -> None:
    course = c.model.course
    bottom = top + h
    c.rect(c.x0, top, c.x1, bottom, GAP)
    for s, e, _ in course.straights:
        c.rect(c.px(s), top, c.px(e), bottom, STRAIGHT)
        c.rect(c.px(s), bottom - 3, c.px(e), bottom, STRAIGHT_LINE)
        _segment_label(c, c.px(s), c.px(e), top, bottom, [[("Straight →", "seg")], [("→", "seg")]], SEG_TEXT)
    arrow = "↻" if course.turn == 1 else "↺"
    for i, (s, e) in enumerate(course.corners):
        number = 4 - (len(course.corners) - 1 - i) % 4
        c.rect(c.px(s), top, c.px(e), bottom, CORNERS[i % 2])
        c.rect(c.px(s), bottom - 3, c.px(e), bottom, CORNER_LINE)
        _segment_label(c, c.px(s), c.px(e), top, bottom,
                       [[("Corner ", "seg"), (arrow, "seg_arrow"), (str(number), "seg")],
                        [(arrow, "seg_arrow"), (str(number), "seg")]], SEG_TEXT)
    edges = [v for s, e, _ in course.straights for v in (s, e)] + [v for s, e in course.corners for v in (s, e)]
    _edges(c, edges, bottom - 10)


def _phase_row(c: _Canvas, top: float, h: float) -> None:
    D = c.dist
    bottom = top + h
    bounds = [phase_start(D, p) for p in range(4)] + [float(D)]
    for p in range(4):
        x0, x1 = c.px(bounds[p]), c.px(bounds[p + 1])
        c.rect(x0, top, x1, bottom, PHASE_FILL[p])
        c.rect(x0, bottom - 3, x1, bottom, PHASE_LINE[p])
        _segment_label(c, x0, x1, top, bottom, [[(PHASE_NAMES[p], "seg")], [(PHASE_NAMES[p][:4], "seg")]], SEG_TEXT)
    _edges(c, bounds[1:4], bottom - 10)


def _ruler(c: _Canvas, top: float, h: float) -> None:
    c.rect(c.x0, top, c.x1, top + h, RULER)
    section = (c.x1 - c.x0) / 24
    for i in range(24):
        x = c.x0 + i * section
        if i:
            c.line([(x, top), (x, top + h)], RULER_LINE, 1)
        c.text(x + section / 2, top + h / 2, str(i + 1), "small", RULER_TEXT, "mm")


def _axis(c: _Canvas, top: float) -> None:
    D = c.dist
    c.line([(c.x0, top + 1), (c.x1, top + 1)], AXIS, 2)
    major = 100 if D <= 1600 else 200
    minor = major / 5
    mark = 0.0
    while mark <= D + 0.1:
        x = c.px(mark)
        big = abs(mark / major - round(mark / major)) < 1e-6
        c.line([(x, top + 1), (x, top + (8 if big else 5))], AXIS, 1)
        if big and (mark == D or D - mark > major * 0.6 or D % major == 0):
            c.text(x, top + 17, f"{mark:,.0f}", "small_b", TEXT, "mm")
        mark += minor
    if D % major:
        c.text(c.x1, top + 17, f"{D:,}", "small_b", TEXT, "rm")


def _boxes(c: _Canvas, boxes: list, top: float, bottom: float, outside: bool = False) -> None:
    """Umalator-style skill boxes, stacked upward from ``bottom`` so none overlap.

    A label may be a tuple of fallbacks, longest first. With ``outside`` a label that
    doesn't fit goes beside its box instead of being cut.
    """
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
        labels = label if isinstance(label, tuple) else (label,)
        name = next((t for t in labels if c.width(t, "box") <= x1 - x0 - 5), None)
        if name:
            c.text(x0 + 3, y1 - BOX_H / 2, name, "box", (255, 255, 255), "lm")
        elif outside:
            text = labels[-1]
            right = x1 + 4 + c.width(text, "box") <= c.x1
            c.text(x1 + 4 if right else x0 - 4, y1 - BOX_H / 2, text, "box", (255, 255, 255), "lm" if right else "rm")
        else:
            name = c.clip(labels[-1], "box", x1 - x0 - 5)
            if name:
                c.text(x0 + 3, y1 - BOX_H / 2, name, "box", (255, 255, 255), "lm")


# ── firing ─────────────────────────────────────────────────────────────────

def _columns(c: _Canvas, r: SkillResult) -> list:
    """Per-pixel [mass, total gain, accel gain, coverage] of the firing distribution."""
    plot_w = int(c.x1 - c.x0) + 1
    cols = [[0.0, 0.0, 0.0, 0.0] for _ in range(plot_w)]
    point = len(r.dist) == 1
    scale = (c.x1 - c.x0) / c.dist
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


def _densities(cols: list) -> list:
    """Mass per covered pixel, lightly smoothed, scaled to a 0-1 peak."""
    density = [cell[0] / min(cell[3], 1.0) if cell[3] > 0 else 0.0 for cell in cols]
    smooth = []
    for i, value in enumerate(density):
        near = [v for v in density[max(0, i - 1):i + 2] if v > 0]
        smooth.append(sum(near) / len(near) if value > 0 else 0.0)
    peak = max(smooth) or 1.0
    return [v / peak for v in smooth]


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
    c.rect(c.x0, y0, c.x1, y1, LANE, radius=4)
    for w in c.model.windows:
        c.rect(c.px(w.start), y0, max(c.px(w.end), c.px(w.start) + 2), y1, (*WINDOW, 30))
    for p in (1, 2, 3):
        x = c.px(phase_start(c.dist, p))
        c.line([(x, y0), (x, y1)], (255, 255, 255, 22), 1)


def _firing(c: _Canvas, r: SkillResult, y0: float, h: float, scale_gain: float) -> None:
    """Where the skill fires in a /track lane: a density curve, or a pin for a fixed spot."""
    for s, e in r.placement.regions:
        c.rect(c.px(s), y0, max(c.px(e), c.px(s) + 1), y0 + h, (255, 255, 255, 18), radius=3)
    if not r.dist:
        return
    spurt_m = c.model.spurt_start
    metres_per_px = c.dist / (c.x1 - c.x0)
    if len(r.dist) == 1:
        x_m = r.dist[0][0]
        x = c.px(x_m)
        colour = _colour(r, x_m, r.gains[0] if r.gains else 0, r.accel_gains[0] if r.accel_gains else 0,
                         scale_gain, spurt_m)
        c.line([(x, y0 + 4), (x, y0 + h)], colour, 2)
        c.d.ellipse([(x - 4) * SCALE, (y0 + 1) * SCALE, (x + 4) * SCALE, (y0 + 9) * SCALE], fill=colour)
        return
    cols = _columns(c, r)
    density = _densities(cols)
    for col, (mass, gain, accel_gain, _) in enumerate(cols):
        if mass <= 1e-9:
            continue
        x = c.x0 + col
        bh = max(2.0, (h - 2) * density[col] ** 0.7)
        colour = _colour(r, col * metres_per_px, gain / mass, accel_gain / mass, scale_gain, spurt_m)
        c.rect(x, y0 + h - bh, x + 1, y0 + h, colour)


def _zone(c: _Canvas, r: SkillResult, top: float, bottom: float) -> None:
    """Highlight where the skill can fire across the course rows, coloured by what a firing there wins."""
    spurt_m = c.model.spurt_start
    for s, e in r.placement.regions:
        c.rect(c.px(s), top, max(c.px(e), c.px(s) + 2), bottom, (255, 255, 255, 30))
    if r.dist and len(r.dist) > 1:
        cols = _columns(c, r)
        density = _densities(cols)
        metres_per_px = c.dist / (c.x1 - c.x0)
        for col, (mass, gain, accel_gain, _) in enumerate(cols):
            if mass <= 1e-9:
                continue
            colour = _colour(r, col * metres_per_px, gain / mass, accel_gain / mass, max(r.best_gain, 0.01), spurt_m)
            # light enough that the row labels underneath stay readable
            alpha = int((35 + 50 * density[col]) if colour == IDLE else (70 + 90 * density[col]))
            c.rect(c.x0 + col, top, c.x0 + col + 1, bottom, (*colour, alpha))
    elif r.dist:
        x_m = r.dist[0][0]
        colour = _colour(r, x_m, r.gains[0] if r.gains else 0, r.accel_gains[0] if r.accel_gains else 0,
                         max(r.best_gain, 0.01), spurt_m)
        x = c.px(x_m)
        c.rect(x - 1.5, top, x + 1.5, bottom, (*colour, 235))
    for s, e in r.placement.regions:
        x0, x1 = c.px(s), max(c.px(e), c.px(s) + 2)
        c.line([(x0, top), (x1, top), (x1, bottom), (x0, bottom), (x0, top)], (255, 255, 255, 150), 1)


def _numbers(r: SkillResult) -> tuple:
    if r.kind == KIND_RECOVERY:
        return f"+{heal_amount(r) * 100:.1f}% HP", f"{r.before_spurt * 100:.0f}%"
    if r.sig and r.sig.moves:
        share = r.accel_hits if r.sig.accel > 0 else r.useful
        return f"{r.mean_gain:+.2f} L", f"{share * 100:.0f}%"
    return "", ""


def _subtitle(choice: CourseChoice) -> str:
    if choice.event:
        return f"Champions Meeting · {choice.event} · {choice.env.label}"
    return choice.label


def _footer(c: _Canvas, y: float, build: Build) -> None:
    note = (f"L = horse lengths gained at the finish · * also needs a race position (assumed) · "
            f"reference uma {build.label}, A aptitudes, running alone")
    c.text(PAD, y + 8, c.fit(note, "small", W - 2 * PAD), "small", FAINT)


# ── /skill: compact strip ──────────────────────────────────────────────────

SK_SPEED_H = 62
SK_ELEV_H = 30
SK_ROW_H = 34


def render_skill(r: SkillResult, choice: CourseChoice, build: Build, model: RaceModel) -> bytes:
    title_h, axis_h = 34, 24
    height = 8 + title_h + SK_SPEED_H + SK_ELEV_H + 3 * SK_ROW_H + axis_h + 6
    c = _Canvas(height, model, x0=PAD + 30, x1=W - PAD, bg=EMBED_BG)
    c.text(W / 2, 8 + title_h / 2, _course_title(choice.course), "chart_s", CHART_TITLE, "mm")
    y = 8 + title_h
    speed_top = y
    _speed_band(c, y, SK_SPEED_H)
    y += SK_SPEED_H
    rows_top = y
    _height_strip(c, y, SK_ELEV_H)
    y += SK_ELEV_H
    _slope_row(c, y, SK_ROW_H)
    y += SK_ROW_H
    _track_row(c, y, SK_ROW_H)
    y += SK_ROW_H
    _phase_row(c, y, SK_ROW_H)
    y += SK_ROW_H
    rows_bottom = y

    if r.possible:
        _zone(c, r, rows_top, rows_bottom)
        policy = r.placement.policy.kind
        tag = "FIXED SPOT" if policy == IMMEDIATE or len(r.dist) == 1 else \
            "DEPENDS ON RUNNERS" if policy == TRAFFIC else "RANDOM"
        first = r.placement.regions[0][0]
        tx = min(max(c.px(first), c.x0), c.x1 - c.width(tag, "small_b") - 12)
        tw = c.width(tag, "small_b") + 10
        c.rect(tx, rows_top - 16, tx + tw, rows_top - 2, (20, 22, 26, 225), radius=6)
        c.text(tx + tw / 2, rows_top - 9, tag, "small_b", TEXT, "mm")
        box = _skill_box(r, model, best=True)
        if box:
            # umalator-style activation box: where it stays active when fired at its best spot;
            # a narrow box shows the gain instead of the name
            start, end, name, colour = box
            short = f"{r.best_gain:+.1f} L" if r.gains and r.best_gain >= USEFUL_LENGTHS else _numbers(r)[0]
            _boxes(c, [(start, end, (name, short) if short else name, colour)], speed_top + 20, rows_top - 18,
                   outside=True)
    else:
        msg = f"Can't fire here: {r.blocked}"
        mw = c.width(msg, "label_b") + 24
        cx, cy = (c.x0 + c.x1) / 2, (rows_top + rows_bottom) / 2
        c.rect(cx - mw / 2, cy - 14, cx + mw / 2, cy + 14, (20, 22, 26, 230), radius=8)
        c.text(cx, cy, msg, "label_b", TEXT, "mm")
    _axis(c, y)
    return c.png()


# ── /track: overview ───────────────────────────────────────────────────────

CHART_TITLE_H = 40
SPEED_H = 86
ELEV_H = 36
ROW_H_COURSE = 40
RULER_H = 32
AXIS_H = 26


def _header(c: _Canvas, title: str, subtitle: str, chips: list) -> float:
    c.rect(0, 0, W, HEAD_H, CARD)
    c.rect(0, HEAD_H - 3, W, HEAD_H, LIME)
    right = W - PAD
    for text in reversed(chips):
        right -= c.chip(right, HEAD_H / 2 - 2, text, (255, 255, 255), LIME) + 8
    c.text(PAD, 24, c.fit(title, "head", right - PAD - 10), "head", TEXT)
    c.text(PAD, 46, c.fit(subtitle, "sub", right - PAD - 10), "sub", MUTED)
    return HEAD_H + 14


def _chart_height() -> float:
    return CHART_TITLE_H + SPEED_H + ELEV_H + 3 * ROW_H_COURSE + RULER_H + AXIS_H + 14 + 12


def _chart(c: _Canvas, choice: CourseChoice, y: float, boxes: Optional[list] = None) -> float:
    m, course = c.model, choice.course
    rows_h = SPEED_H + ELEV_H + 3 * ROW_H_COURSE + RULER_H + AXIS_H
    card_top, card_bottom = y, y + CHART_TITLE_H + rows_h + 14
    c.rect(PAD, card_top, W - PAD, card_bottom, CARD, radius=12, outline=BORDER, width=2)
    c.text((c.x0 + c.x1) / 2, y + 22, _course_title(course), "chart", CHART_TITLE, "mm")
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

    top = y
    _speed_band(c, y, SPEED_H)
    y += SPEED_H
    _height_strip(c, y, ELEV_H)
    y += ELEV_H
    rows_top = y
    _slope_row(c, y, ROW_H_COURSE)
    y += ROW_H_COURSE
    _track_row(c, y, ROW_H_COURSE)
    y += ROW_H_COURSE
    _phase_row(c, y, ROW_H_COURSE)
    y += ROW_H_COURSE
    _ruler(c, y, RULER_H)
    y += RULER_H
    if boxes:
        _boxes(c, boxes, rows_top, y)
    _axis(c, y)

    c.text(c.x0 - 8, top + SPEED_H + ELEV_H / 2, "height", "small_b", FAINT, "rm")
    for i, name in enumerate(("slope", "track", "phase")):
        c.text(c.x0 - 8, rows_top + (i + 0.5) * ROW_H_COURSE, name, "small_b", FAINT, "rm")
    c.text(c.x0 - 8, rows_top + 3 * ROW_H_COURSE + RULER_H / 2, "section", "small_b", FAINT, "rm")
    return card_bottom + 12


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
    c.text(c.x0 - 76, head_y, "HP" if kind == "recovery" else "Avg", "small_b", FAINT, "rm")
    c.text(c.x0 - 16, head_y, share_head, "small_b", FAINT, "rm")
    c.text(c.x0 + 4, head_y, "where it can fire", "small_b", FAINT)
    kx = c.x1
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
            c.rect(PAD + 6, ry, c.x0 - 4, ry + ROW_H, (255, 255, 255, 6), radius=4)
            c.rect(c.x0, ry, c.x1, ry + ROW_H, (255, 255, 255, 5))
        c.rect(PAD + 16, ry + ROW_H / 2 - 4, PAD + 24, ry + ROW_H / 2 + 4,
               RARITY.get(r.skill.rarity_label, TEXT), radius=2)
        flagged = any(not n.startswith(("HP", "first: HP")) for n in r.placement.needs)
        label = r.skill.name.replace(" (inherited)", "")
        room = (c.x0 - 76 - 62) - (PAD + 30) - (c.width(" *", "label") if flagged else 0)
        name = c.fit(label, "label", room) + (" *" if flagged else "")
        c.text(PAD + 30, ry + ROW_H / 2, name, "label", TEXT)
        big, small = _numbers(r)
        c.text(c.x0 - 76, ry + ROW_H / 2, big, "label_b", TEXT, "rm")
        c.text(c.x0 - 16, ry + ROW_H / 2, small, "small", MUTED, "rm")
        _firing(c, r, ry + 3, ROW_H - 6, scale)
    if not rows:
        c.text(c.x0 + 12, y + ROW_H / 2, "Nothing of this type can fire on this course for this running style.",
               "label", MUTED)
    _footer(c, top + list_h + 12, t.build)
    return c.png()
