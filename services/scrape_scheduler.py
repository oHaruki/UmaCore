"""
Rank-ordered scrape dispatcher.

Replaces firing every due club at once. Clubs are submitted with a target
dispatch time (UTC); a single drain loop releases them in time order — which,
because default-time clubs are stamped with `target + rank/rollout` delays, is
also rank order. The actual API rate ceiling is enforced separately by the
shared token bucket inside the scraper, so this layer only has to worry about
ordering, parallelism and re-queueing stale fetches.
"""
import asyncio
import heapq
import itertools
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class ScrapeScheduler:
    """Time-ordered, bounded-concurrency dispatcher for club scrapes."""

    def __init__(self, worker, concurrency: int, max_retries: int, retry_delay: int):
        # worker: async (club, attempt, is_final) -> "ok" | "stale" | "failed"
        self._worker = worker
        self._heap = []                      # (dispatch_ts, seq, job)
        self._seq = itertools.count()
        self._wakeup = asyncio.Event()
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._max_retries = max(1, max_retries)
        self._retry_delay = retry_delay
        self._task = None
        self._running = False
        # club_ids currently queued or in-flight, so the per-minute tick doesn't
        # enqueue the same club twice while it's pending or being retried.
        self._active = set()

    def start(self) -> None:
        if self._task is None:
            self._running = True
            self._task = asyncio.create_task(self._run())
            logger.info("ScrapeScheduler started")

    def stop(self) -> None:
        self._running = False
        self._wakeup.set()
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("ScrapeScheduler stopped")

    def enqueue(self, club, dispatch_dt: datetime, attempt: int = 1) -> None:
        """Queue a club for its first dispatch. No-op if already pending/in-flight."""
        key = str(club.club_id)
        if key in self._active:
            logger.debug(f"Scheduler: {club.club_name} already active, skip enqueue")
            return
        self._active.add(key)
        self._push(club, dispatch_dt, attempt, key)
        logger.info(f"📥 Queued {club.club_name} (attempt {attempt}) for {dispatch_dt.isoformat()}")

    def _push(self, club, dispatch_dt: datetime, attempt: int, key: str) -> None:
        job = {"club": club, "attempt": attempt, "key": key}
        heapq.heappush(self._heap, (dispatch_dt.timestamp(), next(self._seq), job))
        self._wakeup.set()

    async def _run(self) -> None:
        while self._running:
            try:
                if not self._heap:
                    self._wakeup.clear()
                    await self._wakeup.wait()
                    continue

                ts = self._heap[0][0]
                now = datetime.now(timezone.utc).timestamp()
                if ts > now:
                    # Sleep until the next job is due, or until something earlier arrives.
                    self._wakeup.clear()
                    try:
                        await asyncio.wait_for(self._wakeup.wait(), timeout=ts - now)
                    except asyncio.TimeoutError:
                        pass
                    continue

                _, _, job = heapq.heappop(self._heap)
                await self._sem.acquire()          # bound parallel club processing
                asyncio.create_task(self._process(job))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _process(self, job: dict) -> None:
        club = job["club"]
        attempt = job["attempt"]
        key = job["key"]
        is_final = attempt >= self._max_retries
        try:
            status = await self._worker(club, attempt, is_final)

            if status == "stale" and not is_final:
                delay = self._retry_delay * attempt
                next_dt = datetime.now(timezone.utc) + timedelta(seconds=delay)
                logger.warning(
                    f"♻️ {club.club_name} data still rolling out — re-queueing "
                    f"attempt {attempt + 1}/{self._max_retries} in {delay}s"
                )
                self._push(club, next_dt, attempt + 1, key)  # keep key active across retries
            else:
                self._active.discard(key)
        except Exception as e:
            logger.error(f"Scheduler worker error for {club.club_name}: {e}", exc_info=True)
            self._active.discard(key)
        finally:
            self._sem.release()
