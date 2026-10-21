"""MOONLIGHT command line (T40): ``python -m moonlight`` / ``moonlight``.

Subcommands::

    moonlight run [profile]   build the bot from the config and run the loop
    moonlight profiles        list calibration profiles
    moonlight calibrate ...   the calibration tool (moonlight.calibrate)
    moonlight check           serial port? profile? perception imports?


The bot itself is assembled in :mod:`moonlight.wiring`.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Sequence
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from moonlight.calibrate import CalibrationError, CalibrationProfile
from moonlight.config import DEFAULT_CONFIG_FILE, BotConfig, ConfigError
from moonlight.profile import ProfileManager
from moonlight.wiring import build_loop, capture_providers, check_module_regions

__all__ = ["main"]

#: Modules ``check`` imports to prove the perception stack loads.
_PERCEPTION_MODULES = (
    "cv2",
    "moonlight.capture",
    "moonlight.perception.vitals",
    "moonlight.perception.battle",
    "moonlight.perception.floor",
    "moonlight.perception.minimap",
)


def _version() -> str:
    try:
        return version("moonlight")
    except PackageNotFoundError:
        return "0.1.0"


# -- subcommands ----------------------------------------------------------------


def _load_config(args: argparse.Namespace) -> BotConfig:
    path = Path(args.config)
    if path.exists():
        return BotConfig.load(path)
    if args.config != DEFAULT_CONFIG_FILE:
        raise ConfigError(f"config file not found: {path}")
    return BotConfig.default()


def _profiles(args: argparse.Namespace, config: BotConfig | None = None) -> ProfileManager:
    directory = args.profiles_dir or (config.profiles_dir if config is not None else None)
    return ProfileManager(directory)


def _cmd_run(args: argparse.Namespace) -> int:
    from moonlight.capture import ScreenCapture
    from moonlight.input import ArduinoInput

    config = _load_config(args)
    if getattr(args, "minimap_start", None) is not None:
        config = replace(config, minimap_start=tuple(args.minimap_start))
    name = args.profile or config.profile
    profile = _profiles(args, config).load(name)
    capture = ScreenCapture()
    backend = ArduinoInput(config.serial_port)
    try:
        loop = build_loop(
            config, profile, backend, **capture_providers(capture, profile, config)
        )
        print(f"moonlight {_version()}: running profile {name!r}; F12 = kill switch")
        loop.run()
    finally:
        backend.close()  # releases all inputs and stops the heartbeat
    return 0



def _cmd_profiles(args: argparse.Namespace) -> int:
    manager = _profiles(args)
    names = manager.list_profiles()
    if not names:
        print(f"No profiles in {manager.directory}")
    for name in names:
        print(name)
    return 0


def _cmd_calibrate(args: argparse.Namespace) -> int:
    from moonlight import calibrate

    return calibrate.main(list(args.calibrate_args))


def _cmd_check(args: argparse.Namespace) -> int:
    ok = True

    def report(passed: bool, what: str) -> None:
        nonlocal ok
        ok = ok and passed
        print(f"[{'OK' if passed else 'FAIL'}] {what}")

    try:
        config = _load_config(args)
        report(True, f"config ({args.config})")
    except ConfigError as exc:
        report(False, f"config: {exc}")
        config = None
    try:
        from moonlight.input import find_arduino_port

        port = config.serial_port if config and config.serial_port else find_arduino_port()
        report(port is not None, f"serial port: {port or 'no Arduino found'}")
    except Exception as exc:  # noqa: BLE001 - pyserial missing etc.
        report(False, f"serial port: {exc}")
    name = args.profile or (config.profile if config else None)
    profile: CalibrationProfile | None = None
    try:
        if name is None:
            raise ValueError("no profile name")
        profile = _profiles(args, config).load(name)
        report(True, f"profile {name!r}")
    except Exception as exc:  # noqa: BLE001 - ValueError / CalibrationError
        report(False, f"profile {name!r}: {exc}")
    if profile is not None and config is not None:
        for module, missing in check_module_regions(profile, config).items():
            if missing:
                report(
                    False,
                    f"module {module}: missing calibration region(s) "
                    f"{', '.join(missing)}",
                )
            else:
                report(True, f"module {module}: OK")
    for mod in _PERCEPTION_MODULES:
        try:
            importlib.import_module(mod)
            report(True, f"import {mod}")
        except Exception as exc:  # noqa: BLE001
            report(False, f"import {mod}: {exc}")
    # F-21: log OCR is optional (not wired into the tick), so a missing
    # Tesseract is a warning, not a failed check.
    from moonlight.perception.logocr import health

    ocr_ok, detail = health()
    print(f"[{'OK' if ocr_ok else 'WARN'}] log OCR: {detail}")
    return 0 if ok else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moonlight", description="MOONLIGHT Tibia bot.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_FILE,
        help=f"bot config JSON (default: ./{DEFAULT_CONFIG_FILE}; built-in defaults if absent)",
    )
    parser.add_argument("--profiles-dir", type=Path, default=None, help="profiles directory")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the bot")
    run.add_argument("profile", nargs="?", default=None, help="profile (default: config)")
    run.add_argument(
        "--minimap-start",
        nargs=3,
        type=int,
        default=None,
        metavar=("X", "Y", "Z"),
        help="absolute (x, y, z) seed for minimap position tracking (F-03); "
        "overrides the config's minimap_start",
    )
    run.set_defaults(func=_cmd_run)
    sub.add_parser("profiles", help="list calibration profiles").set_defaults(
        func=_cmd_profiles
    )
    # Arguments after "calibrate" are passed through (see main()).
    sub.add_parser("calibrate", help="calibration tool (args passed through)").set_defaults(
        func=_cmd_calibrate
    )
    check = sub.add_parser("check", help="check serial port, profile, perception")
    check.add_argument("profile", nargs="?", default=None)
    check.set_defaults(func=_cmd_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = _parser()
    args, extra = parser.parse_known_args(argv)
    if args.command == "calibrate":
        args.calibrate_args = extra
    elif extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    try:
        code: int = args.func(args)
    except (ConfigError, CalibrationError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
    return code
