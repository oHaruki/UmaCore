"""
Process-wide async rate limiter for outbound Uma.moe API calls.

A single shared token bucket gates EVERY uma.moe request — scheduled scrapes,
live board refreshes, manual /force_check, chart commands and the web API server
— so the bot never exceeds the API's per-minute limit no matter how many code
paths fire at once.

Waiters are served by **priority, then arrival order**. That matters at scale: a
daily clump of 200 club scrapes takes ~2 minutes to drain at 100/min, and under
plain FIFO an admin running /force_check during that window would wait behind all
200. Interactive work now jumps the queue and is served on the next token.

Within a priority level, ordering is strictly FIFO — the scrape scheduler submits
clubs in rank order and relies on that being preserved.
"""
import asyncio
import heapq
import itertools
import logging
import time

from config.settings import UMAMOE_RATE_PER_MIN, UMAMOE_RATE_BURST

logger = logging.getLogger(__name__)

# Lower number wins. Interactive means a human or a web request is waiting on it.
PRIORITY_INTERACTIVE = 0
PRIORITY_BACKGROUND = 10


class RateLimiter:
    """Async token bucket with priority-aware, FIFO-within-priority fairness."""

    def __init__(self, rate_per_min: float, burst: int, name: str = "ratelimiter"):
        self.rate_per_sec = max(rate_per_min, 1) / 60.0
        self.capacity = max(1, burst)
        self.name = name
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()
        self._waiters = []                 # heap of (priority, seq, future)
        self._seq = itertools.count()
        self._dispatcher = None

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate_per_sec)
        self._updated = now

    async def acquire(self, priority: int = PRIORITY_BACKGROUND) -> None:
        """Block until a token is available, then consume one.

        Args:
            priority: ``PRIORITY_INTERACTIVE`` for anything a user is waiting on,
                ``PRIORITY_BACKGROUND`` (the default) for scheduled work.
        """
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        async with self._lock:
            # Fast path: a token is free and nobody is ahead of us.
            self._refill()
            if not self._waiters and self._tokens >= 1:
                self._tokens -= 1
                return
            heapq.heappush(self._waiters, (priority, next(self._seq), fut))
            self._ensure_dispatcher(loop)

        try:
            await fut
        except asyncio.CancelledError:
            # If the token was already granted, hand it back rather than losing it.
            if fut.done() and not fut.cancelled():
                async with self._lock:
                    self._tokens = min(self.capacity, self._tokens + 1)
            raise

    def _ensure_dispatcher(self, loop) -> None:
        """Start the drain loop if it isn't running. Caller must hold the lock."""
        if self._dispatcher is None:
            self._dispatcher = loop.create_task(self._dispatch())

    async def _dispatch(self) -> None:
        """Hand out tokens to queued waiters, highest priority first."""
        try:
            while True:
                async with self._lock:
                    self._refill()

                    # Discard waiters that were cancelled while queued.
                    while self._waiters and self._waiters[0][2].done():
                        heapq.heappop(self._waiters)

                    if not self._waiters:
                        self._dispatcher = None
                        return

                    if self._tokens >= 1:
                        _, _, fut = heapq.heappop(self._waiters)
                        if not fut.done():
                            self._tokens -= 1
                            fut.set_result(None)
                        continue          # try to serve the next one immediately

                    wait = (1 - self._tokens) / self.rate_per_sec
                    if wait > 5:
                        logger.warning(
                            f"⏳ {self.name}: budget exhausted, next call in {wait:.1f}s "
                            f"({len(self._waiters)} queued)"
                        )

                await asyncio.sleep(min(wait, 5))
        except asyncio.CancelledError:
            raise
        finally:
            # Never leave the flag set, or nothing would ever restart the drain.
            self._dispatcher = None

    @property
    def queue_depth(self) -> int:
        """How many callers are currently waiting (diagnostics)."""
        return len(self._waiters)


# Shared singleton used by every uma.moe caller. Configured from settings so the
# limit can be raised with a single env var once the API owner bumps the cap.
umamoe_limiter = RateLimiter(UMAMOE_RATE_PER_MIN, UMAMOE_RATE_BURST, name="uma.moe")
