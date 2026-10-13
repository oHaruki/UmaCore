"""Skill maps: condition parsing, where skills can fire, the firing-point
distribution, and the race model that decides whether a firing pays off.
"""
import asyncio
import math

import pytest

from skillmap import conditions as cond
from skillmap.analysis import USEFUL_LENGTHS, analyze_skill, analyze_track, skill_kind, strategy_hint
from skillmap.gamedata import Course, CourseChoice, RaceEnv, load
from skillmap.schedule import CmSchedule, parse_cm
from skillmap.sim import Build, EffectSig, RaceModel, phase_start
from skillmap.text import condition_lines

# A 2000m course with two laps' worth of corners, one hill and a final straight.
COURSE = Course(
    id=1, track_id=10006, track="Test", distance=2000, surface=1, turn=2, inout=1, stats=(),
    corners=((300, 500), (500, 700), (1100, 1300), (1300, 1500)),
    straights=((0, 300, 1), (700, 1100, 2), (1500, 2000, 1)),
    slopes=((800, 1000, 1.5), (1600, 1700, -1.0)),
)
CHOICE = CourseChoice("course:1", COURSE.label, COURSE, RaceEnv())


def ctx(strategy=2, model=None):
    return cond.Context(COURSE, RaceEnv(), strategy, model, {})


def regions(text, **kw):
    p = cond.evaluate(text, ctx(**kw))
    return [(round(s), round(e)) for s, e in p.regions] if p.possible else p.blocked


# ── parsing ─────────────────────────────────────────────────────────────

def test_parse_splits_or_then_and():
    branches = cond.parse("phase>=2&corner!=0@order==1")
    assert [[str(t) for t in b] for b in branches] == [["phase>=2", "corner!=0"], ["order==1"]]


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        cond.parse("phase=>2")


# ── where a skill can fire ──────────────────────────────────────────────

def test_phase_boundaries_are_sixths():
    assert regions("phase==1") == [(333, 1333)]
    assert regions("phase>=2") == [(1333, 2000)]
    assert regions("phase<=1") == [(0, 1333)]


def test_corners_count_back_from_the_final_one():
    # The final corner is always corner 4; on a 4-corner course corner 1 is the first.
    assert regions("corner==4") == [(1300, 1500)]
    assert regions("corner==1") == [(300, 500)]
    six = Course(**{**COURSE.__dict__, "corners": ((100, 200), (200, 300)) + COURSE.corners})
    assert cond.corner_spans(six, 3) == [(100, 200), (1100, 1300)]


def test_final_corner_and_beyond_and_straights_after_it():
    assert regions("is_finalcorner==1") == [(1300, 2000)]
    assert regions("is_finalcorner==1&corner==0") == [(1500, 2000)]


def test_remaining_distance_counts_from_the_finish():
    assert regions("remain_distance<=400") == [(1600, 2000)]
    assert regions("remain_distance>=401") == [(0, 1599)]


def test_slopes():
    assert regions("slope==1") == [(800, 1000)]
    assert regions("down_slope_random==1") == [(1600, 1700)]


def test_race_setup_rules_skills_out_with_a_reason():
    assert regions("ground_type==2") == "Dirt only"
    assert regions("running_style==1") == "Front Runner only"
    assert regions("rotation==1") == "right-handed tracks only"
    assert regions("rotation==2") == [(0, 2000)]


def test_other_runner_conditions_are_assumed_and_listed():
    p = cond.evaluate("phase>=2&order==1&is_overtake==1", ctx())
    assert p.possible
    assert p.policy.kind == cond.TRAFFIC
    assert "in 1st place" in p.needs and "while overtaking" in p.needs


# ── how the firing point is picked ──────────────────────────────────────

def _total(dist):
    return sum(p for _, p in dist)


def test_immediate_fires_at_the_start_of_its_window():
    dist = cond.distribution(cond.evaluate("phase>=2&corner!=0", ctx()))
    assert len(dist) == 1 and round(dist[0][0]) == 1333


@pytest.mark.parametrize("text", ["all_corner_random==1", "straight_random==1", "phase_random==2",
                                  "phase>=2&is_overtake==1", "compete_fight_count>0"])
def test_random_policies_sum_to_one_and_stay_in_their_window(text):
    p = cond.evaluate(text, ctx())
    dist = cond.distribution(p)
    assert math.isclose(_total(dist), 1.0, rel_tol=1e-9)
    assert all(any(s <= x <= e for s, e in p.regions) for x, _ in dist)


def test_a_random_corner_is_picked_per_corner_not_per_metre():
    dist = cond.distribution(cond.evaluate("all_corner_random==1", ctx()))
    first = sum(p for x, p in dist if x < 500)
    assert math.isclose(first, 0.25, rel_tol=1e-6)


def test_or_branches_on_different_corners_fire_at_the_earliest():
    p = cond.evaluate("corner_random==1@corner_random==2@corner_random==3@corner_random==4", ctx())
    dist = cond.distribution(p)
    assert math.isclose(_total(dist), 1.0, rel_tol=1e-9)
    assert all(300 <= x < 500 for x, _ in dist)


def test_traffic_conditions_lean_toward_the_start_of_the_window():
    dist = cond.distribution(cond.evaluate("phase>=2&change_order_onetime<0", ctx()))
    mean = sum(x * p for x, p in dist)
    assert 1333 < mean < (1333 + 2000) / 2


def test_describe_merges_one_corner_per_branch():
    assert cond.describe_condition("corner_random==1@corner_random==2@corner_random==3@corner_random==4") == \
        ["random point on corner 1, 2, 3 or 4"]
    assert cond.describe_condition("phase>=2&order_rate<=50") == ["late race or later, in the front 50% of the field"]


# ── the race model ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def model():
    return RaceModel(COURSE, RaceEnv(), Build())


def test_last_spurt_starts_at_the_late_race_and_is_the_big_acceleration(model):
    spurt = next(w for w in model.windows if w.cause == "Last spurt")
    assert abs(spurt.start - phase_start(2000, 2)) < 5
    assert spurt.end - spurt.start > 100
    assert model.windows[0].cause == "Race start"
    assert model.speed_at(1990) == pytest.approx(model.spurt_speed, abs=1e-6)


def test_uphill_slows_the_runner_and_the_top_of_it_is_an_acceleration_window(model):
    assert model.speed_at(950) < model.speed_at(750)
    assert any(w.cause == "Uphill ends" and abs(w.start - 1000) < 10 for w in model.windows)


def test_acceleration_does_nothing_at_cruising_speed_and_a_lot_at_the_spurt(model):
    accel = EffectSig(accel=0.4, duration=3.0 * 2)
    assert model.gain(600, accel) == 0.0
    assert model.gain(phase_start(2000, 2), accel) > 2.0


def test_a_speed_boost_gains_ground_anywhere(model):
    speed = EffectSig(target=0.35, duration=5.0)
    assert model.gain(600, speed) > 0.5
    assert model.gain(1999, speed) == pytest.approx(0.0, abs=0.05)


# ── real game data ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def data():
    return load()


def test_every_skill_condition_parses(data):
    for skill in data.skills.values():
        for alt in skill.alts:
            cond.parse(alt.cond)
            cond.parse(alt.pre)


def test_lookup_keeps_circle_and_cross_variants_apart(data):
    assert data.find_skill("Right-Handed ○").name == "Right-Handed ○"
    assert data.find_skill("right-handed ×").name == "Right-Handed ×"
    assert data.find_skill("corner connoisseur").name == "Corner Connoisseur"


def test_course_lookup_takes_races_and_courses(data):
    assert data.resolve_course("race:1023").course.label.startswith("Nakayama 2500m")
    assert data.resolve_course("course:10701").course.label == "Chukyo 1200m Turf"
    assert data.resolve_course(None) is None


# ── Champions Meeting schedule (uma.moe timeline) ───────────────────────

def _cm(idx, title, start, desc, confirmed=True):
    return {"id": f"champions-meeting-{idx}", "type": "champions_meeting", "title": title,
            "global_release_date": start, "estimated_end_date": None, "is_confirmed": confirmed,
            "description": desc}


TIMELINE = {"events": [
    _cm(19, "Sagittarius Cup", "2026-10-07T22:00:00Z",
        "Nakayama - Turf<br>2500m - Long - Clockwise<br>Good - Winter - Cloudy"),
    _cm(20, "Capricorn Cup", "2026-10-27T22:00:00Z",
        "Chukyo - Turf<br>1200m - Short - Counterclockwise<br>Firm - Winter - Sunny"),
    _cm(21, "Aquarius Cup", "2026-11-18T22:00:00Z",
        "Tokyo - Dirt<br>1600m - Mile - Counterclockwise<br>Soft - Winter - Snow", confirmed=False),
    {"id": "jp-only", "type": "champions_meeting", "is_confirmed": True,
     "global_release_date": "2026-10-01T00:00:00Z", "description": "The race event is being held..."},
]}
OCT_13 = 1791849600     # 2026-10-13 00:00 UTC


def test_cm_race_text_maps_to_a_course_and_conditions(data):
    cm = parse_cm(TIMELINE["events"][0], data)
    assert cm.choice.course.label == "Nakayama 2500m Turf (Inner)"
    assert cm.choice.env == RaceEnv(season=4, weather=2, ground=2)
    assert parse_cm(TIMELINE["events"][3], data) is None


def test_ambiguous_layouts_take_the_outer_course(data):
    raw = _cm(1, "Test Cup", "2026-01-01T00:00:00Z", "Kyoto - Turf<br>1600m - Mile - Clockwise<br>Firm - Fall - Sunny")
    assert parse_cm(raw, data).choice.course.inout == 3


def test_schedule_offers_the_running_cm_and_the_next_confirmed_one(data):
    async def fetch():
        return TIMELINE
    schedule = CmSchedule(fetch, data)
    asyncio.run(schedule.refresh())
    labels = [c.label for c in schedule.choices(now=OCT_13)]
    assert labels[0].startswith("Current CM: Sagittarius Cup")
    assert labels[1].startswith("Next CM, Oct 27: Capricorn Cup")
    assert len(labels) == 2                       # unconfirmed and unparseable entries are skipped
    default = data.resolve_course(None, cms=schedule.choices(now=OCT_13))
    assert default.event == "Sagittarius Cup"
    # uma.moe ends CMs early; one still counts as running for its usual nine days.
    later = schedule.choices(now=OCT_13 + 3 * 86400)
    assert later[0].event == "Sagittarius Cup"


# ── card text and lookups ───────────────────────────────────────────────

def test_position_conditions_show_cm_places(data):
    lines = condition_lines(data.find_skill("Neck and Neck"), 0)
    assert "Pace Chaser" in lines
    assert any("1st–5th in CM" in line for line in lines)


def test_gold_and_white_versions_link_up(data):
    lower, higher = data.related(data.find_skill("Neck and Neck"))
    assert lower.name == "Head-On" and higher is None
    assert data.related(data.find_skill("Head-On"))[1].name == "Neck and Neck"
    assert data.cards_for(data.find_skill("Head-On").id)


def test_skill_kind_and_strategy_hint(data):
    assert skill_kind(data.find_skill("Corner Connoisseur")) == "accel"
    assert skill_kind(data.find_skill("Swinging Maestro")) == "recovery"
    assert strategy_hint(data.find_skill("Early Lead")) == 1


def test_corner_acceleration_only_pays_on_the_corners_that_meet_the_spurt(data):
    r = analyze_skill(data.find_skill("Corner Connoisseur"), CHOICE, Build())
    assert r.possible
    assert 0.0 < r.accel_hits < 1.0
    # Corners 1-2 only brush the small mid-race pace change; none of those firings count.
    early = [g for (x, _), g in zip(r.dist, r.gains) if x < 700]
    assert max(early) < USEFUL_LENGTHS
    late = [g for (x, _), g in zip(r.dist, r.gains) if 1300 <= x < 1400]
    assert min(late) > 1.0


def test_track_overview_lists_only_skills_that_can_fire(data):
    t = analyze_track(CHOICE, Build(strategy=1), "accel", data)
    assert t.results and all(r.possible for r in t.results)
    assert all(not r.skill.is_unique for r in t.results)
    assert [r.mean_gain for r in t.results] == sorted((r.mean_gain for r in t.results), reverse=True)


def test_card_verdict_and_style_comparison(data):
    from skillmap.text import stat_fields, style_line, verdict
    choice = data.resolve_course("course:10701")
    skill = data.find_skill("Groundwork")
    by_style = {k: analyze_skill(skill, choice, Build(strategy=k)) for k in (1, 2, 3, 4)}
    head, _, _ = verdict(by_style[1])
    assert head in ("Strong here", "Decent here")
    assert "**Front +" in style_line(by_style, 1)
    assert [name for name, _ in stat_fields(by_style[1])] == ["Avg gain", "Fires"]
    locked = data.find_skill("Neck and Neck")
    only_pace = {k: analyze_skill(locked, choice, Build(strategy=k)) for k in (1, 2, 3, 4)}
    assert style_line(only_pace, 2) is None
    assert verdict(only_pace[4])[0] == "Can't fire here"
