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
is not adjacent); after the same time again it is ignored for 30 s. With
nothing else to attack it gets a ``pause`` action instead: the loop turns
targeting off for ``PAUSE_SECONDS`` so the cavebot walks on.

With ``only_attack_within_range`` the battle list gives no tile, so a
``select_on_screen`` action right-clicks the best creature within
``attack_range_tiles`` on the game screen instead; monsters further away are
treated as absent (:meth:`TargetingDecision.fight_in_range`).
# doc: docs/prs/near-targeting.md#pick-on-screen

``client_focused`` / ``screen_ok`` gating is the loop's job. The module only
reads a snapshot and returns a decision; one decision thread drives it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from moonlight.core.state import Monster, WorldState
from moonlight.healing.healing import check_hotkey
from moonlight.perception.battle import NAME_START_X, NAME_WIDTH, SLOT_HEIGHT, TOP_PADDING
from moonlight.perception.corpses import slot_ring
from moonlight.perception.creatures import PLAYER_SLOT

__all__ = [
    "MOVEMENTS",
    "PAUSE_SECONDS",
    "TARGET_ORDERS",
    "AreaRule",
    "MonsterRow",
    "TargetingAction",
    "TargetingConfig",
    "TargetingDecision",
]

logger = logging.getLogger(__name__)

MOVEMENTS = ("follow", "none")
# doc: docs/prs/GUI_visual_changes.md#target-order
#: Which of the highest-priority monsters is attacked first. "battle list"
#: is the client's order: nearest first when its battle list sorts by distance.
TARGET_ORDERS = ("lowest hp", "highest hp", "battle list")

Rect = tuple[int, int, int, int]

#: Seconds an unreachable target (or a skipped on-screen tile) is left alone.
_IGNORE_SECONDS = 30.0

#: Seconds targeting stays off after a lone target's HP stood still.
PAUSE_SECONDS = 5.0


# doc: docs/prs/targeting.md#monster-rows
@dataclass(frozen=True)
class MonsterRow:
    """How one monster kind is fought. ``hotkey`` ``""`` = melee only."""

    name: str = ""
    priority: int = 1
    ignore: bool = False
    hotkey: str = ""
    #: Mana points (not percent) needed to press the hotkey; 0 = none.
    minimum_mana: int = 0
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
        if isinstance(self.minimum_mana, bool) or self.minimum_mana < 0:
            raise ValueError(f"{where}: minimum_mana must be 0 or more mana points")


# doc: docs/prs/targeting.md#area-rules
@dataclass(frozen=True)
class AreaRule:
    """Press ``hotkey`` when this many creature slots touch the player (only while ``enabled``)."""

    hotkey: str
    minimum_adjacent_monsters: int = 1
    #: Mana points (not percent) needed to press the hotkey; 0 = none.
    minimum_mana: int = 0
    enabled: bool = True

    def validate(self) -> None:
        """Raise ValueError on a bad hotkey, monster count or mana minimum."""
        where = f"area rule {self.hotkey!r}"
        check_hotkey(self.hotkey, where)
        if isinstance(self.minimum_adjacent_monsters, bool) or self.minimum_adjacent_monsters < 1:
            raise ValueError(f"{where}: minimum_adjacent_monsters must be >= 1")
        if isinstance(self.minimum_mana, bool) or self.minimum_mana < 0:
            raise ValueError(f"{where}: minimum_mana must be 0 or more mana points")


@dataclass(frozen=True)
class TargetingConfig:
    """Monster rows, the row for everything else, area rules and the delays."""

    monsters: tuple[MonsterRow, ...] = ()
    default_monster: MonsterRow = MonsterRow()
    area_rules: tuple[AreaRule, ...] = ()
    attack_cooldown_ms: int = 2000
    # doc: docs/prs/first-spell.md#give-up-sooner
    #: Owner 2026-11-01: 8 s was "way too much".
    unreachable_after_seconds: float = 4.0
    #: The client's "Toggle Chase Mode" key; "" = the bot leaves the owner's mode.
    toggle_chase_hotkey: str = ""
    target_order: str = "lowest hp"
    # doc: docs/prs/near-targeting.md#in-range
    only_attack_within_range: bool = False
    #: Tiles from the character in any direction (3 = a 7x7 box).
    attack_range_tiles: int = 3

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
        if self.target_order not in TARGET_ORDERS:
            raise ValueError(
                f"targeting.target_order must be one of {TARGET_ORDERS}, got {self.target_order!r}"
            )
        if self.toggle_chase_hotkey:
            check_hotkey(self.toggle_chase_hotkey, "targeting.toggle_chase_hotkey")
        if isinstance(self.attack_range_tiles, bool) or self.attack_range_tiles < 1:
            raise ValueError("targeting.attack_range_tiles must be 1 or more")


@dataclass(frozen=True)
class TargetingAction:
    """``"select"``: click battle-list row ``battle_list_row``.

    ``"select_on_screen"``: right-click the creature at game-grid slot
    ``creature_slot`` (world tile ``creature_tile``); ``battle_list_row`` is -1.

    ``"step_closer"``: press one arrow key for the unit step ``step``.

    ``"pause"``: the selected target is unreachable and nothing else is
    there to attack; the loop turns targeting off for ``PAUSE_SECONDS``.
    """

    kind: str
    battle_list_row: int
    step: tuple[int, int] = (0, 0)
    reason: str = ""
    creature_slot: tuple[int, int] = (0, 0)
    creature_tile: tuple[int, int, int] = (0, 0, 0)


def _step_toward_creature(
    slots: tuple[tuple[int, int], ...], echo_raid_slots: tuple[tuple[int, int], ...] = ()
) -> tuple[int, int] | None:
    """Unit arrow step toward the nearest slot not adjacent to the player, or None.

    The step never lands on an echo raid trigger: the other axis is tried, else None.
    """
    px, py = PLAYER_SLOT
    far = [(x - px, y - py) for x, y in slots if max(abs(x - px), abs(y - py)) > 1]
    if not far:
        return None
    dx, dy = min(far, key=lambda d: (max(abs(d[0]), abs(d[1])), abs(d[0]) + abs(d[1])))
    along_x = [(1 if dx > 0 else -1, 0)] if dx else []
    along_y = [(0, 1 if dy > 0 else -1)] if dy else []
    steps = along_x + along_y if abs(dx) >= abs(dy) else along_y + along_x
    # doc: docs/prs/echo-raid.md#avoid
    return next((s for s in steps if (px + s[0], py + s[1]) not in echo_raid_slots), None)


def _world_tile(state: WorldState, slot: tuple[int, int]) -> tuple[int, int, int]:
    """World tile under game-grid *slot*, from the player's position."""
    pos = state.position
    return pos.x + slot[0] - PLAYER_SLOT[0], pos.y + slot[1] - PLAYER_SLOT[1], pos.z


# doc: docs/prs/targeting.md#target-choice
# doc: docs/prs/targeting.md#unreachable-drop
class TargetingDecision:
    """Pick the battle-list row to attack; see the module docstring."""

    def __init__(
        self,
        config: TargetingConfig | None = None,
        battle_list_region: Rect | None = None,
        ui_scale: float = 1.0,
        attack_top_row_first: bool = False,
        game_grid_region: Rect | None = None,
    ) -> None:
        self.config = config if config is not None else TargetingConfig()
        # Stuck in a corridor: the top row is the nearest creature (the battle
        # list sorted by distance), most often the one in the way.
        self._attack_top_row_first = attack_top_row_first
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
        self._game_grid_region = game_grid_region
        #: World tiles whose on-screen click selected nothing or an ignored monster.
        self._skipped_tiles: dict[tuple[int, int, int], float] = {}

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

    def _watch_hp(
        self, state: WorldState, now: float, candidates: list[Monster]
    ) -> TargetingAction | None:
        """Step closer once when the selected target's HP stands still, then ignore it.

        With no other candidate it asks for a pause instead.
        """
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
        name = selected.name or "?"
        if all(m.battle_list_row == selected.battle_list_row for m in candidates):
            self._watched = None
            reason = f"{name} hp stuck at {selected.hp_percent:.0f}, nothing else to attack"
            return TargetingAction("pause", selected.battle_list_row, reason=reason)
        if not self._stepped:
            self._stepped = True
            step = _step_toward_creature(state.creature_slots, state.echo_raid_slots)
            if step is None:
                return None
            reason = f"{name} hp stuck at {selected.hp_percent:.0f}, step closer"
            return TargetingAction("step_closer", selected.battle_list_row, step, reason)
        self._ignored_until[key] = now + _IGNORE_SECONDS
        self._watched = None
        return None

    def decide(self, state: WorldState, now: float) -> TargetingAction | None:
        """The action to take, or None when the selection is already right."""
        self._ignored_until = {k: end for k, end in self._ignored_until.items() if end > now}
        candidates = [
            m
            for m in state.monsters
            if not m.is_player and not self.row_for(m).ignore and not self.is_unreachable(m)
        ]
        watched = self._watch_hp(state, now, candidates)
        if watched is not None:
            return watched
        candidates = [m for m in candidates if not self.is_unreachable(m)]  # just ignored
        if not candidates:
            return None
        if state.selected_target in candidates:
            return None  # finish the current target, even when a higher priority one comes
        if self.config.only_attack_within_range:
            return self._pick_on_screen(state, now)
        if self._attack_top_row_first:
            best = min(candidates, key=lambda m: m.battle_list_row)
        else:
            best = min(candidates, key=self._target_order_key)
        priority = self.row_for(best).priority
        reason = f"{best.name or '?'} priority {priority}, hp {best.hp_percent:.0f}"
        return TargetingAction("select", best.battle_list_row, reason=reason)

    def _near_creatures(self, state: WorldState, now: float) -> list[tuple[tuple[int, int], float]]:
        """``(slot, hp)`` of the creatures within range whose tile is not skipped."""
        self._skipped_tiles = {t: end for t, end in self._skipped_tiles.items() if end > now}
        reach = self.config.attack_range_tiles
        px, py = PLAYER_SLOT
        return [
            (slot, hp)
            for slot, hp in zip(state.creature_slots, state.creature_hp_percents, strict=True)
            if max(abs(slot[0] - px), abs(slot[1] - py)) <= reach
            and _world_tile(state, slot) not in self._skipped_tiles
        ]

    # doc: docs/prs/near-targeting.md#fight-in-range
    def fight_in_range(self, state: WorldState, now: float) -> bool:
        """True while a fight holds the cavebot: a selected target or a creature in range.

        Without ``only_attack_within_range``: any monster in the battle list.
        """
        if not self.config.only_attack_within_range:
            return bool(state.monsters)
        return state.selected_target is not None or bool(self._near_creatures(state, now))

    def skip_tile(self, tile: tuple[int, int, int], now: float, why: str) -> None:
        """Leave the creature on world *tile* alone for 30 s (an on-screen click went wrong)."""
        logger.info("targeting: %s at %s,%s,%s, skipping that tile 30 s", why, *tile)
        self._skipped_tiles[tile] = now + _IGNORE_SECONDS

    def _pick_on_screen(self, state: WorldState, now: float) -> TargetingAction | None:
        """Right-click the best creature in range: by ``target_order`` on its screen-bar HP."""
        near = self._near_creatures(state, now)
        if not near:
            return None
        px, py = PLAYER_SLOT

        def distance(slot: tuple[int, int]) -> tuple[int, int]:
            dx, dy = abs(slot[0] - px), abs(slot[1] - py)
            return max(dx, dy), dx + dy

        order = self.config.target_order
        if self._attack_top_row_first or order == "battle list":
            slot, hp = min(near, key=lambda c: distance(c[0]))
        else:
            sign = 1 if order == "lowest hp" else -1
            slot, hp = min(near, key=lambda c: (sign * c[1], distance(c[0])))
        tile = _world_tile(state, slot)
        reason = f"creature at {tile[0]},{tile[1]} hp {hp:.0f}"
        return TargetingAction(
            "select_on_screen", -1, reason=reason, creature_slot=slot, creature_tile=tile
        )

    def slot_center(self, slot: tuple[int, int]) -> tuple[int, int]:
        """Frame pixel at the middle of game-grid *slot*.

        Raises:
            ValueError: If no ``game_grid`` region was given.
        """
        if self._game_grid_region is None:
            raise ValueError("targeting has no game_grid region to click in")
        return slot_ring(self._game_grid_region, slot).point

    def _target_order_key(self, monster: Monster) -> tuple[float, ...]:
        """Sort key: highest priority first, then ``target_order``, then the top row."""
        priority = -self.row_for(monster).priority
        order = self.config.target_order
        if order == "battle list":
            return (priority, monster.battle_list_row)
        hp = monster.hp_percent if order == "lowest hp" else -monster.hp_percent
        return (priority, hp, monster.battle_list_row)

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
