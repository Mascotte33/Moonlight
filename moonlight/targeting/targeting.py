"""Targeting: which battle-list row to click (monster rows, target choice).

Every monster in the battle list gets a :class:`MonsterRow`: the row whose
name matches (ignoring case), or ``default_monster`` for a monster with no
row or no name yet. So every monster is attacked by default; ``ignore``
drops it from target choice.

:meth:`TargetingDecision.decide` returns a ``select`` action with the
battle-list row to click, or ``None`` when the selection is already right.
The loop clicks the row's name through the ``ClickController``
(:meth:`TargetingDecision.row_center` gives the pixel); the attack key is
never used. Players are never candidates; the alarms keep their own
``players_on_screen`` path.

``client_focused`` / ``screen_ok`` gating is the loop's job. The module only
reads a snapshot and returns a decision; one decision thread drives it.
"""

from __future__ import annotations

from dataclasses import dataclass

from moonlight.core.state import Monster, WorldState
from moonlight.healing.healing import check_hotkey
from moonlight.perception.battle import NAME_START_X, NAME_WIDTH, SLOT_HEIGHT, TOP_PADDING

__all__ = [
    "MOVEMENTS",
    "MonsterRow",
    "TargetingAction",
    "TargetingConfig",
    "TargetingDecision",
]

MOVEMENTS = ("follow", "keep_distance", "none")

Rect = tuple[int, int, int, int]


# doc: docs/prs/targeting.md#monster-rows
@dataclass(frozen=True)
class MonsterRow:
    """How one monster kind is fought. ``hotkey`` ``""`` = melee only."""

    name: str = ""
    priority: int = 1
    ignore: bool = False
    hotkey: str = ""
    minimum_mana_percent: float = 0.0
    movement: str = "follow"

    def validate(self) -> None:
        """Raise ValueError on a bad priority, movement or hotkey."""
        where = f"monster row {self.name!r}"
        if isinstance(self.priority, bool) or self.priority not in range(1, 6):
            raise ValueError(f"{where}: priority must be 1-5, got {self.priority!r}")
        if self.movement not in MOVEMENTS:
            raise ValueError(f"{where}: movement must be one of {MOVEMENTS}, got {self.movement!r}")
        if self.hotkey:
            check_hotkey(self.hotkey, where)


@dataclass(frozen=True)
class TargetingConfig:
    """Monster rows, the row for everything else, and the unreachable delay."""

    monsters: tuple[MonsterRow, ...] = ()
    default_monster: MonsterRow = MonsterRow()
    unreachable_after_seconds: float = 8.0

    def validate(self) -> None:
        """Raise ValueError on a bad row, a duplicate name or a bad delay."""
        if self.default_monster.name:
            raise ValueError("targeting.default_monster: name must be empty")
        self.default_monster.validate()
        seen: set[str] = set()
        for row in self.monsters:
            if not row.name.strip():
                raise ValueError("targeting.monsters: every row needs a name")
            if row.name.lower() in seen:
                raise ValueError(f"targeting.monsters: duplicate name {row.name!r} (ignoring case)")
            seen.add(row.name.lower())
            row.validate()
        if self.unreachable_after_seconds <= 0:
            raise ValueError("targeting.unreachable_after_seconds must be > 0")


@dataclass(frozen=True)
class TargetingAction:
    """Click battle-list row ``battle_list_row`` (``kind`` ``"select"``)."""

    kind: str
    battle_list_row: int
    step: tuple[int, int] = (0, 0)
    reason: str = ""


# doc: docs/prs/targeting.md#target-choice
class TargetingDecision:
    """Pick the battle-list row to attack; see the module docstring."""

    def __init__(
        self,
        config: TargetingConfig | None = None,
        battle_list_region: Rect | None = None,
        ui_scale: float = 1.0,
    ) -> None:
        self.config = config if config is not None else TargetingConfig()
        self.config.validate()
        self._rows = {row.name.lower(): row for row in self.config.monsters}
        self._battle_list_region = battle_list_region
        self._ui_scale = ui_scale

    def row_for(self, monster: Monster) -> MonsterRow:
        """The row for *monster*: its name's row, else ``default_monster``."""
        return self._rows.get(monster.name.lower(), self.config.default_monster)

    def decide(self, state: WorldState, now: float) -> TargetingAction | None:
        """The row to select, or None when the selection is already right."""
        candidates = [
            m for m in state.monsters if not m.is_player and not self.row_for(m).ignore
        ]
        if not candidates:
            return None
        top_priority = max(self.row_for(m).priority for m in candidates)
        selected = state.selected_target
        if (
            selected is not None
            and selected in candidates
            and self.row_for(selected).priority == top_priority
        ):
            return None  # stay on a valid target
        best = min(
            candidates,
            key=lambda m: (-self.row_for(m).priority, m.hp_percent, m.battle_list_row),
        )
        priority = self.row_for(best).priority
        reason = f"{best.name or '?'} priority {priority}, hp {best.hp_percent:.0f}"
        return TargetingAction("select", best.battle_list_row, reason=reason)

    def row_center(self, battle_list_row: int) -> tuple[int, int]:
        """Frame pixel at the middle of *battle_list_row*'s name.

        Scales the slot geometry as :class:`BattleReader` does.

        Raises:
            ValueError: If no ``battle_list`` region was given.
        """
        if self._battle_list_region is None:
            raise ValueError("targeting has no battle_list region to click in")
        left, top, _, _ = self._battle_list_region

        def px(value: int) -> int:
            return round(value * self._ui_scale)

        slot = px(SLOT_HEIGHT)
        x = left + px(NAME_START_X) + px(NAME_WIDTH) // 2
        y = top + px(TOP_PADDING) + battle_list_row * slot + slot // 2
        return x, y
