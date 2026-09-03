"""Tests for component health announcements.

The feature is one paragraph of logic and a great deal of judgement about when
to stay quiet, so that is what these cover: a status channel that posts on every
blip gets muted, and a muted channel is worth less than no channel at all.

Time is passed in explicitly throughout. Every rule here is about *when*
something is said, and a test that cannot control the clock can only assert that
it was said at all.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.health_monitor import Component, HealthMonitor, _humanise

UTC = timezone.utc
START = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
#: Past the start-up grace period, so a sweep is allowed to say something.
LATER = START + timedelta(minutes=5)


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, embed=None, **kw):
        self.sent.append(embed)


class FakeBot:
    def __init__(self, channel):
        self._c = channel

    def get_channel(self, _):
        return self._c


def component(key="thing", **kw):
    base = dict(key=key, label=key.title(), impact="It is broken.",
                recovery="It is fixed.", fail_threshold=3, pass_threshold=2)
    base.update(kw)
    return Component(**base)


def build(*components, channel=None, channel_id=999):
    """A monitor with the database probe stubbed out.

    Tests drive the database component through ``record`` instead: the probe
    exists to turn a live connection into a signal, and standing up a database
    to test the announcement logic would be testing asyncpg."""
    monitor = HealthMonitor(components or (component(),), channel_id=channel_id)

    async def no_probe():
        return None
    monitor._probe_database = no_probe
    monitor.start(FakeBot(channel if channel is not None else FakeChannel()),
                  now=START)
    return monitor


def run(coro):
    return asyncio.run(coro)


def titles(channel):
    return [e.title for e in channel.sent]


class TestThresholds:
    """A component is announced after N consecutive failures, not the first."""

    def test_one_failure_says_nothing(self):
        ch = FakeChannel()
        m = build(component(fail_threshold=3), channel=ch)
        m.record("thing", False)
        run(m.sweep(now=LATER))
        assert ch.sent == []

    def test_announced_once_the_threshold_is_reached(self):
        ch = FakeChannel()
        m = build(component(fail_threshold=3), channel=ch)
        for _ in range(3):
            m.record("thing", False)
        run(m.sweep(now=LATER))
        assert len(ch.sent) == 1
        assert "having problems" in ch.sent[0].title

    def test_never_repeats_while_it_stays_down(self):
        """The difference between a status channel and a spam channel."""
        ch = FakeChannel()
        m = build(component(fail_threshold=1), channel=ch)
        for minute in range(10):
            m.record("thing", False)
            run(m.sweep(now=LATER + timedelta(minutes=minute)))
        assert len(ch.sent) == 1

    def test_flapping_below_the_threshold_is_silent(self):
        """One failed call between successes is a blip, not an outage. Uma.moe
        does this daily and it must never reach the channel."""
        ch = FakeChannel()
        m = build(component(fail_threshold=3), channel=ch)
        for minute in range(20):
            m.record("thing", minute % 2 == 0)
            run(m.sweep(now=LATER + timedelta(minutes=minute)))
        assert ch.sent == []

    def test_recovery_waits_for_the_pass_threshold(self):
        ch = FakeChannel()
        m = build(component(fail_threshold=1, pass_threshold=2), channel=ch)
        m.record("thing", False)
        run(m.sweep(now=LATER))

        m.record("thing", True)
        run(m.sweep(now=LATER + timedelta(minutes=1)))
        assert len(ch.sent) == 1, "recovered on one success, before it was proven"

        m.record("thing", True)
        run(m.sweep(now=LATER + timedelta(minutes=2)))
        assert titles(ch)[1].startswith("🟢")

    def test_a_recovery_reports_how_long_it_was_down(self):
        ch = FakeChannel()
        m = build(component(fail_threshold=1, pass_threshold=1), channel=ch)
        m.record("thing", False)
        run(m.sweep(now=LATER))
        m.record("thing", True)
        run(m.sweep(now=LATER + timedelta(minutes=14)))

        field = ch.sent[1].fields[0]
        assert field.name == "Was down for"
        assert field.value == "14 minutes"

    def test_the_pair_can_run_twice(self):
        """Down, up, down again — the second outage is announced too."""
        ch = FakeChannel()
        m = build(component(fail_threshold=1, pass_threshold=1), channel=ch)
        for i, ok in enumerate([False, True, False, True]):
            m.record("thing", ok)
            run(m.sweep(now=LATER + timedelta(minutes=i)))
        assert [t[0] for t in titles(ch)] == ["🔴", "🟢", "🔴", "🟢"]


class TestStartupGrace:
    """A deploy must not look like an outage."""

    def test_nothing_is_announced_in_the_first_two_minutes(self):
        ch = FakeChannel()
        m = build(component(fail_threshold=1), channel=ch)
        m.record("thing", False)
        run(m.sweep(now=START + timedelta(seconds=90)))
        assert ch.sent == []

    def test_a_stale_component_is_not_down_the_moment_it_boots(self):
        """``start`` seeds a success. Without it, "never reported" reads as
        "stopped reporting" and every restart posts an outage."""
        ch = FakeChannel()
        m = build(component(stale_after=timedelta(minutes=5)), channel=ch)
        run(m.sweep(now=START + timedelta(minutes=4)))
        assert ch.sent == []


class TestStaleness:
    """The only way to notice a task loop that stopped: a dead loop reports
    nothing, which otherwise looks exactly like a healthy idle one."""

    def test_a_missing_heartbeat_is_an_outage(self):
        ch = FakeChannel()
        m = build(component(stale_after=timedelta(minutes=5)), channel=ch)
        run(m.sweep(now=START + timedelta(minutes=20)))
        assert len(ch.sent) == 1

    def test_a_beating_component_stays_up(self):
        ch = FakeChannel()
        m = build(component(stale_after=timedelta(minutes=5)), channel=ch)
        for minute in range(20):
            when = START + timedelta(minutes=minute)
            m.beat("thing", now=when)
            run(m.sweep(now=when))
        assert ch.sent == []

    def test_it_recovers_when_the_beat_returns(self):
        ch = FakeChannel()
        m = build(component(stale_after=timedelta(minutes=5), pass_threshold=1),
                  channel=ch)
        run(m.sweep(now=START + timedelta(minutes=20)))
        back = START + timedelta(minutes=21)
        m.beat("thing", now=back)
        run(m.sweep(now=back))
        assert titles(ch)[1].startswith("🟢")


class TestDependencies:
    """One outage, one message."""

    def _pair(self, channel):
        return build(
            component("database", fail_threshold=1, pass_threshold=1),
            component("worker", fail_threshold=1, pass_threshold=1,
                      depends_on="database"),
            channel=channel,
        )

    def test_a_dependent_stays_quiet_while_its_dependency_is_down(self):
        ch = FakeChannel()
        m = self._pair(ch)
        m.record("database", False)
        m.record("worker", False)
        run(m.sweep(now=LATER))
        assert len(ch.sent) == 1
        assert "Database" in ch.sent[0].title

    def test_it_does_not_then_announce_a_recovery_it_never_announced(self):
        """The bug this ordering exists to prevent: latching a suppressed
        component leaves it owing a '🟢 is back' for an outage nobody heard."""
        ch = FakeChannel()
        m = self._pair(ch)
        m.record("database", False)
        m.record("worker", False)
        run(m.sweep(now=LATER))

        m.record("database", True)
        m.record("worker", True)
        run(m.sweep(now=LATER + timedelta(minutes=1)))

        assert titles(ch) == ["🔴 Database is having problems",
                              "🟢 Database is back"]

    def test_a_dependent_still_broken_afterwards_gets_its_turn(self):
        """Suppression defers a message, it must not swallow it: if the worker
        is still down once the database is back, that is now its own outage."""
        ch = FakeChannel()
        m = self._pair(ch)
        m.record("database", False)
        m.record("worker", False)
        run(m.sweep(now=LATER))

        m.record("database", True)
        m.record("worker", False)
        run(m.sweep(now=LATER + timedelta(minutes=1)))

        assert titles(ch)[-1] == "🔴 Worker is having problems"

    def test_a_dependent_alone_is_announced_normally(self):
        ch = FakeChannel()
        m = self._pair(ch)
        m.record("worker", False)
        run(m.sweep(now=LATER))
        assert titles(ch) == ["🔴 Worker is having problems"]


class TestApiCallTranslation:
    """Not every failed request is a provider outage."""

    def _monitor(self):
        return build(component("umamoe", fail_threshold=1))

    def test_a_success_is_a_success(self):
        m = self._monitor()
        m.note_api_call("uma.moe", True, 200)
        assert m.snapshot()["components"]["umamoe"]["status"] == "ok"

    def test_a_transport_failure_counts(self):
        """No status code at all — DNS, timeout, connection refused."""
        m = self._monitor()
        m.note_api_call("uma.moe", False, None)
        assert m.snapshot()["components"]["umamoe"]["status"] == "down"

    def test_a_server_error_counts(self):
        m = self._monitor()
        m.note_api_call("uma.moe", False, 503)
        assert m.snapshot()["components"]["umamoe"]["status"] == "down"

    def test_rate_limiting_counts(self):
        m = self._monitor()
        m.note_api_call("uma.moe", False, 429)
        assert m.snapshot()["components"]["umamoe"]["status"] == "down"

    def test_a_404_does_not(self):
        """One club with a stale circle id must not read as uma.moe being down —
        it is this request that is wrong, not the provider."""
        m = self._monitor()
        for _ in range(10):
            m.note_api_call("uma.moe", False, 404)
        assert m.snapshot()["components"]["umamoe"]["status"] == "ok"

    def test_a_404_does_not_clear_a_real_outage_either(self):
        """Ignored means ignored: counting it as a success would let a stream of
        404s cancel a genuine outage."""
        m = self._monitor()
        m.note_api_call("uma.moe", False, None)
        m.note_api_call("uma.moe", False, 404)
        assert m.snapshot()["components"]["umamoe"]["status"] == "down"

    def test_another_provider_is_not_confused_for_it(self):
        m = self._monitor()
        m.note_api_call("gametora", False, None)
        assert m.snapshot()["components"]["umamoe"]["status"] == "ok"


class TestPrivacy:
    """The channel is public to a support server."""

    def test_a_private_component_is_tracked_but_not_posted(self):
        ch = FakeChannel()
        m = build(component("backup", fail_threshold=1, public=False), channel=ch)
        m.record("backup", False)
        run(m.sweep(now=LATER))

        assert ch.sent == []
        assert m.snapshot()["components"]["backup"]["status"] == "down"

    def test_messages_carry_no_diagnostics(self):
        """Whatever an operator needs belongs in the log and /health. Anything
        that reached this embed would be readable by the whole server."""
        ch = FakeChannel()
        m = build(component(fail_threshold=1), channel=ch)
        m.record("thing", False)
        run(m.sweep(now=LATER))

        embed = ch.sent[0]
        text = (embed.description or "") + "".join(
            f.name + f.value for f in embed.fields)
        for leak in ("Traceback", "postgres", "/opt/", "127.0.0.1", "Exception"):
            assert leak not in text


class TestSnapshot:
    """What ``/health`` serves, and what a prober alerts on."""

    def test_healthy_reports_ok(self):
        assert build().snapshot()["status"] == "ok"

    def test_stays_backwards_compatible_with_the_old_constant(self):
        """This route used to return ``{'status': 'ok'}``. Anything already
        watching it keeps working."""
        assert build().snapshot()["status"] == "ok"

    def test_degraded_names_the_component(self):
        m = build(component("umamoe", fail_threshold=1))
        m.record("umamoe", False)
        snap = m.snapshot()
        assert snap["status"] == "degraded"
        assert snap["degraded"] == ["umamoe"]

    def test_reports_truth_rather_than_what_was_announced(self):
        """A prober runs its own thresholds, so it needs to see a component
        failing before the channel has earned a message about it."""
        m = build(component(fail_threshold=99))
        m.record("thing", False)
        assert m.snapshot()["components"]["thing"]["consecutive_failures"] == 1
        assert m.snapshot()["components"]["thing"]["announced"] == "ok"


class TestNeverBreaksTheBot:
    def test_no_channel_configured_is_not_an_error(self):
        m = build(component(fail_threshold=1), channel_id=0)
        m.record("thing", False)
        run(m.sweep(now=LATER))          # must not raise

    def test_an_uncached_channel_is_not_an_error(self):
        m = HealthMonitor((component(fail_threshold=1),), channel_id=999)

        async def no_probe():
            return None
        m._probe_database = no_probe
        m.start(SimpleNamespace(get_channel=lambda _: None), now=START)
        m.record("thing", False)
        run(m.sweep(now=LATER))          # must not raise

    def test_recording_an_unknown_component_is_ignored(self):
        build().record("no-such-thing", False)

    def test_sweeping_before_start_does_nothing(self):
        m = HealthMonitor((component(),), channel_id=999)
        run(m.sweep(now=LATER))


class TestHumanise:
    @pytest.mark.parametrize("delta,expected", [
        (timedelta(seconds=8), "8 seconds"),
        (timedelta(minutes=14), "14 minutes"),
        (timedelta(hours=3), "3.0 hours"),
        (timedelta(days=5), "5.0 days"),
    ])
    def test_reads_as_someone_would_say_it(self, delta, expected):
        assert _humanise(delta) == expected

    def test_never_says_zero(self):
        assert _humanise(timedelta(seconds=0)) == "1 seconds"


class TestProductionConfig:
    """The shipped component list, checked for the properties the design relies
    on rather than for its exact contents."""

    def test_the_loops_depend_on_the_database(self):
        from services.health_monitor import COMPONENTS
        by_key = {c.key: c for c in COMPONENTS}
        assert by_key["scrape_tick"].depends_on == "database"
        assert by_key["live_board_tick"].depends_on == "database"

    def test_the_loops_are_watched_for_silence(self):
        from services.health_monitor import COMPONENTS
        by_key = {c.key: c for c in COMPONENTS}
        assert by_key["scrape_tick"].stale_after is not None
        assert by_key["live_board_tick"].stale_after is not None

    def test_backups_are_not_announced_publicly(self):
        from services.health_monitor import COMPONENTS
        by_key = {c.key: c for c in COMPONENTS}
        assert by_key["backup"].public is False

    def test_every_public_component_explains_the_impact(self):
        from services.health_monitor import COMPONENTS
        for c in COMPONENTS:
            assert c.impact.endswith("."), c.key
            assert c.recovery.endswith("."), c.key


class TestHeartbeat:
    """The one signal that survives the process dying.

    Everything else in this module is the bot's opinion of itself, and a crashed
    bot has no opinions. Liveness is proved by a ping that *stops*, watched by
    something that isn't on this host.
    """

    def _wired(self, url="https://hc.example/abc", **kw):
        monitor = build(component(), **kw)
        monitor._heartbeat_url = url
        monitor.pinged = []

        async def send(target):
            monitor.pinged.append(target)
        monitor._send_ping = send
        return monitor

    def test_pings_the_configured_url(self):
        m = self._wired()
        run(m.ping())
        assert m.pinged == ["https://hc.example/abc"]

    def test_does_nothing_when_unconfigured(self):
        m = self._wired(url="")
        run(m.ping())
        assert m.pinged == []

    def test_still_pings_while_a_component_is_degraded(self):
        """Liveness, not health. Merging the two would page 'the bot is down'
        for a failed nightly backup, and would make a genuinely dead bot
        indistinguishable from one whose uma.moe calls are timing out."""
        m = self._wired()
        m.record("thing", False)
        m.record("thing", False)
        m.record("thing", False)
        assert m.snapshot()["status"] == "degraded"

        run(m.ping())
        assert len(m.pinged) == 1

    def test_a_failed_ping_never_raises(self):
        """It runs inside the task loop. A monitoring call that takes down the
        loop it monitors from would be the most embarrassing possible outage."""
        m = self._wired()

        async def boom(_):
            raise OSError("network unreachable")
        m._send_ping = boom

        run(m.ping())                      # must not raise
        assert m._ping_failures == 1

    def test_repeated_failures_are_counted_not_reset(self):
        m = self._wired()

        async def boom(_):
            raise OSError("network unreachable")
        m._send_ping = boom

        for _ in range(5):
            run(m.ping())
        assert m._ping_failures == 5

    def test_the_counter_clears_once_it_gets_through(self):
        m = self._wired()

        async def boom(_):
            raise OSError("down")
        m._send_ping = boom
        run(m.ping())
        assert m._ping_failures == 1

        async def fine(target):
            m.pinged.append(target)
        m._send_ping = fine
        run(m.ping())
        assert m._ping_failures == 0
