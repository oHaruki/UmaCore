"""
Uma.moe trainer-profile fetcher (used by the trainer card and transfer requests).

Pulls the rich per-trainer payload from ``GET /api/v4/user/profile/{account_id}``
— team rating, global fan ranks, follower count and circle standing — that the
circle-data scraper doesn't expose. This endpoint is scrape-protected and
requires an ``X-API-Key`` header, so without ``UMAMOE_API_KEY`` set it simply
returns ``None`` and the card falls back to local DB data.

All calls go through the shared uma.moe rate limiter so this never competes with
the daily scrape budget.
"""
from dataclasses import dataclass
from typing import Dict, Optional
import logging
import aiohttp

from config.settings import UMAMOE_API_KEY
from utils.rate_limiter import umamoe_limiter, PRIORITY_INTERACTIVE
from utils.api_metrics import track_api_call

logger = logging.getLogger(__name__)

_PROFILE_URL = "https://uma.moe/api/v4/user/profile/{account_id}"


class ProfileUnavailable(Exception):
    """uma.moe could not be asked — as opposed to answering "no such trainer"."""


@dataclass(frozen=True)
class TrainerIdentity:
    """Who a trainer ID belongs to, as uma.moe reports it."""
    account_id: str
    name: str
    circle_id: Optional[str]
    circle_name: Optional[str]

    @classmethod
    def from_profile(cls, account_id: str, profile: Dict) -> Optional['TrainerIdentity']:
        """None when the profile carries no trainer name to show anyone."""
        trainer = profile.get("trainer") or {}
        name = (trainer.get("name") or "").strip()
        if not name:
            return None

        # Not in a circle comes back as a null or empty ``circle``.
        circle = profile.get("circle") or {}
        circle_id = circle.get("circle_id")
        return cls(
            account_id=str(trainer.get("account_id") or account_id),
            name=name,
            circle_id=str(circle_id) if circle_id else None,
            circle_name=(circle.get("name") or "").strip() or None,
        )


async def _request_profile(account_id: str) -> Optional[Dict]:
    """GET one profile.

    Returns ``None`` when uma.moe answers that it has no such trainer, and raises
    ``ProfileUnavailable`` when it could not answer at all.
    """
    if not UMAMOE_API_KEY:
        logger.info("UMAMOE_API_KEY not set — can't fetch trainer profiles")
        raise ProfileUnavailable("UMAMOE_API_KEY not set")

    url = _PROFILE_URL.format(account_id=account_id)
    headers = {
        "Accept-Encoding": "gzip, deflate",
        "X-API-Key": UMAMOE_API_KEY,
    }

    try:
        await umamoe_limiter.acquire(PRIORITY_INTERACTIVE)
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
                        raise ProfileUnavailable(f"HTTP {response.status}")
                    return await response.json()
    except ProfileUnavailable:
        raise
    except aiohttp.ClientError as e:
        logger.warning(f"Network error fetching uma.moe profile {account_id}: {e}")
        raise ProfileUnavailable(str(e)) from e
    except Exception as e:
        logger.warning(f"Failed to fetch uma.moe profile {account_id}: {e}")
        raise ProfileUnavailable(str(e)) from e


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

    try:
        return await _request_profile(account_id)
    except ProfileUnavailable:
        return None


async def lookup_trainer(account_id: str) -> Optional[TrainerIdentity]:
    """Who owns a trainer ID, for checking an ID someone typed.

    Unlike ``fetch_trainer_profile`` this keeps the two kinds of "no" apart:
    ``None`` means uma.moe answered and knows no such trainer, while
    ``ProfileUnavailable`` means it could not be asked. A caller deciding whether
    to accept a typed ID must not tell someone their ID is wrong because uma.moe
    happened to be down.
    """
    profile = await _request_profile(account_id)
    if profile is None:
        return None

    identity = TrainerIdentity.from_profile(account_id, profile)
    if identity is None:
        raise ProfileUnavailable("profile has no trainer name")
    return identity
