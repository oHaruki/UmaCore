"""Tests for the priority-aware uma.moe rate limiter.

The scenario that motivated priorities: at 200 clubs the daily clump takes ~2
minutes to drain, and under plain FIFO an admin running /force_check during that
window waited behind all 200 background scrapes.
"""
import asyncio
import time

import pytest

from utils.rate_limiter import (
    PRIORITY_BACKGROUND, PRIORITY_INTERACTIVE, RateLimiter,
)


def run(coro):
    return asyncio.run(coro)


class TestRateIsRespected:
    def test_burst_then_throttle(self):
        """Capacity is available immediately; beyond it, calls are paced."""
        async def go():
            lim = RateLimiter(rate_per_min=600, burst=5)   # 10/sec
            start = time.monotonic()
            for _ in range(5):
                await lim.acquire()
            burst_done = time.monotonic() - start
            await lim.acquire()                            # must wait ~0.1s
            return burst_done, time.monotonic() - start

        burst, total = run(go())
        assert burst < 0.05, "burst capacity was not immediate"
        assert total >= 0.08, "6th call was not throttled"

    def test_never_exceeds_the_configured_rate(self):
        async def go():
            lim = RateLimiter(rate_per_min=600, burst=1)   # 10/sec
            start = time.monotonic()
            await asyncio.gather(*(lim.acquire() for _ in range(10)))
            return time.monotonic() - start

        elapsed = run(go())
        # 10 calls at 10/sec with burst 1 => ~0.9s of pacing.
        assert elapsed >= 0.8, f"10 calls took only {elapsed:.2f}s — rate exceeded"


class TestPriority:
    def test_interactive_jumps_a_background_queue(self):
        """The actual bug: a slash command behind 200 scheduled scrapes."""
        async def go():
            lim = RateLimiter(rate_per_min=600, burst=1)
            order = []

            async def bg(i):
                await lim.acquire(PRIORITY_BACKGROUND)
                order.append(f"bg{i}")

            async def interactive():
                await lim.acquire(PRIORITY_INTERACTIVE)
                order.append("USER")

            tasks = [asyncio.create_task(bg(i)) for i in range(20)]
            await asyncio.sleep(0.05)          # let them all queue up
            tasks.append(asyncio.create_task(interactive()))
            await asyncio.gather(*tasks)
            return order

        order = run(go())
        assert "USER" in order
        # Without priority the user would land at position ~20.
        assert order.index("USER") <= 2, f"user served at position {order.index('USER')}"

    def test_fifo_preserved_within_a_priority(self):
        """The scrape scheduler submits clubs in rank order and depends on this."""
        async def go():
            lim = RateLimiter(rate_per_min=6000, burst=1)
            order = []

            async def worker(i):
                await lim.acquire(PRIORITY_BACKGROUND)
                order.append(i)

            tasks = []
            for i in range(15):
                tasks.append(asyncio.create_task(worker(i)))
                await asyncio.sleep(0)          # deterministic submission order
            await asyncio.gather(*tasks)
            return order

        assert run(go()) == list(range(15))

    def test_multiple_interactive_calls_stay_in_order(self):
        async def go():
            lim = RateLimiter(rate_per_min=6000, burst=1)
            order = []

            async def bg():
                await lim.acquire(PRIORITY_BACKGROUND)
                order.append("bg")

            async def user(i):
                await lim.acquire(PRIORITY_INTERACTIVE)
                order.append(f"u{i}")

            tasks = [asyncio.create_task(bg()) for _ in range(10)]
            await asyncio.sleep(0.02)
            for i in range(3):
                tasks.append(asyncio.create_task(user(i)))
                await asyncio.sleep(0)
            await asyncio.gather(*tasks)
            return order

        order = run(go())
        users = [x for x in order if x.startswith("u")]
        assert users == ["u0", "u1", "u2"], f"interactive order broke: {users}"

    def test_background_still_completes(self):
        """Priority must not starve background work."""
        async def go():
            lim = RateLimiter(rate_per_min=6000, burst=2)
            done = []

            async def bg(i):
                await lim.acquire(PRIORITY_BACKGROUND)
                done.append(i)

            async def user():
                for _ in range(5):
                    await lim.acquire(PRIORITY_INTERACTIVE)

            await asyncio.gather(
                *(bg(i) for i in range(30)), user()
            )
            return done

        assert len(run(go())) == 30


class TestCancellation:
    def test_cancelled_waiter_does_not_block_others(self):
        async def go():
            lim = RateLimiter(rate_per_min=600, burst=1)
            served = []

            async def worker(i):
                await lim.acquire()
                served.append(i)

            first = asyncio.create_task(worker(0))
            await asyncio.sleep(0.01)
            doomed = asyncio.create_task(worker(1))
            await asyncio.sleep(0.01)
            rest = [asyncio.create_task(worker(i)) for i in range(2, 5)]
            await asyncio.sleep(0.01)

            doomed.cancel()
            await asyncio.gather(first, *rest)
            return served

        served = run(go())
        assert sorted(served) == [0, 2, 3, 4], f"cancellation broke the queue: {served}"

    def test_queue_drains_empty_after_use(self):
        async def go():
            lim = RateLimiter(rate_per_min=6000, burst=1)
            await asyncio.gather(*(lim.acquire() for _ in range(10)))
            await asyncio.sleep(0.05)
            return lim.queue_depth, lim._dispatcher

        depth, dispatcher = run(go())
        assert depth == 0
        assert dispatcher is None, "dispatcher task was left running"

    def test_recovers_after_the_queue_empties(self):
        """A second burst must work after the dispatcher has exited."""
        async def go():
            lim = RateLimiter(rate_per_min=6000, burst=1)
            await asyncio.gather(*(lim.acquire() for _ in range(5)))
            await asyncio.sleep(0.05)
            await asyncio.gather(*(lim.acquire() for _ in range(5)))
            return True

        assert run(go()) is True


class TestRealisticClump:
    def test_two_hundred_scrapes_do_not_delay_a_user(self):
        """200 clubs draining while an admin runs a command."""
        async def go():
            # 100/min is the production setting.
            lim = RateLimiter(rate_per_min=100 * 600, burst=10)   # time-compressed
            waits = {}

            async def bg(i):
                await lim.acquire(PRIORITY_BACKGROUND)

            async def user():
                t = time.monotonic()
                await lim.acquire(PRIORITY_INTERACTIVE)
                waits["user"] = time.monotonic() - t

            tasks = [asyncio.create_task(bg(i)) for i in range(200)]
            await asyncio.sleep(0.02)
            tasks.append(asyncio.create_task(user()))
            await asyncio.gather(*tasks)
            return waits["user"]

        wait = run(go())
        # At 1000/sec the whole 200-clump takes ~0.2s; the user must not wait for it.
        assert wait < 0.05, f"user waited {wait:.3f}s behind the clump"
