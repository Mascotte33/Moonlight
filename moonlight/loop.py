"""Main game loop and priority state machine (T13).

Runs the bot at 20 Hz (50 ms per tick) and decides on every tick which
state the bot is in, using a strict priority order (highest wins):

1. KILL            F12 pressed or the kill switch activated: release all
                   inputs and stop.
2. ALARM           Player on screen, PM open, or fast HP drop: stop
                   attack/loot/walk; healing continues.
3. EMERGENCY_HEAL  HP at or below the critical threshold.
4. NORMAL_HEAL     HP at or below the normal threshold.
5. ATTACK          A target is selected (or, with targeting wired, the
                   targeting module chose one).
6. LOOT            A kill is waiting to be looted (looting wired).
7. REFILL          Cap is full or a refill trip is running (refill wired).
8. MOVE            A waypoint route is active (cavebot / move_provider).
9. IDLE            Nothing to do.

Decision modules (T40): an optional :class:`DecisionModules` bundle wires
the ``moonlight.decision`` engines into the tick. Each one is optional;
without it the loop keeps its built-in behaviour.

* **alarms** -- ``AlarmDecision.decide`` every tick; a critical alarm -> ALARM.
* **targeting** -- ``TargetingDecision.decide`` every tick; attack/switch
  -> ATTACK, flee -> ALARM (disengage).
* **healing** -- ``HealingDecision.decide`` + ``send`` first in every
  non-KILL state (healing continues through ALARM); replaces the
  built-in heal combos.
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

* **Stop on !screen_ok** -- as soon as the world state reports
  ``screen_ok == False`` the loop halts (``run()`` returns).
* **No input unless focused** -- no wire command is sent unless the
  snapshot has both ``client_focused`` and ``screen_ok`` True. This also
  applies to the kill-switch release; the Arduino kill pin (T06 firmware)
  provides independent hardware-level release.
* **Rate limit** -- at most ``max_actions_per_second`` input actions
  (default 10/s). A 20 Hz tick can therefore only send input on every
  second tick; the limiter enforces the cap, not the tick rate. Two
  deliberate exceptions: the kill switch's release-all (an emergency
  release must not wait for a token) and the MOVE waypoint step (a
  continuous motion handed to the waypoint runner once per tick, which
  owns its own cadence). Both remain subject to the focus gate.
* **F12 kill switch** -- a daemon thread polls the keyboard (10 ms,
  GetAsyncKeyState, no hook) and triggers the kill switch on a rising
  edge; :meth:`GameLoop.kill` does the same programmatically (UI button,
  Arduino kill pin, watchdog).

The loop only reads :class:`moonlight.state.WorldState` via
``snapshot()`` (perception is the single writer) and is the only
component that talks to the :class:`moonlight.input.InputBackend`.

Everything timing-related (clock, sleep) and environment-related (PM
check, move route, F12 key probe) is injectable, so the loop is fully
unit-testable without hardware, Windows, or a running client.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from moonlight.input import InputBackend
from moonlight.state import WorldState

if TYPE_CHECKING:
    from moonlight.decision.alarms import AlarmAction, AlarmDecision
    from moonlight.decision.buffs import BuffManager
    from moonlight.decision.cavebot import Cavebot
    from moonlight.decision.healing import HealingDecision
    from moonlight.decision.looting import Looting
    from moonlight.decision.refill import Refill
    from moonlight.decision.targeting import TargetingAction, TargetingDecision

__all__ = [
    "VK_F12",
    "BotState",
    "DecisionModules",
    "F12Monitor",
    "GameLoop",
    "LoopConfig",
    "RateLimiter",
]

logger = logging.getLogger("moonlight.loop")

#: Win32 virtual-key code for F12.
VK_F12 = 0x7B


def default_key_state(vk: int) -> bool:
    """Return True if virtual key *vk* is currently held down.

    Uses Win32 ``GetAsyncKeyState`` polling on Windows; always False
    elsewhere (the programmatic kill switch still works everywhere).
    """
    if sys.platform != "win32":
        return False
    state = ctypes.windll.user32.GetAsyncKeyState(vk)
    return bool(state & 0x0001)


@dataclass(frozen=True)
class LoopConfig:
    """Tunable parameters of the main loop (defaults are the T13 spec values)."""

    tick_rate: float = 20.0
    """Ticks per second (20 Hz = 50 ms per tick)."""

    max_actions_per_second: float = 10.0
    """Hard cap on input actions per second."""

    critical_hp: float = 30.0
    """HP % at or below which EMERGENCY_HEAL triggers."""

    normal_hp: float = 60.0
    """HP % at or below which NORMAL_HEAL triggers (above critical)."""

    hp_drop_alarm_delta: float = 30.0
    """HP % drop within the window that raises ALARM."""

    hp_drop_alarm_window: float = 2.0
    """Time window (seconds) for the fast-HP-drop alarm check."""

    attack_combo: str = "r"
    """Tibia's default attack hotkey."""

    emergency_heal_combo: str = "1"
    """Hotkey for emergency healing (first potion slot)."""

    normal_heal_combo: str = "2"
    """Hotkey for routine healing (second potion slot)."""

    f12_poll_interval: float = 0.01
    """Seconds between F12 keyboard polls."""

    enable_f12_monitor: bool = True
    """Whether :meth:`GameLoop.run` starts the F12 polling thread."""


class BotState(StrEnum):
    """Priority states, declared from highest to lowest priority."""

    KILL = "kill"
    ALARM = "alarm"
    EMERGENCY_HEAL = "emergency_heal"
    NORMAL_HEAL = "normal_heal"
    ATTACK = "attack"
    LOOT = "loot"
    REFILL = "refill"
    MOVE = "move"
    IDLE = "idle"

    @property
    def priority(self) -> int:
        """Priority rank: 0 = highest. Derived from declaration order."""
        return list(BotState).index(self)


@dataclass
class DecisionModules:
    """Decision engines the loop calls every tick; ``None`` = not wired."""

    healing: HealingDecision | None = None
    targeting: TargetingDecision | None = None
    buffs: BuffManager | None = None
    alarms: AlarmDecision | None = None
    cavebot: Cavebot | None = None
    looting: Looting | None = None
    refill: Refill | None = None


class RateLimiter:
    """Token-bucket rate limiter.

    Refills at *rate* tokens per second up to *capacity* tokens.
    ``try_acquire`` never blocks: it returns ``False`` when not enough
    tokens are available. A monotonic clock may be injected for tests.
    """

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self._rate = float(rate)
        self._capacity = float(rate if capacity is None else capacity)
        if self._capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._clock = clock if clock is not None else time.monotonic
        self._tokens = self._capacity
        self._last_refill = self._clock()

    @property
    def rate(self) -> float:
        """Tokens per second."""
        return self._rate

    @property
    def capacity(self) -> float:
        """Maximum stored tokens."""
        return self._capacity

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Consume *tokens* if available; never blocks."""
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now
        if tokens > self._tokens:
            return False
        self._tokens -= tokens
        return True

    def reset(self) -> None:
        """Refill the bucket (e.g. between test scenarios)."""
        self._tokens = self._capacity
        self._last_refill = self._clock()


class F12Monitor:
    """Daemon thread that polls F12 and fires ``on_press`` on a rising edge.

    Uses keyboard *polling* (GetAsyncKeyState) rather than a low-level
    hook: simpler, and it cannot be blocked by hooks installed by other
    applications. ``on_press`` runs in the monitor thread, so it must be
    thread-safe and fast (the loop only sets an event). The key-state
    probe is injectable for tests.
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        poll_interval: float = 0.01,
        key_state: Callable[[int], bool] | None = None,
        vk: int = VK_F12,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0")
        self._on_press = on_press
        self._poll_interval = poll_interval
        self._key_state = key_state if key_state is not None else default_key_state
        self._vk = vk
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        """True while the monitor thread is alive."""
        t = self._thread
        return t is not None and t.is_alive()

    def start(self) -> None:
        """Start the monitor thread (no-op if already running)."""
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="moonlight-f12", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the monitor thread and wait (bounded) for it to exit."""
        self._stop_event.set()
        t = self._thread
        self._thread = None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=max(0.2, 5.0 * self._poll_interval))

    def _run(self) -> None:
        prev_down = False
        while not self._stop_event.wait(self._poll_interval):
            down = self._key_state(self._vk)
            if down and not prev_down:
                logger.info("F12 kill switch pressed")
                try:
                    self._on_press()
                except Exception:
                    logger.exception("F12 on_press callback failed")
            prev_down = down


class GameLoop:
    """The 20 Hz main game loop with the priority state machine.

    Typical usage (wired up by the UI in T27)::

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
            move_action: Called (gated, not rate-limited) in the MOVE state:
                once per tick while the route is active (continuous motion).
            f12_monitor: Monitor override; when ``None``, the default is
                built unless the config disables it.
            modules: Decision engines to call each tick (see the module
                docstring); a wired cavebot is the default move provider.
            perceive: Called at the start of every tick, before the
                snapshot (capture -> perception -> WorldState).
        """
        cfg = config if config is not None else LoopConfig()
        self._validate_config(cfg)

        self.state = state
        self.input = input_backend
        self.config = cfg
        self._clock = clock if clock is not None else time.monotonic
        self._sleep_fn = sleep_fn if sleep_fn is not None else time.sleep
        self._pm_open_check = pm_open_check
        self.modules = modules if modules is not None else DecisionModules()
        self._perceive = perceive
        cavebot = self.modules.cavebot
        if cavebot is not None and move_provider is None and move_action is None:
            move_provider = lambda _s: cavebot.is_active()  # noqa: E731
            move_action = cavebot.step
        self._move_provider = move_provider
        self._move_action = move_action
        # Per-tick module decisions (refreshed by _observe each tick).
        self._alarm: AlarmAction | None = None
        self._target: TargetingAction | None = None
        self._last_target: str | None = None
        self._f12_monitor = f12_monitor
        if self._f12_monitor is None and cfg.enable_f12_monitor:
            self._f12_monitor = F12Monitor(
                on_press=self.kill,
                poll_interval=cfg.f12_poll_interval,
            )

        # Capacity 1 (not rate): with 20 Hz ticks this yields exactly
        # 10 actions/s sustained and never more than 11 in any second.
        self._rate_limiter = RateLimiter(rate=cfg.max_actions_per_second, capacity=1.0, clock=clock)
        self._kill_event = threading.Event()
        self._stop_event = threading.Event()
        self._state = BotState.IDLE

        # Stats (written by the tick thread; read via get_stats()).
        self._tick_count = 0
        self._actions_sent = 0
        self._actions_dropped_rate = 0
        self._actions_dropped_unfocused = 0
        self._tick_time_total = 0.0
        self._tick_time_max = 0.0
        self._state_transitions: dict[BotState, int] = {}
        self._start_time: float | None = None

        # (monotonic_time, hp) samples for the fast-HP-drop alarm.
        self._hp_history: deque[tuple[float, float]] = deque()

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
        tick, which releases all inputs (subject to the focus gate) and
        stops the loop.
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
        """Run the 20 Hz loop until kill, ``stop()``, or !screen_ok.

        Blocks the calling thread. Returns cleanly on every exit path and
        always shuts the F12 monitor down.
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
                self.tick()
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

        if self._perceive is not None:
            self._perceive()
        snap = self.state.snapshot()

        # Stop gate: !screen_ok halts the loop (fail-closed).
        if not snap.screen_ok:
            logger.warning("screen_ok is false -> stopping the loop")
            self._stop_event.set()
            self._record_tick_time(t0)
            return

        self._record_hp(snap)
        self._observe(snap)
        new_state = self._decide_state(snap)
        if new_state is not self._state:
            self._enter_state(new_state, self._transition_reason(snap, new_state))
        self._dispatch(snap)
        self._record_tick_time(t0)

    # -- state machine -----------------------------------------------------

    def _observe(self, snap: WorldState) -> None:
        """Run the per-tick decision modules (alarms, targeting, kill check)."""
        m = self.modules
        alarm = m.alarms.decide(snap) if m.alarms is not None else None
        # Only critical alarms stop the bot; warnings are advisory.
        self._alarm = alarm if alarm is not None and alarm.severity == "critical" else None
        self._target = m.targeting.decide(snap) if m.targeting is not None else None
        if m.looting is not None:
            # ponytail: "selected target vanished from the battle list" = kill
            # (a target lost out of range counts too); use the server-log OCR
            # (perception/logocr.py) if false loot attempts matter.
            prev = self._last_target
            cur = snap.selected_target
            self._last_target = cur.name if cur is not None else None
            if prev and cur is None and all(mon.name != prev for mon in snap.monsters):
                m.looting.on_target_died(prev)

    def _decide_state(self, snap: WorldState) -> BotState:
        """Priority state machine: the first matching condition wins.

        KILL is never returned here -- it is handled in :meth:`tick`
        before the snapshot, since it must win even over a stale state.
        """
        if self._is_alarm(snap):
            return BotState.ALARM
        hp = snap.hp_percent
        if 0.0 < hp <= self.config.critical_hp:
            return BotState.EMERGENCY_HEAL
        if 0.0 < hp <= self.config.normal_hp:
            return BotState.NORMAL_HEAL
        m = self.modules
        if m.targeting is not None:
            if self._target is not None:  # flee was already handled as ALARM
                return BotState.ATTACK
        elif snap.selected_target is not None:
            # Perception only selects targets that are in range and attackable.
            return BotState.ATTACK
        if m.looting is not None and m.looting.should_loot(snap):
            return BotState.LOOT
        if m.refill is not None and (m.refill.is_active() or m.refill.should_refill(snap)):
            return BotState.REFILL
        if self._move_provider is not None and self._move_provider(snap):
            return BotState.MOVE
        return BotState.IDLE

    def _is_alarm(self, snap: WorldState) -> bool:
        """ALARM: player on screen, PM open, fast HP drop, critical module alarm or flee."""
        if snap.players_on_screen > 0 or self._alarm is not None or self._fleeing():
            return True
        if self._pm_open_check is not None and self._pm_open_check(snap):
            return True
        return self._hp_drop_fast(snap)

    def _fleeing(self) -> bool:
        return self._target is not None and self._target.action == "flee"

    def _transition_reason(self, snap: WorldState, new_state: BotState) -> str:
        """Short human-readable reason for a transition (for logs)."""
        match new_state:
            case BotState.ALARM:
                if self._alarm is not None:
                    return f"alarm {self._alarm.type.value}: {self._alarm.message}"
                if self._target is not None and self._fleeing():
                    return f"targeting flee: {self._target.reason}"
                if snap.players_on_screen > 0:
                    return f"players on screen: {snap.players_on_screen}"
                if self._pm_open_check is not None and self._pm_open_check(snap):
                    return "PM window open"
                return "fast HP drop"
            case BotState.EMERGENCY_HEAL:
                return f"hp {snap.hp_percent:.0f}% <= critical {self.config.critical_hp:.0f}%"
            case BotState.NORMAL_HEAL:
                return f"hp {snap.hp_percent:.0f}% <= normal {self.config.normal_hp:.0f}%"
            case BotState.ATTACK:
                if self._target is not None:
                    return f"target: {self._target.target_name} ({self._target.reason})"
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
        if self._state is BotState.KILL:
            self._act_kill()
            return
        # Healing continues through every state (including ALARM).
        if self.modules.healing is not None and self._heal(snap):
            return
        busy = (BotState.ALARM, BotState.EMERGENCY_HEAL, BotState.NORMAL_HEAL)
        if self.modules.buffs is not None and self._state not in busy and self._buff(snap):
            return
        match self._state:
            case BotState.ALARM:
                self._act_alarm(snap)
            case BotState.EMERGENCY_HEAL:
                self._act_heal(snap, self.config.emergency_heal_combo, "emergency")
            case BotState.NORMAL_HEAL:
                self._act_heal(snap, self.config.normal_heal_combo, "normal")
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

    def _send(
        self,
        snap: WorldState,
        action: Callable[[], object],
        *,
        rate_limited: bool = True,
    ) -> bool:
        """Gate (and, when *rate_limited*, rate-limit) one input action.

        Never sends input unless the snapshot has both ``client_focused``
        and ``screen_ok`` True, and never faster than the rate limit.
        The rate-limit bypass is reserved for the kill switch's
        release-all (an emergency release must not wait for a token) and
        the MOVE waypoint step (continuous motion, one per tick).
        An *action* returning ``False`` (a module declined to send) is not
        counted as sent.
        """
        if not (snap.client_focused and snap.screen_ok):
            self._actions_dropped_unfocused += 1
            return False
        if rate_limited and not self._rate_limiter.try_acquire(1.0):
            self._actions_dropped_rate += 1
            return False
        if action() is False:
            return False
        self._actions_sent += 1
        return True

    def _act_kill(self) -> None:
        """KILL: release all inputs (gated; rate-limit bypassed) and stop."""
        snap = self.state.snapshot()
        self._send(snap, self.input.release_all, rate_limited=False)
        self._stop_event.set()

    def _act_alarm(self, snap: WorldState) -> None:
        """ALARM: no attack/move/loot; healing continues.

        (The audible/visual alarm itself is T25's job.)
        """
        hp = snap.hp_percent
        if 0.0 < hp <= self.config.critical_hp:
            self._act_heal(snap, self.config.emergency_heal_combo, "emergency (alarm)")
        elif 0.0 < hp <= self.config.normal_hp:
            self._act_heal(snap, self.config.normal_heal_combo, "normal (alarm)")

    def _heal(self, snap: WorldState) -> bool:
        """Healing module: decide + send; True when a heal was sent."""
        healing = self.modules.healing
        assert healing is not None
        action = healing.decide(snap)
        return action is not None and self._send(snap, lambda: healing.send(action, self.input))

    def _buff(self, snap: WorldState) -> bool:
        """Buff module: send the top-priority due cast; True when sent."""
        buffs = self.modules.buffs
        assert buffs is not None
        actions = buffs.track(snap)
        return bool(actions) and self._send(snap, lambda: buffs.send(actions[0], self.input))

    def _act_heal(self, snap: WorldState, combo: str, label: str) -> None:
        if self.modules.healing is not None:
            return  # the healing module already ran in _dispatch
        if self._send(snap, lambda: self.input.press_combo(combo)):
            logger.debug("heal [%s] combo=%s", label, combo)

    def _act_attack(self, snap: WorldState) -> None:
        """ATTACK: tap the attack key on the selected / chosen target.

        With targeting wired and nothing selected yet, the attack key is
        what selects a target in the client.
        """
        # ponytail: the attack key takes the client's next target, not
        # necessarily targeting's target_name; click the battle-list row
        # once picking a specific monster matters.
        if snap.selected_target is None and self._target is None:
            return  # decided from a stale snapshot; skip this tick
        combo = self.config.attack_combo
        if self._send(snap, lambda: self.input.press_combo(combo)):
            logger.debug("attack combo=%s", combo)

    def _act_loot(self, snap: WorldState) -> None:
        """LOOT: one looting step per tick (rate-limited: it presses keys)."""
        looting = self.modules.looting
        assert looting is not None

        def step() -> None:
            looting.step(snap)

        self._send(snap, step)

    def _act_refill(self, snap: WorldState) -> None:
        """REFILL: start the trip, then one step per tick (gated like MOVE).

        When the trip is done, the cavebot's farming route is resumed; a
        refill alarm leaves the route stopped (fail closed).
        """
        refill = self.modules.refill
        assert refill is not None
        cavebot = self.modules.cavebot

        def step() -> None:
            if not refill.is_active():
                refill.start()
            if not refill.step(snap) and refill.phase == "done" and cavebot is not None:
                cavebot.start()

        self._send(snap, step, rate_limited=False)

    def _act_move(self, snap: WorldState) -> None:
        """MOVE: hand control to the waypoint runner (T22).

        The step is gated (focus/screen_ok) but not rate-limited: the
        runner gets one step per 20 Hz tick and owns its input cadence.
        """
        if self._move_action is None:
            return
        if self._send(snap, self._move_action, rate_limited=False):
            logger.debug("move: waypoint step")

    # -- stats and lifecycle -------------------------------------------------

    def _record_tick_time(self, t0: float) -> None:
        elapsed = self._clock() - t0
        self._tick_time_total += elapsed
        if elapsed > self._tick_time_max:
            self._tick_time_max = elapsed

    def _shutdown(self) -> None:
        if self._f12_monitor is not None:
            self._f12_monitor.stop()
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
        if not 0.0 < cfg.critical_hp < cfg.normal_hp <= 100.0:
            raise ValueError(
                f"expected 0 < critical_hp < normal_hp <= 100, "
                f"got critical={cfg.critical_hp}, normal={cfg.normal_hp}"
            )
        if cfg.hp_drop_alarm_delta <= 0.0 or cfg.hp_drop_alarm_window <= 0.0:
            raise ValueError("hp_drop_alarm_delta and hp_drop_alarm_window must be > 0")

    def get_stats(self) -> dict[str, object]:
        """Performance and safety stats for the UI (T27) and T30 report."""
        uptime = 0.0
        if self._start_time is not None:
            uptime = self._clock() - self._start_time
        ticks = self._tick_count
        return {
            "state": self._state.value,
            "tick_count": ticks,
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
