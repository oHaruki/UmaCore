#!/usr/bin/env python3
"""
Build skillmap/assets/gamedata.json from the Global client's master.mdb.

Skills (names, descriptions, trigger conditions, effects), support card skill
hints and graded races come straight from master.mdb. Corner, straight and slope
positions are not in master.mdb (they live in the course asset files), so they
come from the uma-tools course data, which is extracted from those assets.
Champions Meeting schedules are not built here: the bot reads them from uma.moe.

Run it after a game update, from the repo root:

  python scripts/build_skillmap_data.py
  python scripts/build_skillmap_data.py --mdb path/to/master.mdb --courses course_data.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MDB = Path(os.environ.get("USERPROFILE", "~")).expanduser() / "AppData/LocalLow/Cygames/Umamusume/master/master.mdb"
COURSES_URL = "https://raw.githubusercontent.com/alpha123/uma-tools/master/umalator-global/course_data.json"
OUT = Path(__file__).resolve().parent.parent / "skillmap" / "assets" / "gamedata.json"

# text_data categories
TXT_SKILL_NAME = 47
TXT_SKILL_DESC = 48
TXT_RACE = 32
TXT_TRACK = 35
TXT_CARD = 75
TXT_CARD_TITLE = 76
TXT_CARD_CHARA = 77
CARD_TYPES = {101: "Speed", 105: "Stamina", 102: "Power", 103: "Guts", 106: "Wit"}
UNRELEASED = 4_000_000_000      # placeholder cards are dated 2099


def _texts(cur, category: int) -> dict[int, str]:
    return {i: t for i, t in cur.execute('SELECT "index", text FROM text_data WHERE category = ?', (category,))}


def build_skills(cur) -> dict:
    names = _texts(cur, TXT_SKILL_NAME)
    descs = _texts(cur, TXT_SKILL_DESC)
    sp = dict(cur.execute("SELECT id, need_skill_point FROM single_mode_skill_need_point"))
    cols = [c[1] for c in cur.execute("PRAGMA table_info(skill_data)")]
    skills = {}
    for row in cur.execute("SELECT * FROM skill_data ORDER BY id"):
        r = dict(zip(cols, row))
        sid = r["id"]
        # 1000000+ are event-only bonus skills, not something a trainee can carry.
        if sid not in names or sid >= 1_000_000:
            continue
        alts = []
        for a in (1, 2):
            cond = r[f"condition_{a}"]
            if not cond:
                continue
            effects = []
            for e in (1, 2, 3):
                kind = r[f"ability_type_{a}_{e}"]
                if kind:
                    effects.append([kind, r[f"float_ability_value_{a}_{e}"] / 10000, r[f"target_type_{a}_{e}"]])
            dur = r[f"float_ability_time_{a}"]
            alts.append({
                "pre": r[f"precondition_{a}"],
                "cond": cond,
                "dur": dur / 10000 if dur > 0 else dur,
                "cd": r[f"float_cooldown_time_{a}"] / 10000,
                "eff": effects,
            })
        if not alts:
            continue
        name = names[sid]
        if 900_000 <= sid < 1_000_000:
            name += " (inherited)"
        skills[str(sid)] = {
            "n": name,
            "d": descs.get(sid, ""),
            "r": r["rarity"],
            "g": r["group_id"],
            "gv": r["grade_value"],
            "lot": r["activate_lot"],
            "sp": sp.get(sid),
            "icon": r["icon_id"],
            "alts": alts,
        }
    return skills


def build_courses(cur, geometry: dict) -> dict:
    tracks = _texts(cur, TXT_TRACK)
    courses = {}
    for cid, track, dist, ground, inout, turn, css in cur.execute(
        "SELECT id, race_track_id, distance, ground, inout, turn, course_set_status_id FROM race_course_set"
    ).fetchall():
        geo = geometry.get(str(cid))
        if not geo:
            continue
        status = cur.execute(
            "SELECT target_status_1, target_status_2 FROM race_course_set_status WHERE course_set_status_id = ?", (css,)
        ).fetchone()
        courses[str(cid)] = {
            "track": track,
            "name": tracks.get(track, str(track)),
            "dist": dist,
            "surf": ground,
            "turn": turn,
            "inout": inout,
            "stats": [s for s in (status or ()) if s],
            "corners": [[c["start"], c["start"] + c["length"]] for c in sorted(geo["corners"], key=lambda c: c["start"])],
            "straights": [[s["start"], s["end"], s["frontType"]] for s in sorted(geo["straights"], key=lambda s: s["start"])],
            "slopes": [[s["start"], s["start"] + s["length"], s["slope"] / 10000] for s in sorted(geo["slopes"], key=lambda s: s["start"])],
        }
    return courses


def build_races(cur, courses: dict) -> list:
    races, seen = [], set()
    for rid, grade, course, name in cur.execute(
        'SELECT r.id, r.grade, r.course_set, t.text FROM race r '
        'JOIN text_data t ON t.category = ? AND t."index" = r.id '
        'WHERE r."group" = 1 AND r.grade IN (100, 200, 300) ORDER BY r.grade, r.id',
        (TXT_RACE,),
    ):
        if str(course) not in courses or name in seen:
            continue
        seen.add(name)
        races.append({"id": rid, "name": name, "course": course, "grade": grade})
    return races


def build_cards(cur, skills: dict) -> dict:
    """Released support cards and the skills they hint."""
    full, title, chara = _texts(cur, TXT_CARD), _texts(cur, TXT_CARD_TITLE), _texts(cur, TXT_CARD_CHARA)
    cards = {}
    for cid, rarity, kind, command, start in cur.execute(
        "SELECT id, rarity, support_card_type, command_id, start_date FROM support_card_data ORDER BY id"
    ).fetchall():
        if cid not in full or start >= UNRELEASED:
            continue
        hints = [h for (h,) in cur.execute(
            "SELECT hint_value_1 FROM single_mode_hint_gain WHERE support_card_id = ? AND hint_gain_type = 0 "
            "ORDER BY hint_group", (cid,)) if str(h) in skills]
        cards[str(cid)] = {
            "t": title.get(cid, "").strip("[]"),
            "c": chara.get(cid, ""),
            "r": rarity,
            "k": CARD_TYPES.get(command, {2: "Friend", 3: "Group"}.get(kind, "")),
            "h": list(dict.fromkeys(hints)),
        }
    return cards


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mdb", type=Path, default=DEFAULT_MDB, help="path to master.mdb")
    ap.add_argument("--courses", help=f"course geometry JSON (path or URL, default {COURSES_URL})", default=COURSES_URL)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    if not args.mdb.exists():
        print(f"master.mdb not found at {args.mdb}", file=sys.stderr)
        return 1
    if args.courses.startswith("http"):
        with urllib.request.urlopen(args.courses, timeout=30) as resp:
            geometry = json.load(resp)
    else:
        geometry = json.loads(Path(args.courses).read_text(encoding="utf-8"))

    con = sqlite3.connect(f"file:{args.mdb.as_posix()}?mode=ro", uri=True)
    cur = con.cursor()
    skills = build_skills(cur)
    courses = build_courses(cur, geometry)
    data = {
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "skills": skills,
        "courses": courses,
        "races": build_races(cur, courses),
        "cards": build_cards(cur, skills),
    }
    con.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{args.out}: {len(skills)} skills, {len(courses)} courses, "
          f"{len(data['races'])} races, {len(data['cards'])} support cards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
