"""Leader-character portrait cache.

Resolves a trainer's leader id (uma.moe ``leader_chara_dress_id``, a 6-digit
``CCCCDD`` value) to a local PNG, downloading it once from gametora and caching
it on disk. Subsequent renders reuse the cached file, so we hit the network at
most once per leader id.

The leader id is the *equipped dress*, not necessarily a playable card costume,
and gametora only hosts stand art for actual card costumes. So we try the exact
id first, then fall back to the character's base cards (``CCCC01`` / ``CCCC02``)
so a trainer wearing a non-card outfit still gets that character's default art.

Images are a runtime cache (gitignored), not bundled assets.
"""
import logging
from pathlib import Path
from typing import Optional

import aiohttp

from utils.api_metrics import track_api_call

logger = logging.getLogger(__name__)

_PORTRAIT_DIR = Path(__file__).parent.parent / "tally" / "assets" / "portraits"
_URL = "https://gametora.com/images/umamusume/characters/chara_stand_{chara}_{card}.png"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# Card ids we've already failed to fetch this process — avoids re-hitting gametora
# every render for characters with no portrait available.
_failed: set[str] = set()


def _candidate_ids(card: str, chara: str) -> list[str]:
    """Exact id first, then the character's base card costumes as fallbacks."""
    ordered = [card, f"{chara}01", f"{chara}02"]
    seen: list[str] = []
    for c in ordered:
        if c not in seen:
            seen.append(c)
    return seen


async def _try_download(session: aiohttp.ClientSession, candidate: str):
    """Fetch one gametora stand image. Returns (bytes|None, status_code|None)."""
    url = _URL.format(chara=candidate[:4], card=candidate)
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        is_image = "image" in resp.headers.get("Content-Type", "")
        if resp.status != 200 or not is_image:
            logger.debug(f"gametora has no stand for id {candidate} (HTTP {resp.status})")
            return None, resp.status
        data = await resp.read()
    if len(data) < 200:  # guard against empty/placeholder responses
        return None, resp.status
    return data, resp.status


async def get_portrait_path(card_id) -> Optional[Path]:
    """Return a local PNG path for the leader id, downloading if needed.

    Tries the exact equipped-dress id, then the character's base cards. Only one
    api_usage row is recorded per resolution — marked failed solely when no
    candidate yields art — so the expected first-probe miss isn't logged as an
    error. The image is cached under the originally-requested id, so even a
    fallback costume resolves from disk next time.
    """
    if not card_id:
        return None

    card = str(card_id).strip()
    if not card.isdigit() or len(card) < 5:
        return None

    path = _PORTRAIT_DIR / f"{card}.png"
    if path.exists():
        return path
    if card in _failed:
        return None

    chara = card[:4]
    headers = {"User-Agent": _UA, "Referer": "https://gametora.com/"}

    try:
        _PORTRAIT_DIR.mkdir(parents=True, exist_ok=True)
        data: Optional[bytes] = None
        used: Optional[str] = None
        last_status: Optional[int] = None

        async with aiohttp.ClientSession(headers=headers) as session:
            async with track_api_call("gametora", "portrait", context=card) as m:
                for candidate in _candidate_ids(card, chara):
                    d, status = await _try_download(session, candidate)
                    if status is not None:
                        last_status = status
                    if d is not None:
                        data, used = d, candidate
                        break
                m["status_code"] = 200 if data is not None else last_status
                m["ok"] = data is not None

        if data is None:
            logger.info(f"No gametora portrait available for leader {card} (tried base costumes)")
            _failed.add(card)
            return None

        if used != card:
            logger.info(f"Using fallback costume {used} for leader {card}")

        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        logger.info(f"Cached leader portrait for {card} via {used} ({len(data)} bytes)")
        return path
    except Exception as e:
        logger.warning(f"Failed to fetch portrait for leader {card}: {e}")
        _failed.add(card)
        return None
