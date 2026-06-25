"""
Uma.moe trainer-profile fetcher (used by the trainer card).

Pulls the rich per-trainer payload from ``GET /api/v4/user/profile/{account_id}``
— team rating, global fan ranks, follower count and circle standing — that the
circle-data scraper doesn't expose. This endpoint is scrape-protected and
requires an ``X-API-Key`` header, so without ``UMAMOE_API_KEY`` set it simply
returns ``None`` and the card falls back to local DB data.

All calls go through the shared uma.moe rate limiter so this never competes with
the daily scrape budget.
"""
from typing import Dict, Optional
import logging
import aiohttp

from config.settings import UMAMOE_API_KEY
from utils.rate_limiter import umamoe_limiter
from utils.api_metrics import track_api_call

logger = logging.getLogger(__name__)

_PROFILE_URL = "https://uma.moe/api/v4/user/profile/{account_id}"


async def fetch_trainer_profile(account_id: Optional[str]) -> Optional[Dict]:
    """
    Fetch a trainer's full uma.moe profile.

    Args:
        account_id: The trainer/viewer ID (9-12 digit string). This is the same
            value UmaCore stores as ``member.trainer_id``.

    Returns:
        The parsed ``ProfileResponse`` dict, or ``None`` when the profile can't
        be fetched (no API key, unknown trainer, network/HTTP error). Callers
        must treat enrichment as best-effort and degrade gracefully.
    """
    if not account_id:
        return None

    if not UMAMOE_API_KEY:
        logger.info("UMAMOE_API_KEY not set — skipping trainer profile enrichment")
        return None

    url = _PROFILE_URL.format(account_id=account_id)
    headers = {
        "Accept-Encoding": "gzip, deflate",
        "X-API-Key": UMAMOE_API_KEY,
    }

    try:
        await umamoe_limiter.acquire()
        async with aiohttp.ClientSession(headers=headers) as session:
            async with track_api_call("uma.moe", "profile", context=str(account_id)) as m:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    m["status_code"] = response.status
                    # A missing profile is an expected outcome, not an error.
                    m["ok"] = response.status in (200, 404)
                    if response.status == 404:
                        logger.info(f"uma.moe has no profile for trainer {account_id}")
                        return None
                    if response.status != 200:
                        body = await response.text()
                        logger.warning(
                            f"uma.moe profile {account_id} returned HTTP "
                            f"{response.status}: {body[:200]}"
                        )
                        return None
                    return await response.json()
    except aiohttp.ClientError as e:
        logger.warning(f"Network error fetching uma.moe profile {account_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch uma.moe profile {account_id}: {e}")
        return None
