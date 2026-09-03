"""Lightweight recorder for outbound third-party API calls.

Every call to uma.moe (circle/profile data) and gametora (portraits) writes one
row to ``api_usage``, which the owner-only web analytics page reads. Recording is
strictly best-effort: it must never raise into, slow down, or break the actual
API call it's measuring.
"""
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger(__name__)


async def record_api_call(
    provider: str,
    endpoint: str,
    *,
    ok: bool,
    status_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
    context: Optional[str] = None,
) -> None:
    """Insert one api_usage row. Swallows all errors (metrics must not break calls)."""
    # Every outbound call already passes through here with the two facts the
    # health monitor needs, so provider health costs one line rather than a hook
    # in each scraper. Imported lazily and guarded separately from the insert
    # below: the monitor must still hear about a failure when the database is
    # the thing that is failing.
    try:
        from services.health_monitor import health
        health.note_api_call(provider, ok, status_code)
    except Exception as e:  # pragma: no cover - monitoring must not break calls
        logger.debug(f"Failed to record health signal ({provider}): {e}")

    try:
        from config.database import db
        if not getattr(db, "pool", None):
            return
        await db.execute(
            """
            INSERT INTO api_usage
                (provider, endpoint, status_code, ok, duration_ms, context)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            provider, endpoint, status_code, ok, duration_ms,
            (context[:200] if context else None),
        )
    except Exception as e:  # pragma: no cover - metrics are non-critical
        logger.debug(f"Failed to record api_usage ({provider}/{endpoint}): {e}")


@asynccontextmanager
async def track_api_call(provider: str, endpoint: str, *, context: Optional[str] = None):
    """Time an API call and record it. Yields a mutable dict; set ``status_code``
    and ``ok`` on it inside the block.

    Example:
        async with track_api_call("uma.moe", "circles", context=cid) as m:
            async with session.get(url) as resp:
                m["status_code"] = resp.status
                m["ok"] = resp.status == 200
                ...
    """
    meta = {"status_code": None, "ok": False}
    start = time.monotonic()
    try:
        yield meta
    finally:
        await record_api_call(
            provider,
            endpoint,
            ok=bool(meta.get("ok")),
            status_code=meta.get("status_code"),
            duration_ms=int((time.monotonic() - start) * 1000),
            context=context,
        )
