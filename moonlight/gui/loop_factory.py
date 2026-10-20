"""Build the real bot loop for the GUI "Start bot" button (F-13).

The GUI must never run a bare :class:`moonlight.loop.GameLoop` with no
``perceive`` — the first tick would see ``screen_ok=False`` and stop (F-13).
Instead the Start action builds the loop through
:func:`moonlight.cli.build_loop`, which wires a **real** screen capture
(frame provider + focus check), the focus gate and every enabled decision
module — exactly the wiring the CLI's ``run`` subcommand uses.

A missing Arduino requires an explicit *simulation* toggle — there is **no**
silent :class:`moonlight.input.FakeInput` fallback (F-13). Headless /
supervised runs use the CLI (``moonlight run``); the GUI is a supervisory
front-end and T33 uses the CLI.

This module deliberately has no GUI (customtkinter) imports so it can be
imported and unit-tested headlessly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from moonlight.cli import build_loop, capture_providers
from moonlight.config import DEFAULT_CONFIG_FILE, BotConfig
from moonlight.gui.config_bridge import BotConfig as GuiBotConfig
from moonlight.input import FakeInput, InputBackend
from moonlight.profile import ProfileManager
from moonlight.state import WorldState

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.calibrate import CalibrationProfile
    from moonlight.gui.state_bridge import HardwareController
    from moonlight.loop import GameLoop

__all__ = ["build_gui_loop", "load_profile", "load_real_config", "make_backend"]


def load_real_config(config_path: str | Path | None = None) -> BotConfig:
    """Load the authoritative real :class:`~moonlight.config.BotConfig`.

    The GUI Start uses the same config source as the CLI: the ``config.yaml``
    file, or :meth:`~moonlight.config.BotConfig.default` when the file does
    not exist yet.
    """
    path = Path(config_path) if config_path is not None else Path(DEFAULT_CONFIG_FILE)
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


def make_backend(
    gui_config: GuiBotConfig, hardware: HardwareController
) -> InputBackend:
    """Create the input backend for the GUI Start button (F-13).

    * ``gui_config.simulation`` set → a :class:`~moonlight.input.FakeInput`
      (the operator explicitly opted into simulation).
    * Otherwise a real :class:`~moonlight.input.ArduinoInput` opened through
      the shared :class:`~moonlight.gui.state_bridge.HardwareController`,
      which re-opens a fresh backend on every call (so a restarted loop
      always gets a live port). When no Arduino is found,
      :class:`~moonlight.input.NoArduinoFoundError` propagates — the Start
      fails with a clear message instead of silently going fake (F-13).
    """
    if gui_config.simulation:
        return FakeInput()
    return hardware.connect(
        port=gui_config.serial_port or None,
        baudrate=gui_config.serial_baudrate,
    )


def build_gui_loop(
    gui_config: GuiBotConfig,
    *,
    hardware: HardwareController,
    state: WorldState,
    config_path: str | Path | None = None,
    profiles_dir: str | Path | None = None,
) -> GameLoop:
    """Build the real bot loop for the GUI Start button (F-13).

    Wires a real screen capture (frame provider + focus check) and every
    enabled decision module through :func:`moonlight.cli.build_loop`. Never
    builds a bare ``GameLoop`` with no ``perceive`` (which stops on tick 1).

    Args:
        gui_config: The GUI-level config-bridge snapshot (serial port,
            profile name, the explicit ``simulation`` toggle).
        hardware: The shared hardware controller (owns the Arduino).
        state: The shared :class:`~moonlight.state.WorldState` to mutate.
        config_path: Path to the real config file; ``None`` uses the default.
        profiles_dir: Directory holding calibration profiles.

    Raises:
        ValueError: When no calibration profile can be loaded.
        NoArduinoFoundError: When no Arduino is found and ``simulation`` is
            not enabled (F-13: no silent ``FakeInput``).
        ConfigError: When the real config fails validation.
        RuntimeError: When a calibration region is missing for an enabled
            module (raised by :func:`moonlight.cli.build_loop`).
    """
    real_config = load_real_config(config_path)
    profile = load_profile(
        gui_config.profile_name or real_config.profile,
        profiles_dir or real_config.profiles_dir,
    )
    backend = make_backend(gui_config, hardware)
    # Real screen capture: frame provider + focus check (F-13). Imported at
    # call time so importing this module never pulls in mss.
    from moonlight.capture import ScreenCapture

    capture = ScreenCapture()
    return build_loop(
        real_config,
        profile,
        backend,
        state=state,
        **capture_providers(capture, profile, real_config),
    )
