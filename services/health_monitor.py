"""Component health, announced once, in the words of the people it affects.

Two consumers, one state:

* the **status channel** — a short message when something breaks and another
  when it comes back, written for club admins who only want to know whether
  their reports are late and whether they need to do anything;
* **``/health``** — the same state as JSON, for an external prober.

They read the same object on purpose. A status page and a chat announcement
that disagree are worse than either alone, and they disagree the moment the two
are computed from separate code.

**What this cannot do.** The bot cannot announce its own death: a crashed
process posts nothing, and silence is indistinguishable from everything being
fine. That gap belongs to a prober outside this host, which is what ``/health``
exists for. Nothing here should ever grow a "the bot is offline" message,
because the only process that could send it is the one that isn't running.

Two rules keep the channel readable, and both were designed in rather than
learned:

*Thresholds.* Uma.moe blips. Announcing the first failed call would post several
times an hour, and a channel that cries wolf is one people mute — after which it
may as well not exist. A component is announced only after
``fail_threshold`` consecutive failures and recovered after ``pass_threshold``
consecutive successes, and only ever on a *transition*.

*Dependencies.* When Postgres goes, everything that reads it fails within the
minute. Four messages describing one outage is noise, so a component whose
``depends_on`` is already down stays quiet and lets the dependency speak.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import discord

from config.settings import (
    COLOR_BOMB, COLOR_ON_TRACK, STATUS_CHANNEL_ID,
    HEARTBEAT_URL, HEARTBEAT_TIMEOUT_SEC,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Component:
    """One thing that can be up or down, and what it means to a person if it is.

    ``impact`` and ``recovery`` are the whole message. They say what someone
    notices and what they should do, not which subsystem returned what — the
    status channel is public to a support server, so an error string, a club
    name or a host path in here is both useless to the reader and a small leak.
    Anything genuinely diagnostic goes to the log and to ``/health``.
    """
    key: str
    label: str
    impact: str
    recovery: str
    fail_threshold: int = 3
    pass_threshold: int = 2
    #: Announce as down when nothing has reported success within this long.
    #: The only way to notice a ``@tasks.loop`` that died: a dead loop does not
    #: report failures, it reports nothing at all, which otherwise reads exactly
    #: like a healthy idle one.
    stale_after: Optional[timedelta] = None
    #: Stay quiet while this component is down — it is already being announced.
    depends_on: Optional[str] = None
    #: False keeps it out of the status channel while still tracking it for
    #: ``/health``. For operator concerns a club admin can neither act on nor
    #: benefit from knowing.
    public: bool = True


@dataclass
class _State:
    ok_streak: int = 0
    bad_streak: int = 0
    last_ok: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    #: What has been *said*, which is not the same as what is true: a component
    #: below its threshold is failing and correctly unannounced.
    announced_down: bool = False
    down_since: Optional[datetime] = None


COMPONENTS: Tuple[Component, ...] = (
    Component(
        key="database",
        label="Database",
        impact=("Quota data can't be read or written, so commands and reports "
                "will fail until this clears."),
        recovery="Commands and reports are working normally again.",
        fail_threshold=3,          # sweeps once a minute, so ~3 minutes
        pass_threshold=2,
    ),
    Component(
        key="umamoe",
        label="Uma.moe",
        impact=("Fan figures can't be fetched, so daily reports, live boards and "
                "tracking channel names are paused. Nothing is lost — clubs catch "
                "up on their next run once it's back."),
        recovery=("Fan figures are coming through again. Anything missed re-runs "
                  "on its next scheduled slot."),
        # Counted in calls, not minutes. Five consecutive failures is seconds
        # during a busy tick and stays quiet through the single timeouts that
        # happen daily.
        fail_threshold=5,
        pass_threshold=2,
    ),
    Component(
        key="scrape_tick",
        label="Report scheduler",
        impact=("Daily reports aren't being scheduled. Clubs due in this window "
                "will be late."),
        recovery="Daily reports are being scheduled again.",
        fail_threshold=1,
        pass_threshold=1,
        stale_after=timedelta(minutes=5),      # the loop runs every minute
        depends_on="database",
    ),
    Component(
        key="live_board_tick",
        label="Live boards",
        impact="Live boards and tracking channel names have stopped updating.",
        recovery="Live boards and channel names are updating again.",
        fail_threshold=1,
        pass_threshold=1,
        stale_after=timedelta(minutes=5),
        depends_on="database",
    ),
    Component(
        key="backup",
        label="Database backup",
        impact="The nightly database backup did not complete.",
        recovery="The nightly database backup completed.",
        fail_threshold=1,
        pass_threshold=1,
        stale_after=timedelta(hours=26),       # daily job, one run of slack
        # Nobody in a support server can act on this and it says something about
        # the host that they have no reason to know. The log and /health carry it.
        public=False,
    ),
)


def _humanise(delta: timedelta) -> str:
    """A duration as someone would say it out loud."""
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return f"{max(seconds, 1)} seconds"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} minutes"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f} hours"
    return f"{hours / 24:.1f} days"


class HealthMonitor:
    """Tracks each component and announces only when the verdict changes."""

    def __init__(self, components: Tuple[Component, ...] = COMPONENTS,
                 channel_id: int = STATUS_CHANNEL_ID,
                 heartbeat_url: str = HEARTBEAT_URL):
        self._components: Dict[str, Component] = {c.key: c for c in components}
        self._state: Dict[str, _State] = {c.key: _State() for c in components}
        self._channel_id = channel_id
        self._heartbeat_url = heartbeat_url
        self._ping_failures = 0
        self._bot = None
        self._started_at: Optional[datetime] = None

    # ----------------------------------------------------------------- wiring

    def start(self, bot, *, now: Optional[datetime] = None) -> None:
        """Begin monitoring. Called once the bot is ready.

        Every component is seeded as having just succeeded. Without that, each
        one with a ``stale_after`` would be announced down on the first sweep
        after a restart, because "never reported" and "stopped reporting" are the
        same absence — and a deploy would post an outage and a recovery every
        time. The grace period in :meth:`sweep` covers the same window from the
        other side.
        """
        self._bot = bot
        self._started_at = now or datetime.now(timezone.utc)
        for state in self._state.values():
            state.last_ok = self._started_at
            state.ok_streak = 1
        if not self._channel_id:
            logger.info("Status channel not configured (STATUS_CHANNEL_ID unset) "
                        "— health is tracked for /health but not announced")
        if not self._heartbeat_url:
            logger.info("Heartbeat not configured (HEARTBEAT_URL unset) — nothing "
                        "outside this host can tell whether the bot is running")

    # ---------------------------------------------------------------- signals

    def record(self, key: str, ok: bool, *, now: Optional[datetime] = None) -> None:
        """Note one success or failure. Cheap, synchronous, and never raises.

        Deliberately does no posting. This is called from inside the request path
        (:func:`utils.api_metrics.track_api_call` wraps every outbound call), so
        it must not await, must not touch Discord, and must not be able to break
        the thing it is measuring. Announcing is :meth:`sweep`'s job.
        """
        state = self._state.get(key)
        if state is None:
            return
        stamp = now or datetime.now(timezone.utc)
        if ok:
            state.ok_streak += 1
            state.bad_streak = 0
            state.last_ok = stamp
        else:
            state.bad_streak += 1
            state.ok_streak = 0
            state.last_failure = stamp

    def beat(self, key: str, *, now: Optional[datetime] = None) -> None:
        """Report that a periodic job ran. Absence of these is what's detected."""
        self.record(key, True, now=now)

    def note_api_call(self, provider: str, ok: bool,
                      status_code: Optional[int]) -> None:
        """Translate one outbound API call into a provider verdict.

        Not every failed call means the provider is down, and conflating the two
        would announce an outage every time one club has a stale circle id. A
        4xx is *this request* being wrong — a bad circle, a revoked key — and
        says nothing about uma.moe's health, so it counts as neither success nor
        failure. Only a transport failure (no status code at all: DNS, timeout,
        refused), a 5xx, or a 429 is evidence about the provider itself.
        """
        if provider != "uma.moe":
            return
        if ok:
            self.record("umamoe", True)
            return
        if status_code is None or status_code >= 500 or status_code == 429:
            self.record("umamoe", False)

    # ---------------------------------------------------------------- verdict

    def _is_down(self, component: Component, state: _State,
                 now: datetime) -> bool:
        if state.bad_streak >= component.fail_threshold:
            return True
        if component.stale_after and state.last_ok is not None:
            return now - state.last_ok > component.stale_after
        return False

    def snapshot(self, *, now: Optional[datetime] = None) -> dict:
        """Current health as plain data, for ``/health`` and for tests.

        Reports what is *true*, not what has been announced — a prober should see
        a component failing before it has crossed the threshold that would earn a
        message, since its own conditions decide what it does about that.
        """
        stamp = now or datetime.now(timezone.utc)
        components = {}
        for key, component in self._components.items():
            state = self._state[key]
            down = self._is_down(component, state, stamp)
            components[key] = {
                "label": component.label,
                "status": "down" if down else "ok",
                "announced": "down" if state.announced_down else "ok",
                "consecutive_failures": state.bad_streak,
                "last_ok": state.last_ok.isoformat() if state.last_ok else None,
            }
        degraded = [k for k, c in components.items() if c["status"] == "down"]
        return {
            "status": "degraded" if degraded else "ok",
            "degraded": degraded,
            "components": components,
            "started_at": self._started_at.isoformat() if self._started_at else None,
        }

    # -------------------------------------------------------------- heartbeat

    async def ping(self) -> None:
        """Tell an outside service the process is still alive.

        The half of monitoring that has to leave the machine. Everything else
        here is the bot's opinion of itself, and a crashed process has no
        opinions — so liveness is proved by a signal that *stops*, watched by
        something that isn't running on this host. No inbound exposure, no
        reverse-proxy rule, and nothing for a firewall or a CDN in front of the
        domain to interfere with.

        Deliberately liveness only, not health. It is sent whether or not a
        component is degraded, because the two answer different questions and
        merging them makes both worse: a failed nightly backup would page
        "the bot is down", and a bot that is genuinely down would be
        indistinguishable from one whose uma.moe calls are timing out. Component
        health is the status channel's job, and it is already better at it.

        Liveness only also means any heartbeat service works — the contract is
        just "a request arrived" — rather than tying the deployment to one
        provider's success/fail URL scheme.

        Never raises. A monitoring call that takes down the loop it monitors
        from would be the most embarrassing possible outage.
        """
        if not self._heartbeat_url:
            return
        try:
            await self._send_ping(self._heartbeat_url)
            self._ping_failures = 0
        except Exception as e:
            self._ping_failures += 1
            # Every minute of an internet outage would otherwise be a warning.
            # The first one says it, then one an hour keeps it in the log
            # without burying everything else.
            if self._ping_failures == 1 or self._ping_failures % 60 == 0:
                logger.warning(
                    f"health: heartbeat ping failed ({self._ping_failures} in a "
                    f"row): {e}. The monitor will report this bot as down."
                )

    async def _send_ping(self, url: str) -> None:
        """The HTTP call itself, kept separate so tests can stub the transport."""
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=HEARTBEAT_TIMEOUT_SEC)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url) as response:
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")

    # -------------------------------------------------------------- announcing

    async def sweep(self, *, now: Optional[datetime] = None) -> None:
        """Probe, re-evaluate every component, and post any transitions.

        Runs on its own once-a-minute loop rather than at the sites that record
        signals, so that announcing happens in exactly one place and a component
        cannot post twice for one event.

        Never raises: this runs inside a task loop next to the daily report, and
        a monitoring failure must not become an outage of its own.
        """
        stamp = now or datetime.now(timezone.utc)

        if self._started_at is None:
            return
        # A restart clears every in-memory streak, and the loops take a moment to
        # take their first beat. Announcing inside that window would report an
        # outage that is really just a deploy.
        if stamp - self._started_at < timedelta(minutes=2):
            return

        await self._probe_database()

        # Dependencies first, so a dependent sees this sweep's verdict for its
        # parent rather than last sweep's. Evaluating in registration order
        # happens to do the same thing today; sorting means reordering
        # COMPONENTS can't silently cost a message a minute of delay.
        for key, component in self._in_dependency_order():
            state = self._state[key]
            try:
                await self._evaluate(component, state, stamp)
            except Exception as e:
                logger.error(f"health: evaluating {key} failed: {e}", exc_info=True)

    def _in_dependency_order(self):
        """Components with ``depends_on`` last. One level deep is all there is."""
        return sorted(self._components.items(),
                      key=lambda kv: kv[1].depends_on is not None)

    async def _evaluate(self, component: Component, state: _State,
                        now: datetime) -> None:
        down = self._is_down(component, state, now)

        if down and not state.announced_down:
            # Checked before latching, so ``announced_down`` means exactly "we
            # posted this" and the two branches stay symmetric. Latching while
            # suppressed would announce a recovery for an outage nobody was told
            # about — and would strand a component that is still broken after
            # its dependency comes back, because the verdict would already read
            # as delivered.
            if self._suppressed_by(component):
                return
            state.down_since = now
            state.announced_down = True
            logger.warning(f"🔴 health: {component.label} is DOWN")
            await self._announce(component, up=False)
            return

        if not down and state.announced_down:
            if state.ok_streak < component.pass_threshold:
                return
            since = state.down_since
            state.announced_down = False
            state.down_since = None
            logger.info(f"🟢 health: {component.label} recovered")
            await self._announce(component, up=True,
                                 downtime=(now - since) if since else None)

    def _suppressed_by(self, component: Component) -> bool:
        """True when a dependency is already down and speaking for this one."""
        if not component.depends_on:
            return False
        parent = self._state.get(component.depends_on)
        if parent is None or not parent.announced_down:
            return False
        # Debug, not info: this is re-checked every sweep for as long as the
        # dependency stays down, and one outage should not fill the log either.
        logger.debug(
            f"health: holding back {component.label} — {component.depends_on} "
            f"is already down and explains it"
        )
        return True

    async def _probe_database(self) -> None:
        """Ask the database whether it is there, rather than infer it.

        Every other component is measured from work the bot was doing anyway.
        The database is the one worth asking directly: it is the dependency the
        others are suppressed in favour of, so a wrong reading here silences the
        whole channel rather than one line of it.
        """
        try:
            from config.database import db
            if not getattr(db, "pool", None):
                self.record("database", False)
                return
            await db.fetchval("SELECT 1")
            self.record("database", True)
        except Exception as e:
            self.record("database", False)
            logger.debug(f"health: database probe failed: {e}")

    async def _announce(self, component: Component, *, up: bool,
                        downtime: Optional[timedelta] = None) -> None:
        if not component.public or not self._channel_id or self._bot is None:
            return

        channel = self._bot.get_channel(self._channel_id)
        if channel is None:
            logger.warning(
                f"health: status channel {self._channel_id} isn't in my cache — "
                f"'{component.label}' went {'up' if up else 'down'} unannounced. "
                f"Check STATUS_CHANNEL_ID and that I'm in that server."
            )
            return

        if up:
            embed = discord.Embed(
                title=f"🟢 {component.label} is back",
                description=component.recovery,
                colour=COLOR_ON_TRACK,
                timestamp=discord.utils.utcnow(),
            )
            if downtime is not None:
                embed.add_field(name="Was down for",
                                value=_humanise(downtime), inline=True)
        else:
            embed = discord.Embed(
                title=f"🔴 {component.label} is having problems",
                description=component.impact,
                colour=COLOR_BOMB,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="Do I need to do anything?",
                value="No — this is on my side and recovers on its own.",
                inline=False,
            )

        embed.set_footer(text="UmaCore service status")

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            # Named rather than generic: the status channel is set once and then
            # never looked at until it matters, which is precisely when a silent
            # permission problem costs the most.
            from utils.permissions import (
                post_requirements, missing_channel_permissions, post_forbidden_advice,
            )
            me = getattr(getattr(channel, "guild", None), "me", None)
            lacking = missing_channel_permissions(channel, me, *post_requirements(channel))
            logger.error(
                f"health: can't post to the status channel — "
                f"{post_forbidden_advice({'missing': lacking}, channel, what='status updates')}"
            )
        except Exception as e:
            logger.error(f"health: failed to post status update: {e}")


#: Module-level singleton, matching ``db`` and ``umamoe_limiter``. Imported by
#: the metrics hook, the task loops and the API server, all of which need the
#: same instance for the channel and ``/health`` to ever agree.
health = HealthMonitor()
