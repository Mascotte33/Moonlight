#!/usr/bin/env python3
"""HIL harness: click latency / click accuracy / serial-loss release (T18).

    python tools/hil_harness.py                     # offline (mock serial)
    python tools/hil_harness.py --real [--port COM5] [--allow-click]

Offline runs the three scenarios against an in-memory device. ``--real``
runs them on the attached Arduino; with no device the real scenarios are
SKIPPED (exit 0), never failed. Exit 1 only if a scenario that ran failed.

Real mode sends only harmless HID: a 0,0 mouse move and a brief ``shift``
hold released with ``X``. A real mouse click needs ``--allow-click``.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "hil")]

from moonlight.input import ArduinoInput, ArduinoInputError, ClickSide  # noqa: E402

DEFAULT_PORT = "COM5" if sys.platform == "win32" else "/dev/ttyACM0"
MAX_PING_MS = 50.0
SAMPLES = 50

Result = tuple[str, str]  # (PASS | FAIL | SKIP, detail)


def latency(ard: ArduinoInput) -> Result:
    ms = []
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        ok = ard.ping()
        ms.append((time.perf_counter() - t0) * 1000.0)
        if not ok:
            return "FAIL", "ping got no PONG"
    p95 = sorted(ms)[int(0.95 * (len(ms) - 1))]
    detail = f"ping->PONG min {min(ms):.2f} med {statistics.median(ms):.2f} p95 {p95:.2f} ms"
    return ("PASS" if p95 < MAX_PING_MS else "FAIL"), detail


def accuracy(ard: ArduinoInput, line_log: Callable[[], list[str]] | None, click: bool) -> Result:
    ard.mouse_move(0, 0)  # raises unless the device replies OK
    if click:
        ard.click(ClickSide.LEFT)
        if line_log is not None and line_log()[-1] != "C L":
            return "FAIL", f"wire was {line_log()[-1]!r}, expected 'C L'"
        return "PASS", "M 0 0 and C L acknowledged OK"
    return "PASS", "M 0 0 acknowledged OK (C L skipped: no --allow-click)"


def safety_real(ard: ArduinoInput) -> Result:
    ard.hold_combo("shift")
    ard.release_all()
    if not ard.ping():
        return "FAIL", "no PONG after X"
    return "PASS", "D shift -> X acknowledged (cable-pull test is manual)"


def safety_mock() -> Result:
    from mock_serial import MockBus
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    bus = MockBus()
    bus.install(mp)
    ard = bus.connect()
    try:
        ard.hold_combo("shift")
        bus.plugged = False
        bus.current.drop()
        try:
            ard.press_combo("F1")
        except ArduinoInputError:
            pass
        if ard.is_alive or ard.ping():
            return "FAIL", "still alive after serial loss"
        return "PASS", "loss detected, commands refused (fail-closed)"
    finally:
        ard.close()
        mp.undo()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--real", action="store_true", help="use the attached Arduino")
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--allow-click", action="store_true", help="send a real left click")
    args = ap.parse_args()

    results: dict[str, Result] = {}
    if args.real:
        try:
            ard = ArduinoInput(args.port)
        except ImportError as exc:
            ard, why = None, f"pyserial missing: {exc}"
        except ArduinoInputError as exc:
            ard, why = None, str(exc)
        if ard is None:
            results = {k: ("SKIP", why) for k in ("latency", "accuracy", "serial-loss")}
        else:
            try:
                results["latency"] = latency(ard)
                results["accuracy"] = accuracy(ard, None, args.allow_click)
                results["serial-loss"] = safety_real(ard)
            except ArduinoInputError as exc:
                results["device"] = ("FAIL", str(exc))
            finally:
                ard.close()
    else:
        from _pytest.monkeypatch import MonkeyPatch
        from mock_serial import MockBus

        mp = MonkeyPatch()
        bus = MockBus()
        bus.install(mp)
        ard = bus.connect()
        try:
            results["latency"] = latency(ard)
            results["accuracy"] = accuracy(ard, lambda: bus.current.lines_written, True)
        finally:
            ard.close()
            mp.undo()
        results["serial-loss"] = safety_mock()

    print(f"HIL report ({'REAL ' + args.port if args.real else 'offline mock'})")
    for name, (status, detail) in results.items():
        print(f"  [{status}] {name}: {detail}")
    return 1 if any(s == "FAIL" for s, _ in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
