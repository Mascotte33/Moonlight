"""Buff decisions (T26).

Decides which buffs to recast based on :class:`moonlight.state.WorldState`:

* **MISSING** (reason ``"missing"``): the configured buff is not present in
  ``WorldState.buffs`` -> cast it.
* **EXPIRING_SOON** (reason ``"expiring_soon"``): the buff is present but
  its remaining time is at or below ``expiring_warning_ms`` -> recast it
  early so the effect never lapses.

:method:`BuffManager.track` returns every cast that is due, ordered by
priority (lower ``BuffConfig.priority`` = more important; equal priorities
keep the configured list order). The loop sends them one per tick (gated on
focus/screen_ok and rate-limited). Cooldown bookkeeping uses one key per
buff (``buff:<name>``); it is recorded when a cast is actually sent via
:meth:`send`, and :meth:`track` omits candidates still on cooldown so the
bot never spams a recast.

Optional buffs (``required=False``) are a second tier: they are only
considered while **all required buffs are active** in the world state.
That keeps the bot's spell budget on the essentials first; an optional buff
is never the reason to skip a missing required one.

Conventions inherited from the rest of the pipeline:

* Buff names are read from ``WorldState.buffs`` (name -> seconds
  remaining); perception is the writer and must use lowercase names.
  Matching is case-insensitive anyway.
* ``client_focused`` / ``screen_ok`` gating is the loop's job (it refuses
  to send input without both), so this module does not check them.
* The optional input-backend integration lives in :meth:`BuffManager.send`
  and :meth:`BuffManager.track_and_send`: a spell is mapped to a key combo
  through the configurable *spell_combos* mapping and pressed through an
  :class:`moonlight.input.InputBackend`.

Everything timing-related is injectable, so the module is fully
unit-testable without hardware or a running client.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from moonlight.input import InputBackend
from moonlight.state import WorldState

__all__ = [
    "DEFAULT_BUFFS",
    "REASON_EXPIRING_SOON",
    "REASON_MISSING",
    "BuffAction",
    "BuffConfig",
    "BuffManager",
    "BuffManagerConfig",
    "buff_cooldown_key",
]

logger = logging.getLogger("moonlight.decision.buffs")

#: Reason tokens for :attr:`BuffAction.reason` (stable identifiers; shared
#: with the loop, the UI and the tests).
REASON_MISSING = "missing"
REASON_EXPIRING_SOON = "expiring_soon"


def buff_cooldown_key(buff_name: str) -> str:
    """Cooldown key for *buff_name* (one independent key per buff)."""
    return f"buff:{buff_name.lower()}"


@dataclass(frozen=True)
class BuffConfig:
    """One configured buff: the spell that grants it and how important it is.

    Attributes:
        buff_name: Name as tracked in ``WorldState.buffs`` (lowercase, no
            whitespace; matching is case-insensitive anyway).
        spell: Spell to cast to (re)apply the buff, e.g. ``"utego"``.
        priority: Priority rank within this module: lower = more important.
            :meth:`BuffManager.track` orders its result by this field.
        required: False marks the buff *optional*: it is only recast while
            every required buff is active (see the module docstring).
    """

    buff_name: str
    spell: str
    priority: int
    required: bool = True


@dataclass(frozen=True)
class BuffAction:
    """One buff decision: what to cast and why.

    Attributes:
        spell: Spell to cast, e.g. ``"exori flamatio"``.
        buff_name: The buff this spell provides (config identity).
        reason: :data:`REASON_MISSING` or :data:`REASON_EXPIRING_SOON`.
        priority: Priority rank (lower = more important).
    """

    spell: str
    buff_name: str
    reason: str
    priority: int


#: Common Tibia buffs, highest priority first: haste, then the four
#: elemental/holy protections. Used as the default for
#: :attr:`BuffManagerConfig.buffs`.
DEFAULT_BUFFS: tuple[BuffConfig, ...] = (
    BuffConfig(buff_name="speed", spell="utemo", priority=10),
    BuffConfig(buff_name="fire_protection", spell="exori flamatio", priority=20),
    BuffConfig(buff_name="energy_protection", spell="exori visatio", priority=30),
    BuffConfig(buff_name="earth_protection", spell="exori terratio", priority=40),
    BuffConfig(buff_name="holy_protection", spell="exori sanatio", priority=50),
)


@dataclass(frozen=True)
class BuffManagerConfig:
    """Tunable buff parameters (defaults are the T26 spec values)."""

    buffs: list[BuffConfig] = field(default_factory=lambda: list(DEFAULT_BUFFS))
    """Configured buffs; :meth:`BuffManager.track` orders them by priority."""

    expiring_warning_ms: int = 5000
    """Recast a buff once its remaining time is at or below this (ms)."""

    cast_cooldown_ms: int = 1000
    """Minimum interval in ms between two casts of the same buff."""

    def validate(self) -> BuffManagerConfig:
        """Check invariants and return self (fail fast, loop-style validation).

        Raises:
            ValueError: If cooldowns, buff names, spells or duplicates are invalid.
        """
        if self.cast_cooldown_ms <= 0:
            raise ValueError("cast_cooldown_ms must be > 0")
        if self.expiring_warning_ms < 0:
            raise ValueError("expiring_warning_ms must be >= 0")
        seen: set[str] = set()
        for entry in self.buffs:
            if not entry.buff_name or any(ch.isspace() for ch in entry.buff_name):
                raise ValueError(
                    "buff_name must be non-empty without whitespace, "
                    f"got {entry.buff_name!r}"
                )
            if not entry.spell.strip():
                raise ValueError(
                    f"spell for buff {entry.buff_name!r} must be non-empty"
                )
            key = entry.buff_name.lower()
            if key in seen:
                raise ValueError(f"duplicate buff_name: {entry.buff_name}")
            seen.add(key)
        return self


class BuffManager:
    """Tracks buff expiry and decides which buffs to recast.

    Typical usage (wired into the loop)::

        manager = BuffManager(
            BuffManagerConfig(),
            spell_combos={"utemo": "f3", "exori flamatio": "f4"},
        )
        for action in manager.track(state.snapshot()):
            # loop sends one per tick (rate-limited, gated on focus):
            #   manager.send(action, input_backend)

    ``track()`` is pure with respect to the game: it only reads the
    (snapshot of) :class:`WorldState`. Cooldowns are recorded when a cast is
    actually sent via :meth:`send`.

    Thread-safety contract: a single decision thread (the 20 Hz loop)
    drives this class; there is no internal locking (same as GameLoop).
    """

    def __init__(
        self,
        config: BuffManagerConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
        spell_combos: Mapping[str, str] | None = None,
    ) -> None:
        """Create the buff manager.

        Args:
            config: Buff parameters; defaults to :class:`BuffManagerConfig`.
            clock: Monotonic-clock override (tests).
            spell_combos: Spell -> key-combo mapping used by :meth:`send`.
                Combos must be non-empty and contain no whitespace (wire
                protocol is one command per line). An empty mapping is fine:
                tracking still works, :meth:`send` just declines to cast.

        Raises:
            ValueError: If the config or a combo mapping is invalid.
        """
        cfg = config if config is not None else BuffManagerConfig()
        cfg.validate()
        self.config = cfg
        self._clock = clock if clock is not None else time.monotonic
        self._spell_combos = dict(spell_combos) if spell_combos else {}
        for spell, combo in self._spell_combos.items():
            if not combo or any(ch.isspace() for ch in combo):
                raise ValueError(
                    f"combo for spell {spell!r} must be non-empty and contain no "
                    f"whitespace (wire protocol is one command per line), got {combo!r}"
                )
        self._last_sent: dict[str, float] = {}

    # -- decision ------------------------------------------------------------

    def track(self, state: WorldState) -> list[BuffAction]:
        """Return all buffs that should be cast, ordered by priority.

        A buff needs a cast when:

        * it is absent from ``state.buffs`` (reason ``"missing"``), or
        * its remaining time is at or below ``expiring_warning_ms``
          (reason ``"expiring_soon"``).

        Optional buffs (``required=False``) are only considered while every
        required buff is active in *state*; candidates still on cooldown are
        omitted. The result is sorted by ``priority`` (lower = more
        important); equal priorities keep the configured list order.

        Args:
            state: A (snapshot of) the world state.

        Returns:
            The casts that are due, highest priority first; empty when
            everything is covered and nothing is on cooldown.
        """
        remaining = self._buff_remaining(state)
        required_active = all(
            entry.buff_name.lower() in remaining for entry in self.config.buffs
            if entry.required
        )
        actions: list[BuffAction] = []
        for entry in sorted(self.config.buffs, key=lambda e: e.priority):
            if not entry.required and not required_active:
                continue
            key = buff_cooldown_key(entry.buff_name)
            if self.on_cooldown(key):
                logger.debug("buff %s on cooldown; not offered", entry.buff_name)
                continue
            seconds = remaining.get(entry.buff_name.lower())
            if seconds is None:
                actions.append(
                    BuffAction(
                        spell=entry.spell,
                        buff_name=entry.buff_name,
                        reason=REASON_MISSING,
                        priority=entry.priority,
                    )
                )
            elif seconds * 1000.0 <= self.config.expiring_warning_ms:
                actions.append(
                    BuffAction(
                        spell=entry.spell,
                        buff_name=entry.buff_name,
                        reason=REASON_EXPIRING_SOON,
                        priority=entry.priority,
                    )
                )
        return actions

    @staticmethod
    def _buff_remaining(state: WorldState) -> dict[str, float]:
        """Seconds remaining per buff, keyed by lowercase name."""
        return {name.lower(): seconds for name, seconds in state.buffs.items()}

    # -- cooldowns ------------------------------------------------------------

    def on_cooldown(self, key: str) -> bool:
        """True while *key* was cast less than ``cast_cooldown_ms`` ago."""
        last = self._last_sent.get(key)
        if last is None:
            return False
        return (self._clock() - last) * 1000.0 < self.config.cast_cooldown_ms

    def cooldown_remaining_ms(self, key: str) -> float:
        """Milliseconds until *key* can be cast again (0.0 when ready)."""
        last = self._last_sent.get(key)
        if last is None:
            return 0.0
        cooldown_s = self.config.cast_cooldown_ms / 1000.0
        return max(0.0, (last + cooldown_s - self._clock()) * 1000.0)

    def reset_cooldowns(self) -> None:
        """Clear all cooldown bookkeeping (tests, UI reset button)."""
        self._last_sent.clear()

    # -- input integration ------------------------------------------------------

    def send(self, action: BuffAction, backend: InputBackend) -> bool:
        """Send *action* through the input backend (spell -> combo -> press).

        The spell is looked up in the *spell_combos* mapping and pressed
        with ``backend.press_combo``. On success the buff's cooldown key is
        recorded.

        Args:
            action: The buff decision to send.
            backend: The output path to the game.

        Returns:
            True if the combo was sent; False if the spell has no combo
            mapping or the buff's cooldown key is still active.
        """
        combo = self._spell_combos.get(action.spell)
        if combo is None:
            logger.debug("no combo mapped for spell %r; not sent", action.spell)
            return False
        key = buff_cooldown_key(action.buff_name)
        if self.on_cooldown(key):
            logger.debug("cooldown active for %s; not sent", key)
            return False
        backend.press_combo(combo)
        self._last_sent[key] = self._clock()
        logger.info(
            "buff cast: %s (combo %s) -- %s", action.spell, combo, action.reason
        )
        return True

    def track_and_send(self, state: WorldState, backend: InputBackend) -> BuffAction | None:
        """:meth:`track` + :meth:`send` of the highest-priority cast.

        Returns the decision (or ``None`` when nothing is due); the backend
        is only used when a decision exists. The caller (loop) still owns
        the focus gate and the rate limit.
        """
        actions = self.track(state)
        if not actions:
            return None
        action = actions[0]
        self.send(action, backend)
        return action
