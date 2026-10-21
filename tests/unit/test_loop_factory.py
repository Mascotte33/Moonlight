"""F-13: the GUI "Start bot" builds the REAL loop through ``wiring.build_loop``.

Covers the four F-13 guarantees (see ``docs/senior_review_findings.md``):

* :func:`make_backend` returns a :class:`FakeInput` **only** when the explicit
  ``simulation`` toggle is on — there is no silent fallback.
* :func:`make_backend` re-opens a **fresh** backend on every call (a
  restarted loop always gets a live port; the previous backend is closed).
* :func:`build_gui_loop` wires a real ``perceive`` (frame provider + focus
  check) — it never builds a bare :class:`GameLoop` without ``perceive``
  (which would stop on tick 1).
* With ``simulation`` off and no Arduino present, the start fails with a clear
  :class:`NoArduinoFoundError` instead of silently going fake.

The module under test (``moonlight.gui.loop_factory``) deliberately has no
customtkinter imports, so this whole module is headless-safe.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from moonlight.bot.state import WorldState
from moonlight.gui.loop_factory import build_gui_loop, make_backend
from moonlight.gui.state_bridge import HardwareController
from moonlight.hardware.input import FakeInput, NoArduinoFoundError
from moonlight.setup.calibrate import REGIONS, CalibrationProfile, Region
from moonlight.setup.config import BotConfig


class _SpyBackend(FakeInput):
    """A :class:`FakeInput` that records :meth:`close` calls (for re-open)."""

    def __init__(self) -> None:
        super().__init__()
        self.close_called = False

    def close(self) -> None:
        self.close_called = True
        super().close()


def _config(**overrides: object) -> BotConfig:
    return replace(BotConfig.default(), **overrides)  # type: ignore[arg-type]


def _config_file(tmp_path: Path, *, simulation: bool) -> Path:
    """The real config file Start reads, naming the ``test_profile``."""
    path = tmp_path / "moonlight_config.json"
    _config(profile="test_profile", simulation=simulation).save(path)
    return path


@pytest.fixture()
def profile_dir(tmp_path: Path) -> Path:
    """A profiles dir holding a *full* calibration profile (every region)."""
    d = tmp_path / "profiles"
    d.mkdir()
    p = CalibrationProfile(landmark=(100, 100))
    for name in REGIONS:
        p.set_region(name, Region(0, 0, 10, 10))
    p.save(d / "test_profile.json")
    return d


@pytest.fixture()
def state() -> WorldState:
    return WorldState()


# -- make_backend: explicit simulation toggle ------------------------------


def test_make_backend_simulation_returns_fake() -> None:
    """simulation ON -> FakeInput, with no Arduino connection attempted."""
    hw = HardwareController()  # no factory: must NOT be consulted
    backend = make_backend(_config(simulation=True), hw)
    assert isinstance(backend, FakeInput)
    assert hw.backend is None  # connect() was never called


def test_make_backend_real_when_simulation_off() -> None:
    """simulation OFF -> a real backend is opened via the controller."""
    calls: list[_SpyBackend] = []

    def factory(port: str | None, baudrate: int) -> _SpyBackend:
        b = _SpyBackend()
        calls.append(b)
        return b

    hw = HardwareController(input_factory=factory)
    backend = make_backend(_config(simulation=False, serial_port="COM9"), hw)
    assert backend is calls[0]
    assert backend is hw.backend
    assert calls[0] is not None


def test_make_backend_reopens_fresh_on_restart() -> None:
    """A second start closes the previous backend and opens a fresh one."""
    calls: list[_SpyBackend] = []

    def factory(port: str | None, baudrate: int) -> _SpyBackend:
        b = _SpyBackend()
        calls.append(b)
        return b

    hw = HardwareController(input_factory=factory)
    cfg = _config(simulation=False)
    first = make_backend(cfg, hw)
    second = make_backend(cfg, hw)
    assert isinstance(first, _SpyBackend)
    assert isinstance(second, _SpyBackend)
    assert first is not second
    assert first.close_called  # previous connection closed on re-open
    assert second is hw.backend  # the fresh backend is the active one


# -- build_gui_loop: real perceive + no silent FakeInput --------------------


def test_build_gui_loop_wires_real_perceive(
    profile_dir: Path, tmp_path: Path, state: WorldState
) -> None:
    """Start builds the real loop: a wired ``perceive`` (never bare GameLoop)."""

    def factory(port: str | None, baudrate: int) -> _SpyBackend:
        return _SpyBackend()

    hw = HardwareController(input_factory=factory)
    loop = build_gui_loop(
        hardware=hw,
        state=state,
        config_path=_config_file(tmp_path, simulation=True),
        profiles_dir=profile_dir,
    )
    # A bare GameLoop has _perceive is None; build_loop always wires one.
    assert loop._perceive is not None


def test_build_gui_loop_no_silent_fakeinput(
    profile_dir: Path, tmp_path: Path, state: WorldState
) -> None:
    """simulation OFF + no Arduino -> NoArduinoFoundError (no silent FakeInput)."""

    def factory(port: str | None, baudrate: int) -> FakeInput:
        raise NoArduinoFoundError("no Arduino Leonardo found on the bus")

    hw = HardwareController(input_factory=factory)
    with pytest.raises(NoArduinoFoundError):
        build_gui_loop(
            hardware=hw,
            state=state,
            config_path=_config_file(tmp_path, simulation=False),
            profiles_dir=profile_dir,
        )
    # The controller recorded the error and has no active backend.
    assert hw.backend is None
    assert hw.last_error is not None
