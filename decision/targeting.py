"""Targeting decisions (T20).

Decides which creature to attack, given :class:`moonlight.state.WorldState`:

* **ATTACK** (reasons: ``"already targeting ..."`` / ``"first monster ..."`` /
  ``"weakest monster ..."``): a monster was chosen and is (or must become)
  the attack target.
* **SWITCH** (reason ``"switching from ..."``): the currently selected
  target is not a usable monster (a player, or no longer in the battle
  list) -> change the attack target.
* **FLEE** (reasons: players on screen / too many monsters): the situation
  is unsafe -> the loop should disengage instead of fighting.

``decide()`` returns ``None`` (idle) when the battle list holds no
creatures at all.

Safety rules (hard, non-negotiable):

* **Never target a player**: player entries are filtered out of the
  candidate list even if perception mislabels them, and a player currently
  selected as target is always abandoned in favour of a monster.
* ``players_on_screen > 0`` with :attr:`TargetingConfig.player_protection`
  enabled -> ``FLEE``.
* More monsters in the battle list than
  :attr:`TargetingConfig.max_monsters_to_engage` -> ``FLEE``.

Target selection order:

1. The currently selected target, when it is still a monster (matches an
   entry of ``WorldState.monsters`` case-insensitively).
2. Otherwise, with :attr:`TargetingConfig.prefer_low_hp` enabled (default),
   the monster with the lowest ``hp_percent`` (ties keep battle-list
   order); without it, the first monster in the battle list.

``client_focused`` / ``screen_ok`` gating is the loop's job (it refuses to
send input without both), so this module does not check them. The module
is pure with respect to the game: it only reads a (snapshot of)
:class:`WorldState` and returns a decision. A single decision thread (the
20 Hz loop) drives this class; there is no internal locking (same as
GameLoop).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from moonlight.state import Monster, WorldState

__all__ = [
    "TargetingAction",
    "TargetingActionKind",
    "TargetingConfig",
    "TargetingDecision",
]

logger = logging.getLogger("moonlight.decision.targeting")


class TargetingActionKind(StrEnum):
    """Kind of a targeting action (the ``TargetingAction.action`` tokens)."""

    ATTACK = "attack"
    SWITCH = "switch"
    FLEE = "flee"


@dataclass(frozen=True)
class TargetingConfig:
    """Tunable targeting parameters (defaults are the T20 spec values)."""

    max_monsters_to_engage: int = 1
    """Do not engage when more monsters than this are in the battle list."""

    prefer_low_hp: bool = True
    """When picking a target, prefer the monster with the lowest HP percent."""

    player_protection: bool = True
    """Abort (flee) as soon as any player is on screen."""

    def validate(self) -> TargetingConfig:
        """Check invariants and return self (fail fast, loop-style validation).

        Raises:
            ValueError: If ``max_monsters_to_engage`` is not a positive integer.
        """
        if not isinstance(self.max_monsters_to_engage, int) or isinstance(
            self.max_monsters_to_engage, bool
        ):
            raise ValueError("max_monsters_to_engage must be an int")
        if self.max_monsters_to_engage < 1:
            raise ValueError("max_monsters_to_engage must be >= 1")
        return self


@dataclass(frozen=True)
class TargetingAction:
    """One targeting decision: what to do about the attack target and why.

    Attributes:
        target_name: Creature to act on; empty string for ``FLEE`` actions.
        action: ``attack`` / ``switch`` / ``flee``.
        reason: Human-readable justification (logging / UI).
    """

    target_name: str
    action: TargetingActionKind
    reason: str


class TargetingDecision:
    """Decides which creature to attack.

    Typical usage (wired into the loop)::

        targeting = TargetingDecision()   # or TargetingDecision(custom_config)
        action = targeting.decide(state.snapshot())  # TargetingAction | None
        # loop maps the action onto input: press/hold the attack key on
        # target_name, or disengage on flee.

    ``decide()`` is pure with respect to the game: it only reads the
    (snapshot of) :class:`WorldState` and returns a decision.

    Thread-safety contract: a single decision thread (the 20 Hz loop)
    drives this class; there is no internal locking (same as GameLoop).
    """

    def __init__(self, config: TargetingConfig | None = None) -> None:
        """Create the targeting decision engine.

        Args:
            config: Targeting parameters; defaults to :class:`TargetingConfig`.

        Raises:
            ValueError: If the config is invalid.
        """
        cfg = config if config is not None else TargetingConfig()
        cfg.validate()
        self.config = cfg

    # -- decision ------------------------------------------------------------

    def decide(self, state: WorldState) -> TargetingAction | None:
        """Decide what to do about the attack target, given the world state.

        Returns:
            A :class:`TargetingAction` for ``attack`` / ``switch`` /
            ``flee``, or ``None`` when the battle list is empty (idle).

        Safety rules are checked first and win over any selection logic:
        players on screen (with protection enabled) -> ``flee``; more
        monsters than ``max_monsters_to_engage`` -> ``flee``; a player is
        never returned as ``target_name``.
        """
        if self.config.player_protection and state.players_on_screen > 0:
            logger.debug(
                "fleeing: %d player(s) on screen", state.players_on_screen
            )
            return TargetingAction(
                target_name="",
                action=TargetingActionKind.FLEE,
                reason=(
                    f"{state.players_on_screen} player(s) on screen "
                    f"(player protection active)"
                ),
            )

        monsters = self._candidate_monsters(state)
        if not monsters:
            logger.debug("no monsters in battle list; idle")
            return None

        if len(monsters) > self.config.max_monsters_to_engage:
            logger.debug(
                "fleeing: %d monsters exceed max_monsters_to_engage=%d",
                len(monsters),
                self.config.max_monsters_to_engage,
            )
            return TargetingAction(
                target_name="",
                action=TargetingActionKind.FLEE,
                reason=(
                    f"{len(monsters)} monsters in battle list exceed "
                    f"max_monsters_to_engage={self.config.max_monsters_to_engage}"
                ),
            )

        selected = self._valid_selected(state.selected_target, monsters)
        if selected is not None:
            logger.debug("continuing on current target %r", selected.name)
            return TargetingAction(
                target_name=selected.name,
                action=TargetingActionKind.ATTACK,
                reason=f"already targeting {selected.name!r}",
            )

        target = self._choose_target(monsters)
        if state.selected_target is None:
            reason = self._selection_reason(target)
            kind = TargetingActionKind.ATTACK
        else:
            reason = (
                f"current selection {state.selected_target.name!r} is not a "
                f"valid monster; switching to {target.name!r}"
            )
            kind = TargetingActionKind.SWITCH
        logger.debug("targeting %r (%s)", target.name, kind.value)
        return TargetingAction(target_name=target.name, action=kind, reason=reason)

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _candidate_monsters(state: WorldState) -> list[Monster]:
        """Monsters that may be targeted: never a player, even if mislabeled."""
        return [monster for monster in state.monsters if not monster.is_player]

    @staticmethod
    def _valid_selected(
        selected: Monster | None, monsters: list[Monster]
    ) -> Monster | None:
        """The current selection when it is a monster still in the battle list.

        A selection counts as valid when it is not flagged as a player and
        matches one of *monsters* by name (case-insensitive).
        """
        if selected is None or selected.is_player:
            return None
        for monster in monsters:
            if monster.name.lower() == selected.name.lower():
                return monster
        return None

    def _choose_target(self, monsters: list[Monster]) -> Monster:
        """Pick the monster to engage from the candidate list."""
        if self.config.prefer_low_hp:
            return min(monsters, key=lambda monster: monster.hp_percent)
        return monsters[0]

    def _selection_reason(self, target: Monster) -> str:
        """Reason for a freshly chosen (not pre-selected) target."""
        if self.config.prefer_low_hp:
            return (
                f"weakest monster: {target.name!r} at {target.hp_percent:.0f}% HP"
            )
        return f"first monster in battle list: {target.name!r}"
