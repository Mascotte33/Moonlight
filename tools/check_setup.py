#!/usr/bin/env python3
"""Check the bot is ready to run: config, serial port, profile, perception.

Usage:
    python tools/check_setup.py                 # profile from the config
    python tools/check_setup.py my-profile      # check a specific profile
    python tools/check_setup.py --config bot.json --profiles-dir path/

Prints one ``[OK]`` / ``[FAIL]`` line per check. Exit code 0 = all passed,
1 = something failed, 2 = bad arguments / config.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from moonlight.calibrate import CalibrationProfile
from moonlight.config import DEFAULT_CONFIG_FILE, BotConfig, ConfigError
from moonlight.profile import ProfileManager
from moonlight.wiring import check_module_regions

#: Modules imported to prove the perception stack loads.
_PERCEPTION_MODULES = (
    "cv2",
    "moonlight.capture",
    "moonlight.perception.vitals",
    "moonlight.perception.battle",
    "moonlight.perception.floor",
    "moonlight.perception.minimap",
)


def _load_config(config_arg: str) -> BotConfig:
    path = Path(config_arg)
    if path.exists():
        return BotConfig.load(path)
    if config_arg != DEFAULT_CONFIG_FILE:
        raise ConfigError(f"config file not found: {path}")
    return BotConfig.default()


def check(config_arg: str, profiles_dir: Path | None, profile_name: str | None) -> int:
    ok = True

    def report(passed: bool, what: str) -> None:
        nonlocal ok
        ok = ok and passed
        print(f"[{'OK' if passed else 'FAIL'}] {what}")

    config: BotConfig | None
    try:
        config = _load_config(config_arg)
        report(True, f"config ({config_arg})")
    except ConfigError as exc:
        report(False, f"config: {exc}")
        config = None
    try:
        from moonlight.input import find_arduino_port

        port = config.serial_port if config and config.serial_port else find_arduino_port()
        report(port is not None, f"serial port: {port or 'no Arduino found'}")
    except Exception as exc:  # noqa: BLE001 - pyserial missing etc.
        report(False, f"serial port: {exc}")
    name = profile_name or (config.profile if config else None)
    profile: CalibrationProfile | None = None
    try:
        if name is None:
            raise ValueError("no profile name")
        directory = profiles_dir or (config.profiles_dir if config else None)
        profile = ProfileManager(directory).load(name)
        report(True, f"profile {name!r}")
    except Exception as exc:  # noqa: BLE001 - ValueError / CalibrationError
        report(False, f"profile {name!r}: {exc}")
    if profile is not None and config is not None:
        for module, missing in check_module_regions(profile, config).items():
            if missing:
                report(
                    False,
                    f"module {module}: missing calibration region(s) {', '.join(missing)}",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the MOONLIGHT setup.")
    parser.add_argument("profile", nargs="?", default=None, help="profile (default: config)")
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_FILE,
        help=f"bot config JSON (default: ./{DEFAULT_CONFIG_FILE}; built-in defaults if absent)",
    )
    parser.add_argument("--profiles-dir", type=Path, default=None, help="profiles directory")
    args = parser.parse_args(argv)
    return check(args.config, args.profiles_dir, args.profile)


if __name__ == "__main__":
    sys.exit(main())
