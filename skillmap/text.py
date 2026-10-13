"""
Short text for skill maps: the one line posted with each image, and the effect
and trigger summaries drawn on the /skill image.
"""
from __future__ import annotations

from . import conditions as cond
from .analysis import KIND_ACCEL, KIND_PASSIVE, KIND_RECOVERY, SkillResult, TrackAnalysis, heal_amount
from .gamedata import STAT_UPS, STRATEGIES, Alternative, CourseChoice, Skill

_EFFECTS = {
    27: lambda v: f"Speed {v:+.2f} m/s",
    31: lambda v: f"Acceleration {v:+.2f} m/s²",
    21: lambda v: f"Current speed {v:+.2f} m/s",
    22: lambda v: f"Current speed {v:+.2f} m/s",
    9: lambda v: f"{'Recovers' if v > 0 else 'Drains'} {abs(v) * 100:.1f}% HP",
    28: lambda v: f"Lane change speed {v:+.2f}",
    8: lambda v: f"Field of view {v:+.0f}",
    10: lambda v: f"Start reaction time x{v:.1f}",
    14: lambda v: "Fixed start reaction time",
    13: lambda v: "Longer rushing",
    29: lambda v: f"Rushing chance {v:+.0f}%",
    37: lambda v: f"Triggers {v:.0f} random gold skill{'s' if v != 1 else ''}",
    6: lambda v: "Runaway running style",
}


def effect_text(alt: Alternative) -> str:
    parts = []
    for e in alt.effects:
        text = f"{STAT_UPS[e.kind]} {e.value:+.0f}" if e.kind in STAT_UPS else \
            _EFFECTS.get(e.kind, lambda v: "Special effect")(e.value)
        if e.target != 1:
            text += " (other runners)"
        parts.append(text)
    return ", ".join(dict.fromkeys(parts)) or "No race effect"


def fires_text(skill: Skill, used: int) -> str:
    alt = skill.alts[used]
    text = "; or ".join(cond.describe_condition(alt.cond))
    if alt.pre:
        text = f"after {'; or '.join(cond.describe_condition(alt.pre))}: {text}"
    return text


def _where(choice: CourseChoice) -> str:
    if choice.key.startswith("cm:"):
        name = choice.label.split(" - ")[0].replace("CM ", "").split(" (")[0]
        return f"the {name} course, {choice.course.label}"
    return choice.course.label


def skill_line(r: SkillResult, choice: CourseChoice) -> str:
    name = f"**{r.skill.name}**"
    if r.kind == KIND_PASSIVE:
        alt = r.skill.alts[r.alt_index]
        state = "active on" if r.possible else "not active on"
        return f"{name}: {effect_text(alt)} for the whole race, {state} {_where(choice)}."
    if not r.possible:
        return f"{name} can't fire on {_where(choice)}: {r.blocked}."
    if r.kind == KIND_RECOVERY:
        return (f"{name} on {_where(choice)}: +{heal_amount(r) * 100:.1f}% HP, before the late race "
                f"**{r.before_spurt * 100:.0f}%** of the time.")
    if r.sig and r.sig.moves:
        line = f"{name} on {_where(choice)}: **{r.mean_gain:+.2f} lengths** on average"
        if r.kind == KIND_ACCEL:
            line += f", acceleration lands **{r.accel_hits * 100:.0f}%** of the time"
        return line + "."
    return f"{name} on {_where(choice)}: fires around {r.mean_position:,.0f}m."


def track_line(t: TrackAnalysis, kind: str) -> str:
    what = {"accel": "Acceleration", "speed": "Speed", "recovery": "Recovery"}[kind]
    return f"**{what} skills** on {_where(t.choice)}, {STRATEGIES[t.build.strategy]}."
