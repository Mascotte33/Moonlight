"""Offline tests for user scripts: the generator API, waits, timers, find."""

# doc: docs/prs/scripts.md#script-api

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from moonlight.core.safety import RateLimitedInput, RateLimiter
from moonlight.core.state import WorldState
from moonlight.hardware.input import FakeInput
from moonlight.scripts.scripts import ScriptRunner, ScriptsConfig, parse_timers


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def runner(tmp_path: Path, clock: Clock | None = None, **srcs: str) -> ScriptRunner:
    for name, src in srcs.items():
        (tmp_path / f"{name}.py").write_text(src)
    return ScriptRunner(tmp_path, clock=clock or Clock())


def ticks(r: ScriptRunner, inp: FakeInput, n: int, snap: WorldState | None = None) -> None:
    for _ in range(n):
        r.step(snap or WorldState(), inp)


PRESS_WAIT_SAY = """
def run(bot):
    yield bot.press("f2")
    yield 1
    yield bot.say("hi")
"""


def test_one_command_per_tick_and_a_wait(tmp_path: Path) -> None:
    clock = Clock()
    r, inp = runner(tmp_path, clock, s=PRESS_WAIT_SAY), FakeInput()
    assert r.names() == ["s"] and r.start("s")
    ticks(r, inp, 5)
    assert inp.sent_lines == ["K f2"]  # waiting
    clock.now = 1.0
    ticks(r, inp, 5)
    assert inp.sent_lines == ["K f2", "K h", "K i", "K enter"]
    assert r.running is None


def test_reads_hp_from_the_tick(tmp_path: Path) -> None:
    src = "def run(bot):\n    if bot.hp < 50:\n        yield bot.press('f3')\n"
    r, inp = runner(tmp_path, s=src), FakeInput()
    r.start("s")
    ticks(r, inp, 3, WorldState(hp_percent=40.0))
    assert inp.sent_lines == ["K f3"]


def test_a_command_the_limiter_dropped_is_sent_again(tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeInput()
    limited = RateLimitedInput(fake, RateLimiter(rate=1.0, capacity=1.0, clock=clock))
    limited.reserve = 0.0
    limited.press_combo("F1")  # empties the bucket
    r = runner(tmp_path, clock, s="def run(bot):\n    yield bot.press('f2')\n")
    r.start("s")
    assert not r.step(WorldState(), limited)  # f2 dropped
    clock.now = 1.0
    assert r.step(WorldState(), limited)
    assert fake.sent_lines == ["K F1", "K f2"]


BROKEN = ["def run(bot):\n    yield 1 / 0\n", "x = (", "import sys; sys.exit()"]


@pytest.mark.parametrize("src", BROKEN)
def test_a_broken_script_stops_without_crashing(tmp_path: Path, src: str) -> None:
    r, inp = runner(tmp_path, s=src), FakeInput()
    if r.start("s"):
        ticks(r, inp, 2)
    assert r.running is None and inp.sent_lines == []


def test_timers_run_at_once_then_every_n_seconds(tmp_path: Path) -> None:
    clock = Clock()
    r, inp = runner(tmp_path, clock, eat="def run(bot):\n    yield bot.press('f5')\n"), FakeInput()
    r.timers = parse_timers("eat 60")
    assert r.poll() == "eat"
    ticks(r, inp, 3)
    assert r.poll() is None
    clock.now = 60.0
    assert r.poll() == "eat"


def test_parse_timers() -> None:
    assert parse_timers(" eat 60, rope 2.5 ,") == [("eat", 60.0), ("rope", 2.5)]
    for bad in ("eat", "eat 0", "eat sixty", "my script 5"):
        with pytest.raises(ValueError):
            ScriptsConfig(bad).validate()


# doc: docs/prs/scripts.md#find-picture
def test_find_returns_the_picture_centre_or_none(tmp_path: Path) -> None:
    import cv2

    rng = np.random.default_rng(1)
    frame = rng.integers(0, 255, (200, 300, 3), dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "pic.png"), cv2.cvtColor(frame[50:70, 100:130], cv2.COLOR_RGB2BGR))
    src = """
def run(bot):
    spot = bot.find("pic.png")
    yield bot.press(f"{spot[0]}x{spot[1]}" if spot else "none")
"""
    r = runner(tmp_path, s=src)
    for shown, line in ((frame, "K 115x60"), (np.zeros_like(frame), "K none")):
        inp = FakeInput()
        r.grab = lambda shown=shown: shown
        r.start("s")
        ticks(r, inp, 2)
        assert inp.sent_lines == [line]
