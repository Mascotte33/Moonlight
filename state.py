"""World state container (T11).

Holds everything the decision layer needs to know about the game at one
moment in time:

* safety flags: ``client_focused``, ``screen_ok``
* vitals: ``hp_percent``, ``mp_percent``
* combat: ``monsters``, ``selected_target``, ``players_on_screen``
* world: ``position``, ``capacity``
* buffs: name -> seconds remaining
* bookkeeping: ``last_update``

``screen_ok`` (per T03 spec) is ``client_focused`` AND all calibrated
regions detected. The perception layer computes it; the loop refuses to
send input unless both flags are set.

Thread-safety contract
----------------------
The capture/perception thread is the single writer; the decision thread
(and the UI) are readers.

* Writers mutate fields only inside ``with state.lock():`` and should
  update a full frame of fields in one such block.
* Readers never touch fields directly: they call ``state.snapshot()``
  to obtain a consistent, isolated copy that is safe to read lock-free.

``last_update`` uses ``time.monotonic()``; ``0.0`` means "never updated"
and the decision layer must treat it as stale.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Self

__all__ = [
    "Capacity",
    "Monster",
    "Position",
    "WorldState",
]


@dataclass(frozen=True)
class Monster:
    """One entry in the client's battle list (bottom-right corner).

    Frozen so a snapshot's monsters cannot be mutated behind the writer's
    back; update the list by replacing entries, not editing them.
    """

    name: str
    hp_percent: float = 100.0
    is_player: bool = False


@dataclass(frozen=True)
class Position:
    """Tibia tile coordinates; ``(0, 0, 0)`` means "not yet known"."""

    x: int
    y: int
    z: int = 0


@dataclass(frozen=True)
class Capacity:
    """Inventory capacity (the "Cap." bar).

    ``percent`` is derived from ``weight``/``max_weight`` instead of being
    stored, so it can never go stale; it is 0.0 while the cap is unknown.
    """

    weight: float = 0.0
    max_weight: float = 0.0

    @property
    def percent(self) -> float:
        if self.max_weight <= 0.0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * self.weight / self.max_weight))


@dataclass
class WorldState:
    """The bot's complete view of the game at one moment.

    All fields fail closed: a default state looks like "nothing known and
    the window is not focused", so acting on it can never cause input.

    See the module docstring for the writer/reader threading contract.
    """

    # Safety flags (the loop must not send input unless both are True).
    client_focused: bool = False
    screen_ok: bool = False

    # Vitals, in percent (only meaningful while ``screen_ok``).
    hp_percent: float = 0.0
    mp_percent: float = 0.0

    # Combat.
    monsters: list[Monster] = field(default_factory=list)
    selected_target: Monster | None = None
    players_on_screen: int = 0

    # World.
    position: Position = field(default_factory=lambda: Position(0, 0, 0))
    capacity: Capacity = field(default_factory=Capacity)

    # Active buffs: name -> seconds remaining.
    buffs: dict[str, float] = field(default_factory=dict)

    # Last full update (``time.monotonic()``); 0.0 = never updated.
    last_update: float = 0.0

    # Writer/reader lock; not part of the state, hidden from repr/eq.
    _lock: threading.RLock = field(
        init=False,
        repr=False,
        compare=False,
        default_factory=threading.RLock,
    )

    @classmethod
    def default(cls) -> Self:
        """Fresh empty state: nothing focused, nothing seen, never updated."""
        return cls()

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Writer lock: mutate fields only inside ``with state.lock():``."""
        with self._lock:
            yield

    def snapshot(self) -> Self:
        """Consistent, isolated copy for readers (safe to use lock-free).

        The copy shares no mutable container with the live state and owns
        its own lock, so it can be read and passed around while the writer
        is updating.
        """
        with self._lock:
            clone = replace(self)
            clone.monsters = list(self.monsters)
            clone.buffs = dict(self.buffs)
            return clone
