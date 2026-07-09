"""
Uma.moe club leaderboard fetcher (used by the rank-promotion feature).

Two small, best-effort reads against the uma.moe API — kept separate from the
long ``umamoe_api_scraper`` so the daily-scrape path stays untouched:

* :func:`fetch_entry_at_rank` — the club currently sitting at a given position,
  via the public club-leaderboard endpoint ``GET /api/v4/circles/list`` (the one
  that powers uma.moe's ``/circles`` page).
* :func:`fetch_own_standing` — our own club's current rank and monthly fan total,
  via the per-circle endpoint ``GET /api/v4/circles``.

Both endpoints are scrape-protected and require an ``X-API-Key`` header — without
``UMAMOE_API_KEY`` the list endpoint returns a Cloudflare browser-proof 403. When
the key is missing or any call fails these functions return ``None`` and callers
degrade gracefully, mirroring ``umamoe_profile.fetch_trainer_profile``.

All calls go through the shared uma.moe rate limiter so they never compete with
the daily scrape budget.

NOTE on accuracy: uma.moe scrapes each circle at staggered times, so per-club
``monthly_point`` snapshots reflect slightly different moments. Any gap computed
from them is therefore an estimate, not an exact figure.
"""
from typing import Dict, Optional
import logging
from datetime import datetime

import aiohttp

from config.settings import UMAMOE_API_KEY
from utils.rate_limiter import umamoe_limiter
from utils.api_metrics import track_api_call

logger = logging.getLogger(__name__)

_LIST_URL = "https://uma.moe/api/v4/circles/list"
_CIRCLE_URL = "https://uma.moe/api/v4/circles"


def _headers() -> Dict[str, str]:
    return {
        "Accept-Encoding": "gzip, deflate",
        "X-API-Key": UMAMOE_API_KEY or "",
    }


async def fetch_entry_at_rank(rank: int) -> Optional[Dict]:
    """
    Return the leaderboard entry at the given position rank, or ``None``.

    Uses the paginated club-leaderboard endpoint: with ``limit=1`` the entry at
    ``page = rank - 1`` (0-based) is the club at that rank. The returned dict is a
    raw circle record with fields like ``circle_id``, ``name``, ``monthly_rank``
    and ``monthly_point`` (the club's monthly fan total).
    """
    if rank is None or rank < 1:
        return None
    if not UMAMOE_API_KEY:
        logger.info("UMAMOE_API_KEY not set — skipping leaderboard fetch")
        return None

    params = {
        "page": rank - 1,
        "limit": 1,
        "sort_by": "rank",
        "sort_dir": "asc",
    }
    try:
        await umamoe_limiter.acquire()
        async with aiohttp.ClientSession(headers=_headers()) as session:
            async with track_api_call("uma.moe", "circles_list", context=f"rank:{rank}") as m:
                async with session.get(
                    _LIST_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    m["status_code"] = resp.status
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(
                            f"uma.moe leaderboard rank {rank} returned HTTP "
                            f"{resp.status}: {body[:200]}"
                        )
                        return None
                    data = await resp.json()
                    m["ok"] = True
    except aiohttp.ClientError as e:
        logger.warning(f"Network error fetching leaderboard rank {rank}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch leaderboard rank {rank}: {e}")
        return None

    circles = data.get("circles") or data.get("list") or []
    if not circles:
        logger.info(f"uma.moe leaderboard returned no entry at rank {rank}")
        return None
    return circles[0]


async def fetch_own_standing(circle_id: str) -> Optional[Dict]:
    """
    Return our own club's current standing, or ``None``.

    Reads the per-circle endpoint for the current competition month and returns the
    nested ``circle`` object (``monthly_rank``, ``monthly_point``, ``live_rank``,
    ``live_points``, …) enriched with the top-level ``fans_to_next_tier`` /
    ``fans_to_lower_tier`` values uma.moe already computes.
    """
    if not circle_id or not str(circle_id).isdigit():
        return None
    if not UMAMOE_API_KEY:
        logger.info("UMAMOE_API_KEY not set — skipping own-standing fetch")
        return None

    now = datetime.now()
    params = {"circle_id": circle_id, "year": now.year, "month": now.month}
    try:
        await umamoe_limiter.acquire()
        async with aiohttp.ClientSession(headers=_headers()) as session:
            async with track_api_call("uma.moe", "circle_standing", context=str(circle_id)) as m:
                async with session.get(
                    _CIRCLE_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    m["status_code"] = resp.status
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(
                            f"uma.moe standing for circle {circle_id} returned HTTP "
                            f"{resp.status}: {body[:200]}"
                        )
                        return None
                    data = await resp.json()
                    m["ok"] = True
    except aiohttp.ClientError as e:
        logger.warning(f"Network error fetching standing for circle {circle_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch standing for circle {circle_id}: {e}")
        return None

    circle = data.get("circle")
    if not circle:
        return None
    # Surface uma.moe's own tier-gap math alongside the circle fields.
    circle = dict(circle)
    for key in ("fans_to_next_tier", "fans_to_lower_tier"):
        if key in data:
            circle[key] = data[key]
    return circle
