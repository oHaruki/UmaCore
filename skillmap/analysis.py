"""
Skill-on-course analysis: where a skill can fire, where it most likely fires,
and how much ground it wins from each of those spots.

For acceleration the spot is everything - the effect only works while the
runner is still speeding up (race start, the last-spurt kick, the top of a hill),
so a skill that fires on a corner where the runner is already at full speed
does nothing. ``SkillResult.accel_hits`` is the share of firings where the
acceleration lands while she is still speeding up; ``useful`` is the share where
the skill as a whole (speed boost included) gains anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from . import conditions as cond
from .gamedata import (
    ACCEL, CURRENT_SPEED, CURRENT_SPEED_DECEL, RECOVERY, STAT_UPS, TARGET_SELF, TARGET_SPEED,
    CourseChoice, GameData, Skill, load,
)
from .sim import Build, EffectSig, RaceModel, effect_sig, phase_start

USEFUL_LENGTHS = 0.05       # below this a firing counts as doing nothing

KIND_ACCEL, KIND_SPEED, KIND_RECOVERY, KIND_PASSIVE, KIND_DEBUFF, KIND_OTHER = (
    "accel", "speed", "recovery", "passive", "debuff", "other")


def skill_kind(skill: Skill) -> str:
    effects = skill.self_effects()
    if any(e.kind == ACCEL and e.value > 0 for e in effects):
        return KIND_ACCEL
    if any(e.kind in (TARGET_SPEED, CURRENT_SPEED, CURRENT_SPEED_DECEL) and e.value > 0 for e in effects):
        return KIND_SPEED
    if any(e.kind == RECOVERY and e.value > 0 for e in effects):
        return KIND_RECOVERY
    if any(e.kind in STAT_UPS for e in effects):
        return KIND_PASSIVE
    if any(e.target != TARGET_SELF for a in skill.alts for e in a.effects):
        return KIND_DEBUFF
    return KIND_OTHER


def strategy_hint(skill: Skill) -> Optional[int]:
    """The strategy a skill is written for, if its conditions name one."""
    for alt in skill.alts:
        for branch in cond.parse(alt.cond):
            for t in branch:
                if t.name == "running_style" and t.op == "==":
                    return t.value
    return None


@dataclass
class SkillResult:
    skill: Skill
    kind: str
    placement: cond.Placement
    alt_index: int = 0
    dist: list = field(default_factory=list)     # [(position, probability)]
    gains: list = field(default_factory=list)    # gain in lengths for each dist entry
    accel_gains: list = field(default_factory=list)  # the same, acceleration part only
    sig: Optional[EffectSig] = None
    duration: float = 0.0                        # seconds on this course
    mean_gain: float = 0.0
    best_gain: float = 0.0
    useful: float = 0.0                          # share of firings that gain anything
    accel_hits: float = 0.0                      # share where the acceleration lands while speeding up
    before_spurt: float = 0.0                    # share firing before the late race
    blocked: Optional[str] = None
    wit_chance: float = 1.0

    @property
    def possible(self) -> bool:
        return self.blocked is None

    @property
    def mean_position(self) -> float:
        return sum(x * p for x, p in self.dist)


@dataclass
class TrackAnalysis:
    choice: CourseChoice
    build: Build
    model: RaceModel
    results: list            # every skill that can fire here, best first
    blocked: int = 0         # skills of the kind that can't fire on this course/strategy


def _context(choice: CourseChoice, build: Build, model: RaceModel) -> cond.Context:
    base = {"speed": build.speed, "stamina": build.stamina, "power": build.power,
            "guts": build.guts, "wiz": build.wit}
    return cond.Context(choice.course, choice.env, build.strategy, model, base)


def _place(alt, ctx: cond.Context) -> cond.Placement:
    start = None
    pre_needs = []
    if alt.pre:
        pre = cond.evaluate(alt.pre, ctx)
        if not pre.possible:
            return cond.Placement(blocked=f"its opening condition can't happen ({pre.blocked})", needs=pre.needs)
        start = [(pre.regions[0][0], float(ctx.course.distance))]
        pre_needs = [f"first: {n}" for n in pre.needs]
    placement = cond.evaluate(alt.cond, ctx, start)
    placement.needs = pre_needs + placement.needs
    return placement


@lru_cache(maxsize=16)      # each model keeps its replay cache; a few MB apiece
def race_model(choice: CourseChoice, build: Build) -> RaceModel:
    return RaceModel(choice.course, choice.env, build)


def analyze_skill(skill: Skill, choice: CourseChoice, build: Build) -> SkillResult:
    model = race_model(choice, build)
    ctx = _context(choice, build, model)
    kind = skill_kind(skill)
    distance = choice.course.distance

    placement, index = None, 0
    for i, alt in enumerate(skill.alts):
        p = _place(alt, ctx)
        if p.possible:
            placement, index = p, i
            break
        if placement is None:
            placement = p
    result = SkillResult(skill, kind, placement, index)
    result.wit_chance = model.wit_check if skill.wit_check else 1.0
    if not placement.possible:
        result.blocked = placement.blocked or "can't fire on this course"
        return result

    alt = skill.alts[index]
    result.duration = alt.duration * distance / 1000 if alt.duration > 0 else 0.0
    result.dist = cond.distribution(placement)
    result.sig = effect_sig(alt.effects, alt.duration, distance)
    spurt = phase_start(distance, 2)
    result.before_spurt = sum(p for x, p in result.dist if x < spurt)
    sig = result.sig
    if sig.moves:
        result.gains = [model.gain(x, sig) for x, _ in result.dist]
        result.mean_gain = sum(g * p for g, (_, p) in zip(result.gains, result.dist))
        result.best_gain = max(result.gains, default=0.0)
        result.useful = sum(p for g, (_, p) in zip(result.gains, result.dist) if g >= USEFUL_LENGTHS)
    if sig.accel > 0:
        accel_only = EffectSig(accel=sig.accel, duration=sig.duration)
        result.accel_gains = (result.gains if accel_only == sig
                              else [model.gain(x, accel_only) for x, _ in result.dist])
        result.accel_hits = sum(p for g, (_, p) in zip(result.accel_gains, result.dist) if g >= USEFUL_LENGTHS)
    return result


def heal_amount(r: SkillResult) -> float:
    """Share of max HP the skill restores."""
    return sum(e.value for e in r.skill.alts[r.alt_index].effects if e.kind == RECOVERY and e.target == TARGET_SELF)


TRACK_KINDS = {
    "accel": (KIND_ACCEL,),
    "speed": (KIND_SPEED, KIND_ACCEL),
    "recovery": (KIND_RECOVERY,),
}


def analyze_track(choice: CourseChoice, build: Build, kind: str = "accel",
                  data: Optional[GameData] = None) -> TrackAnalysis:
    """Every white/gold skill and inherited unique of ``kind`` that can fire on the course."""
    data = data or load()
    model = race_model(choice, build)
    wanted = TRACK_KINDS[kind]
    results, blocked = [], 0
    for skill in data.skills.values():
        if skill.is_unique or skill_kind(skill) not in wanted:
            continue
        if kind == "speed" and not any(e.kind in (TARGET_SPEED, CURRENT_SPEED, CURRENT_SPEED_DECEL) and e.value > 0
                                       for e in skill.self_effects()):
            continue
        r = analyze_skill(skill, choice, build)
        if r.possible:
            results.append(r)
        else:
            blocked += 1
    if kind == "recovery":
        # HP that arrives before the late race, when it can still pay for the last spurt.
        results.sort(key=lambda r: (-heal_amount(r) * r.before_spurt, r.mean_position, r.skill.name))
    else:
        results.sort(key=lambda r: (-r.mean_gain, r.skill.name))
    return TrackAnalysis(choice, build, model, results, blocked)
