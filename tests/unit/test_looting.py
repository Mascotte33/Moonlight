"""Unit tests for moonlight.perception.loot and moonlight.decision.looting (T23).

Offline: synthetic frames with a random-noise title bar and item sprites
drawn into a 5x5 grid; hotkeys go to FakeInput.
"""

from __future__ import annotations

import numpy as np

from moonlight.bot.state import Capacity, WorldState
from moonlight.decision.looting import OPEN_TIMEOUT_TICKS, Looting, LootingConfig
from moonlight.hardware.clicker import ClickController
from moonlight.hardware.input import FakeInput
from moonlight.perception.cursor import CursorReader
from moonlight.perception.loot import TITLE_TEMPLATE, LootReader, LootWindow
from moonlight.setup.calibrate import CalibrationProfile, Region

REGION = (100, 50, 220, 220)
TITLE_AT = (110, 60)  # frame coords of the title bar
SLOT = 34
_rng = np.random.RandomState(23)
TITLE = _rng.randint(0, 255, (12, 120)).astype(np.uint8)
GOLD = _rng.randint(0, 255, (30, 30)).astype(np.uint8)
SWORD = _rng.randint(0, 255, (30, 30)).astype(np.uint8)
TEMPLATES = {TITLE_TEMPLATE: TITLE, "gold coin": GOLD, "sword": SWORD}


def render(items: dict[int, np.ndarray] | None) -> np.ndarray:
    """Frame with the loot window (and *items* by slot) or, if None, without it."""
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    if items is None:
        return frame
    tx, ty = TITLE_AT
    frame[ty : ty + TITLE.shape[0], tx : tx + TITLE.shape[1]] = TITLE[..., None]
    gy = ty + TITLE.shape[0]
    for index, sprite in items.items():
        row, col = divmod(index, 5)
        x, y = tx + col * SLOT + 2, gy + row * SLOT + 2
        frame[y : y + 30, x : x + 30] = sprite[..., None]
    return frame


def profile() -> CalibrationProfile:
    return CalibrationProfile(regions={"loot_window": Region.from_tuple(REGION)})


# -- LootReader -------------------------------------------------------------


def test_reader_returns_none_without_templates() -> None:
    assert LootReader({}, profile()).read_loot_window(render({0: GOLD})) is None


def test_reader_returns_none_without_region() -> None:
    assert LootReader(TEMPLATES).read_loot_window(render({0: GOLD})) is None


def test_reader_returns_none_when_window_closed() -> None:
    assert LootReader(TEMPLATES, profile()).read_loot_window(render(None)) is None


def test_reader_reads_items_by_slot() -> None:
    window = LootReader(TEMPLATES, profile()).read_loot_window(render({0: GOLD, 6: SWORD}))
    assert window == LootWindow(open=True, items=[(0, "gold coin", 1), (6, "sword", 1)])


def test_reader_open_empty_corpse() -> None:
    window = LootReader(TEMPLATES, profile()).read_loot_window(render({}))
    assert window == LootWindow(open=True, items=[])


# -- Looting ----------------------------------------------------------------


class Sim:
    def __init__(self, config: LootingConfig | None = None) -> None:
        self.items: dict[int, np.ndarray] | None = None  # None = window closed
        self.fake = FakeInput()
        self.state = WorldState(client_focused=True, screen_ok=True)
        clicker = ClickController(self.fake, CursorReader(), self.frame)
        reader = LootReader(TEMPLATES, profile())
        self.looting = Looting(
            reader, clicker, self.fake, self.state, config, frame_provider=self.frame
        )

    def frame(self) -> np.ndarray:
        return render(self.items)


def test_config_defaults() -> None:
    c = LootingConfig()
    assert (c.loot_hotkey, c.move_all_hotkey) == ("F7", None)
    assert c.max_items_per_loot == 25
    assert c.min_cap_to_loot == 10.0


def test_should_loot_needs_a_kill() -> None:
    sim = Sim()
    assert not sim.looting.should_loot(sim.state)
    sim.looting.on_target_died("rat")
    assert sim.looting.should_loot(sim.state)  # unknown cap does not block


def test_should_loot_respects_free_cap() -> None:
    sim = Sim()
    sim.looting.on_target_died("rat")
    sim.state.capacity = Capacity(weight=50, max_weight=100)
    assert sim.looting.should_loot(sim.state)
    sim.state.capacity = Capacity(weight=95, max_weight=100)  # 5% free < 10%
    assert not sim.looting.should_loot(sim.state)


def test_step_opens_moves_closes_in_three_ticks() -> None:
    sim = Sim(LootingConfig(move_all_hotkey="F8"))
    sim.looting.on_target_died("rat")

    assert sim.looting.step(sim.state) is True  # opens the corpse
    assert sim.fake.sent_lines == ["K F7"]

    sim.items = {0: GOLD}
    assert sim.looting.step(sim.state) is True  # moves all
    assert sim.fake.sent_lines[-1] == "K F8"

    assert sim.looting.step(sim.state) is False  # closes
    assert sim.fake.sent_lines == ["K F7", "K F8", "K F7"]
    assert sim.looting.pending is None


def test_step_without_move_all_hotkey_just_closes() -> None:
    sim = Sim()
    sim.looting.on_target_died("rat")
    sim.looting.step(sim.state)
    sim.items = {0: GOLD}
    assert sim.looting.step(sim.state) is True
    assert sim.looting.step(sim.state) is False
    assert sim.fake.sent_lines == ["K F7", "K F7"]


def test_step_gives_up_when_window_never_opens() -> None:
    sim = Sim()
    sim.looting.on_target_died("rat")
    for _ in range(OPEN_TIMEOUT_TICKS):
        assert sim.looting.step(sim.state) is True
    assert sim.looting.step(sim.state) is False
    assert sim.fake.sent_lines == ["K F7"]  # pressed once, not spammed
    assert sim.looting.pending is None


def test_step_idle_without_kill_or_focus() -> None:
    sim = Sim()
    assert sim.looting.step(sim.state) is False
    sim.looting.on_target_died("rat")
    sim.state.client_focused = False
    assert sim.looting.step(sim.state) is False
    assert sim.fake.sent_lines == []
