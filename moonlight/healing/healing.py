"""Healing decisions (T14).

Decides when and how to heal based on :class:`moonlight.core.state.WorldState`:

* **EMERGENCY** (loop priority 3): HP at or below the emergency threshold
  -> cast the emergency spell immediately.
* **CURE**: a condition in :data:`CURES` is active and has a spell ->
  cast that condition's own cure spell.
* **NORMAL** (loop priority 4): HP at or below the normal threshold ->
  cast the normal spell when convenient.
* **POTION**: HP or MP at or below its potion % -> press the potion's
  hotkey (T4.F2.K1). Potions skip the mana gate: an MP potion is for low MP.

Priority within this module (highest wins): emergency > HP potion > cure >
normal > MP potion. Each has its own cooldown key, so while the emergency
spell cools down the HP potion goes next.
KILL/ALARM handling stays in :class:`moonlight.core.loop.GameLoop` (healing
continues in ALARM mode there).

:class:`HealingDecision` is a decision engine plus cooldown bookkeeping;
it never talks to the game itself. The optional input-backend
integration lives in :meth:`HealingDecision.send` and
:meth:`HealingDecision.decide_and_send`: a spell is mapped to a key
combo through the configurable *spell_combos* mapping and pressed
through an :class:`moonlight.hardware.input.InputBackend`.

Conventions inherited from the rest of the pipeline:

* ``hp_percent`` / ``mp_percent`` of 0.0 mean "unknown" (never updated).
  Unknown HP -> no heal; unknown MP -> casting is still allowed (the MP
  gate only blocks on a *known* low MP value).
* ``client_focused`` / ``screen_ok`` gating is the loop's job (it refuses
  to send input without both), so this module does not check them.
* Conditions are read from ``WorldState.buffs`` (name -> seconds
  remaining); perception is the writer and must use lowercase names.
  Matching is case-insensitive anyway.

Everything timing-related is injectable, so the module is fully
unit-testable without hardware or a running client.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from moonlight.core.state import WorldState
from moonlight.hardware.input import InputBackend

__all__ = [
    "CURES",
    "EMERGENCY_COOLDOWN_KEY",
    "HEAL_GAP_MS",
    "NORMAL_COOLDOWN_KEY",
    "HealType",
    "HealingAction",
    "HealingConfig",
    "HealingDecision",
    "cure_cooldown_key",
]

logger = logging.getLogger("moonlight.healing.healing")

#: Cooldown keys for the two heal tiers (stable identifiers; shared with
#: the loop, the UI and the tests).
EMERGENCY_COOLDOWN_KEY = "heal_emergency"
NORMAL_COOLDOWN_KEY = "heal_normal"
# ponytail: guessed. The game seems to drop a spell key sent 20 ms after a potion
# key; raise it if heals still go missing.
HEAL_GAP_MS = 300.0


class HealType(StrEnum):
    """Kind of a healing action (values are the ``HealingAction.type`` tokens)."""

    EMERGENCY = "emergency"
    NORMAL = "normal"
    CURE = "cure"
    POTION = "potion"


def cure_cooldown_key(condition: str) -> str:
    """Cooldown key for curing *condition* (one independent key per condition)."""
    return f"cure:{condition}"


#: Curable conditions (``WorldState.buffs`` names), highest priority first.
CURES = ("paralyze", "poison", "burn", "energy", "curse", "bleed")


@dataclass(frozen=True)
class HealingConfig:
    """Tunable healing parameters (defaults are the T14 spec values)."""

    emergency_hp_percent: float = 30.0
    """HP % at or below which an EMERGENCY heal triggers."""

    normal_hp_percent: float = 70.0
    """HP % at or below which a NORMAL heal triggers (must be > emergency)."""

    emergency_spell: str = "exura gran"
    """Spell spoken for emergency heals; blank = no emergency heal."""

    normal_spell: str = "exura"
    """Spell spoken for normal heals; blank = no normal heal."""

    cure_paralyze: str = ""
    cure_poison: str = ""
    cure_burn: str = ""
    cure_energy: str = ""
    cure_curse: str = ""
    cure_bleed: str = ""
    """Each condition's cure spell (``cure_<condition>``); blank = not cured."""

    heal_cooldown_ms: int = 500
    """Minimum interval in ms between two actions sharing one cooldown key."""

    mana_threshold: float = 20.0
    """No spell is cast while MP is below this % (0.0 = unknown MP passes)."""

    hp_potion: str = ""
    hp_potion_percent: float = 0.0
    mp_potion: str = ""
    mp_potion_percent: float = 0.0
    """Potion name (keyed in ``hotkeys``) and the % to use it at; blank or 0 = off."""

    def validate(self) -> HealingConfig:
        """Check invariants and return self (fail fast, loop-style validation).

        Raises:
            ValueError: If thresholds, cooldown, spells or conditions are invalid.
        """
        if not 0.0 < self.emergency_hp_percent < self.normal_hp_percent <= 100.0:
            raise ValueError(
                "expected 0 < emergency_hp_percent < normal_hp_percent <= 100, "
                f"got emergency={self.emergency_hp_percent}, "
                f"normal={self.normal_hp_percent}"
            )
        if not 0.0 <= self.mana_threshold <= 100.0:
            raise ValueError(
                f"expected 0 <= mana_threshold <= 100, got {self.mana_threshold}"
            )
        for name in ("hp_potion_percent", "mp_potion_percent"):
            if not 0.0 <= getattr(self, name) <= 100.0:
                raise ValueError(f"expected 0 <= {name} <= 100, got {getattr(self, name)}")
        if self.heal_cooldown_ms <= 0:
            raise ValueError("heal_cooldown_ms must be > 0")
        return self

    def cure_spells(self) -> dict[str, str]:
        """Condition -> cure spell, for conditions that have one, in :data:`CURES` order."""
        spells = {c: getattr(self, f"cure_{c}").strip() for c in CURES}
        return {c: s for c, s in spells.items() if s}

    def potions(self) -> dict[str, tuple[str, float]]:
        """``"hp"``/``"mp"`` -> (potion, use at %), for the potions that are on."""
        pots = {
            "hp": (self.hp_potion.strip(), self.hp_potion_percent),
            "mp": (self.mp_potion.strip(), self.mp_potion_percent),
        }
        return {k: v for k, v in pots.items() if v[0] and v[1] > 0}


@dataclass(frozen=True)
class HealingAction:
    """One healing decision: what to cast and why.

    Attributes:
        type: Action kind (``emergency`` / ``normal`` / ``cure``).
        spell: Spell to cast, e.g. ``"exura gran"``.
        reason: Human-readable justification (logging / UI).
        cooldown_key: Key used for cooldown tracking (see
            :data:`EMERGENCY_COOLDOWN_KEY`, :data:`NORMAL_COOLDOWN_KEY`,
            :func:`cure_cooldown_key`).
    """

    type: HealType
    spell: str
    reason: str
    cooldown_key: str


class HealingDecision:
    """Decides when to heal and tracks heal cooldowns.

    Typical usage (wired into the loop or the UI)::

        healer = HealingDecision(
            HealingConfig(),
            spell_combos={"exura gran": "f1", "exura": "f2"},
        )
        action = healer.decide(state.snapshot())   # HealingAction | None
        if action is not None:
            healer.send(action, input_backend)

    ``decide()`` is pure with respect to the game: it only reads the
    (snapshot of) :class:`WorldState` and returns a decision. Cooldowns are
    recorded when an action is actually sent via :meth:`send`.

    Thread-safety contract: a single decision thread (the 20 Hz loop)
    drives this class; there is no internal locking (same as GameLoop).
    """

    def __init__(
        self,
        config: HealingConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
        spell_combos: Mapping[str, str] | None = None,
    ) -> None:
        """Create the healer.

        Args:
            config: Healing parameters; defaults to :class:`HealingConfig`.
            clock: Monotonic-clock override (tests).
            spell_combos: Spell -> key-combo mapping used by :meth:`send`.
                Combos must be non-empty and contain no whitespace (wire
                protocol is one command per line). An empty mapping is fine:
                decisions still work, :meth:`send` just declines to cast.

        Raises:
            ValueError: If the config or a combo mapping is invalid.
        """
        cfg = config if config is not None else HealingConfig()
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

    def decide(self, state: WorldState) -> HealingAction | None:
        """Decide whether to heal, given a (snapshot of) the world state.

        Priority: emergency heal > cure > normal heal. The heal tiers are
        mutually exclusive: at or below the emergency threshold only the
        emergency spell is considered (never the weaker normal spell);
        between the thresholds only the normal spell. Candidates that are
        still on cooldown are skipped in favour of the next eligible one.

        Returns:
            A :class:`HealingAction`, or ``None`` when nothing should be
            cast (HP healthy, HP unknown, MP known to be too low, or every
            eligible candidate is on cooldown).
        """
        mp = state.mp_percent
        hp = state.hp_percent
        potions = self._potion_candidates(hp, mp)
        if mp > 0.0 and mp < self.config.mana_threshold:
            logger.debug(
                "spells skipped: mp %.1f%% below threshold %.1f%%",
                mp,
                self.config.mana_threshold,
            )
            candidates = list(potions.values())
        else:
            candidates = self._spell_candidates(state)
            if "hp" in potions:
                after_emergency = bool(candidates) and candidates[0].type == HealType.EMERGENCY
                candidates.insert(int(after_emergency), potions["hp"])
            if "mp" in potions:
                candidates.append(potions["mp"])

        last = max(self._last_sent.values(), default=None)
        if candidates and last is not None and (self._clock() - last) * 1000.0 < HEAL_GAP_MS:
            return None
        for action in candidates:
            if not self.on_cooldown(action.cooldown_key):
                return action
        if candidates:
            logger.debug(
                "heal candidates on cooldown: %s",
                [action.cooldown_key for action in candidates],
            )
        return None

    def _potion_candidates(self, hp: float, mp: float) -> dict[str, HealingAction]:
        """Potions whose bar is known and at or below its %, by ``"hp"``/``"mp"``."""
        bars = {"hp": hp, "mp": mp}
        return {
            kind: HealingAction(
                type=HealType.POTION,
                spell=potion,
                reason=f"{kind} {bars[kind]:.0f}% <= potion threshold {percent:.0f}%",
                cooldown_key=f"potion:{kind}",
            )
            for kind, (potion, percent) in self.config.potions().items()
            if 0.0 < bars[kind] <= percent
        }

    def _spell_candidates(self, state: WorldState) -> list[HealingAction]:
        """Emergency heal, cures and normal heal that are due, in priority order."""
        candidates: list[HealingAction] = []
        hp = state.hp_percent
        # A blank spell is off; the normal heal then covers the emergency range.
        emergency_due = (
            bool(self.config.emergency_spell.strip())
            and 0.0 < hp <= self.config.emergency_hp_percent
        )
        if emergency_due:
            candidates.append(
                HealingAction(
                    type=HealType.EMERGENCY,
                    spell=self.config.emergency_spell,
                    reason=(
                        f"hp {hp:.0f}% <= emergency threshold "
                        f"{self.config.emergency_hp_percent:.0f}%"
                    ),
                    cooldown_key=EMERGENCY_COOLDOWN_KEY,
                )
            )
        candidates.extend(self._cure_candidates(state))
        if (
            not emergency_due
            and self.config.normal_spell.strip()
            and 0.0 < hp <= self.config.normal_hp_percent
        ):
            candidates.append(
                HealingAction(
                    type=HealType.NORMAL,
                    spell=self.config.normal_spell,
                    reason=(
                        f"hp {hp:.0f}% <= normal threshold "
                        f"{self.config.normal_hp_percent:.0f}%"
                    ),
                    cooldown_key=NORMAL_COOLDOWN_KEY,
                )
            )
        return candidates

    def _cure_candidates(self, state: WorldState) -> list[HealingAction]:
        """Cure actions for active conditions, in config priority order."""
        actions: list[HealingAction] = []
        for condition, spell in self.config.cure_spells().items():
            remaining = self._condition_remaining(state, condition)
            if remaining is None:
                continue
            actions.append(
                HealingAction(
                    type=HealType.CURE,
                    spell=spell,
                    reason=(
                        f"active condition: {condition} "
                        f"({remaining:.1f}s remaining)"
                    ),
                    cooldown_key=cure_cooldown_key(condition),
                )
            )
        return actions

    @staticmethod
    def _condition_remaining(state: WorldState, condition: str) -> float | None:
        """Seconds remaining for *condition* in ``state.buffs`` (or None)."""
        target = condition.lower()
        for name, seconds in state.buffs.items():
            if name.lower() == target:
                return seconds
        return None

    # -- cooldowns ------------------------------------------------------------

    def on_cooldown(self, key: str) -> bool:
        """True while *key* was sent less than ``heal_cooldown_ms`` ago."""
        last = self._last_sent.get(key)
        if last is None:
            return False
        return (self._clock() - last) * 1000.0 < self.config.heal_cooldown_ms

    def cooldown_remaining_ms(self, key: str) -> float:
        """Milliseconds until *key* can be sent again (0.0 when ready)."""
        last = self._last_sent.get(key)
        if last is None:
            return 0.0
        cooldown_s = self.config.heal_cooldown_ms / 1000.0
        return max(0.0, (last + cooldown_s - self._clock()) * 1000.0)

    def reset_cooldowns(self) -> None:
        """Clear all cooldown bookkeeping (tests, UI reset button)."""
        self._last_sent.clear()

    # -- input integration ------------------------------------------------------

    def send(self, action: HealingAction, backend: InputBackend) -> bool:
        """Send *action* through the input backend (spell -> combo -> press).

        The spell is looked up in the *spell_combos* mapping and pressed
        with ``backend.press_combo``. On success the action's cooldown key
        is recorded.

        Args:
            action: The healing decision to send.
            backend: The output path to the game.

        Returns:
            True if the combo was sent; False if the spell has no combo
            mapping or the cooldown key is still active.
        """
        combo = self._spell_combos.get(action.spell)
        if combo is None:
            logger.debug("no combo mapped for spell %r; not sent", action.spell)
            return False
        if self.on_cooldown(action.cooldown_key):
            logger.debug("cooldown active for %s; not sent", action.cooldown_key)
            return False
        backend.press_combo(combo)
        self._record_sent(action.cooldown_key)
        logger.info("heal sent: %s (combo %s) -- %s", action.spell, combo, action.reason)
        return True

    def decide_and_send(self, state: WorldState, backend: InputBackend) -> HealingAction | None:
        """:meth:`decide` + :meth:`send` in one step.

        Returns the decision (or ``None``); the backend is only used when a
        decision exists. The caller (loop) still owns the focus gate and the
        rate limit.
        """
        action = self.decide(state)
        if action is not None:
            self.send(action, backend)
        return action

    def _record_sent(self, key: str) -> None:
        self._last_sent[key] = self._clock()
