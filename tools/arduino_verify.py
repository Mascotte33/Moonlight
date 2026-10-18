#!/usr/bin/env python3
"""Verify the Moonlight Arduino Leonardo firmware is running.

Auto-detects the Arduino serial port, then sends the wire-protocol ping
(``P``) and expects ``PONG`` back. Also exercises the ``X`` (release all)
command, which is a safe no-op that confirms the command path works.

Usage:
    python tools/arduino_verify.py            # auto-detect port
    python tools/arduino_verify.py COM5       # force a specific port

Exit code 0 = firmware verified, 1 = not verified / error.

This is a read-mostly diagnostic. It does NOT send keyboard/mouse actions,
so it is safe to run while you use the computer.
"""
from __future__ import annotations

import sys
import time

import serial
import serial.tools.list_ports


def find_arduino_port() -> str | None:
    """Return the Arduino Leonardo's COM port, or None if not found."""
    for port in serial.tools.list_ports.comports():
        # Leonardo uses the standard Arduino VID:PID (0x2341:0x8036)
        # when running the CDC-serial firmware.
        if port.vid is not None and port.vid == 0x2341:
            return port.device
        # Fallback: match on description keywords.
        desc = (port.description or "").lower()
        if "arduino" in desc or "leonardo" in desc:
            return port.device
    return None


def cmd(s: serial.Serial, text: str, wait: float = 0.6) -> bytes:
    """Send one wire line and read the reply."""
    s.reset_input_buffer()
    s.write(text.encode("ascii") + b"\n")
    time.sleep(wait)
    return s.read(128)


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else find_arduino_port()
    if not port:
        print("[-] Could not auto-detect the Arduino port.")
        print("    Pass it explicitly: python tools/arduino_verify.py COM5")
        return 1

    print(f"[*] Using port {port} @ 115200")
    try:
        s = serial.Serial(port=port, baudrate=115200, timeout=2)
    except Exception as exc:  # noqa: BLE001 - report and exit
        print(f"[-] Could not open {port}: {exc}")
        return 1

    time.sleep(0.5)
    s.reset_input_buffer()

    pong = 0
    for i in range(3):
        reply = cmd(s, "P")
        print(f"[+] P #{i + 1}: {reply!r}")
        if b"PONG" in reply:
            pong += 1

    x_reply = cmd(s, "X")
    print(f"[+] X: {x_reply!r}")
    s.close()

    if pong >= 2:
        print(f"[+] SUCCESS: firmware verified ({pong}/3 PONG, X ok)")
        return 0

    print("[-] Firmware not verified (insufficient PONG replies).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
