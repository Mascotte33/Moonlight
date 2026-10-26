"""Healing rules and cures: which action-bar hotkeys to press this tick.

# doc: docs/prs/healing.md#healing-rules

:class:`HealingDecision` checks the owner's ordered :class:`HealingRule` list
and the :class:`CureRule` per condition, and returns at most two
:class:`HealingAction` per tick: one for the spell slot, one for the potion
slot. It never names a spell or a potion, only hotkeys.

``hp_percent`` / ``mp_percent`` of 0.0 mean "unknown": an unknown bar never
triggers a rule, and an unknown MP reading passes the mana floor. The
focus and rate gates are the loop's job. The clock is injectable, so the
module is unit-testable without hardware.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from moonlight.core.state import WorldState
from moonlight.hardware.input import InputBackend

__all__ = [
    "CONDITIONS",
    "CureRule",
    "HealingAction",
    "HealingConfig",
    "HealingDecision",
    "HealingRule",
]

logger = logging.getLogger("moonlight.healing.healing")

# doc: docs/prs/healing.md#cures
#: Curable conditions (``WorldState.active_status_icons`` names), cure priority order.
CONDITIONS = ("poison", "burn", "electrify", "bleeding", "paralyse")

_SLOTS = ("spell", "potion")


@dataclass(frozen=True)
class HealingRule:
    """One healing rule: press *hotkey* when HP or MP is at or below its trigger.

    A trigger of 0 is unused; ``minimum_mana_percent`` of 0 is no floor.
    """

    kind: str
    """``"spell"`` or ``"potion"``: the slot (and cooldown) the key uses."""
    hotkey: str
    hp_below_percent: float = 0.0
    mp_below_percent: float = 0.0
    minimum_mana_percent: float = 0.0


@dataclass(frozen=True)
class CureRule:
    """Press *hotkey* (spell slot) while *condition*'s status icon shows."""

    condition: str
    enabled: bool = False
    hotkey: str = ""


_DEFAULT_RULES = (
    HealingRule("spell", "F2", hp_below_percent=50.0),
    HealingRule("spell", "F1", hp_below_percent=85.0),
    HealingRule("potion", "F3", hp_below_percent=40.0),
    HealingRule("potion", "F4", mp_below_percent=30.0),
)
_DEFAULT_CURES = tuple(
    CureRule(condition, False, f"shift+F{number}")
    for number, condition in enumerate(CONDITIONS, start=1)
)


def check_hotkey(hotkey: str, where: str) -> None:
    if not hotkey or any(ch.isspace() for ch in hotkey):
        raise ValueError(f"{where}: hotkey must be a combo without spaces, got {hotkey!r}")


@dataclass(frozen=True)
class HealingConfig:
    """Healing rules (checked top to bottom), cures and the two slot cooldowns."""

    rules: tuple[HealingRule, ...] = _DEFAULT_RULES
    cures: tuple[CureRule, ...] = _DEFAULT_CURES
    spell_cooldown_ms: int = 1000
    potion_cooldown_ms: int = 1000

    def validate(self) -> HealingConfig:
        """Check every rule, cure and cooldown; return self.

        Raises:
            ValueError: On the first invalid rule, cure or cooldown.
        """
        for number, rule in enumerate(self.rules, start=1):
            where = f"healing rule {number}"
            if rule.kind not in _SLOTS:
                raise ValueError(f"{where}: kind must be 'spell' or 'potion', got {rule.kind!r}")
            check_hotkey(rule.hotkey, where)
            for name in ("hp_below_percent", "mp_below_percent", "minimum_mana_percent"):
                percent = getattr(rule, name)
                if not 0.0 <= percent <= 100.0:
                    raise ValueError(f"{where}: {name} must be 0..100, got {percent}")
            if rule.hp_below_percent == 0.0 and rule.mp_below_percent == 0.0:
                raise ValueError(f"{where}: needs hp_below_percent or mp_below_percent")
        seen: set[str] = set()
        for cure in self.cures:
            if cure.condition not in CONDITIONS:
                raise ValueError(f"cure: unknown condition {cure.condition!r}")
            if cure.condition in seen:
                raise ValueError(f"cure: condition {cure.condition!r} is listed twice")
            seen.add(cure.condition)
            check_hotkey(cure.hotkey, f"cure {cure.condition}")
        if self.spell_cooldown_ms <= 0 or self.potion_cooldown_ms <= 0:
            raise ValueError("spell_cooldown_ms and potion_cooldown_ms must be > 0")
        return self


@dataclass(frozen=True)
class HealingAction:
    """One key to press: its slot (``"spell"`` / ``"potion"``), hotkey and why."""

    slot: str
    hotkey: str
    reason: str


def _rule_reason(rule: HealingRule, hp: float, mp: float) -> str | None:
    """Why *rule* is due at *hp* / *mp*, or None when it is not."""
    floor = rule.minimum_mana_percent
    if 0.0 < mp < floor:
        return None
    if 0.0 < hp <= rule.hp_below_percent:
        return f"hp {hp:.0f}% <= {rule.hp_below_percent:.0f}%"
    if 0.0 < mp <= rule.mp_below_percent:
        return f"mp {mp:.0f}% <= {rule.mp_below_percent:.0f}%"
    return None


class HealingDecision:
    """Decides the heal keys for a tick and tracks each slot's cooldown.

    Typical usage (the loop)::

        for action in healer.decide(state.snapshot()):
            healer.send(action, input_backend)

    One decision thread (the loop) drives it; there is no locking.
    """

    def __init__(
        self,
        config: HealingConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Create the healer.

        Raises:
            ValueError: If the config is invalid.
        """
        self.config = (config if config is not None else HealingConfig()).validate()
        self._clock = clock if clock is not None else time.monotonic
        self._last_sent: dict[str, float] = {}

    def decide(self, state: WorldState) -> list[HealingAction]:
        """The keys to press now: 0 to 2 actions, the spell slot first.

        The first due spell rule, or else the first due cure, takes the spell
        slot; the first due potion rule takes the potion slot. A slot on
        cooldown sends nothing, and no later rule takes its place.
        """
        hp, mp = state.hp_percent, state.mp_percent
        due = {slot: self._first_due_rule(slot, hp, mp) for slot in _SLOTS}
        if due["spell"] is None:
            due["spell"] = self._first_due_cure(state)
        actions = []
        for slot in _SLOTS:
            action = due[slot]
            if action is None:
                continue
            if self.on_cooldown(slot):
                logger.debug("%s slot on cooldown: %s skipped", slot, action.hotkey)
                continue
            actions.append(action)
        return actions

    def _first_due_rule(self, slot: str, hp: float, mp: float) -> HealingAction | None:
        for rule in self.config.rules:
            if rule.kind != slot:
                continue
            reason = _rule_reason(rule, hp, mp)
            if reason is not None:
                return HealingAction(slot, rule.hotkey, reason)
        return None

    def _first_due_cure(self, state: WorldState) -> HealingAction | None:
        active = state.active_status_icons
        for condition in CONDITIONS:
            for cure in self.config.cures:
                if cure.condition == condition and cure.enabled and condition in active:
                    return HealingAction("spell", cure.hotkey, f"cure {condition}")
        return None

    def on_cooldown(self, slot: str) -> bool:
        """True while *slot* was sent less than its cooldown ago."""
        last = self._last_sent.get(slot)
        if last is None:
            return False
        cooldown_ms = (
            self.config.spell_cooldown_ms if slot == "spell" else self.config.potion_cooldown_ms
        )
        return (self._clock() - last) * 1000.0 < cooldown_ms

    def send(self, action: HealingAction, backend: InputBackend) -> None:
        """Press *action*'s hotkey and start its slot's cooldown."""
        backend.press_combo(action.hotkey)
        self._last_sent[action.slot] = self._clock()
        logger.info("heal sent: %s %s -- %s", action.slot, action.hotkey, action.reason)
