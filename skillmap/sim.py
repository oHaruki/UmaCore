"""
Single-runner race model for working out *when* a skill pays off.

It follows the game's movement rules: target speed per race phase, acceleration
only while the runner is below its target speed, the start dash, the uphill
slowdown, and the last spurt beginning at the late-race boundary (enough stamina
assumed). The random parts of a real race - position keep, rushing, downhill
mode, per-section speed noise, other runners - are left out, so this is the
clean-run baseline that skill gains are measured against.

An acceleration effect only does anything while the runner is accelerating; the
baseline marks those stretches (``accel_windows``) and ``gain`` replays the race
from a trigger point with the effect applied to see how much ground it wins.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Optional

from .gamedata import (
    ACCEL, CURRENT_SPEED, CURRENT_SPEED_DECEL, TARGET_SPEED, Course, RaceEnv,
)

DT = 1 / 15
LENGTH_M = 2.5          # one horse length
MIN_WINDOW_M = 10.0     # shorter acceleration stretches are left off the map
APTITUDES = "SABCDEFG"

# [strategy][phase]; phase 2 also covers the final stretch.
SPEED_COEF = {1: (1.0, 0.98, 0.962), 2: (0.978, 0.991, 0.975), 3: (0.938, 0.998, 0.994), 4: (0.931, 1.0, 1.0)}
ACCEL_COEF = {1: (1.0, 1.0, 0.996), 2: (0.985, 1.0, 0.996), 3: (0.975, 1.0, 1.0), 4: (0.945, 1.0, 0.997)}
HP_COEF = {1: 0.95, 2: 0.89, 3: 1.0, 4: 0.995}
DIST_APT_SPEED = (1.05, 1.0, 0.9, 0.8, 0.6, 0.4, 0.2, 0.1)
DIST_APT_ACCEL = (1.0, 1.0, 1.0, 1.0, 1.0, 0.6, 0.5, 0.4)
SURF_APT_ACCEL = (1.05, 1.0, 0.9, 0.8, 0.7, 0.5, 0.3, 0.1)
STRAT_APT_WIT = (1.1, 1.0, 0.85, 0.75, 0.6, 0.4, 0.2, 0.1)
GROUND_SPEED = {1: (0, 0, 0, 0, -50), 2: (0, 0, 0, 0, -50)}       # [surface][ground]
GROUND_POWER = {1: (0, 0, -50, -50, -50), 2: (0, -100, -50, -100, -100)}
HP_GROUND = {1: (0, 1.0, 1.0, 1.02, 1.02), 2: (0, 1.0, 1.0, 1.01, 1.02)}
PHASE_DECEL = (-1.2, -0.8, -1.0)
START_DASH_ACCEL = 24.0


@dataclass(frozen=True)
class Build:
    speed: int = 1200
    stamina: int = 1000
    power: int = 1000
    guts: int = 600
    wit: int = 1000
    strategy: int = 2
    distance_apt: str = "A"
    surface_apt: str = "A"
    strategy_apt: str = "A"
    mood: int = 2           # -2 awful .. 2 great

    @property
    def label(self) -> str:
        return f"{self.speed}/{self.stamina}/{self.power}/{self.guts}/{self.wit}"


def phase_start(distance: float, phase: int) -> float:
    return (0.0, distance / 6, distance * 2 / 3, distance * 5 / 6)[phase]


def phase_end(distance: float, phase: int) -> float:
    return (distance / 6, distance * 2 / 3, distance * 5 / 6, float(distance))[phase]


def _overcap(stat: float) -> float:
    return stat if stat <= 1200 else 1200 + (stat - 1200) // 2


@dataclass(frozen=True)
class EffectSig:
    """The movement part of a skill: what it adds and for how long (seconds, already scaled)."""
    accel: float = 0.0
    target: float = 0.0
    current: float = 0.0
    current_decel: bool = False
    duration: float = 0.0

    @property
    def moves(self) -> bool:
        return bool(self.accel or self.target or self.current)


def effect_sig(effects, duration: float, distance: int) -> EffectSig:
    accel = target = current = 0.0
    decel = False
    for e in effects:
        if e.target != 1:
            continue
        if e.kind == ACCEL:
            accel += e.value
        elif e.kind == TARGET_SPEED:
            target += e.value
        elif e.kind in (CURRENT_SPEED, CURRENT_SPEED_DECEL):
            current += e.value
            decel = decel or e.kind == CURRENT_SPEED_DECEL
    scaled = max(duration, 0.0) * distance / 1000
    return EffectSig(round(accel, 4), round(target, 4), round(current, 4), decel, round(scaled, 4))


@dataclass
class AccelWindow:
    start: float
    end: float
    cause: str


@dataclass
class Baseline:
    """Per-frame record of the reference run."""
    ts: list = field(default_factory=list)
    xs: list = field(default_factory=list)
    vs: list = field(default_factory=list)
    targets: list = field(default_factory=list)
    accels: list = field(default_factory=list)
    hp: list = field(default_factory=list)       # remaining HP ratio
    dash: list = field(default_factory=list)
    accelerating: list = field(default_factory=list)


class RaceModel:
    def __init__(self, course: Course, env: RaceEnv, build: Build):
        self.course = course
        self.env = env
        self.build = build
        d = self.distance = course.distance
        s = build.strategy
        motiv = 1 + 0.02 * build.mood
        speed = _overcap(build.speed) * motiv
        stamina = _overcap(build.stamina) * motiv
        power = _overcap(build.power) * motiv
        guts = _overcap(build.guts) * motiv
        wit = _overcap(build.wit) * motiv
        if course.stats:
            by_stat = {1: speed, 2: stamina, 3: power, 4: guts, 5: wit}
            bonus = sum((1 + math.floor(min(by_stat[x], 901) / 300.01)) * 0.05 for x in course.stats)
            speed *= 1 + bonus / len(course.stats)
        ground = env.ground if 1 <= env.ground <= 4 else 1
        surf = course.surface if course.surface in GROUND_SPEED else 1
        speed = max(speed + GROUND_SPEED[surf][ground], 1)
        power = max(power + GROUND_POWER[surf][ground], 1)
        wit *= STRAT_APT_WIT[APTITUDES.index(build.strategy_apt)]
        self.stats = {"speed": speed, "stamina": stamina, "power": power, "guts": guts, "wit": wit}

        dist_speed = DIST_APT_SPEED[APTITUDES.index(build.distance_apt)]
        accel_apt = SURF_APT_ACCEL[APTITUDES.index(build.surface_apt)] * DIST_APT_ACCEL[APTITUDES.index(build.distance_apt)]
        self.base_speed = 20.0 - (d - 2000) / 1000.0
        speed_term = math.sqrt(500.0 * speed) * dist_speed * 0.002
        self.phase_target = [self.base_speed * SPEED_COEF[s][p] + (speed_term if p == 2 else 0.0) for p in range(3)]
        self.spurt_speed = ((self.phase_target[2] + 0.01 * self.base_speed) * 1.05 + speed_term
                            + math.pow(450.0 * guts, 0.597) * 0.0001)
        root_power = math.sqrt(500.0 * power)
        self.phase_accel = [0.0006 * root_power * ACCEL_COEF[s][p] * accel_apt for p in range(3)]
        self.uphill_accel = [0.0004 * root_power * ACCEL_COEF[s][p] * accel_apt for p in range(3)]
        self.min_speed = 0.85 * self.base_speed + math.sqrt(200.0 * guts) * 0.001
        self.dash_cap = 0.85 * self.base_speed
        # Mean of the per-section random speed adjustment.
        peak = wit / 5500.0 * math.log10(max(wit * 0.1, 1.0))
        self.section_bonus = self.base_speed * (peak - 0.325) / 100.0
        self.uphill_penalty = 200.0 / power
        self.spurt_start = phase_start(d, 2)
        self.max_hp = 0.8 * HP_COEF[s] * stamina + d
        self.hp_ground = HP_GROUND[surf][ground]
        self.guts_hp = 1.0 + 200.0 / math.sqrt(600.0 * guts)
        self.wit_check = max(100.0 - 9000.0 / wit, 20.0) / 100.0
        self.baseline = self._run_baseline()
        self.windows = self._find_windows()
        self._gain_cache: dict = {}

    # ── course lookup ──────────────────────────────────────────────────────

    def phase_at(self, x: float) -> int:
        if x < self.distance / 6:
            return 0
        return 1 if x < self.distance * 2 / 3 else 2

    def slope_at(self, x: float) -> float:
        for start, end, pct in self.course.slopes:
            if start <= x < end:
                return pct
        return 0.0

    def _forces(self, x: float, v: float, dash: bool, mod_target: float, mod_accel: float):
        """Target speed and acceleration at position ``x`` for current speed ``v``."""
        phase = self.phase_at(x)
        if x >= self.spurt_start:
            target = self.spurt_speed
        else:
            target = self.phase_target[phase] + self.section_bonus
        target += mod_target
        slope = self.slope_at(x)
        if slope > 0:
            target = max(target - slope * self.uphill_penalty, self.min_speed)
        if v > target:
            return target, PHASE_DECEL[phase]
        accel = (self.uphill_accel if slope > 0 else self.phase_accel)[phase] + mod_accel
        if dash:
            accel += START_DASH_ACCEL
        return target, accel

    def _max_speed(self, v: float, target: float, dash: bool) -> float:
        if dash:
            return min(target, self.dash_cap)
        return 9999.0 if v > target else target

    # ── baseline ───────────────────────────────────────────────────────────

    def _run_baseline(self) -> Baseline:
        b = Baseline()
        t, x, v, a, dash, hp = 0.0, 0.0, 3.0, 0.0, True, self.max_hp
        target = self.dash_cap
        base = self.base_speed
        while x < self.distance:
            v_before = v
            halfv = min(v + 0.5 * DT * a, self._max_speed(v, target, dash))
            x += halfv * DT
            t += DT
            phase = self.phase_at(x)
            hp -= (20.0 * (v - base + 12.0) ** 2 / 144.0 * self.hp_ground
                   * (self.guts_hp if phase >= 2 else 1.0)) * DT
            target, a = self._forces(x, v, dash, 0.0, 0.0)
            v = min(halfv + 0.5 * DT * a, self._max_speed(v, target, dash))
            if not dash and v < self.min_speed:
                v = self.min_speed
            elif dash and v >= self.dash_cap:
                dash = False
            b.ts.append(t)
            b.xs.append(x)
            b.vs.append(v)
            b.targets.append(target)
            b.accels.append(a)
            b.hp.append(max(hp, 0.0) / self.max_hp)
            b.dash.append(dash)
            b.accelerating.append(a > 0 and v_before < target - 1e-6)
        return b

    def _find_windows(self) -> list:
        b, d = self.baseline, self.distance
        spans, start = [], None
        for i, acc in enumerate(b.accelerating):
            x0 = b.xs[i - 1] if i else 0.0
            if acc and start is None:
                start = x0
            elif not acc and start is not None:
                spans.append([start, x0])
                start = None
        if start is not None:
            spans.append([start, b.xs[-1]])
        merged = []
        for s, e in spans:
            if merged and s - merged[-1][1] < 5:
                merged[-1][1] = e
            else:
                merged.append([s, e])
        hill_ends = [end for start_, end, pct in self.course.slopes if pct > 0]
        windows = []
        for s, e in merged:
            # A few metres of catch-up after easing off at a slope's edge isn't worth showing.
            if e - s < MIN_WINDOW_M:
                continue
            if s < 5:
                cause = "Race start"
            elif abs(s - self.spurt_start) < 8:
                cause = "Last spurt"
            elif abs(s - d / 6) < 8:
                cause = "Mid-race pace"
            elif any(abs(s - h) < 8 for h in hill_ends):
                cause = "Hill eases" if self.slope_at(s + 8) > 0 else "Uphill ends"
            else:
                cause = "Speed change"
            windows.append(AccelWindow(s, min(e, d), cause))
        return windows

    def index_at(self, x: float) -> int:
        """Baseline frame at or just past position ``x``."""
        i = bisect_left(self.baseline.xs, x)
        return min(i, len(self.baseline.xs) - 1)

    def time_at(self, x: float) -> float:
        b = self.baseline
        i = bisect_left(b.xs, x)
        if i <= 0:
            return b.ts[0] * x / b.xs[0] if b.xs[0] else 0.0
        if i >= len(b.xs):
            return b.ts[-1] + (x - b.xs[-1]) / b.vs[-1]
        x0, x1 = b.xs[i - 1], b.xs[i]
        f = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
        return b.ts[i - 1] + f * (b.ts[i] - b.ts[i - 1])

    def speed_at(self, x: float) -> float:
        b = self.baseline
        i = bisect_left(b.xs, x)
        if i <= 0:
            return b.vs[0]
        if i >= len(b.xs):
            return b.vs[-1]
        x0, x1 = b.xs[i - 1], b.xs[i]
        f = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
        return b.vs[i - 1] + f * (b.vs[i] - b.vs[i - 1])

    def hp_at(self, x: float) -> float:
        return self.baseline.hp[self.index_at(x)]

    def finish_time(self) -> float:
        return self.time_at(self.distance)

    def position_at_time(self, t: float) -> float:
        b = self.baseline
        i = bisect_left(b.ts, t)
        return b.xs[min(i, len(b.xs) - 1)]

    def accelerating_near(self, i: int, frames: int) -> bool:
        acc = self.baseline.accelerating
        return any(acc[i:min(i + frames, len(acc))])

    # ── skill gains ────────────────────────────────────────────────────────

    def gain(self, x: float, sig: EffectSig) -> float:
        """Horse lengths gained at the finish by firing ``sig`` at position ``x``.

        Replays run on a grid (finer for pure acceleration, whose gain changes
        quickly at the edges of an acceleration window) and are interpolated.
        """
        if not sig.moves or x >= self.distance:
            return 0.0
        step = 5.0 if not (sig.target or sig.current) else 10.0
        lo = math.floor(x / step) * step
        g0 = self._gain_at(lo, sig)
        g1 = self._gain_at(lo + step, sig)
        return g0 + (g1 - g0) * (x - lo) / step

    def _gain_at(self, x: float, sig: EffectSig) -> float:
        if x >= self.distance:
            return 0.0
        key = (x, sig)
        cached = self._gain_cache.get(key)
        if cached is not None:
            return cached
        i = self.index_at(x)
        frames = int(sig.duration / DT) + 2
        if not sig.target and not sig.current and not self.accelerating_near(i, frames):
            result = 0.0    # acceleration while already at target speed does nothing
        else:
            result = self._replay(i, sig) * self.baseline.vs[-1] / LENGTH_M
        self._gain_cache[key] = result
        return result

    def _replay(self, i: int, sig: EffectSig) -> float:
        """Seconds saved: replay from baseline frame ``i`` with the effect active."""
        b, d = self.baseline, self.distance
        t, x, v, a, dash = b.ts[i], b.xs[i], b.vs[i], b.accels[i], b.dash[i]
        target = b.targets[i]
        remaining = sig.duration
        active = True
        while True:
            mod_t = sig.target if active else 0.0
            mod_a = sig.accel if active else 0.0
            mod_c = sig.current if active else 0.0
            halfv = min(v + 0.5 * DT * a, self._max_speed(v, target, dash))
            step = (halfv + mod_c) * DT
            if x + step >= d:
                t_finish = t + (d - x) / max(halfv + mod_c, 0.1)
                return self.time_at(d) - t_finish
            x += step
            t += DT
            if active:
                remaining -= DT
                if remaining <= 0:
                    active = False
                    mod_t = mod_a = 0.0
                    if sig.current_decel:
                        v += sig.current
            target, a = self._forces(x, v, dash, mod_t, mod_a)
            v = min(halfv + 0.5 * DT * a, self._max_speed(v, target, dash))
            if not dash and v < self.min_speed:
                v = self.min_speed
            elif dash and v >= self.dash_cap:
                dash = False
            if not active and abs(v - self.speed_at(x)) < 1e-3:
                return self.time_at(x) - t
