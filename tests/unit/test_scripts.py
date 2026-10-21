"""Offline tests for the user script loader (T27.D)."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonlight.bot.scripts import BotScript, Condition, ScriptRunner, Trigger
from moonlight.bot.state import WorldState
from moonlight.hardware.input import ClickSide, FakeInput

GOOD = """
from moonlight.bot.scripts import BotScript
class S(BotScript):
    def tick(self, state, input):
        input.press_combo("f2")
"""

ALL_CALLS = """
from moonlight.bot.scripts import BotScript
from moonlight.hardware.input import ClickSide
class S(BotScript):
    def tick(self, state, input):
        input.press_combo('f1'); input.hold_combo('a')
        input.mouse_move(1, 1); input.click(ClickSide.LEFT)
"""


def ok_state(hp: float = 100.0) -> WorldState:
    return WorldState(client_focused=True, screen_ok=True, hp_percent=hp)


def runner(tmp_path: Path, **srcs: str) -> ScriptRunner:
    for n, s in srcs.items():
        (tmp_path / f"{n}.py").write_text(s)
    r = ScriptRunner(tmp_path)
    r.load_all()
    return r


def test_load_valid(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    assert list(r.scripts) == ["good"]
    assert isinstance(r.scripts["good"], BotScript)


def test_syntax_error_does_not_crash(tmp_path: Path) -> None:
    r = runner(tmp_path, bad="def (:\n", good=GOOD)
    assert list(r.scripts) == ["good"]


def test_import_error_does_not_crash(tmp_path: Path) -> None:
    assert runner(tmp_path, bad="import no_such_module_xyz\n").scripts == {}


def test_raise_at_import_does_not_crash(tmp_path: Path) -> None:
    assert runner(tmp_path, bad="raise RuntimeError('x')\n").scripts == {}


def test_sys_exit_at_import_does_not_crash(tmp_path: Path) -> None:
    assert runner(tmp_path, bad="import sys\nsys.exit(1)\n").scripts == {}


def test_no_subclass(tmp_path: Path) -> None:
    assert runner(tmp_path, empty="x = 1\n").scripts == {}


def test_ctor_error_does_not_crash(tmp_path: Path) -> None:
    src = (
        "from moonlight.bot.scripts import BotScript\n"
        "class S(BotScript):\n    def __init__(self): raise ValueError\n"
    )
    assert runner(tmp_path, s=src).scripts == {}


def test_missing_dir() -> None:
    assert ScriptRunner(Path("/definitely/not/here")).load_all() == []


def test_empty_dir(tmp_path: Path) -> None:
    assert ScriptRunner(tmp_path).load_all() == []


def test_missing_file_load(tmp_path: Path) -> None:
    assert ScriptRunner(tmp_path).load(tmp_path / "nope.py") is False


def test_tick_runs_script_and_emits(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    fake = FakeInput()
    assert r.start("good", 1)
    assert r.tick(ok_state(), fake) is True
    assert fake.sent_lines == ["K f2"]


def test_idle_runner_does_nothing(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    fake = FakeInput()
    assert r.tick(ok_state(), fake) is False
    assert fake.sent_lines == []


def test_exception_in_tick_swallowed(tmp_path: Path) -> None:
    src = (
        "from moonlight.bot.scripts import BotScript\n"
        "class S(BotScript):\n    def tick(self, state, input): 1/0\n"
    )
    r = runner(tmp_path, boom=src)
    r.start("boom", 2)
    assert r.tick(ok_state(), FakeInput()) is True
    assert r.tick(ok_state(), FakeInput()) is True


def test_start_unknown_or_bad_ticks(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    assert not r.start("nope")
    assert not r.start("good", 0)


def test_runs_exactly_n_ticks(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    fake = FakeInput()
    r.start("good", 3)
    for _ in range(6):
        r.tick(ok_state(), fake)
    assert len(fake.sent_lines) == 3
    assert r.active is None


def test_trigger_fires_and_stops(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    r.triggers.append(Trigger(Condition("hp_percent", "<", 30), "good", 2))
    fake = FakeInput()
    r.tick(ok_state(20), fake)
    r.tick(ok_state(90), fake)  # still running its 2nd tick
    r.tick(ok_state(90), fake)  # done, condition false
    assert len(fake.sent_lines) == 2
    assert r.active is None


def test_trigger_not_fired_when_false(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    r.triggers.append(Trigger(Condition("hp_percent", "<", 30), "good"))
    fake = FakeInput()
    r.tick(ok_state(80), fake)
    assert fake.sent_lines == []


def test_trigger_unknown_script_ignored(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    r.triggers.append(Trigger(Condition("hp_percent", "<", 30), "missing"))
    assert r.tick(ok_state(1), FakeInput()) is False


@pytest.mark.parametrize(
    ("op", "val", "expected"),
    [
        ("<", 50, False),
        ("<=", 50, True),
        (">", 49, True),
        (">=", 51, False),
        ("==", 50, True),
        ("!=", 50, False),
    ],
)
def test_condition_ops(op: str, val: float, expected: bool) -> None:
    assert Condition("hp_percent", op, val).evaluate(ok_state(50)) is expected


def test_condition_validation() -> None:
    with pytest.raises(ValueError):
        Condition("hp_percent", "~", 1)
    with pytest.raises(ValueError):
        Condition("_lock", "==", 1)


def test_condition_missing_or_nonnumeric_field() -> None:
    assert not Condition("nonexistent", "<", 1).evaluate(ok_state())
    assert not Condition("position", "<", 1).evaluate(ok_state())


@pytest.mark.parametrize(
    "state",
    [WorldState(), WorldState(client_focused=True), WorldState(screen_ok=True)],
)
def test_fail_closed_no_input(tmp_path: Path, state: WorldState) -> None:
    r = runner(tmp_path, s=ALL_CALLS)
    fake = FakeInput()
    r.start("s", 1)
    r.tick(state, fake)
    assert fake.sent_lines == []


def test_safe_state_passes_all_calls(tmp_path: Path) -> None:
    r = runner(tmp_path, s=ALL_CALLS)
    fake = FakeInput()
    r.start("s")
    r.tick(ok_state(), fake)
    assert len(fake.sent_lines) == 4


def test_unsafe_state_does_not_start_trigger(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    r.triggers.append(Trigger(Condition("hp_percent", "<", 30), "good"))
    assert r.tick(WorldState(hp_percent=1), FakeInput()) is False
    assert r.active is None


def test_yields_to_higher_priority(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    fake = FakeInput()
    r.start("good", 2)
    assert r.tick(ok_state(), fake, higher_priority_active=True) is False
    assert fake.sent_lines == [] and r.remaining == 2
    r.tick(ok_state(), fake)
    assert len(fake.sent_lines) == 1


def test_stop(tmp_path: Path) -> None:
    r = runner(tmp_path, good=GOOD)
    r.start("good", 5)
    r.stop()
    assert r.tick(ok_state(), FakeInput()) is False


def test_click_wire_format(tmp_path: Path) -> None:
    src = (
        "from moonlight.bot.scripts import BotScript\n"
        "from moonlight.hardware.input import ClickSide\n"
        "class S(BotScript):\n"
        "    def tick(self, state, input): input.click(ClickSide.RIGHT)\n"
    )
    r = runner(tmp_path, s=src)
    fake = FakeInput()
    r.start("s")
    r.tick(ok_state(), fake)
    assert fake.sent_lines == [f"C {ClickSide.RIGHT.value}"]


def test_example_script_runs() -> None:
    r = ScriptRunner()
    assert "example" in r.load_all()
    fake = FakeInput()
    r.start("example")
    r.tick(ok_state(), fake)
    assert fake.sent_lines == ["K f1"]
