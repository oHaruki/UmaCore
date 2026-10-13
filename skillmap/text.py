"""
Text for skill maps: the /skill card (verdict, numbers, conditions, effect) and
the one line posted with a /track image.
"""
from __future__ import annotations

import math
from typing import Optional

from . import conditions as cond
from .analysis import (
    KIND_ACCEL, KIND_DEBUFF, KIND_PASSIVE, KIND_RECOVERY, KIND_SPEED, USEFUL_LENGTHS, SkillResult, TrackAnalysis,
    heal_amount,
)
from .gamedata import STAT_UPS, STRATEGIES, Alternative, CourseChoice, Skill

CM_FIELD = 9                # runners in a Champions Meeting race
STRONG_LENGTHS = 1.0        # average gain that counts as strong, and as decent
DECENT_LENGTHS = 0.4

KIND_NAMES = {KIND_ACCEL: "Acceleration", KIND_SPEED: "Speed", KIND_RECOVERY: "Recovery",
              KIND_PASSIVE: "Passive", KIND_DEBUFF: "Debuff"}
SHORT_STYLES = {1: "Front", 2: "Pace", 3: "Late", 4: "End"}

GOOD, OKAY, POOR, NONE, BLOCKED, NEUTRAL = 0x57F287, 0xA3D977, 0xFEE75C, 0x80848E, 0xED4245, 0x5865F2

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


def effect_line(alt: Alternative, distance: int) -> str:
    line = effect_text(alt)
    if alt.duration > 0:
        line += f"\nLasts {alt.duration * distance / 1000:.1f}s here ({alt.duration:g}s base)"
    elif alt.duration < 0:
        line += "\nLasts the whole race"
    return line


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _cm_places(t: cond.Term) -> str:
    """order_rate as places in a 9-runner Champions Meeting race (the game rounds halves up)."""
    cut = math.floor(CM_FIELD * t.value / 100 + 0.5)
    if t.op in ("<=", "<"):
        last = cut if t.op == "<=" else max(cut - 1, 1)
        return f"1st–{_ordinal(last)}"
    first = cut if t.op == ">=" else cut + 1
    return f"{_ordinal(max(first, 1))}–{_ordinal(CM_FIELD)}"


def _phrase(t: cond.Term) -> str:
    text = cond.describe(t)
    if t.name == "order_rate":
        text += f" ({_cm_places(t)} in CM)"
    return text


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def condition_lines(skill: Skill, used: int) -> list:
    """Bullet lines for the alternative the course uses; OR-branches become "or" lines."""
    alt = skill.alts[used]
    lines = []
    if alt.pre:
        lines.append("First: " + "; or ".join(cond.describe_condition(alt.pre)))
    branches = cond.parse(alt.cond)
    merged = cond.describe_condition(alt.cond)
    if len(branches) == 1:
        lines += list(dict.fromkeys(_sentence(_phrase(t)) for t in branches[0] if t.name != "always")) or ["Always"]
    elif len(merged) == 1:
        lines.append(_sentence(merged[0]))
    else:
        for i, branch in enumerate(branches):
            text = ", ".join(dict.fromkeys(_phrase(t) for t in branch if t.name != "always"))
            lines.append(("Or: " + text) if i else _sentence(text))
    return lines


def kind_label(r: SkillResult) -> str:
    kind = KIND_NAMES.get(r.kind)
    return f"{r.skill.rarity_label} skill" + (f" · {kind}" if kind else "")


def verdict(r: SkillResult) -> tuple:
    """(headline, the fact behind it, embed colour) for the skill on this course."""
    if r.kind == KIND_PASSIVE:
        if r.possible:
            return "Always on", "Active for the whole race.", NEUTRAL
        return "Not active here", _sentence(r.blocked) + ".", BLOCKED
    if not r.possible:
        return "Can't fire here", _sentence(r.blocked) + ".", BLOCKED
    if r.kind == KIND_RECOVERY:
        share = r.before_spurt
        head, colour = (("Heals in time", GOOD) if share >= 0.8 else
                        ("Sometimes heals too late", POOR) if share >= 0.3 else ("Heals too late", NONE))
        return head, f"Fires before the late race **{share * 100:.0f}%** of the time, while HP still feeds the last spurt.", colour
    if r.sig and r.sig.moves:
        g = r.mean_gain
        head, colour = (("Strong here", GOOD) if g >= STRONG_LENGTHS else ("Decent here", OKAY) if g >= DECENT_LENGTHS
                        else ("Weak here", POOR) if g >= USEFUL_LENGTHS else ("Wasted here", NONE))
        if r.sig.accel > 0:
            fact = f"The acceleration lands while she's still speeding up **{r.accel_hits * 100:.0f}%** of the time."
        else:
            fact = f"Wins ground **{r.useful * 100:.0f}%** of the times it fires."
        return head, fact, colour
    return "Fires here", "Its effect isn't simulated, so the map only shows where it fires.", NEUTRAL


def _fires(r: SkillResult) -> str:
    spots = [x for x, p in r.dist if p > 0]
    lo, hi = min(spots), max(spots)
    return f"{lo:,.0f}m" if hi - lo < 1 else f"{lo:,.0f}–{hi:,.0f}m"


def stat_fields(r: SkillResult) -> list:
    """Short (name, value) pairs shown side by side under the verdict."""
    if not r.possible or not r.dist or r.kind == KIND_PASSIVE:
        return []
    if r.kind == KIND_RECOVERY:
        return [("Heals", f"{heal_amount(r) * 100:.1f}% HP"), ("Fires", _fires(r))]
    if not (r.sig and r.sig.moves):
        return [("Fires", _fires(r))]
    fields = [("Avg gain", f"{r.mean_gain:+.2f} L")]
    if len(r.dist) > 1 and r.gains and r.best_gain >= USEFUL_LENGTHS:
        i = max(range(len(r.gains)), key=lambda k: r.gains[k])
        fields.append(("Best spot", f"{r.dist[i][0]:,.0f}m · {r.best_gain:+.1f} L"))
    fields.append(("Fires", _fires(r)))
    return fields


def style_line(by_style: dict, current: int) -> Optional[str]:
    """Average gain for each running style, when more than one can use the skill."""
    usable = {k: r for k, r in by_style.items() if r.possible and r.sig and r.sig.moves}
    if len(usable) < 2:
        return None
    parts = []
    for k in STRATEGIES:
        r = usable.get(k)
        text = f"{SHORT_STYLES[k]} {r.mean_gain:+.2f}" if r else f"{SHORT_STYLES[k]} –"
        parts.append(f"**{text}**" if k == current else text)
    return " · ".join(parts) + " L"


def _where(choice: CourseChoice) -> str:
    if choice.event:
        return f"the {choice.event} course, {choice.course.label}"
    return choice.course.label


def card_footer(choice: CourseChoice, strategy: int) -> str:
    where = f"{choice.event} · " if choice.event else ""
    return f"{where}{choice.course.label} · {STRATEGIES[strategy]} · L = lengths, solo sim"


def track_line(t: TrackAnalysis, kind: str) -> str:
    what = {"accel": "Acceleration", "speed": "Speed", "recovery": "Recovery"}[kind]
    return f"**{what} skills** on {_where(t.choice)}, {STRATEGIES[t.build.strategy]}."
