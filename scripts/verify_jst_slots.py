"""Read-only check of the JST slot mapping against every active club.

Runs the real scraper path for each club that has a circle_id and reports:

  * which daily_fans slot it now reads, and the date that data is attributed to
  * how far the parsed club total drifts from uma.moe's own monthly_point
    (a one-slot error is ~9% of the month; churn noise is ~0.3%)
  * whether the club's scrape time put it in the range that was reading
    live in-progress data before this fix
  * the current live standing, for comparison

Writes no business data. It SELECTs clubs and calls scrape()/fetch_live(), which
only read. (The api_usage metrics table does get one row per outbound call, which
is correct — those calls really happened.)

Usage:
    python scripts/verify_jst_slots.py              # every active club
    python scripts/verify_jst_slots.py 452414222    # specific circle_id(s)
"""
import asyncio
import logging
import sys
from datetime import datetime, time as dtime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.database import db
from config.settings import (
    DATABASE_URL, UMAMOE_SLOT_ERROR_FRACTION,
    UMAMOE_CHECKSUM_TOLERANCE, UMAMOE_CHECKSUM_MIN_ABS,
)
from models import Club
from scrapers.base_scraper import StaleDataError
from scrapers.umamoe_api_scraper import UmaMoeAPIScraper
from utils.jst_calendar import ROLLOVER_UTC_HOUR, resolve_finalized, resolve_live
from utils.timezone_helper import resolve_timezone

logging.basicConfig(level=logging.WARNING, format="      %(levelname)s %(message)s")

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def scrape_hour_utc(club) -> float:
    """The club's configured scrape time expressed as a UTC hour (today)."""
    try:
        tz = resolve_timezone(club.timezone)
        local = tz.localize(datetime.combine(
            datetime.now(tz).date(),
            dtime(club.scrape_time.hour, club.scrape_time.minute),
        ))
        u = local.astimezone(timezone.utc)
        return u.hour + u.minute / 60
    except Exception:
        return float("nan")


async def check(circle_id: str, label: str, club=None) -> str:
    """Returns 'ok' | 'drift' | 'stale' | 'error'."""
    print(f"\n{'-' * 78}\n{label}  [circle_id={circle_id}]")

    scraper = UmaMoeAPIScraper(circle_id)
    try:
        data = await scraper.scrape()
    except StaleDataError as e:
        print(f"  {YELLOW}STALE{RESET} — uma.moe hasn't finalized this circle yet")
        print(f"  {DIM}{str(e)[:140]}{RESET}")
        return "stale"
    except Exception as e:
        print(f"  {RED}ERROR{RESET} — {type(e).__name__}: {str(e)[:140]}")
        return "error"

    meta = scraper.get_meta()
    target = scraper.get_slot_target()
    total = sum(m["fans"][-1] for m in data.values() if m.get("fans"))

    print(f"  reads      : slot[{target.slot}] of {target.year}-{target.month:02d} "
          f"(JST day {target.jst_day})")
    print(f"  reported as: {scraper.get_data_date()}   members: {len(data)}")

    verdict = "ok"
    if meta.monthly_point:
        diff = abs(total - meta.monthly_point)
        rel = diff / meta.monthly_point
        # Scale against one day of fans — that is the size of a slot-index error.
        day_gain = (meta.monthly_point - meta.yesterday_points
                    if meta.yesterday_points is not None else None)
        if day_gain and day_gain > 0:
            ratio = diff / day_gain
            bad = ratio > UMAMOE_SLOT_ERROR_FRACTION
            mark = f"{RED}DRIFT{RESET}" if bad else f"{GREEN}OK{RESET}"
            print(f"  checksum   : {mark}  parsed {total:,} vs monthly_point "
                  f"{meta.monthly_point:,}")
            print(f"               off by {diff:,} = {ratio:.1%} of a day "
                  f"({day_gain:,}/day), {rel:.2%} of the month")
        else:
            bad = rel > UMAMOE_CHECKSUM_TOLERANCE and diff > UMAMOE_CHECKSUM_MIN_ABS
            mark = f"{RED}DRIFT{RESET}" if bad else f"{GREEN}OK{RESET}"
            print(f"  checksum   : {mark}  parsed {total:,} vs monthly_point "
                  f"{meta.monthly_point:,}  ({rel:.3%}, no day reference)")
        if bad:
            verdict = "drift"
    else:
        print(f"  checksum   : {DIM}skipped (no monthly_point in response){RESET}")

    # Was this club affected by the pre-fix bug?
    if club is not None:
        h = scrape_hour_utc(club)
        if h == h:  # not NaN
            when = f"{int(h):02d}:{int(h % 1 * 60):02d} UTC"
            if h < ROLLOVER_UTC_HOUR:
                print(f"  before fix : {RED}was reading live in-progress data{RESET} "
                      f"(scrapes {when}, before the {ROLLOVER_UTC_HOUR}:00 UTC finalize)")
            else:
                print(f"  before fix : {GREEN}unaffected{RESET} "
                      f"(scrapes {when}, after the finalize)")

    live = await scraper.fetch_live()
    if live and live.live_points:
        gained = f"{live.gained_today:,}" if live.gained_today is not None else "?"
        print(f"  live now   : {live.live_points:,} (rank {live.live_rank}) "
              f"as of {live.as_of}  |  +{gained} into the open day")
    return verdict


async def main():
    wanted = [a for a in sys.argv[1:] if a.isdigit()]
    now = datetime.now(timezone.utc)

    print(f"\nnow {now.isoformat(timespec='seconds')}")
    print(f"finalized target : {resolve_finalized(now).describe()}")
    print(f"live target      : {resolve_live(now).describe()}")
    print(f"slot-error threshold: {UMAMOE_SLOT_ERROR_FRACTION:.0%} of one day's fans")

    results = {}
    if wanted:
        for cid in wanted:
            results[cid] = await check(cid, f"circle {cid}")
    else:
        db.url = DATABASE_URL
        await db.connect()
        try:
            clubs = [c for c in await Club.get_all_active()
                     if c.circle_id and c.is_circle_id_valid()]
            print(f"active clubs with a valid circle_id: {len(clubs)}")
            for c in clubs:
                results[c.circle_id] = await check(c.circle_id, c.club_name, club=c)
        finally:
            await db.disconnect()

    print(f"\n{'=' * 78}")
    tally = {v: sum(1 for x in results.values() if x == v) for v in set(results.values())}
    print("summary: " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    bad = [c for c, v in results.items() if v in ("drift", "error")]
    if bad:
        print(f"{RED}needs attention:{RESET} {', '.join(bad)}")
        sys.exit(1)
    print(f"{GREEN}all clubs parsed within tolerance{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
