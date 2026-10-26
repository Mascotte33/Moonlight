"""Build the real bot loop for the GUI "Start bot" button (F-13).

The GUI must never run a bare :class:`moonlight.bot.loop.GameLoop` with no
``perceive`` — the first tick would see ``screen_ok=False`` and stop (F-13).
Instead the Start action builds the loop through
:func:`moonlight.bot.wiring.build_loop`, which wires a **real** screen capture
(frame provider + focus check), the focus gate and every enabled decision
module.

A missing Arduino requires an explicit *simulation* toggle — there is **no**
silent :class:`moonlight.hardware.input.FakeInput` fallback (F-13).

This module deliberately has no GUI (customtkinter) imports so it can be
imported and unit-tested headlessly.
"""

from __future__ import annotations

import ctypes
import logging
import re
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from moonlight.bot.state import WorldState
from moonlight.bot.wiring import build_loop, capture_providers
from moonlight.hardware.input import ClickSide, FakeInput, InputBackend
from moonlight.setup import autofind
from moonlight.setup.config import DEFAULT_CONFIG_FILE, BotConfig, ConfigError
from moonlight.setup.profile import ProfileManager

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.loop import GameLoop
    from moonlight.gui.state_bridge import HardwareController
    from moonlight.setup.calibrate import CalibrationProfile

__all__ = [
    "CONFIGS_DIR",
    "LoggedInput",
    "build_gui_loop",
    "check_layout",
    "watch_layout",
    "config_names",
    "config_path",
    "delete_config",
    "load_profile",
    "load_real_config",
    "make_backend",
    "pick_config",
    "picked_config",
    "save_config_as",
    "window_titles",
]

logger = logging.getLogger(__name__)

#: Named configs live here as ``<name>.json``; ``last.txt`` holds the pick.
CONFIGS_DIR = Path("configs")
_NAME = re.compile(r"[A-Za-z0-9_-]+")


def window_titles() -> list[str]:
    """Titles of the visible top-level windows, sorted; empty off Windows."""
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    titles: set[str] = set()

    def add(hwnd: int, _lparam: int) -> bool:
        length = user32.GetWindowTextLengthW(hwnd)
        if length and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            titles.add(buf.value)
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(proc(add), 0)
    return sorted(titles)


def config_names(configs_dir: str | Path = CONFIGS_DIR) -> list[str]:
    """The names of the saved configs, sorted (what the dropdown lists)."""
    return sorted(p.stem for p in Path(configs_dir).glob("*.json"))


def config_path(name: str, configs_dir: str | Path = CONFIGS_DIR) -> Path:
    """The file for config *name*.

    Raises:
        ConfigError: When *name* is not only letters, digits, ``-`` and ``_``.
    """
    if not _NAME.fullmatch(name):
        raise ConfigError(f"bad config name {name!r}: use letters, digits, - and _")
    return Path(configs_dir) / f"{name}.json"


def picked_config(configs_dir: str | Path = CONFIGS_DIR) -> Path:
    """The picked config file, from ``last.txt`` (else the first name).

    On first run (no configs yet) it creates ``default.json`` from
    ``moonlight_config.json``, or from :meth:`BotConfig.default`.
    """
    folder = Path(configs_dir)
    if not config_names(folder):
        folder.mkdir(parents=True, exist_ok=True)
        old = Path(DEFAULT_CONFIG_FILE)
        if old.exists():
            shutil.copyfile(old, folder / "default.json")
        else:
            BotConfig.default().save(folder / "default.json")
    last = folder / "last.txt"
    name = last.read_text(encoding="utf-8").strip() if last.exists() else ""
    names = config_names(folder)
    return folder / f"{name if name in names else names[0]}.json"


def pick_config(name: str, configs_dir: str | Path = CONFIGS_DIR) -> Path:
    """Make config *name* the picked one and remember it in ``last.txt``."""
    path = config_path(name, configs_dir)
    if not path.exists():
        raise ConfigError(f"no config named {name!r}")
    (Path(configs_dir) / "last.txt").write_text(name, encoding="utf-8")
    return path


def delete_config(name: str, configs_dir: str | Path = CONFIGS_DIR) -> Path:
    """Delete config *name*; return the picked config afterwards.

    Raises:
        ConfigError: When *name* is the only config left, or doesn't exist.
    """
    names = config_names(configs_dir)
    if name not in names:
        raise ConfigError(f"no config named {name!r}")
    if len(names) == 1:
        raise ConfigError("the only config can't be deleted")
    config_path(name, configs_dir).unlink()
    if picked_config(configs_dir).stem == name:
        return pick_config(config_names(configs_dir)[0], configs_dir)
    return picked_config(configs_dir)


def save_config_as(
    cfg: BotConfig, name: str, configs_dir: str | Path = CONFIGS_DIR, *, overwrite: bool = False
) -> Path:
    """Save *cfg* as config *name* and pick it.

    Raises:
        ConfigError: For a bad name.
        FileExistsError: When *name* exists and *overwrite* is false.
    """
    path = config_path(name, configs_dir)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg.save(path)
    return pick_config(name, configs_dir)


def load_real_config(config_path: str | Path | None = None) -> BotConfig:
    """Load the authoritative real :class:`~moonlight.setup.config.BotConfig`.

    With no *config_path* it loads the picked config (:func:`picked_config`).
    A missing file gives :meth:`~moonlight.setup.config.BotConfig.default`.
    """
    path = Path(config_path) if config_path is not None else picked_config()
    # A missing config file means "use safe defaults" (fresh install); a
    # present-but-malformed file still raises ConfigError (mirrors cli._load_config).
    if not path.exists():
        return BotConfig.default()
    return BotConfig.load(path)


def load_profile(
    profile_name: str, profiles_dir: str | Path | None = None
) -> CalibrationProfile:
    """Load and validate calibration profile *profile_name* (F-11)."""
    return ProfileManager(profiles_dir).load(profile_name)


class LoggedInput(InputBackend):
    """Sends every command to *inner* and writes it to *log* as one line."""

    def __init__(self, inner: InputBackend, log: Callable[[str], None]) -> None:
        self.inner = inner
        self._log = log

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def press_combo(self, combo: str) -> None:
        self._log(f"input: press {combo}")
        self.inner.press_combo(combo)

    def hold_combo(self, combo: str) -> None:
        self._log(f"input: hold {combo}")
        self.inner.hold_combo(combo)

    def release_combo(self, combo: str) -> None:
        self._log(f"input: release {combo}")
        self.inner.release_combo(combo)

    def mouse_move(self, dx: int, dy: int) -> None:
        self._log(f"input: mouse {dx} {dy}")
        self.inner.mouse_move(dx, dy)

    def click(self, side: ClickSide) -> None:
        self._log(f"input: click {side.name.lower()}")
        self.inner.click(side)

    def ping(self) -> bool:
        return self.inner.ping()

    def release_all(self) -> None:
        self._log("input: release all")
        self.inner.release_all()

    def close(self) -> None:
        self.inner.close()


def make_backend(config: BotConfig, hardware: HardwareController) -> InputBackend:
    """Create the input backend for the GUI Start button (F-13).

    * ``config.simulation`` set → a :class:`~moonlight.hardware.input.FakeInput`
      (the operator explicitly opted into simulation).
    * Otherwise a real :class:`~moonlight.hardware.input.ArduinoInput` opened through
      the shared :class:`~moonlight.gui.state_bridge.HardwareController`,
      which re-opens a fresh backend on every call (so a restarted loop
      always gets a live port). When no Arduino is found,
      :class:`~moonlight.hardware.input.NoArduinoFoundError` propagates — the Start
      fails with a clear message instead of silently going fake (F-13).
    """
    backend: InputBackend
    if config.simulation:
        backend = FakeInput()
    else:
        backend = hardware.connect(
            port=config.serial_port or None, baudrate=config.serial_baudrate
        )
    if config.log_commands and hardware.log is not None:
        backend = LoggedInput(backend, hardware.log.info)
    return backend


LAYOUT_CHANGED = "Layout changed, recalibrate"
LANDMARK_SLACK = 2  # px the Stop button may drift before it counts as moved


# doc: docs/prs/gui-calibration.md#layout-check
def check_layout(
    profile: CalibrationProfile, frame: Callable[[], Any], log: Callable[[str], None]
) -> None:
    """Raise ``ValueError(LAYOUT_CHANGED)`` when the client no longer fits *profile*.

    *frame* returns the client as RGB. It is called only when a check runs:
    profiles without ``window_size`` skip the size check, and a missing
    ``stop_button.png`` or an unrecorded landmark skips the Stop button check.
    """
    has_anchor = (autofind.ANCHORS_DIR / "stop_button.png").exists()
    if not has_anchor:
        log("no stop_button.png, Stop button check skipped")
    stop_check = has_anchor and profile.landmark != (0, 0)
    if profile.window_size is None and not stop_check:
        return
    rgb = frame()
    h, w = rgb.shape[:2]
    if profile.window_size is not None and tuple(profile.window_size) != (w, h):
        raise ValueError(LAYOUT_CHANGED)
    if stop_check:
        at = autofind.find_stop_button(rgb[..., ::-1], profile.ui_scale)
        lx, ly = profile.landmark
        if at is None or max(abs(at[0] - lx), abs(at[1] - ly)) > LANDMARK_SLACK:
            raise ValueError(LAYOUT_CHANGED)


LAYOUT_EVERY = 5.0  # s between layout checks while the bot runs


# doc: docs/prs/gui-calibration.md#layout-check
def watch_layout(
    profile: CalibrationProfile,
    frame: Callable[[], Any],
    log: Callable[[str], None],
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], bool]:
    """False while the layout no longer fits *profile*.

    The loop pauses while this is False, so a changed layout pauses the bot
    and it resumes when the layout fits again. Checks every ``LAYOUT_EVERY`` s.
    Window focus is not checked: the bot keeps running in the background.
    """
    ok, next_at = True, clock() + LAYOUT_EVERY

    def check() -> bool:
        nonlocal ok, next_at
        if clock() >= next_at:
            next_at = clock() + LAYOUT_EVERY
            try:
                check_layout(profile, frame, lambda _: None)
                fits = True
            except ValueError:
                fits = False
            if fits != ok:
                log("Layout fits again, resuming" if fits else f"{LAYOUT_CHANGED}, bot paused")
            ok = fits
        return ok

    return check


def build_gui_loop(
    *,
    hardware: HardwareController,
    state: WorldState,
    config_path: str | Path | None = None,
    profiles_dir: str | Path | None = None,
) -> GameLoop:
    """Build the real bot loop for the GUI Start button (F-13).

    Wires a real screen capture (frame provider + focus check) and every
    enabled decision module through :func:`moonlight.bot.wiring.build_loop`. Never
    builds a bare ``GameLoop`` with no ``perceive`` (which stops on tick 1).

    Args:
        hardware: The shared hardware controller (owns the Arduino).
        state: The shared :class:`~moonlight.bot.state.WorldState` to mutate.
        config_path: Path to the real config file; ``None`` uses the picked one.
        profiles_dir: Directory holding calibration profiles.

    Raises:
        ValueError: When no calibration profile can be loaded, or the
            layout changed since calibration (:func:`check_layout`).
        NoArduinoFoundError: When no Arduino is found and ``simulation`` is
            not enabled (F-13: no silent ``FakeInput``).
        ConfigError: When the real config fails validation.
        RuntimeError: When a calibration region is missing for an enabled
            module (raised by :func:`moonlight.bot.wiring.build_loop`).
    """
    real_config = load_real_config(config_path)
    profile = load_profile(real_config.profile, profiles_dir or real_config.profiles_dir)
    # Real screen capture: frame provider + focus check (F-13). Imported at
    # call time so importing this module never pulls in mss.
    from moonlight.perception.capture import ScreenCapture

    capture = ScreenCapture(window_title=real_config.window_title)
    log = hardware.log.info if hardware.log is not None else logger.info
    check_layout(profile, capture.get_frame, log)  # before the Arduino opens
    backend = make_backend(real_config, hardware)
    providers = capture_providers(capture, profile, real_config)
    providers["focus_check"] = watch_layout(profile, capture.get_frame, log)
    return build_loop(real_config, profile, backend, state=state, **providers)
