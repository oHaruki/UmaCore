"""
Skill trigger conditions: parse the game's condition strings and work out where
on a course a skill can fire, and how the game picks the spot.

A condition is ``term&term@term&term`` - ``&`` is AND, ``@`` is OR, and each term
is ``name<op>value`` (``phase>=2``, ``corner!=0``, ``order_rate<=50``). Terms that
describe the course (phase, corners, slopes, remaining distance) narrow the
stretch where the skill can fire. Terms about the race setup (distance type,
surface, strategy, weather) either allow it or rule it out. Terms about other
runners (order, overtaking, being blocked) can't be known ahead of time; they
are assumed met and listed as requirements, and the ones that come down to
racing traffic shift the likely firing point toward the start of the window.

The semantics follow the game's rules as documented by the community race
simulators (uma-skill-tools / umalator).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from .gamedata import (
    DISTANCE_TYPES, GROUNDS, SEASONS, STRATEGIES, SURFACES, WEATHERS, Course, RaceEnv,
)
from .sim import RaceModel, phase_end, phase_start

_TERM = re.compile(r"^([a-z_0-9]+)(==|!=|>=|<=|>|<)(-?\d+)$")

# How the game picks the trigger point inside the allowed stretch.
IMMEDIATE = "immediate"   # the first point where everything holds
RANDOM = "random"         # a random point, longer stretches more likely
PICK_ONE = "pick"         # a random corner/straight first, then a random point in it
TRAFFIC = "traffic"       # depends on other runners: modelled as skewed toward the start
UNIFORM = "uniform"       # anywhere in the stretch, evenly

_RANK = {IMMEDIATE: 0, TRAFFIC: 1, UNIFORM: 1, RANDOM: 2, PICK_ONE: 3}

PHASE_NAMES = ("early race", "mid race", "late race", "final stretch")


@dataclass(frozen=True)
class Term:
    name: str
    op: str
    value: int

    def __str__(self) -> str:
        return f"{self.name}{self.op}{self.value}"


@dataclass(frozen=True)
class Policy:
    kind: str = IMMEDIATE
    k: int = 3            # Erlang shape for TRAFFIC
    lam: float = 2.0

    def combine(self, other: "Policy") -> "Policy":
        return other if _RANK[other.kind] > _RANK[self.kind] else self


@dataclass
class Placement:
    """Where one condition (or condition branch) lets a skill fire on a course."""
    regions: list = field(default_factory=list)   # [(start, end)], sorted, not merged
    policy: Policy = field(default_factory=Policy)
    blocked: Optional[str] = None                  # why it can never fire here
    needs: list = field(default_factory=list)      # assumed-met requirements, as text
    chance: float = 1.0
    # OR-branches that each place their own trigger; the skill fires at whichever comes first.
    branches: list = field(default_factory=list)

    @property
    def possible(self) -> bool:
        return self.blocked is None and bool(self.regions)


@dataclass
class Context:
    course: Course
    env: RaceEnv
    strategy: int
    model: Optional[RaceModel] = None
    base_stats: dict = field(default_factory=dict)   # displayed stats, for base_power>= etc.


def parse(cond: str) -> list:
    """``a&b@c`` -> [[a, b], [c]]."""
    branches = []
    for branch in cond.split("@"):
        terms = []
        for raw in branch.split("&"):
            raw = raw.strip()
            if not raw:
                continue
            m = _TERM.match(raw)
            if not m:
                raise ValueError(f"unreadable condition term: {raw!r}")
            terms.append(Term(m.group(1), m.group(2), int(m.group(3))))
        if terms:
            branches.append(terms)
    return branches


# ── region helpers ─────────────────────────────────────────────────────────

def clip(regions: list, lo: float, hi: float) -> list:
    out = []
    for s, e in regions:
        s2, e2 = max(s, lo), min(e, hi)
        if e2 > s2:
            out.append((s2, e2))
    return out


def cut(regions: list, spans) -> list:
    out = []
    for s, e in regions:
        for a, b in spans:
            s2, e2 = max(s, a), min(e, b)
            if e2 > s2:
                out.append((s2, e2))
    return sorted(out)


def union(a: list, b: list) -> list:
    """Merge overlapping stretches, but keep touching ones (two corners back to back) apart."""
    out = []
    for s, e in sorted(set(a) | set(b)):
        if out and s < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def corner_spans(course: Course, number: int) -> list:
    """Corner ``number`` (1-4, counted so the final corner is 4) on every lap."""
    out, idx = [], len(course.corners) + number - 5
    while idx >= 0:
        out.append(tuple(course.corners[idx]))
        idx -= 4
    return sorted(out)


def non_corner_spans(course: Course) -> list:
    out, last = [], 0.0
    for s, e in course.corners:
        out.append((last, s))
        last = e
    if last < course.distance:
        out.append((last, float(course.distance)))
    return [(s, e) for s, e in out if e > s]


def slope_spans(course: Course, kind: int) -> list:
    """kind 1 = uphills, 2 = downhills, 0 = flat ground."""
    if kind == 1:
        return [(s, e) for s, e, p in course.slopes if p > 0]
    if kind == 2:
        return [(s, e) for s, e, p in course.slopes if p < 0]
    out, last = [], 0.0
    for s, e, _ in course.slopes:
        out.append((last, s))
        last = e
    out.append((last, float(course.distance)))
    return [(s, e) for s, e in out if e > s]


def _cmp(a: float, op: str, b: float) -> bool:
    return {"==": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]


# ── term evaluation ────────────────────────────────────────────────────────

# Conditions that depend on other runners; Erlang (k, lambda) shape of the
# firing point, following the community simulators.
_TRAFFIC = {
    "bashin_diff_behind": (3, 2.0), "bashin_diff_infront": (3, 2.0),
    "behind_near_lane_time": (3, 2.0), "behind_near_lane_time_set1": (3, 2.0),
    "blocked_all_continuetime": (3, 2.0), "blocked_front": (3, 2.0),
    "blocked_front_continuetime": (3, 2.0), "blocked_side_continuetime": (3, 2.0),
    "change_order_onetime": (3, 2.0), "infront_near_lane_time": (3, 2.0),
    "is_surrounded": (3, 2.0), "near_count": (3, 2.0),
    "overtake_target_no_order_up_time": (3, 2.0), "overtake_target_time": (3, 2.0),
    "is_overtake": (1, 2.0), "is_move_lane": (5, 1.0),
}

# Requirements we can't check and simply assume (they don't move the window).
_ASSUMED = {
    "order", "order_rate", "order_rate_in20_continue", "order_rate_in40_continue", "order_rate_in50_continue",
    "order_rate_in80_continue", "order_rate_out20_continue", "order_rate_out40_continue",
    "order_rate_out50_continue", "order_rate_out70_continue", "distance_diff_rate", "distance_diff_top",
    "distance_diff_top_float", "popularity", "post_number", "is_behind_in", "lane_type", "visiblehorse",
    "same_skill_horse_count", "running_style_count_same", "running_style_count_same_rate",
    "running_style_equal_popularity_one", "temptation_count", "is_temptation", "is_activate_any_skill",
    "is_exist_chara_id", "is_used_skill_id", "is_activate_other_skill_detail", "is_exist_skill_id",
    "is_other_character_activate_advantage_skill", "remain_distance_viewer_id", "is_badstart",
    "running_style_count_nige_otherself", "running_style_count_senko_otherself",
    "running_style_count_sashi_otherself", "running_style_count_oikomi_otherself",
}

_DIRT_GRADE_TRACKS = (10101, 10103, 10104, 10105)


def evaluate(cond: str, ctx: Context, start: Optional[list] = None) -> Placement:
    """Placement for a whole condition string (OR of AND-branches)."""
    whole = start if start is not None else [(0.0, float(ctx.course.distance))]
    branches = [evaluate_branch(b, ctx, whole) for b in parse(cond)]
    possible = [b for b in branches if b.possible]
    if not possible:
        blocked = next((b.blocked for b in branches if b.blocked), "never on this course")
        return Placement(blocked=blocked, needs=[n for b in branches for n in b.needs])
    out = Placement(policy=possible[0].policy, chance=max(b.chance for b in possible))
    for b in possible:
        out.regions = union(out.regions, b.regions)
        out.policy = out.policy.combine(b.policy)
        for n in b.needs:
            if n not in out.needs:
                out.needs.append(n)
    # Branches that differ only in where they are on the course ("corner 1, 2, 3 or 4",
    # "moving inward or outward") race each other. Branches that need different race
    # positions are alternatives for different situations, so they are pooled instead.
    if len(possible) > 1 and all(set(b.needs) == set(possible[0].needs) for b in possible):
        out.branches = possible
    return out


def evaluate_branch(terms: list, ctx: Context, start: list) -> Placement:
    p = Placement(regions=list(start))
    for term in terms:
        _apply(term, p, ctx)
        if p.blocked:
            break
        if not p.regions:
            p.blocked = p.blocked or "its window never opens on this course"
            break
    return p


def _apply(t: Term, p: Placement, ctx: Context) -> None:  # noqa: C901 - one branch per game condition
    c, d, n, op, v = ctx.course, float(ctx.course.distance), t.name, t.op, t.value
    model = ctx.model

    def rule(ok: bool, why: str) -> None:
        if not ok:
            p.blocked = why

    # course position
    if n == "phase":
        if op == "==":
            p.regions = clip(p.regions, phase_start(d, v), phase_end(d, v))
        elif op == ">=":
            p.regions = clip(p.regions, phase_start(d, v), d)
        elif op == ">":
            p.regions = clip(p.regions, phase_start(d, min(v + 1, 3)), d)
        elif op == "<=":
            p.regions = clip(p.regions, 0, phase_end(d, v))
        elif op == "<":
            p.regions = clip(p.regions, 0, phase_start(d, v))
    elif n in ("phase_random", "phase_firsthalf_random", "phase_laterhalf_random", "phase_firstquarter_random",
               "phase_firsthalf", "phase_firstquarter"):
        s, e = phase_start(d, v), phase_end(d, v)
        if "firsthalf" in n:
            e = (s + e) / 2
        elif "laterhalf" in n:
            s = (s + e) / 2
        elif "firstquarter" in n:
            e = s + (e - s) / 4
        p.regions = clip(p.regions, s, e)
        if n.endswith("random"):
            p.policy = p.policy.combine(Policy(RANDOM))
    elif n == "phase_corner_random":
        p.regions = cut(clip(p.regions, phase_start(d, v), phase_end(d, v)), c.corners)
        p.policy = p.policy.combine(Policy(RANDOM))
    elif n == "phase_straight_random":
        p.regions = cut(clip(p.regions, phase_start(d, v), phase_end(d, v)), [(s, e) for s, e, _ in c.straights])
        p.policy = p.policy.combine(Policy(PICK_ONE))
    elif n == "distance_rate":
        x = d * v / 100
        p.regions = clip(p.regions, x, d) if op in (">=", ">") else clip(p.regions, 0, x)
    elif n == "distance_rate_after_random":
        p.regions = clip(p.regions, d * v / 100, d)
        p.policy = p.policy.combine(Policy(RANDOM))
    elif n == "remain_distance":
        if op == "==":
            p.regions = clip(p.regions, d - v, d - v + 1)
        elif op in ("<=", "<"):
            p.regions = clip(p.regions, d - v, d)
        else:
            p.regions = clip(p.regions, 0, d - v)
    elif n == "corner":
        if op == "==" and v == 0:
            p.regions = cut(p.regions, non_corner_spans(c))
        elif op == "!=" and v == 0:
            p.regions = cut(p.regions, c.corners)
        elif op == "==":
            p.regions = cut(p.regions, corner_spans(c, v))
        else:
            p.regions = cut(p.regions, [s for s in c.corners if s not in corner_spans(c, v)])
    elif n == "corner_random":
        idx = len(c.corners) + v - 5
        p.regions = cut(p.regions, [c.corners[idx]]) if idx >= 0 else []
        p.policy = p.policy.combine(Policy(RANDOM))
    elif n == "all_corner_random":
        p.regions = cut(p.regions, c.corners)
        p.policy = p.policy.combine(Policy(PICK_ONE))
    elif n == "is_finalcorner":
        if not c.corners:
            p.regions = []
        else:
            fc = c.corners[-1][0]
            p.regions = clip(p.regions, fc, d) if v == 1 else clip(p.regions, 0, fc)
    elif n == "is_finalcorner_laterhalf":
        if not c.corners:
            p.regions = []
        else:
            s, e = c.corners[-1]
            p.regions = clip(p.regions, (s + e) / 2, e)
    elif n == "is_finalcorner_random":
        p.regions = clip(p.regions, *c.corners[-1]) if c.corners else []
        p.policy = p.policy.combine(Policy(RANDOM))
    elif n == "straight_random":
        p.regions = cut(p.regions, [(s, e) for s, e, _ in c.straights])
        p.policy = p.policy.combine(Policy(PICK_ONE))
    elif n == "straight_front_type":
        p.regions = cut(p.regions, [(s, e) for s, e, f in c.straights if f == v])
    elif n in ("is_last_straight", "is_last_straight_onetime", "last_straight_random"):
        if not c.straights:
            p.regions = []
        else:
            s, e, _ = c.straights[-1]
            p.regions = clip(p.regions, s, s + 10 if n.endswith("onetime") else e)
            if n == "last_straight_random":
                p.policy = p.policy.combine(Policy(RANDOM))
    elif n == "slope":
        p.regions = cut(p.regions, slope_spans(c, v))
    elif n in ("up_slope_random", "down_slope_random"):
        p.regions = cut(p.regions, slope_spans(c, 1 if n.startswith("up") else 2))
        p.policy = p.policy.combine(Policy(RANDOM))
    elif n == "compete_fight_count":
        p.regions = clip(p.regions, *c.straights[-1][:2]) if c.straights else []
        p.policy = p.policy.combine(Policy(UNIFORM))
        p.needs.append(describe(t))
    elif n in ("change_order_up_end_after", "change_order_up_finalcorner_after", "change_order_up_middle"):
        if n.endswith("end_after"):
            p.regions = clip(p.regions, phase_start(d, 2), d)
        elif n.endswith("finalcorner_after"):
            p.regions = clip(p.regions, c.corners[-1][0], d) if c.corners else []
        else:
            p.regions = clip(p.regions, phase_start(d, 1), phase_end(d, 1))
        p.policy = p.policy.combine(Policy(TRAFFIC))
        p.needs.append(describe(t))

    # race state the reference run can answer
    elif n in ("is_lastspurt", "lastspurt"):
        spurt = model.spurt_start if model else phase_start(d, 2)
        if n == "lastspurt" and v != 2:
            rule(False, "only when the runner can't afford a full last spurt")
        p.regions = clip(p.regions, spurt, d)
    elif n == "accumulatetime":
        x = model.position_at_time(v) if model else 0.85 * (20 - (d - 2000) / 1000) * v
        p.regions = clip(p.regions, x, d)
    elif n == "hp_per":
        if model:
            level = v / 100
            xs = model.baseline.xs
            hp = model.baseline.hp
            if op in ("<=", "<"):
                x = next((xs[i] for i in range(len(xs)) if hp[i] <= level), None)
                if x is None:
                    rule(False, f"HP stays above {v}% for this build")
                else:
                    p.regions = clip(p.regions, x, d)
            else:
                x = next((xs[i] for i in range(len(xs)) if hp[i] < level), d)
                p.regions = clip(p.regions, 0, x)
        p.needs.append(describe(t))
    elif n == "is_hp_empty_onetime":
        rule(False, "only after running out of HP")
    elif n == "activate_count_start":
        p.regions = clip(p.regions, 0, phase_end(d, 0))
        p.needs.append(describe(t))
    elif n == "activate_count_middle":
        s, e = phase_start(d, 1), phase_end(d, 1)
        p.regions = clip(p.regions, s, s + v / 10 * (e - s))
        p.policy = p.policy.combine(Policy(RANDOM))
        p.needs.append(describe(t))
    elif n in ("activate_count_end_after", "activate_count_later_half"):
        p.regions = clip(p.regions, phase_start(d, 2) if n.endswith("end_after") else d / 2, d)
        p.policy = p.policy.combine(Policy(RANDOM))
        p.needs.append(describe(t))
    elif n == "activate_count_all":
        if op in (">=", ">"):
            p.regions = clip(p.regions, min(v / 23 - 0.2, 0.6) * d, min(v / 23 + 0.2, 1.0) * d)
            p.policy = p.policy.combine(Policy(RANDOM))
        p.needs.append(describe(t))
    elif n == "activate_count_heal":
        p.policy = p.policy.combine(Policy(RANDOM))
        p.needs.append(describe(t))
    elif n in ("running_style_temptation_opponent_count_nige", "running_style_temptation_opponent_count_senko",
               "running_style_temptation_opponent_count_sashi", "running_style_temptation_opponent_count_oikomi",
               "temptation_opponent_count_behind", "temptation_opponent_count_infront",
               "running_style_temptation_count_nige", "running_style_temptation_count_senko",
               "running_style_temptation_count_sashi", "running_style_temptation_count_oikomi",
               "temptation_count_behind", "temptation_count_infront"):
        p.regions = clip(p.regions, 2 * d / 24, 9 * d / 24)
        p.policy = p.policy.combine(Policy(RANDOM))
        p.needs.append(describe(t))
    elif n == "random_lot":
        p.chance *= v / 100

    # race setup
    elif n == "distance_type":
        rule(_cmp(c.distance_type, op, v), f"{DISTANCE_TYPES.get(v, v)} races only")
    elif n == "ground_type":
        rule(_cmp(c.surface, op, v), f"{SURFACES.get(v, v)} only")
    elif n == "rotation":
        rule(_cmp(c.turn, op, v), "right-handed tracks only" if v == 1 else "left-handed tracks only")
    elif n == "track_id":
        rule(_cmp(c.track_id, op, v), "a different racecourse only")
    elif n == "is_basis_distance":
        rule((c.distance % 400 == 0) == bool(v), "standard distances only" if v else "non-standard distances only")
    elif n == "is_dirtgrade":
        rule((c.track_id in _DIRT_GRADE_TRACKS) == (op == "=="), "local dirt tracks only")
    elif n == "ground_condition":
        rule(_cmp(ctx.env.ground, op, v), f"{GROUNDS.get(v, v)} ground only")
    elif n == "weather":
        rule(_cmp(ctx.env.weather, op, v), f"{WEATHERS.get(v, v)} weather only")
    elif n == "season":
        season = 1 if ctx.env.season == 5 else ctx.env.season
        rule(_cmp(season, op, 1 if v == 5 else v), f"{SEASONS.get(v, v)} races only")
    elif n == "time":
        rule(_cmp(ctx.env.time, op, v), "races at a different time of day only")
    elif n == "grade":
        rule(_cmp(ctx.env.grade, op, v), "a different race grade only")
    elif n == "running_style":
        rule(_cmp(ctx.strategy, op, v), f"{STRATEGIES.get(v, v)} only")
    elif n == "motivation":
        mood = (model.build.mood if model else 2) + 3
        rule(_cmp(mood, op, v), "needs a better mood")
    elif n in ("base_speed", "base_stamina", "base_power", "base_guts", "base_wiz"):
        stat = ctx.base_stats.get(n[5:], 0)
        rule(_cmp(stat, op, v), f"needs {n[5:].title()} {op}{v}")
    elif n in ("course_distance", "corner_count"):
        value = c.distance if n == "course_distance" else len(c.corners)
        rule(_cmp(value, op, v), "a different course only")
    elif n == "always":
        pass

    # other runners
    elif n in _TRAFFIC:
        k, lam = _TRAFFIC[n]
        p.policy = p.policy.combine(Policy(TRAFFIC, k, lam))
        p.needs.append(describe(t))
    elif n in _ASSUMED:
        p.needs.append(describe(t))
    else:
        p.needs.append(f"unmodelled condition {t}")


# ── firing-point distribution ──────────────────────────────────────────────

def _erlang_cdf(x: float, k: int, lam: float) -> float:
    if x <= 0:
        return 0.0
    term, total = 1.0, 1.0
    for i in range(1, k):
        term *= lam * x / i
        total += term
    return 1.0 - math.exp(-lam * x) * total


def _erlang_range(k: int, lam: float) -> tuple:
    """0.1% and 99.9% points (Wilson-Hilferty), the window the community sims stretch to."""
    z = 3.09023
    w = 1 / (9 * k)
    lo = k * (1 - w - z * math.sqrt(w)) ** 3 / lam
    hi = k * (1 - w + z * math.sqrt(w)) ** 3 / lam
    return max(lo, 0.0), hi


def _spread(lo: float, hi: float, mass: float, step: float) -> list:
    if hi <= lo:
        return [(lo, mass)]
    bins = max(1, math.ceil((hi - lo) / step))
    width = (hi - lo) / bins
    return [(lo + (j + 0.5) * width, mass / bins) for j in range(bins)]


def _earliest(dists: list, step: float) -> list:
    """Distribution of the earliest of several independent firing points."""
    end = max(x for d in dists for x, _ in d)
    bins = int(end / step) + 2
    survive = [1.0] * bins          # P(no branch has fired by the end of bin i)
    weight = [0.0] * bins           # sum of p*x per bin, to keep each bin's mass where it sits
    total = [0.0] * bins
    for d in dists:
        mass = [0.0] * bins
        for x, p in d:
            i = int(x / step)
            mass[i] += p
            weight[i] += p * x
            total[i] += p
        acc = 0.0
        for i in range(bins):
            acc += mass[i]
            survive[i] *= max(0.0, 1.0 - acc)
    out, before = [], 1.0
    for i in range(bins):
        m = before - survive[i]
        if m > 1e-12:
            out.append((weight[i] / total[i] if total[i] else (i + 0.5) * step, m))
        before = survive[i]
    return out


def distribution(placement: Placement, step: float = 2.0) -> list:
    """[(position, probability)] of the firing point, given that the skill fires."""
    regions = placement.regions
    if not placement.possible:
        return []
    if placement.branches:
        return _earliest([distribution(b, step) for b in placement.branches], step)
    kind = placement.policy.kind
    if kind == IMMEDIATE:
        return [(regions[0][0], 1.0)]
    if kind in (RANDOM, PICK_ONE):
        total = sum(e - s for s, e in regions)
        out = []
        for s, e in regions:
            mass = (e - s) / total if kind == RANDOM else 1 / len(regions)
            out += _spread(s, max(s, e - 10), mass, step)
        return out
    # UNIFORM / TRAFFIC: an offset along the regions laid end to end
    lengths = [e - s for s, e in regions]
    total = sum(lengths)
    bins = max(1, math.ceil(total / step))
    width = total / bins
    if kind == TRAFFIC:
        k, lam = placement.policy.k, placement.policy.lam
        lo, hi = _erlang_range(k, lam)
        cdf = [_erlang_cdf(lo + (j * width / total) * (hi - lo), k, lam) for j in range(bins + 1)]
        cdf[0], cdf[-1] = 0.0, 1.0
        masses = [cdf[j + 1] - cdf[j] for j in range(bins)]
    else:
        masses = [1 / bins] * bins
    out = []
    for j, mass in enumerate(masses):
        offset = (j + 0.5) * width
        for (s, e), length in zip(regions, lengths):
            if offset <= length:
                out.append((s + offset, mass))
                break
            offset -= length
    return out


# ── English ────────────────────────────────────────────────────────────────

def _order_rate(op: str, v: int) -> str:
    if op in ("<=", "<"):
        return f"in the front {v}% of the field"
    return f"in the back {100 - v}% of the field"


def describe(t: Term) -> str:  # noqa: C901
    n, op, v = t.name, t.op, t.value
    ph = PHASE_NAMES[v] if 0 <= v <= 3 else str(v)
    if n == "phase":
        return {"==": ph, ">=": f"{ph} or later", ">": f"after the {ph}",
                "<=": f"up to the {ph}", "<": f"before the {ph}"}[op]
    if n == "phase_random":
        return f"random point in the {ph}"
    if n == "phase_firsthalf_random":
        return f"random point in the first half of the {ph}"
    if n == "phase_laterhalf_random":
        return f"random point in the second half of the {ph}"
    if n == "phase_firstquarter_random":
        return f"random point in the first quarter of the {ph}"
    if n == "phase_corner_random":
        return f"random corner in the {ph}"
    if n == "phase_straight_random":
        return f"random straight in the {ph}"
    if n == "distance_rate":
        return f"after {v}% of the race" if op in (">=", ">") else f"within the first {v}% of the race"
    if n == "distance_rate_after_random":
        return f"random point after {v}% of the race"
    if n == "remain_distance":
        if op == "==":
            return f"exactly {v}m from the finish"
        return f"last {v}m" if op in ("<=", "<") else f"more than {v}m from the finish"
    if n == "corner":
        if v == 0:
            return "not on a corner" if op == "==" else "on a corner"
        return f"on corner {v}"
    if n == "corner_random":
        return f"random point on corner {v}"
    if n == "all_corner_random":
        return "random point on a corner"
    if n == "is_finalcorner":
        return "final corner or later" if v else "before the final corner"
    if n == "is_finalcorner_laterhalf":
        return "second half of the final corner"
    if n == "is_finalcorner_random":
        return "random point on the final corner"
    if n == "straight_random":
        return "random point on a straight"
    if n == "straight_front_type":
        return "on the home stretch" if v == 1 else "on the back stretch"
    if n == "is_last_straight_onetime":
        return "entering the final straight"
    if n == "is_last_straight":
        return "on the final straight"
    if n == "last_straight_random":
        return "random point on the final straight"
    if n == "slope":
        return ("on flat ground", "on an uphill", "on a downhill")[v] if 0 <= v <= 2 else str(t)
    if n == "up_slope_random":
        return "random point on an uphill"
    if n == "down_slope_random":
        return "random point on a downhill"
    if n in ("is_lastspurt", "lastspurt"):
        return "during the last spurt" if v != 2 or n == "is_lastspurt" else "during a full last spurt"
    if n == "accumulatetime":
        return f"after {v}s of racing"
    if n == "hp_per":
        return f"HP {v}% or less" if op in ("<=", "<") else f"HP above {v}%"
    if n == "order":
        if op == "==":
            return f"in {_ordinal(v)} place"
        if op in ("<=", "<"):
            return "in 1st place" if (op == "<=" and v == 1) else f"in the top {v if op == '<=' else v - 1}"
        return f"{_ordinal(v if op == '>=' else v + 1)} place or lower"
    if n == "order_rate":
        return _order_rate(op, v)
    m = re.match(r"order_rate_(in|out)(\d+)_continue", n)
    if m:
        rate = int(m.group(2))
        return f"stayed in the front {rate}%" if m.group(1) == "in" else f"stayed behind the front {rate}%"
    if n == "running_style":
        return STRATEGIES.get(v, f"strategy {v}")
    if n == "distance_type":
        return f"{DISTANCE_TYPES.get(v, v)} race"
    if n == "ground_type":
        return SURFACES.get(v, str(v))
    if n == "rotation":
        return "right-handed track" if v == 1 else "left-handed track"
    if n == "ground_condition":
        return f"{GROUNDS.get(v, v)} ground"
    if n == "weather":
        return f"{WEATHERS.get(v, v)} weather"
    if n == "season":
        return SEASONS.get(v, str(v))
    if n == "track_id":
        return "specific racecourse"
    if n == "change_order_onetime":
        return "after passing a runner" if op in ("<", "<=") else "after being passed"
    if n == "is_overtake":
        return "while overtaking"
    if n == "blocked_front":
        return "blocked by the runner ahead"
    if n == "blocked_front_continuetime":
        return f"blocked ahead for {v}s"
    if n == "blocked_side_continuetime":
        return f"side by side with a rival for {v}s"
    if n == "blocked_all_continuetime":
        return f"boxed in for {v}s"
    if n == "bashin_diff_behind":
        return f"a runner within {v} length{'s' if v != 1 else ''} behind" if op in ("<=", "<") \
            else f"{v}+ lengths clear of the runner behind"
    if n == "bashin_diff_infront":
        return f"within {v} length{'s' if v != 1 else ''} of the runner ahead"
    if n == "near_count":
        return f"{v}+ runners close by"
    if n == "is_surrounded":
        return "surrounded"
    if n == "infront_near_lane_time":
        return f"blocked by a runner ahead for {v}s"
    if n in ("behind_near_lane_time", "behind_near_lane_time_set1"):
        return f"a runner close behind for {v}s"
    if n == "overtake_target_time":
        return f"pressured by a runner trying to pass for {v}s"
    if n == "overtake_target_no_order_up_time":
        return f"on another runner's heels for {v}s"
    if n == "is_behind_in":
        return "passing on the outside"
    if n == "is_badstart":
        return "no late start" if v == 0 else "after a late start"
    if n == "is_move_lane":
        return "moving inward" if v == 1 else "moving outward"
    if n == "compete_fight_count":
        return "dueling on the final straight"
    if n in ("change_order_up_end_after", "change_order_up_finalcorner_after", "change_order_up_middle"):
        where = {"change_order_up_end_after": "in the late race",
                 "change_order_up_finalcorner_after": "from the final corner",
                 "change_order_up_middle": "in the mid race"}[n]
        return f"passed {v}+ runners {where}"
    if n == "distance_diff_top":
        return f"within {v}m of the leader" if op in ("<=", "<") else f"{v}m+ behind the leader"
    if n == "distance_diff_rate":
        return "near the front of the pack" if op in ("<=", "<") else "far behind the leader"
    if n == "popularity":
        return "as the favourite" if (op == "==" and v == 1) else f"popularity {op}{v}"
    if n == "post_number":
        return f"gate block {op}{v}"
    if n in ("temptation_count", "is_temptation"):
        return "not rushing"
    if n == "is_activate_any_skill":
        return "right after another skill"
    if n == "activate_count_start":
        return f"after {v} early-race skills"
    if n == "activate_count_middle":
        return f"after {v} mid-race skills"
    if n == "activate_count_end_after":
        return f"after {v} late-race skills"
    if n == "activate_count_later_half":
        return f"after {v} skills in the second half"
    if n == "activate_count_all":
        return f"after {v} skills" if op in (">=", ">") else f"{v} or fewer skills used"
    if n == "activate_count_heal":
        return f"after {v} recovery skills"
    if n == "random_lot":
        return f"{v}% chance"
    if n == "is_exist_chara_id":
        return "a specific runner in the race"
    if n == "motivation":
        return "good mood"
    if n.startswith("running_style_count_") and n.endswith("_otherself"):
        return "affects other strategies"
    if n == "always":
        return "always"
    return str(t)


def _join(items) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"


def describe_condition(cond: str) -> list:
    """One readable line per OR-branch."""
    branches = parse(cond)
    # "corner_random==1@corner_random==2@..." reads better as one line.
    if (len(branches) > 1 and all(len(b) == 1 for b in branches)
            and len({(b[0].name, b[0].op) for b in branches}) == 1):
        name, values = branches[0][0].name, [b[0].value for b in branches]
        if name in ("corner_random", "corner"):
            where = "random point on" if name == "corner_random" else "on"
            return [f"{where} corner {_join(str(v) for v in values)}"]
        if name == "ground_condition":
            return [f"{_join(GROUNDS.get(v, str(v)) for v in values)} ground"]
        if name == "weather":
            return [f"{_join(WEATHERS.get(v, str(v)) for v in values)} weather"]
        if name == "season":
            return [_join(dict.fromkeys(SEASONS.get(v, str(v)) for v in values))]
        if name == "track_id":
            return ["specific racecourses"]
    lines = []
    for branch in branches:
        parts = [describe(t) for t in branch if t.name != "always"]
        lines.append(", ".join(dict.fromkeys(parts)) or "always")
    return lines


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
