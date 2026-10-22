"""F-13: the GUI "Start bot" builds the REAL loop through ``wiring.build_loop``.

Covers the four F-13 guarantees from the senior review (commit c8aa239):

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

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from moonlight.bot.state import WorldState
from moonlight.gui.loop_factory import build_gui_loop, make_backend
from moonlight.gui.state_bridge import HardwareController, LogFeed
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


@pytest.fixture(autouse=True)
def _no_owner_anchors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the owner's local stop_button.png out of every Start test.

    ``_anchor`` is cached, so it is cleared before and after each test.
    """
    import moonlight.setup.autofind as autofind

    monkeypatch.setattr(autofind, "ANCHORS_DIR", tmp_path / "no-anchors")
    autofind._anchor.cache_clear()
    yield
    autofind._anchor.cache_clear()


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


@pytest.mark.parametrize("on", [True, False])
def test_log_commands_switch(on: bool) -> None:
    log = LogFeed()
    backend = make_backend(_config(simulation=True, log_commands=on), HardwareController(log=log))
    backend.press_combo("F1")
    lines = [msg for _, _, msg in log.recent()]
    assert lines == (["input: press F1"] if on else [])


def test_start_passes_window_title_to_capture(
    profile_dir: Path, tmp_path: Path, state: WorldState, monkeypatch: pytest.MonkeyPatch
) -> None:
    import moonlight.perception.capture as capture

    seen: list[str] = []

    class SpyCapture(capture.ScreenCapture):
        def __init__(self, window_title: str = capture.TIBIA_WINDOW_TITLE) -> None:
            seen.append(window_title)
            super().__init__(window_title=window_title)

    monkeypatch.setattr(capture, "ScreenCapture", SpyCapture)
    path = _config_file(tmp_path, simulation=True)
    replace(BotConfig.load(path), window_title="Tibia - Test").save(path)
    build_gui_loop(
        hardware=HardwareController(), state=state, config_path=path, profiles_dir=profile_dir
    )
    assert seen == ["Tibia - Test"]


# -- layout check at Start (C4.F1) -------------------------------------------


@pytest.fixture()
def anchors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty anchors dir, so the owner's real images never leak in."""
    import moonlight.setup.autofind as autofind

    d = tmp_path / "anchors"
    d.mkdir()
    monkeypatch.setattr(autofind, "ANCHORS_DIR", d)
    return d


def _start_with_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frame: np.ndarray, **profile: Any
) -> LogFeed:
    """Start against a fake client window showing *frame*; return the log."""
    import moonlight.perception.capture as capture

    class FakeWindow(capture.ScreenCapture):
        def get_frame(self, regions: object = None) -> np.ndarray:
            return frame

    monkeypatch.setattr(capture, "ScreenCapture", FakeWindow)
    d = tmp_path / "profiles"
    d.mkdir(exist_ok=True)
    p = CalibrationProfile(**profile)
    for name in REGIONS:
        p.set_region(name, Region(0, 0, 10, 10))
    p.save(d / "test_profile.json")
    log = LogFeed()
    build_gui_loop(
        hardware=HardwareController(log=log),
        state=WorldState(),
        config_path=_config_file(tmp_path, simulation=True),
        profiles_dir=d,
    )
    return log


def test_layout_size_changed_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anchors: Path
) -> None:
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="Layout changed, recalibrate"):
        _start_with_window(tmp_path, monkeypatch, frame, window_size=(1024, 768))


def test_layout_same_size_starts_and_logs_the_skipped_stop_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anchors: Path
) -> None:
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    log = _start_with_window(tmp_path, monkeypatch, frame, window_size=(800, 600))
    assert "no stop_button.png, Stop button check skipped" in [m for _, _, m in log.recent()]


def test_layout_stop_button_must_stay_at_the_landmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anchors: Path
) -> None:
    stop = np.random.RandomState(5).randint(0, 255, (13, 42)).astype(np.uint8)
    cv2.imwrite(str(anchors / "stop_button.png"), stop)
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    frame[300:313, 500:542] = stop[..., None]
    _start_with_window(tmp_path, monkeypatch, frame, landmark=(500, 300))
    with pytest.raises(ValueError, match="Layout changed, recalibrate"):
        _start_with_window(tmp_path, monkeypatch, frame, landmark=(400, 300))


def test_watch_layout_pauses_until_the_layout_fits_again(anchors: Path) -> None:
    from moonlight.gui.loop_factory import LAYOUT_EVERY, watch_layout

    now = [0.0]
    frame = [np.zeros((600, 800, 3), dtype=np.uint8)]
    logged: list[str] = []
    profile = CalibrationProfile(window_size=(800, 600))
    focused = watch_layout(profile, lambda: frame[0], logged.append, lambda: now[0])
    assert focused()
    frame[0] = np.zeros((500, 800, 3), dtype=np.uint8)
    assert focused()  # not checked again yet
    now[0] = LAYOUT_EVERY
    assert not focused() and not focused()
    frame[0] = np.zeros((600, 800, 3), dtype=np.uint8)
    now[0] = 2 * LAYOUT_EVERY
    assert focused()
    assert logged == ["Layout changed, recalibrate, bot paused", "Layout fits again, resuming"]
