"""Leader-character portrait cache.

Resolves a trainer's leader card id (uma.moe ``leader_chara_dress_id``, a 6-digit
``CCCCDD`` card id) to a local PNG, downloading it once from gametora and caching
it on disk. Subsequent renders — for any trainer with that character — reuse the
cached file, so we hit the network at most once per character.

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


async def get_portrait_path(card_id) -> Optional[Path]:
    """Return a local PNG path for the leader card id, downloading if needed."""
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
    url = _URL.format(chara=chara, card=card)
    headers = {"User-Agent": _UA, "Referer": "https://gametora.com/"}

    try:
        _PORTRAIT_DIR.mkdir(parents=True, exist_ok=True)
        async with aiohttp.ClientSession(headers=headers) as session:
            async with track_api_call("gametora", "portrait", context=card) as m:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    m["status_code"] = resp.status
                    is_image = "image" in resp.headers.get("Content-Type", "")
                    m["ok"] = resp.status == 200 and is_image
                    if resp.status != 200 or not is_image:
                        logger.info(f"No gametora portrait for card {card} (HTTP {resp.status})")
                        _failed.add(card)
                        return None
                    data = await resp.read()

        if len(data) < 200:  # guard against empty/placeholder responses
            _failed.add(card)
            return None

        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        logger.info(f"Cached leader portrait for card {card} ({len(data)} bytes)")
        return path
    except Exception as e:
        logger.warning(f"Failed to fetch portrait for card {card}: {e}")
        _failed.add(card)
        return None
