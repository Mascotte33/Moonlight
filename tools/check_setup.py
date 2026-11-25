#!/usr/bin/env python3
"""Check the bot is ready to run: config, serial port, profile, perception.

Usage:
    python tools/check_setup.py                 # data/calibration.json
    python tools/check_setup.py --config bot.json --calibration path/calibration.json

Prints one ``[OK]`` / ``[FAIL]`` line per check. Exit code 0 = all passed,
1 = something failed, 2 = bad arguments / config.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from moonlight.calibration.calibrate import CalibrationProfile, load_calibration
from moonlight.core.config import BotConfig, ConfigError
from moonlight.core.wiring import check_module_regions

#: Modules imported to prove the perception stack loads.
_PERCEPTION_MODULES = (
    "cv2",
    "moonlight.perception.capture",
    "moonlight.perception.vitals",
    "moonlight.perception.battle",
    "moonlight.perception.floor",
    "moonlight.perception.minimap",
)


def _load_config(config_arg: str | None) -> BotConfig:
    if config_arg is None:  # the config the GUI's Start uses
        from moonlight.gui.loop_factory import load_real_config

        return load_real_config()
    path = Path(config_arg)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    return BotConfig.load(path)


def check(config_arg: str | None, calibration: Path | None) -> int:
    ok = True

    def report(passed: bool, what: str) -> None:
        nonlocal ok
        ok = ok and passed
        print(f"[{'OK' if passed else 'FAIL'}] {what}")

    config: BotConfig | None
    try:
        config = _load_config(config_arg)
        report(True, f"config ({config_arg or 'the one picked in the GUI'})")
    except ConfigError as exc:
        report(False, f"config: {exc}")
        config = None
    try:
        from moonlight.hardware.input import find_arduino_port

        # A typed port that is gone (COM3 from another PC) falls back to the detected one.
        port = find_arduino_port()
        typed = config.serial_port if config else None
        note = f" (typed {typed} is not here)" if typed and typed != port else ""
        report(port is not None, f"serial port: {port or 'no Arduino found'}{note}")
    except Exception as exc:  # noqa: BLE001 - pyserial missing etc.
        report(False, f"serial port: {exc}")
    profile: CalibrationProfile | None = None
    try:
        profile = load_calibration(calibration)
        report(True, "calibration")
    except Exception as exc:  # noqa: BLE001 - none yet (Start makes it) / malformed
        report(False, f"calibration: {exc} (Start finds the client and saves one)")
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
    parser.add_argument(
        "--config", default=None,
        help="bot config JSON (default: the config picked in the GUI)",
    )
    parser.add_argument(
        "--calibration", type=Path, default=None, help="default: data/calibration.json"
    )
    args = parser.parse_args(argv)
    return check(args.config, args.calibration)


if __name__ == "__main__":
    sys.exit(main())
