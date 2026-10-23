"""Refill route (T24): walk to storage when cap is full, deposit, walk back.

The refill route is an ordinary waypoint file,
``data/waypoints/<route_name>.json`` (format: ``data/waypoints/README.md``),
with two labels:

* ``storage``: placed right after the waypoint where the storage is reached;
* ``farming``: placed after the waypoint back at the farming spot (later
  in the file than ``storage``).

Anything after ``farming`` (e.g. a ``goto`` that loops the route) is
ignored: a refill is one trip. The route is split into two legs at those
labels and each leg is run to completion by the existing :class:`Cavebot`
(its route is swapped in for the trip and restored afterwards):

1. ``TRAVEL_TO_STORAGE``: ``cavebot.step()`` until the first leg ends.
2. ``DEPOSIT``: press ``storage_hotkey`` (open), then ``deposit_hotkey``
   every :data:`DEPOSIT_RETRY_TICKS` until the used cap is ``<= stop_cap``,
   then ``storage_hotkey`` again (close). No cap drop within
   :data:`DEPOSIT_TIMEOUT_TICKS` -> close and stop with alarm
   ``"deposit_failed"``.
3. ``TRAVEL_BACK``: ``cavebot.step()`` until the second leg ends.
4. ``DONE``: cavebot stopped, the previous route restored; the caller
   resumes it (``cavebot.start()``) and its attacking.

A cavebot alarm (``stuck``/``lost``) mid-trip stops the refill and is
copied to :attr:`Refill.alarm`; while an alarm is set ``should_refill`` is
False (fail closed, no endless retries) until the next :meth:`Refill.start`.

Refill is an add-on: it does not touch ``loop.py``; the loop calls
``should_refill`` / ``start`` / ``step`` (e.g. between ATTACK and MOVE).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from moonlight.bot.state import WorldState
from moonlight.decision.cavebot import Cavebot, Route, RouteError, RouteFileLoader
from moonlight.decision.looting import Looting
from moonlight.hardware.input import InputBackend

__all__ = [
    "DEPOSIT_RETRY_TICKS",
    "DEPOSIT_TIMEOUT_TICKS",
    "Refill",
    "RefillConfig",
    "RefillPhase",
]

logger = logging.getLogger(__name__)

#: Ticks between deposit hotkey presses (1 s @ 20 Hz).
DEPOSIT_RETRY_TICKS = 20
#: Ticks to wait for the cap to drop before giving up (5 s @ 20 Hz).
DEPOSIT_TIMEOUT_TICKS = 100


class RefillPhase(StrEnum):
    IDLE = "idle"
    TRAVEL_TO_STORAGE = "travel_to_storage"
    DEPOSIT = "deposit"
    TRAVEL_BACK = "travel_back"
    DONE = "done"


@dataclass(frozen=True)
class RefillConfig:
    trigger_cap: float = 90.0
    stop_cap: float = 10.0
    route_name: str = "refill"
    storage_hotkey: str = "F8"
    deposit_hotkey: str | None = None
    routes_dir: Path = Path("data/waypoints")


class Refill:
    """Drive the cavebot through a refill trip, one tick at a time."""

    def __init__(
        self,
        config: RefillConfig,
        cavebot: Cavebot,
        route_loader: RouteFileLoader,
        looting: Looting,
        state: WorldState,
        input: InputBackend,
    ) -> None:
        self._config = config
        self._cavebot = cavebot
        self._looting = looting
        self._state = state
        self._input = input
        #: Why the last refill stopped early ("stuck", "deposit_failed", ...).
        self.alarm: str | None = None
        self._phase = RefillPhase.IDLE
        self._previous: Route | None = None
        self._ticks = 0
        self._legs: tuple[Route, Route] | None = None
        path = config.routes_dir / f"{config.route_name}.json"
        try:
            self._legs = _split(route_loader.load(path))
        except RouteError as exc:
            logger.warning("refill: no usable route %s: %s", path, exc)

    @property
    def phase(self) -> RefillPhase:
        return self._phase

    def is_active(self) -> bool:
        """True while a refill trip is in progress."""
        return self._phase not in (RefillPhase.IDLE, RefillPhase.DONE)

    def should_refill(self, snap: WorldState) -> bool:
        """True when cap is at the trigger and a refill can start."""
        return (
            self._legs is not None
            and self.alarm is None
            and not self.is_active()
            and self._looting.pending is None  # finish the last corpse first
            and snap.capacity.max_weight > 0
            and snap.capacity.percent >= self._config.trigger_cap
        )

    def start(self) -> None:
        """Swap the refill route into the cavebot and head to storage."""
        if self._legs is None:
            logger.warning("refill: start() without a usable route")
            return
        self.alarm = None
        if not self.is_active():
            self._previous = self._cavebot.route
        self._run_leg(self._legs[0], RefillPhase.TRAVEL_TO_STORAGE)

    def stop(self) -> None:
        """Abort / finish: stop the cavebot, restore its route, go IDLE."""
        self._finish(RefillPhase.IDLE)

    def step(self, snap: WorldState) -> bool:
        """Run one refill tick; True while the refill is still in progress."""
        if self._phase in (RefillPhase.TRAVEL_TO_STORAGE, RefillPhase.TRAVEL_BACK):
            self._cavebot.step()
            if self._cavebot.is_active():
                return True
            if self._cavebot.alarm is not None:
                return self._fail(self._cavebot.alarm)
            if self._phase is RefillPhase.TRAVEL_BACK:
                self._finish(RefillPhase.DONE)
                return False
            self._phase = RefillPhase.DEPOSIT
            self._ticks = 0
            return True
        if self._phase is RefillPhase.DEPOSIT:
            return self._deposit(snap)
        return False

    # -- internals ----------------------------------------------------------

    def _deposit(self, snap: WorldState) -> bool:
        if not (snap.client_focused and snap.screen_ok):
            return True  # no input now; retry next tick
        cfg = self._config
        cap = snap.capacity
        if self._ticks == 0:
            self._input.press_combo(cfg.storage_hotkey)  # open
        elif cap.max_weight > 0 and cap.percent <= cfg.stop_cap:
            self._input.press_combo(cfg.storage_hotkey)  # close
            assert self._legs is not None  # active implies loaded
            self._run_leg(self._legs[1], RefillPhase.TRAVEL_BACK)
            return True
        elif self._ticks >= DEPOSIT_TIMEOUT_TICKS:
            self._input.press_combo(cfg.storage_hotkey)  # close
            return self._fail("deposit_failed")
        elif cfg.deposit_hotkey and self._ticks % DEPOSIT_RETRY_TICKS == 1:
            # ponytail: deposit needs a client "deposit all" hotkey; per-item
            # drags need a backpack reader and a mouse-hold input primitive.
            self._input.press_combo(cfg.deposit_hotkey)
        self._ticks += 1
        return True

    def _run_leg(self, leg: Route, phase: RefillPhase) -> None:
        self._cavebot.stop()
        self._cavebot.route = leg
        self._cavebot.start()
        self._phase = phase

    def _fail(self, alarm: str) -> bool:
        logger.warning("refill: stopped: %s", alarm)
        self.alarm = alarm
        self._finish(RefillPhase.IDLE)
        return False

    def _finish(self, phase: RefillPhase) -> None:
        self._cavebot.stop()
        if self._previous is not None:
            self._cavebot.route = self._previous
            self._previous = None
        self._phase = phase


def _split(route: Route) -> tuple[Route, Route]:
    """Split *route* into (to storage, back to farming) legs at the labels."""
    wps = route.waypoints
    labels = {wp.label: i for i, wp in enumerate(wps) if wp.action == "label"}
    if "storage" not in labels or "farming" not in labels:
        raise RouteError("refill route needs 'storage' and 'farming' labels")
    s, f = labels["storage"], labels["farming"]
    if f < s:
        raise RouteError("'farming' label must come after 'storage'")
    legs = (wps[: s + 1], wps[s + 1 : f + 1])
    for leg in legs:
        names = {wp.label for wp in leg if wp.action == "label"}
        for wp in leg:
            if wp.action == "goto" and wp.label not in names:
                raise RouteError(f"goto {wp.label!r} crosses the storage label")
    return (
        Route(name=f"{route.name}:to_storage", waypoints=list(legs[0]), loop=False),
        Route(name=f"{route.name}:back", waypoints=list(legs[1]), loop=False),
    )
