"""
Process-wide async rate limiter for outbound Uma.moe API calls.

A single shared token bucket gates EVERY uma.moe request — scheduled daily
scrapes, the second call made on Day 1, manual /force_check, chart commands and
the web API server — so the bot never exceeds the API's per-minute circle-data
limit regardless of how many code paths fire at once.

The bucket is FIFO: callers acquire under a lock, so tokens are handed out in
arrival order and paced one refill-interval apart. That ordering is what lets the
scheduler fetch top-ranked clubs first (it submits them in rank order).
"""
import asyncio
import time
import logging

from config.settings import UMAMOE_RATE_PER_MIN, UMAMOE_RATE_BURST

logger = logging.getLogger(__name__)


class RateLimiter:
    """Async token bucket with FIFO fairness."""

    def __init__(self, rate_per_min: float, burst: int, name: str = "ratelimiter"):
        self.rate_per_sec = max(rate_per_min, 1) / 60.0
        self.capacity = max(1, burst)
        self.name = name
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate_per_sec)
        self._updated = now

    async def acquire(self) -> None:
        """Block until a token is available, then consume one.

        The lock is held across the wait so callers are served strictly in the
        order they arrived (FIFO) and never thunder-herd the bucket.
        """
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                deficit = 1 - self._tokens
                wait = deficit / self.rate_per_sec
                if wait > 5:
                    logger.warning(f"⏳ {self.name}: budget exhausted, throttling next call by {wait:.1f}s")
                await asyncio.sleep(wait)


# Shared singleton used by the Uma.moe scraper. Configured from settings so the
# limit can be raised with a single env var once the API owner bumps the cap.
umamoe_limiter = RateLimiter(UMAMOE_RATE_PER_MIN, UMAMOE_RATE_BURST, name="uma.moe")
