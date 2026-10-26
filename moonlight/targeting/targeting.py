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

A selected target whose HP stands still for ``unreachable_after_seconds`` gets
one ``step_closer`` action (a unit step toward the nearest creature slot that
is not adjacent); after the same time again it is ignored for 30 s.

``client_focused`` / ``screen_ok`` gating is the loop's job. The module only
reads a snapshot and returns a decision; one decision thread drives it.
"""

from __future__ import annotations

from dataclasses import dataclass

from moonlight.core.state import Monster, WorldState
from moonlight.healing.healing import check_hotkey
from moonlight.perception.battle import NAME_START_X, NAME_WIDTH, SLOT_HEIGHT, TOP_PADDING
from moonlight.perception.creatures import PLAYER_SLOT

__all__ = [
    "MOVEMENTS",
    "AreaRule",
    "MonsterRow",
    "TargetingAction",
    "TargetingConfig",
    "TargetingDecision",
]

MOVEMENTS = ("follow", "keep_distance", "none")

Rect = tuple[int, int, int, int]

#: Seconds an unreachable target is left alone.
_IGNORE_SECONDS = 30.0


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
        if not 0.0 <= self.minimum_mana_percent <= 100.0:
            raise ValueError(f"{where}: minimum_mana_percent must be within 0..100")


# doc: docs/prs/targeting.md#area-rules
@dataclass(frozen=True)
class AreaRule:
    """Press ``hotkey`` when this many creature slots touch the player."""

    hotkey: str
    minimum_adjacent_monsters: int = 1
    minimum_mana_percent: float = 0.0

    def validate(self) -> None:
        """Raise ValueError on a bad hotkey, monster count or mana minimum."""
        where = f"area rule {self.hotkey!r}"
        check_hotkey(self.hotkey, where)
        if isinstance(self.minimum_adjacent_monsters, bool) or self.minimum_adjacent_monsters < 1:
            raise ValueError(f"{where}: minimum_adjacent_monsters must be >= 1")
        if not 0.0 <= self.minimum_mana_percent <= 100.0:
            raise ValueError(f"{where}: minimum_mana_percent must be within 0..100")


@dataclass(frozen=True)
class TargetingConfig:
    """Monster rows, the row for everything else, area rules and the delays."""

    monsters: tuple[MonsterRow, ...] = ()
    default_monster: MonsterRow = MonsterRow()
    area_rules: tuple[AreaRule, ...] = ()
    attack_cooldown_ms: int = 2000
    unreachable_after_seconds: float = 8.0

    def validate(self) -> None:
        """Raise ValueError on a bad row or rule, a duplicate name or a bad delay."""
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
        for rule in self.area_rules:
            rule.validate()
        if isinstance(self.attack_cooldown_ms, bool) or self.attack_cooldown_ms < 0:
            raise ValueError("targeting.attack_cooldown_ms must be >= 0")
        if self.unreachable_after_seconds <= 0:
            raise ValueError("targeting.unreachable_after_seconds must be > 0")


@dataclass(frozen=True)
class TargetingAction:
    """``"select"``: click battle-list row ``battle_list_row``.

    ``"step_closer"``: press one arrow key for the unit step ``step``.
    """

    kind: str
    battle_list_row: int
    step: tuple[int, int] = (0, 0)
    reason: str = ""


def _step_toward_creature(slots: tuple[tuple[int, int], ...]) -> tuple[int, int] | None:
    """Unit arrow step toward the nearest slot not adjacent to the player, or None."""
    px, py = PLAYER_SLOT
    far = [(x - px, y - py) for x, y in slots if max(abs(x - px), abs(y - py)) > 1]
    if not far:
        return None
    dx, dy = min(far, key=lambda d: (max(abs(d[0]), abs(d[1])), abs(d[0]) + abs(d[1])))
    if abs(dx) >= abs(dy):
        return (1 if dx > 0 else -1, 0)
    return (0, 1 if dy > 0 else -1)


# doc: docs/prs/targeting.md#target-choice
# doc: docs/prs/targeting.md#unreachable-drop
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
        # HP watch on the selected target: its key, since when, and whether
        # the step closer was already taken.
        self._watched: tuple[str, float] | None = None
        self._watched_since = 0.0
        self._stepped = False
        self._ignored_until: dict[tuple[str, float], float] = {}

    def row_for(self, monster: Monster) -> MonsterRow:
        """The row for *monster*: its name's row, else ``default_monster``."""
        return self._rows.get(monster.name.lower(), self.config.default_monster)

    def is_unreachable(self, monster: Monster) -> bool:
        """True while *monster* is on the 30 s ignore list."""
        return self._key(monster) in self._ignored_until

    @staticmethod
    def _key(monster: Monster) -> tuple[str, float]:
        # ponytail: two same-kind monsters at the same HP count as one; the
        # battle list gives no ids.
        return (monster.name_hash, monster.hp_percent)

    def _watch_hp(self, state: WorldState, now: float) -> TargetingAction | None:
        """Step closer once when the selected target's HP stands still, then ignore it."""
        selected = state.selected_target
        if selected is None or self.is_unreachable(selected):
            self._watched = None
            return None
        key = self._key(selected)
        if key != self._watched:
            self._watched, self._watched_since, self._stepped = key, now, False
            return None
        if now - self._watched_since < self.config.unreachable_after_seconds:
            return None
        self._watched_since = now
        if not self._stepped:
            self._stepped = True
            step = _step_toward_creature(state.creature_slots)
            if step is None:
                return None
            reason = f"{selected.name or '?'} hp stuck at {selected.hp_percent:.0f}, step closer"
            return TargetingAction("step_closer", selected.battle_list_row, step, reason)
        self._ignored_until[key] = now + _IGNORE_SECONDS
        self._watched = None
        return None

    def decide(self, state: WorldState, now: float) -> TargetingAction | None:
        """The action to take, or None when the selection is already right."""
        self._ignored_until = {k: end for k, end in self._ignored_until.items() if end > now}
        step_closer = self._watch_hp(state, now)
        if step_closer is not None:
            return step_closer
        candidates = [
            m
            for m in state.monsters
            if not m.is_player and not self.row_for(m).ignore and not self.is_unreachable(m)
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
