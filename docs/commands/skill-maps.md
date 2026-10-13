# Skill Maps

Shows where a skill fires on a course, and whether it actually does anything
there. Anyone can use these commands.

Acceleration is the reason this exists. An acceleration skill only helps while
your uma is still speeding up: at the start, at the kick into the last spurt,
and at the top of a hill. Fire it anywhere else and she is already at full
speed, so it does nothing. Skill maps show those speed-up stretches on the
course and check every place a skill can fire against them.

---

## /skill

One skill on one course.

| Parameter | Required | Description |
|---|---|---|
| `name` | Yes | Skill name (autocomplete) |
| `course` | No | Champions Meeting, race or course (autocomplete). Default: the current Champions Meeting |
| `strategy` | No | Running style. Default: the one the skill is written for, else Pace Chaser |

The reply is one line with the headline number, and an image (click it for full
size) with:

- the course chart: the reference uma's speed, height, slopes, corners and race
  phases, with the stretches where she is still accelerating shaded green
- the skill drawn as a box over the stretch it stays active when fired at its
  best spot
- when it can fire and what it does, from the game's own trigger conditions
- every spot it can fire (taller = more likely), coloured by what a firing there
  wins, plus the average lengths gained and, for acceleration skills, how often
  the acceleration lands while she is still speeding up

A dropdown under the reply switches running style without retyping the command.
Only the person who ran it can use the dropdown.

---

## /track

Every skill of one type on a course, best first.

| Parameter | Required | Description |
|---|---|---|
| `course` | No | Champions Meeting, race or course. Default: the current Champions Meeting |
| `type` | No | Acceleration (default), Speed or Recovery |
| `strategy` | No | Running style. Default: Pace Chaser |

Lists white and gold skills and inherited uniques that can fire on the course for
that running style, best first. Skills that can't (wrong distance, surface or
running style) are left out. The top 10 are also drawn on the chart as boxes,
each over the stretch it is active when fired where it usually fires.

| Colour | Meaning |
|---|---|
| Green | Its acceleration lands while still speeding up, so it wins ground |
| Blue | Only its speed boost helps (or, for recovery, it fires before the late race) |
| Grey | Fires at full speed and does nothing |

For acceleration, **Hits** is the share of firings where the acceleration pays.
For recovery, **Early** is the share that fires before the late
race, while the HP still counts toward the last spurt.

Dropdowns switch running style and skill type in place.

---

## How it works

**Where a skill can fire** comes from the skill's trigger conditions in the
game data (for example `phase>=2&corner!=0&order==1`), evaluated against the
course's real corners, straights and slopes. Conditions about the race setup
(distance, surface, running style, ground, weather, season) rule a skill in or
out. Conditions about other runners (position, overtaking, being blocked) can't
be known in advance, so they are assumed met; skills with any are marked `*`.

**Where it most likely fires** follows how the game picks the spot:

| Trigger | Where it fires |
|---|---|
| Fixed (e.g. "late race, on a corner") | The first point where everything holds |
| Random (e.g. "random point in the late race") | Anywhere in the window, evenly |
| Random corner / random straight | A random corner or straight first, then a random point in it |
| Depends on other runners | Most likely early in the window |

**Whether it pays** comes from a race model of one reference uma running alone:
target speed per race phase, acceleration only while below that speed, the start
dash, slower uphills, and the last spurt starting at the late-race boundary. For
each spot a skill can fire, the race is replayed with the skill switched on and
the ground it wins at the finish is measured.

### Assumptions

- Reference uma: 1200 Speed, 1000 Stamina, 1000 Power, 600 Guts, 1000 Wit, A
  aptitudes, great mood, enough stamina for a full last spurt.
- She runs alone: no blocking, position keep, rushing or downhill speed-ups.
- Gains are for when the skill fires; white and gold skills also have to pass a
  Wit check first (the reply shows the chance).
- Unique skills are shown at level 1.

Real races add traffic, so treat the numbers as a comparison between skills,
not a promise. The timing picture (which skills land in the speed-up windows)
holds regardless.
