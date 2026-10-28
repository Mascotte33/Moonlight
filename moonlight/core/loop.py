"""Main game loop and priority state machine (T13).

Runs the bot at 20 Hz (50 ms per tick) and decides on every tick which
state the bot is in, using a strict priority order (highest wins):

1. KILL            F12 pressed or the kill switch activated: release all
                   inputs and stop.
2. PAUSED          screen_ok is False (client unfocused / perception
                   failed) or the last tick raised: inputs released, the
                   loop keeps ticking (perceive only) and resumes on its own
                   once perception is healthy again.
3. ALARM           Player on screen, PM open, fast HP drop, a critical
                   alarm, or a cavebot/refill alarm: stop attack/walk;
                   healing and a queued loot continue. Entering ALARM
                   alerts the operator.
4. ATTACK          A target is selected (or, with targeting wired, the
                   targeting module chose one).
5. LOOT            A kill is waiting to be looted (looting wired).
6. REFILL          Cap is full or a refill trip is running (refill wired).
7. MOVE            A waypoint route is active (cavebot / move_provider).
8. IDLE            Nothing to do.

Healing is not a state: the healing module runs first in every non-KILL
tick (see below). When healing is off, nothing heals.

Decision modules (T40): an optional :class:`DecisionModules` bundle wires
the feature packages engines into the tick. Each one is optional;
without it the loop keeps its built-in behaviour.

* **alarms** -- ``AlarmDecision.decide`` every tick; a critical alarm -> ALARM.
  PM detection is *not* wired in production (no PM perception exists);
  ``pm_open_check`` stays injectable for when it does.
* **targeting** -- ``TargetingDecision.decide`` every tick; a ``select``
  (or a selected target) -> ATTACK, which clicks the battle-list row
  through the shared ``clicker``, one click in flight at a time.
  # doc: docs/prs/targeting.md#target-choice
  While the cavebot is stuck on creatures, ``stuck_targeting`` (the same
  rows, "all others" attacking) decides instead, also with targeting off;
  a kill clears the cavebot's flag so it looks for a way again.
  # doc: docs/prs/targeting.md#stuck-targeting
* **attack_hotkeys** -- ``AttackHotkeys.decide`` in ATTACK while the
  selection matches targeting's choice and no click is in flight.
  # doc: docs/prs/targeting.md#area-rules
* **healing** -- ``HealingDecision.decide`` + ``send`` of each action
  first in every non-KILL state (healing continues through ALARM); a heal
  sent this tick skips the rest of the dispatch.
  # doc: docs/prs/healing.md#healing-rules
* **buffs** -- ``BuffManager.track`` + ``send`` of the top cast in the
  ATTACK/LOOT/REFILL/MOVE/IDLE states.
* **looting** -- a selected target that vanishes from the battle list is
  reported via ``Looting.on_target_died``; ``should_loot`` -> LOOT,
  which runs ``Looting.step``.
* **refill** -- ``should_refill`` / ``start`` / ``step``; when the trip
  is done the cavebot route is resumed.
* **cavebot** -- default ``move_provider``/``move_action``
  (``is_active`` / ``step``).

An optional ``perceive`` callback runs at the start of every tick (before
the snapshot) so one thread does capture -> perception -> decision.

Safety invariants (re-checked on every tick, never just at startup):

* **Pause on !screen_ok** -- ``screen_ok == False`` (or a tick that
  raised) pauses the loop: inputs are released once on entry, nothing is
  sent, and the loop resumes automatically when perception is healthy.
  It stops for good after ``pause_timeout`` seconds paused or
  ``max_consecutive_errors`` consecutive tick exceptions (operator alert).
* **No input unless focused** -- no action is sent unless the snapshot
  has both ``client_focused`` and ``screen_ok`` True. The one exception is
  ``release_all``: kill, pause, tick exceptions and shutdown call it
  directly (ungated, errors logged and swallowed), so a stop can never
  leave a key held. ``run()`` closes the backend on exit (stops the
  Arduino heartbeat so the firmware watchdog can fire).
* **Rate limit** -- at most ``max_actions_per_second`` serial commands
  (default 10/s sustained), counted at the wire by
  :class:`~moonlight.core.safety.RateLimitedInput`, so multi-command actions (clicker moves,
  rope hotkey + click, MOVE/REFILL steps) cannot exceed it. Heals have
  priority: non-heal commands leave one token in reserve, so a heal never
  waits behind an attack (worst case 12 commands in the first second,
  10/s after). Releases are never rate-limited.
* **F12 kill switch** -- a daemon thread polls the keyboard (10 ms,
  GetAsyncKeyState, no hook) and triggers the kill switch on a rising
  edge; :meth:`GameLoop.kill` does the same programmatically (UI button,
  Arduino kill pin, watchdog).

The loop only reads :class:`moonlight.core.state.WorldState` via
``snapshot()`` (perception is the single writer) and is the only
component that talks to the :class:`moonlight.hardware.input.InputBackend`.

Everything timing-related (clock, sleep) and environment-related (PM
check, move route, F12 key probe) is injectable, so the loop is fully
unit-testable without hardware, Windows, or a running client.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import partial
from typing import TYPE_CHECKING, Any

from moonlight.cavebot.cavebot import WALK_KEYS
from moonlight.core.safety import F12Monitor, RateLimitedInput
from moonlight.core.state import WorldState
from moonlight.hardware.input import InputBackend

if TYPE_CHECKING:
    from moonlight.alarms.alarms import AlarmAction, AlarmDecision
    from moonlight.cavebot.cavebot import Cavebot
    from moonlight.extras.buffs import BuffManager
    from moonlight.hardware.clicker import ClickController
    from moonlight.healing.healing import HealingDecision
    from moonlight.looting.looting import Looting
    from moonlight.refill.refill import Refill
    from moonlight.targeting.attack_hotkeys import AttackHotkeys
    from moonlight.targeting.seen_monsters import SeenMonsters
    from moonlight.targeting.targeting import TargetingAction, TargetingDecision

__all__ = [
    "BotState",
    "DecisionModules",
    "GameLoop",
    "LoopConfig",
]

logger = logging.getLogger("moonlight.core.loop")

#: How long a pressed chase toggle takes to show on the side panel.
CHASE_TOGGLE_SETTLE_SECONDS = 0.5

#: How often the tick timing line goes to the log.
TIMING_LOG_SECONDS = 10.0

# Seconds a sent select click waits for the row's red outline before a
# retry (clicking the attacked row again would stop the attack).
_SELECT_OUTLINE_WAIT = 1.0


@dataclass(frozen=True)
class LoopConfig:
    """Tunable parameters of the main loop (defaults are the T13 spec values)."""

    tick_rate: float = 20.0
    """Ticks per second (20 Hz = 50 ms per tick)."""

    max_actions_per_second: float = 10.0
    """Hard cap on input actions per second."""

    hp_drop_alarm_delta: float = 30.0
    """HP % drop within the window that raises ALARM."""

    hp_drop_alarm_window: float = 2.0
    """Time window (seconds) for the fast-HP-drop alarm check."""

    f12_poll_interval: float = 0.01
    """Seconds between F12 keyboard polls."""

    enable_f12_monitor: bool = True
    """Whether :meth:`GameLoop.run` starts the F12 polling thread."""

    pause_timeout: float = 30.0
    """Seconds PAUSED (unfocused / bad frames) before the loop stops for good.
    30 s covers an alt-tab or a loading screen; longer means a human took over."""

    max_consecutive_errors: int = 5
    """Consecutive tick exceptions (each one pauses) before the loop stops.
    5 ticks = 250 ms: rides out a one-off glitch, not a dead device."""



class BotState(StrEnum):
    """Priority states, declared from highest to lowest priority."""

    KILL = "kill"
    PAUSED = "paused"
    ALARM = "alarm"
    ATTACK = "attack"
    LOOT = "loot"
    REFILL = "refill"
    MOVE = "move"
    IDLE = "idle"

    @property
    def priority(self) -> int:
        """Priority rank: 0 = highest. Derived from declaration order."""
        return list(BotState).index(self)


# What get_stats()["current_task"] says for each state; MOVE names the waypoint.
_TASKS = {
    BotState.ATTACK: "Attacking",
    BotState.LOOT: "Looting",
}


@dataclass
class DecisionModules:
    """Decision engines the loop calls every tick; ``None`` = not wired."""

    healing: HealingDecision | None = None
    targeting: TargetingDecision | None = None
    #: Decides while the cavebot is stuck on creatures: "all others" attack.
    stuck_targeting: TargetingDecision | None = None
    attack_hotkeys: AttackHotkeys | None = None
    buffs: BuffManager | None = None
    alarms: AlarmDecision | None = None
    cavebot: Cavebot | None = None
    looting: Looting | None = None
    refill: Refill | None = None
    #: The one mouse: targeting, cavebot and looting share it.
    clicker: ClickController | None = None


class GameLoop:
    """The 20 Hz main game loop with the priority state machine.

    Typical usage (wired up by ``wiring.build_loop``)::

        loop = GameLoop(world_state, input_backend)
        loop.run()  # blocks until kill / stop() / screen_ok goes false
        print(loop.get_stats())

    The loop never writes to ``WorldState`` (perception is the single
    writer); it only reads through ``snapshot()``.
    """

    def __init__(
        self,
        state: WorldState,
        input_backend: InputBackend,
        config: LoopConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        pm_open_check: Callable[[WorldState], bool] | None = None,
        move_provider: Callable[[WorldState], bool] | None = None,
        move_action: Callable[[], None] | None = None,
        f12_monitor: F12Monitor | None = None,
        modules: DecisionModules | None = None,
        perceive: Callable[[], None] | None = None,
        alert: Callable[[str], None] | None = None,
    ) -> None:
        """Create the loop.

        Args:
            state: Shared world state (read-only for this class).
            input_backend: The only output path to the game.
            config: Loop parameters; defaults to :class:`LoopConfig`.
            clock: Monotonic-clock override (tests).
            sleep_fn: Sleep override (tests).
            pm_open_check: True when the PM window is open (T25/perception).
            move_provider: True while a waypoint route is active (T22).
            move_action: Called (gated, wire rate-limited) in the MOVE state:
                once per tick while the route is active (continuous motion).
            f12_monitor: Monitor override; when ``None``, the default is
                built unless the config disables it.
            modules: Decision engines to call each tick (see the module
                docstring); a wired cavebot is the default move provider.
            perceive: Called at the start of every tick, before the
                snapshot (capture -> perception -> WorldState).
            alert: Operator alert sink (e.g. :func:`beep_alert`), called on
                ALARM entry and on a failure stop; alerts are always logged.
        """
        cfg = config if config is not None else LoopConfig()
        self._validate_config(cfg)

        self.state = state
        # Every serial command goes through the wire limiter (F-20); build_loop
        # passes one in so the modules it builds share the same budget.
        self.input = RateLimitedInput.wrap(input_backend, cfg.max_actions_per_second, clock)
        self.config = cfg
        self._clock = clock if clock is not None else time.monotonic
        self._sleep_fn = sleep_fn if sleep_fn is not None else time.sleep
        self._pm_open_check = pm_open_check
        self.all_modules = modules if modules is not None else DecisionModules()
        #: GUI Pause: cavebot, targeting and refill sit out, the rest keeps running.
        self.paused = False
        self._perceive = perceive
        self._alert_sink = alert
        #: Takes a new BotConfig while running; False when it needs a rebuild
        #: (set by ``wiring.build_loop``).
        self.reconfigure: Callable[[Any], bool] | None = None
        #: Monsters seen in the battle list, for the Targeting tab (set by ``build_loop``).
        self.seen_monsters: SeenMonsters | None = None
        if move_provider is None and move_action is None:
            # Looked up every tick, so a cavebot swapped in by reconfigure is used.
            move_provider = self._cavebot_active
            move_action = self._cavebot_step
        self._move_provider = move_provider
        self._move_action = move_action
        # Per-tick module decisions (refreshed by _observe each tick).
        self._alarm: AlarmAction | None = None
        self._target: TargetingAction | None = None
        self._last_target: str | None = None
        self._last_names: Counter[str] = Counter()
        self._had_selection = False
        # The select click: (battle-list row, when sent; None while in flight).
        self._select_click: tuple[int, float | None] | None = None
        # Until when a pressed chase toggle may not show on screen yet (no second press).
        self._chase_toggle_settles_at = 0.0
        self._warned_no_chase_toggle_key = False
        # Pause bookkeeping (reset by every healthy, exception-free tick).
        self._paused_since: float | None = None
        self._consecutive_errors = 0
        self._f12_monitor = f12_monitor
        if self._f12_monitor is None and cfg.enable_f12_monitor:
            self._f12_monitor = F12Monitor(
                on_press=self.kill,
                poll_interval=cfg.f12_poll_interval,
            )

        # Capacity 2 with one token reserved for heals: non-heal commands
        # still pace at exactly 10/s, and a heal never waits behind them.
        self._rate_limiter = self.input.limiter
        self._kill_event = threading.Event()
        self._stop_event = threading.Event()
        self._state = BotState.IDLE
        self._task = "Idle"

        # Stats (written by the tick thread; read via get_stats()).
        self._tick_count = 0
        self._actions_sent = 0
        self._actions_dropped_rate = 0
        self._actions_dropped_unfocused = 0
        self._tick_time_total = 0.0
        self._tick_time_max = 0.0
        # (start, ticks, total s, perceive s, max s) of the current timing-log window.
        self._timing_window = (0.0, 0, 0.0, 0.0, 0.0)
        self._perceive_time = 0.0
        self._state_transitions: dict[BotState, int] = {}
        self._start_time: float | None = None

        # (monotonic_time, hp) samples for the fast-HP-drop alarm.
        self._hp_history: deque[tuple[float, float]] = deque()
        self._reported_module_alarm: str | None = None

    @property
    def modules(self) -> DecisionModules:
        """The modules that run this tick (``all_modules`` minus the paused ones)."""
        if not self.paused:
            return self.all_modules
        return replace(self.all_modules, cavebot=None, targeting=None, attack_hotkeys=None)

    @modules.setter
    def modules(self, value: DecisionModules) -> None:
        old = self.all_modules.cavebot
        if old is not None and old is not value.cavebot:
            old.release_walk_key()  # nothing ticks a replaced cavebot again
        self.all_modules = value

    def _cavebot_active(self, _snap: WorldState) -> bool:
        return self.modules.cavebot is not None and self.modules.cavebot.is_active()

    def _cavebot_step(self) -> None:
        if self.modules.cavebot is not None:
            self.modules.cavebot.step()

    # -- public control ----------------------------------------------------

    @property
    def current_state(self) -> BotState:
        """The state the loop is currently in."""
        return self._state

    @property
    def kill_switch_active(self) -> bool:
        """True once the kill switch has been activated."""
        return self._kill_event.is_set()

    @property
    def stop_requested(self) -> bool:
        """True once a stop has been requested."""
        return self._stop_event.is_set()

    def kill(self) -> None:
        """Activate the kill switch (F12, UI button, Arduino kill pin).

        Thread-safe: sets an event the loop thread picks up on its next
        tick, which releases all inputs (ungated) and stops the loop.
        """
        if not self._kill_event.is_set():
            logger.warning("KILL SWITCH ACTIVATED")
        self._kill_event.set()

    def stop(self, reason: str = "") -> None:
        """Request a clean stop; ``run()`` returns after the current tick."""
        if reason:
            logger.info("stop requested: %s", reason)
        self._stop_event.set()

    # -- main loop ---------------------------------------------------------

    def run(self) -> None:
        """Run the 20 Hz loop until kill, ``stop()``, or a pause/error limit.

        Blocks the calling thread. Returns cleanly on every exit path; a
        tick exception is logged and pauses the loop (see
        ``max_consecutive_errors``). On exit the F12 monitor is stopped,
        inputs are released and the backend is closed.
        """
        if self._kill_event.is_set() or self._stop_event.is_set():
            return
        self._start_time = self._clock()
        if self._f12_monitor is not None:
            self._f12_monitor.start()
        period = 1.0 / self.config.tick_rate
        next_tick = self._clock()
        try:
            while not self._stop_event.is_set():
                try:
                    self.tick()
                except Exception:
                    logger.exception("tick raised")
                    self._on_tick_error()
                if self._stop_event.is_set():
                    break
                next_tick += period
                delay = next_tick - self._clock()
                if delay > 0:
                    self._sleep_fn(delay)
                else:
                    # Fell behind schedule (slow tick / OS scheduling):
                    # resync instead of firing a burst of catch-up ticks.
                    next_tick = self._clock()
        finally:
            self._shutdown()

    def tick(self) -> None:
        """One iteration: safety gates -> state decision -> action.

        Safe to call directly (tests, HIL harnesses); ``run()`` simply
        calls this in a 20 Hz loop.
        """
        t0 = self._clock()
        self._tick_count += 1

        # Priority 1: KILL beats everything, even a bad screen_ok.
        if self._kill_event.is_set():
            self._enter_state(BotState.KILL, "kill switch active")
            self._act_kill()
            self._record_tick_time(t0)
            return

        cavebot = self.all_modules.cavebot
        if self.paused and cavebot is not None:
            cavebot.release_walk_key()  # Pause hides the cavebot, so let go here

        self._perceive_time = 0.0
        if self._perceive is not None:
            self._perceive()
            self._perceive_time = self._clock() - t0
        snap = self.state.snapshot()

        # Pause gate: !screen_ok (unfocused / bad frame) pauses, fail-closed.
        if not snap.screen_ok:
            self._pause("screen not ok (client unfocused or perception failed)")
            self._record_tick_time(t0)
            return

        self._record_hp(snap)
        self._observe(snap)
        new_state = self._decide_state(snap)
        if new_state is not self._state:
            self._enter_state(new_state, self._transition_reason(snap, new_state))
        self._dispatch(snap)
        self._paused_since = None
        self._consecutive_errors = 0
        self._record_tick_time(t0)

    # -- pause / release / alert ---------------------------------------------

    def _pause(self, reason: str) -> None:
        """Enter (or stay in) PAUSED; stop for good after ``pause_timeout``."""
        now = self._clock()
        if self._state is not BotState.PAUSED:
            self._enter_state(BotState.PAUSED, reason)
            self._release("pause")
        if self._paused_since is None:
            self._paused_since = now
        elif now - self._paused_since > self.config.pause_timeout:
            self._fail_stop(f"paused for more than {self.config.pause_timeout:.0f}s ({reason})")

    def _on_tick_error(self) -> None:
        """A tick raised: release + pause; stop after too many in a row."""
        self._consecutive_errors += 1
        self._pause("tick raised an exception")
        if self._consecutive_errors >= self.config.max_consecutive_errors:
            self._fail_stop(f"{self._consecutive_errors} consecutive tick exceptions")

    def _fail_stop(self, reason: str) -> None:
        self._release("failure stop")
        self._alert(f"loop stopping: {reason}")
        self._stop_event.set()

    def _release(self, why: str) -> None:
        """``release_all`` directly: ungated, never raises (safety path)."""
        try:
            self.input.release_all()
        except Exception:  # noqa: BLE001 - a failed release must not mask the stop
            logger.exception("release_all failed (%s)", why)

    def _alert(self, message: str) -> None:
        """Operator alert: always a log line, plus the sink (never raises)."""
        logger.warning("OPERATOR ALERT: %s", message)
        if self._alert_sink is not None:
            try:
                self._alert_sink(message)
            except Exception:  # noqa: BLE001
                logger.exception("alert sink failed")

    # -- state machine -----------------------------------------------------

    def _observe(self, snap: WorldState) -> None:
        """Run the per-tick decision modules (alarms, targeting, kill check)."""
        m = self.modules
        alarm = m.alarms.decide(snap) if m.alarms is not None else None
        # Only critical alarms stop the bot; warnings are advisory.
        self._alarm = alarm if alarm is not None and alarm.severity == "critical" else None
        self._report_module_alarm()
        selected = snap.selected_target
        if self._had_selection and selected is None and m.cavebot is not None:
            m.cavebot.stuck_on_creatures = False  # a kill: the cavebot looks for a way again
        self._had_selection = selected is not None
        targeting = self._targeting()
        self._target = targeting.decide(snap, self._clock()) if targeting is not None else None
        names = Counter(mon.name for mon in snap.monsters)
        if m.looting is not None:
            # ponytail: one fewer monster named like the target = kill (the
            # client may select the next one at once; one walking off screen
            # counts too); use the server-log OCR (perception/logocr.py) if
            # false loot attempts matter.
            prev = self._last_target
            cur = snap.selected_target
            self._last_target = cur.name if cur is not None else None
            if prev and (
                names[prev] < self._last_names[prev] or (cur is None and not names[prev])
            ):
                m.looting.on_target_died(prev)
            elif self._last_names and not names:
                m.looting.on_target_died("")  # the battle list emptied: loot again
        self._last_names = names

    def _targeting(self) -> TargetingDecision | None:
        """``stuck_targeting`` while the cavebot is stuck on creatures, else ``targeting``."""
        m = self.modules
        if m.stuck_targeting and m.cavebot is not None and m.cavebot.stuck_on_creatures:
            return m.stuck_targeting
        return m.targeting

    def _decide_state(self, snap: WorldState) -> BotState:
        """Priority state machine: the first matching condition wins.

        KILL is never returned here -- it is handled in :meth:`tick`
        before the snapshot, since it must win even over a stale state.
        """
        if self._is_alarm(snap):
            return BotState.ALARM
        m = self.modules
        if m.looting is not None and m.looting.loot_first(snap):
            return BotState.LOOT  # loot each kill before the next target
        targeting = self._targeting()
        if m.cavebot is not None and m.cavebot.luring():
            pass  # a lure waypoint walks past monsters
        elif targeting is not None:
            selected = snap.selected_target
            if self._target is not None or (
                selected is not None and not targeting.is_unreachable(selected)
            ):
                return BotState.ATTACK
        elif snap.selected_target is not None and snap.selected_target.name:
            # Perception only selects targets that are in range and attackable;
            # a nameless (unresolved) selection may be a player (F-01).
            return BotState.ATTACK
        if m.looting is not None and m.looting.should_loot(snap):
            return BotState.LOOT
        if m.refill is not None and (m.refill.is_active() or m.refill.should_refill(snap)):
            return BotState.REFILL
        if self._move_provider is not None and self._move_provider(snap):
            return BotState.MOVE
        return BotState.IDLE

    def _module_alarm(self) -> str | None:
        """A cavebot/refill alarm (``"stuck"``, ``"lost"``, ...), or None."""
        for name, module in (("cavebot", self.modules.cavebot), ("refill", self.modules.refill)):
            alarm = getattr(module, "alarm", None)
            if alarm:
                return f"{name} alarm: {alarm}"
        return None

    def _report_module_alarm(self) -> None:
        """Alert once when the cavebot or refill stops itself (stuck, lost).

        The stopped module only stops walking; healing and targeting go on.
        """
        alarm = self._module_alarm()
        if alarm is not None and alarm != self._reported_module_alarm:
            self._alert(alarm)
        self._reported_module_alarm = alarm

    def _is_alarm(self, snap: WorldState) -> bool:
        """ALARM: player on screen, PM open, fast HP drop or a critical alarm."""
        if snap.players_on_screen > 0 or self._alarm is not None:
            return True
        if self._pm_open_check is not None and self._pm_open_check(snap):
            return True
        return self._hp_drop_fast(snap)

    def _transition_reason(self, snap: WorldState, new_state: BotState) -> str:
        """Short human-readable reason for a transition (for logs)."""
        match new_state:
            case BotState.ALARM:
                if self._alarm is not None:
                    return f"alarm {self._alarm.type.value}: {self._alarm.message}"
                if snap.players_on_screen > 0:
                    return f"players on screen: {snap.players_on_screen}"
                if self._pm_open_check is not None and self._pm_open_check(snap):
                    return "PM window open"
                return "fast HP drop"
            case BotState.ATTACK:
                if self._target is not None:
                    return f"select row {self._target.battle_list_row} ({self._target.reason})"
                name = snap.selected_target.name if snap.selected_target is not None else "?"
                return f"target: {name}"
            case BotState.LOOT:
                return "loot pending"
            case BotState.REFILL:
                return "cap full / refill trip running"
            case BotState.MOVE:
                return "waypoint route active"
            case BotState.IDLE:
                return "no action needed"
            case _:
                return "n/a"

    def _enter_state(self, new_state: BotState, reason: str) -> None:
        old = self._state
        self._state = new_state
        self._state_transitions[new_state] = self._state_transitions.get(new_state, 0) + 1
        logger.info("state %s -> %s (%s)", old.value, new_state.value, reason)
        if old is BotState.PAUSED:
            logger.info("resumed after pause")
        if new_state is BotState.ALARM:
            self._alert(f"ALARM: {reason}")
        if new_state is BotState.ATTACK and self.modules.clicker is not None:
            self.modules.clicker.cancel()  # drop a walk click; the select click goes first
        if self.all_modules.cavebot is not None:
            self.all_modules.cavebot.release_walk_key()  # only MOVE and REFILL walk
        if new_state is BotState.MOVE and self.modules.cavebot is not None:
            self.modules.cavebot.resume()  # fight/loot time is not "stuck" (F-06)

    # -- fast HP drop tracking ---------------------------------------------

    def _record_hp(self, snap: WorldState) -> None:
        """Append one HP sample and trim history beyond 2x the window."""
        if snap.hp_percent > 0.0:
            self._hp_history.append((self._clock(), snap.hp_percent))
        cutoff = self._clock() - (self.config.hp_drop_alarm_window * 2.0)
        while self._hp_history and self._hp_history[0][0] < cutoff:
            self._hp_history.popleft()

    def _hp_drop_fast(self, snap: WorldState) -> bool:
        """True when HP fell by ``hp_drop_alarm_delta`` within the window."""
        if not self._hp_history or snap.hp_percent <= 0.0:
            return False
        target_t = self._clock() - self.config.hp_drop_alarm_window
        ref_hp: float | None = None
        for t, hp in reversed(self._hp_history):
            if t <= target_t:
                ref_hp = hp
                break
        if ref_hp is None:
            return False
        return (ref_hp - snap.hp_percent) >= self.config.hp_drop_alarm_delta

    # -- actions -------------------------------------------------------------

    def _dispatch(self, snap: WorldState) -> None:
        self._task = _TASKS.get(self._state, "Idle")
        if self._state is BotState.KILL:
            self._act_kill()
            return
        # Healing continues through every state (including ALARM).
        if self.modules.healing is not None and self._heal(snap):
            self._task = "Healing"
            return
        looting = self.modules.looting
        if looting is not None and looting.ring_due():
            self._send(snap, looting.press_ring)  # then carry on with this tick
        if self._state is BotState.ALARM:
            # No attack/move/buffs; healing already ran (alert sent on entry).
            if self.modules.looting is not None and self.modules.looting.should_loot(snap):
                self._act_loot(snap)  # the corpse is still there
            return
        if self.modules.buffs is not None and self._buff(snap):
            return
        match self._state:
            case BotState.ATTACK:
                self._act_attack(snap)
            case BotState.LOOT:
                self._act_loot(snap)
            case BotState.REFILL:
                self._act_refill(snap)
            case BotState.MOVE:
                self._act_move(snap)
            case BotState.IDLE:
                pass

    def _send(self, snap: WorldState, action: Callable[[], object], *, heal: bool = False) -> bool:
        """Gate one input action; the wire limiter paces its commands.

        Never sends input unless the snapshot has both ``client_focused``
        and ``screen_ok`` True. The action only runs when its first wire
        command can be paid for (so a module never records a cast that
        was not sent); every command it issues pays one token at the wire
        (:class:`~moonlight.core.safety.RateLimitedInput`), surplus commands are dropped.
        Non-*heal* actions leave one token in reserve so heals never wait
        behind them. An *action* returning ``False`` (a module declined to
        send) or losing a command to the limit is not counted as sent.
        """
        if not (snap.client_focused and snap.screen_ok):
            self._actions_dropped_unfocused += 1
            return False
        wire = self.input
        wire.reserve = 0.0 if heal else 1.0
        # Peek (acquire 0): is one token free above the reserve?
        if not self._rate_limiter.try_acquire(0.0, reserve=wire.reserve + 1.0):
            self._actions_dropped_rate += 1
            return False
        dropped = wire.dropped
        try:
            result = action()
        finally:
            wire.reserve = 1.0
        if wire.dropped > dropped:
            self._actions_dropped_rate += 1
            return False
        if result is False:
            return False
        self._actions_sent += 1
        return True

    def _act_kill(self) -> None:
        """KILL: release all inputs (ungated, even when unfocused) and stop."""
        self._release("kill switch")
        self._stop_event.set()

    def _heal(self, snap: WorldState) -> bool:
        """Healing module: send each decided key; True when any was sent."""
        healing = self.modules.healing
        assert healing is not None
        sent = False
        for action in healing.decide(snap):
            if self._send(snap, partial(healing.send, action, self.input), heal=True):
                sent = True
        return sent

    def _buff(self, snap: WorldState) -> bool:
        """Buff module: send the top-priority due cast; True when sent."""
        buffs = self.modules.buffs
        assert buffs is not None
        actions = buffs.track(snap)
        return bool(actions) and self._send(snap, lambda: buffs.send(actions[0], self.input))

    def _act_attack(self, snap: WorldState) -> None:
        """ATTACK: click the battle-list row targeting chose, one click at a time.

        A click is in flight while the clicker is busy, and after it is sent
        until the row shows as selected or ``_SELECT_OUTLINE_WAIT`` passes.
        A ``step_closer`` action presses one arrow key. Only once the
        selection matches targeting's choice does an attack hotkey go out.
        """
        targeting, clicker = self._targeting(), self.modules.clicker
        if targeting is None:
            return
        if clicker is None:
            raise RuntimeError("targeting is wired without a clicker")
        if self._target is not None and self._target.kind == "step_closer":
            key = WALK_KEYS[self._target.step]
            self._send(snap, lambda: self.input.press_combo(key))
            return
        now = self._clock()
        if clicker.busy:
            self._send(snap, lambda: self._step_click(clicker, now))
            return
        if self._select_click is not None:
            row, sent_at = self._select_click
            selected = snap.selected_target
            outline_shows = selected is not None and selected.battle_list_row == row
            if sent_at is not None and not outline_shows and now - sent_at < _SELECT_OUTLINE_WAIT:
                return
            self._select_click = None
        if self._target is None:
            self._press_attack_hotkey(snap, targeting, now)
            return
        row = self._target.battle_list_row
        point = targeting.row_center(row)

        def click() -> bool | None:
            clicker.request(point)
            self._select_click = (row, None)
            return self._step_click(clicker, now)

        if self._send(snap, click):
            logger.debug("select click row %d at %s", row, point)

    def _press_attack_hotkey(
        self, snap: WorldState, targeting: TargetingDecision, now: float
    ) -> None:
        """Press the attack hotkey for the selected target, once the screen shows its chase mode."""
        # doc: docs/prs/targeting.md#chase-mode
        attack_hotkeys = self.modules.attack_hotkeys
        selected = snap.selected_target
        if selected is None or selected.is_player:
            return
        row = targeting.row_for(selected)
        if row.ignore or targeting.is_unreachable(selected):
            return
        toggle = targeting.config.toggle_chase_hotkey
        if snap.chase_mode is not None and snap.chase_mode != row.movement:
            if toggle:
                # The attack key waits until the screen shows the toggled mode.
                if now >= self._chase_toggle_settles_at and self._send(
                    snap, lambda: self.input.press_combo(toggle)
                ):
                    self._chase_toggle_settles_at = now + CHASE_TOGGLE_SETTLE_SECONDS
                return
            if not self._warned_no_chase_toggle_key:
                logger.warning(
                    "chase mode is %r but %s's row wants %r: set the Toggle Chase Mode key",
                    snap.chase_mode,
                    selected.name,
                    row.movement,
                )
                self._warned_no_chase_toggle_key = True
        if attack_hotkeys is None:
            return
        hotkey = attack_hotkeys.decide(snap, row, now)
        if hotkey is not None:
            self._send(snap, lambda: attack_hotkeys.send(hotkey, self.input, now))

    def _step_click(self, clicker: ClickController, now: float) -> bool | None:
        """One clicker step; on the click, record what it did."""
        result = clicker.step()
        if result is True and self._select_click is not None:
            self._select_click = (self._select_click[0], now)
        return result

    def _act_loot(self, snap: WorldState) -> None:
        """LOOT: one looting step per tick (rate-limited: it presses keys)."""
        looting = self.modules.looting
        assert looting is not None

        self._send(snap, lambda: looting.step(snap))

    def _act_refill(self, snap: WorldState) -> None:
        """REFILL: start the trip, then one step per tick (gated like MOVE).

        When the trip is done, the cavebot's farming route is resumed at
        the nearest waypoint; a
        refill alarm leaves the route stopped (fail closed).
        """
        refill = self.modules.refill
        assert refill is not None
        cavebot = self.modules.cavebot

        def step() -> None:
            if not refill.is_active():
                refill.start()
            if not refill.step(snap) and refill.phase == "done" and cavebot is not None:
                cavebot.start(nearest=True)

        self._send(snap, step)

    def _act_move(self, snap: WorldState) -> None:
        """MOVE: hand control to the waypoint runner (T22).

        The step is gated (focus/screen_ok); its clicks/keys pay at the
        wire limiter like every other command (F-20).
        """
        cavebot = self.modules.cavebot
        self._task = (
            f"Walking to waypoint {cavebot.route.current + 1}" if cavebot else "Walking"
        )
        if self._move_action is None:
            return
        if self._send(snap, self._move_action):
            logger.debug("move: waypoint step")

    # -- stats and lifecycle -------------------------------------------------

    def _record_tick_time(self, t0: float) -> None:
        elapsed = self._clock() - t0
        self._tick_time_total += elapsed
        if elapsed > self._tick_time_max:
            self._tick_time_max = elapsed
        start, ticks, total, perceive, longest = self._timing_window
        ticks, total = ticks + 1, total + elapsed
        perceive, longest = perceive + self._perceive_time, max(longest, elapsed)
        now = self._clock()
        if now - start >= TIMING_LOG_SECONDS:
            if start:
                logger.info(
                    "tick timing: %d ticks, avg %.1f ms (screen reading %.1f ms), max %.1f ms",
                    ticks,
                    total / ticks * 1000.0,
                    perceive / ticks * 1000.0,
                    longest * 1000.0,
                )
            self._timing_window = (now, 0, 0.0, 0.0, 0.0)
        else:
            self._timing_window = (start, ticks, total, perceive, longest)

    def _shutdown(self) -> None:
        if self._f12_monitor is not None:
            self._f12_monitor.stop()
        self._release("loop stopped")
        try:
            # Stops the Arduino heartbeat so the firmware watchdog can fire.
            self.input.close()
        except Exception:  # noqa: BLE001
            logger.exception("closing the input backend failed")
        uptime = 0.0
        if self._start_time is not None:
            uptime = self._clock() - self._start_time
        logger.info(
            "loop stopped: ticks=%d uptime=%.2fs state=%s",
            self._tick_count,
            uptime,
            self._state.value,
        )

    @staticmethod
    def _validate_config(cfg: LoopConfig) -> None:
        if cfg.tick_rate <= 0:
            raise ValueError("tick_rate must be > 0")
        if cfg.max_actions_per_second <= 0:
            raise ValueError("max_actions_per_second must be > 0")
        if cfg.hp_drop_alarm_delta <= 0.0 or cfg.hp_drop_alarm_window <= 0.0:
            raise ValueError("hp_drop_alarm_delta and hp_drop_alarm_window must be > 0")
        if cfg.pause_timeout <= 0.0 or cfg.max_consecutive_errors < 1:
            raise ValueError("pause_timeout must be > 0 and max_consecutive_errors >= 1")

    def get_stats(self) -> dict[str, object]:
        """Performance and safety stats for the GUI and the T30 report."""
        uptime = 0.0
        if self._start_time is not None:
            uptime = self._clock() - self._start_time
        ticks = self._tick_count
        return {
            "state": self._state.value,
            "tick_count": ticks,
            "current_task": self._task,
            "uptime_seconds": round(uptime, 3),
            "avg_tick_time_ms": (
                round(self._tick_time_total / ticks * 1000.0, 3) if ticks else 0.0
            ),
            "max_tick_time_ms": round(self._tick_time_max * 1000.0, 3),
            "actions_sent": self._actions_sent,
            "actions_dropped_rate_limit": self._actions_dropped_rate,
            "actions_dropped_unfocused": self._actions_dropped_unfocused,
            "state_transitions": {s.value: n for s, n in self._state_transitions.items()},
            "kill_switch_active": self.kill_switch_active,
            "stop_requested": self.stop_requested,
        }

    def reset_stats(self) -> None:
        """Zero all counters (tests, UI reset button)."""
        self._tick_count = 0
        self._actions_sent = 0
        self._actions_dropped_rate = 0
        self._actions_dropped_unfocused = 0
        self._tick_time_total = 0.0
        self._tick_time_max = 0.0
        self._state_transitions = {}
        self._hp_history.clear()

    def __enter__(self) -> GameLoop:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop("context exited")
