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

import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

import cv2
import numpy as np
import pytest

import moonlight.gui.loop_factory as loop_factory
from moonlight.calibration.calibrate import REGIONS, CalibrationProfile, Region
from moonlight.core.config import BotConfig
from moonlight.core.state import WorldState
from moonlight.gui.loop_factory import build_gui_loop, make_backend
from moonlight.gui.state_bridge import HardwareController, LogFeed
from moonlight.hardware.cursor_chase import CursorChaseInput
from moonlight.hardware.input import FakeInput, NoArduinoFoundError


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
    """The real config file Start reads."""
    path = tmp_path / "moonlight_config.json"
    _config(simulation=simulation).save(path)
    return path


@pytest.fixture()
def profile_dir(tmp_path: Path) -> Path:
    """A *full* calibration (every region); Start is given its file."""
    d = tmp_path / "profiles"
    d.mkdir()
    p = CalibrationProfile(landmark=(100, 100))
    for name in REGIONS:
        p.set_region(name, Region(0, 0, 10, 10))
    p.save(d / "calibration.json")
    return d / "calibration.json"


@pytest.fixture(autouse=True)
def _no_owner_anchors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the owner's local stop_button.png out of every Start test.

    ``_anchor`` is cached, so it is cleared before and after each test.
    """
    import moonlight.calibration.autofind as autofind

    monkeypatch.setattr(autofind, "ANCHORS_DIR", tmp_path / "no-anchors")
    autofind._anchor.cache_clear()
    yield
    autofind._anchor.cache_clear()


@pytest.fixture(autouse=True)
def _no_display(monkeypatch: pytest.MonkeyPatch) -> None:
    """The clicker's screen size comes from mss, which needs a display CI lacks."""
    monkeypatch.setattr("moonlight.core.wiring._absolute_screen", lambda: (0, 0, 1920, 1080))
    # Start must never raise the owner's real game window during tests.
    monkeypatch.setattr("moonlight.perception.capture.focus_window", lambda _title: False)
    # The side-panel readers need a real client; their own tests are below.
    monkeypatch.setattr(loop_factory, "require_side_panel", lambda _frame, _cfg: None)


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


def _unwrapped(backend: Any) -> Any:
    """The Arduino backend inside Linux's CursorChaseInput."""
    return backend.inner if isinstance(backend, CursorChaseInput) else backend


def test_make_backend_wraps_the_arduino_for_absolute_moves_on_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hw = HardwareController(input_factory=lambda port, baudrate: _SpyBackend())
    for platform, wrapped in (("linux", True), ("win32", False)):
        monkeypatch.setattr(sys, "platform", platform)
        backend = make_backend(_config(simulation=False), hw)
        assert isinstance(backend, CursorChaseInput) is wrapped


def test_make_backend_real_when_simulation_off() -> None:
    """simulation OFF -> a real backend is opened via the controller."""
    calls: list[_SpyBackend] = []

    def factory(port: str | None, baudrate: int) -> _SpyBackend:
        b = _SpyBackend()
        calls.append(b)
        return b

    hw = HardwareController(input_factory=factory)
    backend = _unwrapped(make_backend(_config(simulation=False, serial_port="COM9"), hw))
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
    first = _unwrapped(make_backend(cfg, hw))
    second = _unwrapped(make_backend(cfg, hw))
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
        calibration_file=profile_dir,
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
            calibration_file=profile_dir,
        )
    # The controller recorded the error and has no active backend.
    assert hw.backend is None
    assert hw.last_error is not None


@pytest.mark.parametrize("on", [True, False])
def test_log_commands_switch(on: bool) -> None:
    log = LogFeed()
    backend = make_backend(_config(simulation=True, log_commands=on), HardwareController(log=log))
    backend.press_combo("F1")
    assert log.text().endswith("[info] input: press F1") if on else log.text() == ""


def test_start_captures_any_tibia_client(
    profile_dir: Path, tmp_path: Path, state: WorldState, monkeypatch: pytest.MonkeyPatch
) -> None:
    import moonlight.perception.capture as capture

    seen: list[str] = []

    class SpyCapture(capture.ScreenCapture):
        def __init__(
            self, backend: str = "mss", window_title: str = capture.TIBIA_WINDOW_TITLE
        ) -> None:
            seen.append(window_title)
            super().__init__(window_title=window_title)

    monkeypatch.setattr(capture, "ScreenCapture", SpyCapture)
    path = _config_file(tmp_path, simulation=True)
    replace(BotConfig.load(path), window_title="Tibia - Test").save(path)
    build_gui_loop(
        hardware=HardwareController(), state=state, config_path=path, calibration_file=profile_dir
    )
    assert seen == [capture.TIBIA_WINDOW_TITLE]  # any client, never one character


# -- layout check at Start (C4.F1) -------------------------------------------


@pytest.fixture()
def anchors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty anchors dir, so the owner's real images never leak in."""
    import moonlight.calibration.autofind as autofind

    d = tmp_path / "anchors"
    d.mkdir()
    monkeypatch.setattr(autofind, "ANCHORS_DIR", d)
    return d


def _start_with_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frame: np.ndarray,
    *,
    raise_client: bool = False,
    **profile: Any,
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
    p.save(d / "calibration.json")
    log = LogFeed()
    build_gui_loop(
        hardware=HardwareController(log=log),
        state=WorldState(),
        config_path=_config_file(tmp_path, simulation=True),
        calibration_file=d / "calibration.json",
        raise_client=raise_client,
    )
    return log


@pytest.mark.parametrize("clicked", [True, False])
def test_only_a_clicked_start_raises_the_client_before_the_layout_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anchors: Path, clicked: bool
) -> None:
    import moonlight.gui.loop_factory as loop_factory
    import moonlight.perception.capture as capture

    events: list[str] = []

    def spy_focus(title: str) -> bool:
        events.append(title)
        return True

    monkeypatch.setattr(capture, "focus_window", spy_focus)
    monkeypatch.setattr(loop_factory, "RAISE_WAIT", 0)
    real_check = loop_factory.check_layout

    def spy_check(*args: Any) -> None:
        events.append("check")
        real_check(*args)

    monkeypatch.setattr(loop_factory, "check_layout", spy_check)
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    _start_with_window(tmp_path, monkeypatch, frame, raise_client=clicked)
    assert events == ([capture.TIBIA_WINDOW_TITLE] if clicked else []) + ["check"]


def test_layout_size_changed_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anchors: Path
) -> None:
    frame = np.zeros((600, 800, 3), dtype=np.uint8)  # nothing on it to find
    with pytest.raises(ValueError, match="not found: open them in Tibia"):
        _start_with_window(tmp_path, monkeypatch, frame, window_size=(1024, 768))


# doc: docs/prs/pause-stop.md#size-slack
def test_layout_a_pixel_or_two_off_still_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anchors: Path
) -> None:
    frame = np.zeros((1008, 1034, 3), dtype=np.uint8)
    _start_with_window(tmp_path, monkeypatch, frame, window_size=(1034, 1009))
    with pytest.raises(ValueError, match="not found: open them in Tibia"):
        _start_with_window(tmp_path, monkeypatch, frame, window_size=(1034, 1011))


def test_layout_same_size_starts_and_logs_the_skipped_stop_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anchors: Path
) -> None:
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    log = _start_with_window(tmp_path, monkeypatch, frame, window_size=(800, 600))
    assert "no stop_button.png, Stop button check skipped" in log.text(n=1000)


def test_layout_stop_button_must_stay_at_the_landmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anchors: Path
) -> None:
    stop = np.random.RandomState(5).randint(0, 255, (13, 42)).astype(np.uint8)
    cv2.imwrite(str(anchors / "stop_button.png"), stop)
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    frame[300:313, 500:542] = stop[..., None]
    _start_with_window(tmp_path, monkeypatch, frame, landmark=(500, 300))
    with pytest.raises(ValueError, match="not found: open them in Tibia"):
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


def test_the_absolute_pointer_is_used_on_the_main_monitor() -> None:
    from moonlight.core.wiring import _pointer_screen

    assert _pointer_screen(lambda: (100, 25)) == (0, 0, 1920, 1080)


def test_a_client_on_another_monitor_moves_the_mouse_relative() -> None:
    """Windows maps the HID absolute pointer to the main monitor only."""
    from moonlight.core.wiring import _pointer_screen

    assert _pointer_screen(lambda: (-1680, 25)) is None
    assert _pointer_screen(None) is None


# doc: docs/prs/auto-calibration.md#on-start
def test_a_changed_layout_is_calibrated_again_and_saved(tmp_path: Path) -> None:
    from moonlight.calibration.calibrate import OPTIONAL_REGIONS
    path = tmp_path / "calibration.json"
    old = CalibrationProfile(window_size=(1024, 768))
    for name in REGIONS:
        old.set_region(name, Region(0, 0, 10, 10))
    old.save(path)
    found = {n: (5, 5, 20, 20) for n in REGIONS + OPTIONAL_REGIONS}
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    lines: list[str] = []
    with mock.patch("moonlight.calibration.auto.find_regions", return_value=(found, [])):
        got = loop_factory.start_calibration(path, lambda: frame, lines.append)
    assert got.window_size == (800, 600)
    assert CalibrationProfile.load(path).get_region("battle_list").to_tuple == (5, 5, 20, 20)
    assert path.with_suffix(".png").exists()  # the Calibration tab's summary
    assert any("saved to" in line for line in lines)


def test_a_fitting_calibration_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "calibration.json"
    old = CalibrationProfile(window_size=(800, 600))
    for name in REGIONS:
        old.set_region(name, Region(0, 0, 10, 10))
    old.save(path)
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    with mock.patch("moonlight.calibration.auto.find_regions") as find:
        loop_factory.start_calibration(path, lambda: frame, lambda _l: None)
    find.assert_not_called()


def test_optional_areas_never_stop_the_start(tmp_path: Path) -> None:
    found = {n: (5, 5, 20, 20) for n in REGIONS}  # no Players window, no condition icons
    lines: list[str] = []
    with mock.patch("moonlight.calibration.auto.find_regions", return_value=(found, [])):
        loop_factory.start_calibration(
            tmp_path / "c.json", lambda: np.zeros((6, 8, 3), np.uint8), lines.append
        )
    assert any("Players window, Condition icons not found" in line for line in lines)


@pytest.mark.parametrize(
    ("cap", "level", "mana", "message"),
    [
        (None, 25, 3, "Cap box not found: open it in Tibia"),
        (100, None, 3, r"Skills window \(level\) not found"),
        (100, 25, None, "Potion counts on the action bar not found"),
    ],
)
def test_the_side_panel_readers_are_required(
    cap: int | None, level: int | None, mana: int | None, message: str
) -> None:
    from moonlight.refill.refill import RefillConfig

    cfg = replace(BotConfig.default(), refill=RefillConfig(mana_potions_below=10))
    with (
        mock.patch("moonlight.perception.cap.CapReader.__init__", return_value=None),
        mock.patch("moonlight.perception.cap.CapReader.cap", return_value=cap),
        mock.patch("moonlight.perception.stamina.LevelReader.level", return_value=level),
        mock.patch(
            "moonlight.perception.potions.PotionCounter.counts",
            return_value={"health": 1, "mana": mana},
        ),
        mock.patch("moonlight.perception.potions.PotionCounter.__init__", return_value=None),
        pytest.raises(ValueError, match=message),
    ):
        _real_require_side_panel(lambda: np.zeros((6, 8, 3), np.uint8), cfg)


_real_require_side_panel = loop_factory.require_side_panel
