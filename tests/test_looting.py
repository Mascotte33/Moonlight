"""Unit tests for moonlight.looting.looting: the loot hotkey for rings around the player."""

from __future__ import annotations

import numpy as np
import pytest

import moonlight.looting.looting as looting_module
from moonlight.core.state import Position, WorldState
from moonlight.hardware.input import FakeInput
from moonlight.looting.looting import (
    RING_CONFIRM_SECONDS,
    RING_GONE_SECONDS,
    RING_GONE_TIMEOUT,
    Looting,
    LootingConfig,
)
from tests.loot_ring_pictures import paint_ring


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


GRID = (0, 0, 1440, 1056)  # 96 px tiles; the player's tile is x 672-767, y 480-575
EAST = (816, 528)  # the middle of the tile east of the player
NEAR = range(0, 2)  # the player's tile and the 8 around it
WS = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0)


class RingFrames:
    """A ring turning on the tile at *x*, until ``gone`` (``flash``: one look, then gone)."""

    def __init__(self, flash: bool, x: int) -> None:
        tile_x = x // 96 * 96
        self.frames = [
            paint_ring(np.zeros((1056, 1440, 3), np.uint8), tile_x, 480, p) for p in (0, 1)
        ]
        self.flash, self.gone, self.tick = flash, False, 0

    def __call__(self) -> np.ndarray:
        self.tick += 1
        if self.gone or (self.flash and self.tick > 1):
            return np.zeros_like(self.frames[0])
        return self.frames[self.tick % 2]


def make_ring(
    clock: Clock, flash: bool = False, x: int = 800, inp: FakeInput | None = None,
    **cfg: object,
) -> Looting:
    config = LootingConfig(**{"wait_ms": 400, **cfg})  # type: ignore[arg-type]
    return Looting(
        inp or FakeInput(), config, clock=clock, frames=RingFrames(flash, x), game_region=GRID
    )


def hide_ring(looting: Looting) -> None:
    looting._frames.gone = True  # type: ignore[union-attr]


def see_ring(looting: Looting, clock: Clock, snap: WorldState = WS) -> None:
    """Looks every 0.125 s until the ring is due; a ring counts only after turning a while."""
    for _ in range(10):
        if looting.ring_due(NEAR, snap):
            return
        clock.t += 0.125
    raise AssertionError("the ring was never due")


def wait(looting: Looting, clock: Clock, seconds: float, snap: WorldState = WS) -> None:
    """Ticks every 0.125 s for *seconds*: the bot holds still all along."""
    end = clock.t + seconds
    while clock.t < end:
        assert not looting.ring_due(NEAR, snap) and looting.loot_first(snap)
        clock.t += 0.125


def test_ring_presses_the_hotkey_then_holds_until_the_rings_are_gone() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp)
    see_ring(looting, clock)
    assert clock.t >= RING_CONFIRM_SECONDS  # it turned a while first
    looting.press_ring()
    assert inp.sent_lines == ["K alt+q"]
    wait(looting, clock, 0.5)  # wait_ms, and the ring still turns
    hide_ring(looting)  # looted: the ring goes away
    wait(looting, clock, RING_GONE_SECONDS - 0.2)
    clock.t += 0.3
    assert not looting.ring_due(NEAR, WS)
    assert not looting.loot_first(WS) and looting.step(WS) is False
    assert inp.sent_lines == ["K alt+q"]
    shots = sorted(p.name for p in looting_module.LOOT_SHOTS_DIR.glob("*.png"))
    assert shots[-1].endswith("_ring.png")


def test_a_flash_seen_once_is_not_looted() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, flash=True, inp=inp)  # shows for one look, then gone
    for _ in range(12):
        assert not looting.ring_due(NEAR, WS)
        clock.t += 0.125
    assert inp.sent_lines == []


def test_a_creature_on_the_ring_tile_is_not_looted() -> None:
    clock = Clock()
    looting = make_ring(clock)
    amazon_east = WorldState(
        client_focused=True, screen_ok=True, hp_percent=100.0, creature_slots=((8, 5),)
    )
    for _ in range(8):
        assert not looting.ring_due(NEAR, amazon_east)  # its outfit sparkles like a ring
        clock.t += 0.125
    assert looting.ring_due(NEAR, WS)  # it walked off: the corpse's ring is looted


def test_rings_that_stay_get_one_more_press_then_are_left_until_the_character_moves() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp)
    see_ring(looting, clock)
    looting.press_ring()
    wait(looting, clock, RING_GONE_TIMEOUT)
    for _ in range(2):  # still there: pressed again, up to RING_PRESSES
        see_ring(looting, clock)
        looting.press_ring()
        wait(looting, clock, RING_GONE_TIMEOUT)
    for _ in range(8):  # a full backpack: left alone here
        assert not looting.ring_due(NEAR, WS) and not looting.should_loot(WS)
        clock.t += 0.125
    assert inp.sent_lines == ["K alt+q"] * 3
    moved = WorldState(client_focused=True, screen_ok=True, hp_percent=100.0,
                       position=Position(1, 0, 7))
    assert looting.ring_due(NEAR, moved)  # walked on: rings here are new


def test_no_ring_does_nothing() -> None:
    inp, clock = FakeInput(), Clock()
    looting = Looting(inp, LootingConfig(), clock=clock)
    assert not looting.ring_due(NEAR, WS)
    assert not looting.should_loot(WS) and looting.step(WS) is False
    assert inp.sent_lines == []


def test_low_hp_holds_still_and_loots_once_hp_is_back() -> None:
    clock = Clock()
    looting = make_ring(clock, min_hp_to_loot=70.0)
    low = WorldState(client_focused=True, screen_ok=True, hp_percent=50.0)
    for _ in range(6):
        assert not looting.ring_due(NEAR, low)  # seen, but HP first
        clock.t += 0.125
    assert looting.should_loot(low) and not looting.loot_first(low)  # a fight goes on
    assert looting.step(low) is True  # else it stands still and heals
    assert looting.ring_due(NEAR, WS)


def test_no_ring_loot_while_the_client_is_unfocused() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp)
    unfocused = WorldState(client_focused=False, screen_ok=True, hp_percent=100.0)
    for _ in range(8):
        assert not looting.ring_due(NEAR, unfocused)
        clock.t += 0.125
    assert inp.sent_lines == []


def test_a_ring_2_tiles_away_is_left_to_pass_by() -> None:
    # owner 2026-11-28: loot rings only in passing, never walk to one.
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp, x=912)  # 2 tiles east
    for _ in range(8):
        looting.ring_due(NEAR, WS)
        clock.t += 0.125
    assert not looting.should_loot(WS) and inp.sent_lines == []


def test_a_ring_beyond_5_tiles_is_not_walked_to() -> None:
    inp, clock = FakeInput(), Clock()
    looting = make_ring(clock, inp=inp, x=672 + 6 * 96 + 48)  # 6 tiles east
    for _ in range(8):
        assert not looting.ring_due(NEAR, WS) and not looting.should_loot(WS)
        clock.t += 0.125


def test_a_ring_by_the_player_holds_the_next_attack_while_it_is_confirmed() -> None:
    # 2026-11-21: the bot attacked the next monster before the ring confirmed and
    # walked off the corpse, so nothing was looted.
    clock = Clock()
    looting = make_ring(clock)
    assert not looting.ring_due(NEAR, WS)  # seen once: not confirmed yet ...
    assert looting.loot_first(WS)  # ... but no new target walks off from it
    hide_ring(looting)  # a one-look flash: it goes
    clock.t += 1.1
    assert not looting.ring_due(NEAR, WS) and not looting.loot_first(WS)


def test_a_kill_waits_a_moment_for_its_ring_before_the_next_attack() -> None:
    from moonlight.core.state import Monster
    from moonlight.looting.looting import KILL_RING_WAIT

    clock = Clock()
    looting = make_ring(clock)
    hide_ring(looting)
    dying = WorldState(
        client_focused=True, screen_ok=True, hp_percent=100.0,
        selected_target=Monster(name="swamp troll", hp_percent=10.0),
    )
    assert not looting.loot_first(dying)  # the fight goes on
    assert looting.loot_first(WS)  # killed: its ring shows a moment later
    clock.t += KILL_RING_WAIT * 1.2
    assert not looting.loot_first(WS)  # no ring came: the next attack goes
    walked = WorldState(
        client_focused=True, screen_ok=True, hp_percent=100.0,
        selected_target=Monster(name="swamp troll", hp_percent=100.0),
    )
    looting.loot_first(walked)
    assert looting.loot_first(WS)  # gone at any HP: one hit can kill from 40 %


def test_a_press_the_rate_limit_drops_is_tried_again() -> None:
    clock = Clock()

    class Busy(FakeInput):
        dropped = 0

        def press_combo(self, combo: str) -> None:
            self.dropped += 1  # the limiter kept it

    inp = Busy()
    looting = make_ring(clock, inp=inp)
    see_ring(looting, clock)
    looting.press_ring()
    clock.t += 0.125  # the next tick
    assert looting.ring_due(NEAR, WS)  # still due: pressed again, not "looted"


@pytest.mark.parametrize(("slot", "pressed"), [((8, 5), True), ((10, 5), False)])
def test_a_kill_next_to_the_player_presses_loot_at_once(
    slot: tuple[int, int], pressed: bool
) -> None:
    from moonlight.core.state import Monster

    clock, inp = Clock(), FakeInput()
    looting = make_ring(clock, inp=inp)
    hide_ring(looting)  # no ring shows yet
    fighting = WorldState(
        client_focused=True, screen_ok=True, hp_percent=100.0, creature_slots=(slot,),
        selected_target=Monster(name="swamp troll", hp_percent=10.0),
    )
    looting.loot_first(fighting)
    looting.loot_first(WS)  # killed
    due = looting.ring_due(NEAR, WS)
    assert due is pressed
    if due:
        looting.press_ring()
        assert inp.sent_lines == ["K alt+q"]


def test_a_kill_next_to_the_player_is_looted_twice() -> None:
    # The target can leave the battle list before its corpse lies there: the first
    # press found nothing; a second one a moment later loots it.
    from moonlight.core.state import Monster
    from moonlight.looting.looting import SECOND_PRESS_SECONDS

    clock, inp = Clock(), FakeInput()
    looting = make_ring(clock, inp=inp)
    hide_ring(looting)
    fighting = WorldState(
        client_focused=True, screen_ok=True, hp_percent=100.0, creature_slots=((8, 5),),
        selected_target=Monster(name="witch", hp_percent=60.0),
    )
    looting.loot_first(fighting)
    looting.loot_first(WS)
    assert looting.ring_due(NEAR, WS)
    looting.press_ring()
    clock.t += SECOND_PRESS_SECONDS * 0.5
    assert looting.loot_first(WS) and not looting.ring_due(NEAR, WS)  # waits, no attack
    clock.t += SECOND_PRESS_SECONDS
    assert looting.ring_due(NEAR, WS)
    looting.press_ring()
    assert inp.sent_lines == ["K alt+q"] * 2
    for _ in range(40):  # 5 s of ticks: no third press, the fight goes on
        clock.t += 0.125
        assert not looting.ring_due(NEAR, WS)
    assert inp.sent_lines == ["K alt+q"] * 2 and not looting.loot_first(WS)
