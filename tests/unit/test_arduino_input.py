"""Unit tests for moonlight.input.ArduinoInput (task T16).

The serial layer is fully emulated in memory: a fake ``serial`` module is
patched into ``sys.modules`` so ArduinoInput's lazy pyserial imports resolve
to the fakes. No COM port, no Arduino, and no real pyserial usage.

Covers:
* construction / port auto-detection / error cases
* wire protocol (exact lines sent, replies enforced)
* the 1 Hz heartbeat (default interval, pings flowing)
* reconnect + re-detection after unplug / re-plug
* lost-PONG safety stop (N missed pings -> LOST -> commands refused)
* thread safety (serialized I/O under concurrent 20 Hz-style load)
* clean shutdown (release-all + port close + heartbeat stop)
* module importability with serial blocked entirely
"""

import os
import subprocess
import sys
import threading
import time
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from moonlight.input import (
    ARDUINO_LEONARDO_VID,
    ArduinoInput,
    ArduinoInputError,
    ClickSide,
    DeviceLostError,
    InputBackend,
    NoArduinoFoundError,
    find_arduino_port,
)

COMBO = "ctrl+alt+t"
COM = "COM5"
PID_LEONARDO = 0x8036


class FakeSerialException(OSError):
    """Stand-in for serial.SerialException used by the fake serial module."""


class FakeSerialDevice:
    """Thread-safe in-memory emulation of ``serial.Serial`` + the firmware.

    * ``write()`` feeds whole lines to a firmware-style reply generator.
    * ``readline()`` blocks until a reply line arrives or ``timeout``.
    * ``is_open = False`` simulates an unplug: I/O raises
      FakeSerialException.
    * ``reply_hook`` overrides reply generation (return None = silence).
    * ``max_concurrent_writers`` proves whether host writes ever overlapped.
    """

    def __init__(
        self,
        port: str,
        vid: int = ARDUINO_LEONARDO_VID,
        pid: int = PID_LEONARDO,
    ) -> None:
        self.port = port
        self.baudrate = 115200
        self.vid = vid
        self.pid = pid
        self.timeout: float | None = None
        self.is_open = True
        self.closed = False
        self.reply_hook: Callable[[str], str | None] | None = None
        self.lines_written: list[str] = []
        self.max_concurrent_writers = 0
        self._rx_lock = threading.Lock()
        self._rx: list[bytes] = []
        self._write_buf = b""
        self._io_lock = threading.Lock()
        self._concurrent = 0

    # -- pyserial Serial interface ---------------------------------------------

    def write(self, data: bytes | bytearray | memoryview | str) -> int:
        if isinstance(data, str):
            data = data.encode()
        raw = bytes(data)
        with self._io_lock:
            self._concurrent += 1
            self.max_concurrent_writers = max(self.max_concurrent_writers, self._concurrent)
        try:
            self._require_open()
            self._write_buf += raw
            while b"\n" in self._write_buf:
                line, self._write_buf = self._write_buf.split(b"\n", 1)
                text = line.decode()
                self.lines_written.append(text)
                reply = self._device_reply(text)
                if reply is not None:
                    self._enqueue_reply(reply)
        finally:
            with self._io_lock:
                self._concurrent -= 1
        return len(raw)

    def readline(self, size: int = -1) -> bytes:
        self._require_open()
        deadline = time.monotonic() + (self.timeout if self.timeout is not None else 0.5)
        while True:
            with self._rx_lock:
                if self._rx:
                    head = self._rx[0]
                    idx = head.find(b"\n")
                    if idx != -1:
                        line = head[: idx + 1]
                        self._rx[0] = head[idx + 1 :]
                        if not self._rx[0]:
                            self._rx.pop(0)
                        return line
            if time.monotonic() >= deadline:
                return b""
            time.sleep(0.001)

    @property
    def in_waiting(self) -> int:
        with self._rx_lock:
            return sum(len(b) for b in self._rx)

    def reset_input_buffer(self) -> None:
        with self._rx_lock:
            self._rx.clear()

    def close(self) -> None:
        self.closed = True

    # -- fake Arduino firmware -----------------------------------------------------

    def _require_open(self) -> None:
        if not self.is_open:
            raise FakeSerialException(f"port {self.port!r} is not open (unplugged)")

    def _enqueue_reply(self, reply: str) -> None:
        with self._rx_lock:
            self._rx.append((reply + "\n").encode())

    def _device_reply(self, line: str) -> str | None:
        """Emulate the firmware line parser (see firmware/README.md)."""
        if self.reply_hook is not None:
            return self.reply_hook(line)
        tokens = line.split()
        if not tokens:
            return "ERR"
        cmd, args = tokens[0], tokens[1:]
        if cmd in {"K", "D", "U"}:
            return "OK" if args else "ERR"
        if cmd == "M":
            return "OK" if self._valid_mouse_args(args) else "ERR"
        if cmd == "C":
            return "OK" if len(args) == 1 and args[0] in {"L", "R"} else "ERR"
        if cmd == "P":
            return "PONG" if not args else "ERR"
        if cmd == "X":
            return "OK" if not args else "ERR"
        return "ERR"

    @staticmethod
    def _valid_mouse_args(args: list[str]) -> bool:
        """True if args are two integers each within -127..127."""
        if len(args) != 2:
            return False
        try:
            dx = int(args[0])
            dy = int(args[1])
        except ValueError:
            return False
        return -127 <= dx <= 127 and -127 <= dy <= 127


def install_fake_serial(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Patch sys.modules so moonlight.input's lazy serial imports hit fakes.

    Returns a controller namespace with:
        devices:    dict mapping port name -> FakeSerialDevice
        add_port(port, vid=..., pid=...)
        remove_port(port)
    """
    devices: dict[str, FakeSerialDevice] = {}

    def add_port(
        port: str, vid: int = ARDUINO_LEONARDO_VID, pid: int = PID_LEONARDO
    ) -> FakeSerialDevice:
        devices[port] = FakeSerialDevice(port, vid=vid, pid=pid)
        return devices[port]

    def remove_port(port: str) -> None:
        devices.pop(port, None)

    def serial_factory(
        port: str | None = None, baudrate: int = 9600, **kwargs: Any
    ) -> FakeSerialDevice:
        if port is None or port not in devices:
            raise FakeSerialException(f"could not open port {port!r}")
        return devices[port]

    def comports() -> list[types.SimpleNamespace]:
        return [
            types.SimpleNamespace(
                device=name,
                name=name,
                vid=dev.vid,
                pid=dev.pid,
                description=f"USB Serial Device ({name})",
                manufacturer="Arduino LLC",
            )
            for name, dev in devices.items()
        ]

    lp_mod = types.ModuleType("serial.tools.list_ports")
    lp_mod.comports = comports  # type: ignore[attr-defined]
    tools_mod = types.ModuleType("serial.tools")
    tools_mod.list_ports = lp_mod  # type: ignore[attr-defined]
    serial_mod = types.ModuleType("serial")
    serial_mod.SerialException = FakeSerialException  # type: ignore[attr-defined]
    serial_mod.Serial = serial_factory  # type: ignore[attr-defined]
    serial_mod.tools = tools_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "serial", serial_mod)
    monkeypatch.setitem(sys.modules, "serial.tools", tools_mod)
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", lp_mod)

    return types.SimpleNamespace(
        devices=devices, add_port=add_port, remove_port=remove_port
    )


@pytest.fixture()
def fake_serial(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Install the in-memory fake serial module for the current test."""
    return install_fake_serial(monkeypatch)


def command_lines(dev: FakeSerialDevice) -> list[str]:
    """Wire lines excluding heartbeat/verify pings (P)."""
    return [line for line in dev.lines_written if line != "P"]


def count_pings(dev: FakeSerialDevice) -> int:
    return sum(1 for line in dev.lines_written if line == "P")


def wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    """Poll predicate until true or timeout; return its last state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def make_backend(**kwargs: Any) -> ArduinoInput:
    """ArduinoInput with fast heartbeat/reconnect defaults for tests."""
    opts: dict[str, Any] = {
        "port": COM,
        "heartbeat_interval": 0.05,
        "max_missed_pongs": 3,
        "reconnect_backoff": 0.05,
        "max_reconnect_backoff": 0.2,
        "read_timeout": 0.05,
    }
    opts.update(kwargs)
    return ArduinoInput(**opts)


class TestConstruction:
    """Port selection and constructor validation."""

    def test_is_an_input_backend(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        with ArduinoInput(port=COM) as ard:
            assert isinstance(ard, InputBackend)
            assert ard.is_alive
            assert ard.connected_port == COM

    def test_auto_detects_arduino_by_vid(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        with ArduinoInput() as ard:
            assert ard.connected_port == COM

    def test_no_device_raises(self, fake_serial: types.SimpleNamespace) -> None:
        with pytest.raises(NoArduinoFoundError):
            ArduinoInput()

    def test_explicit_missing_port_raises(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        with pytest.raises(NoArduinoFoundError):
            ArduinoInput(port="COM99")

    def test_vid_mismatch_not_detected(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM, vid=0x1234)
        with pytest.raises(NoArduinoFoundError):
            ArduinoInput()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"heartbeat_interval": 0},
            {"heartbeat_interval": -1},
            {"max_missed_pongs": 0},
            {"reconnect_backoff": 0},
            {"read_timeout": 0},
        ],
    )
    def test_invalid_params_raise(
        self, fake_serial: types.SimpleNamespace, kwargs: dict[str, Any]
    ) -> None:
        fake_serial.add_port(COM)
        with pytest.raises(ValueError):
            ArduinoInput(port=COM, **kwargs)

    def test_requires_pong_at_connect(self, fake_serial: types.SimpleNamespace) -> None:
        """A port that opens but never answers PONG must be rejected."""
        fake_serial.add_port(COM)
        fake_serial.devices[COM].reply_hook = lambda _line: None
        with pytest.raises(NoArduinoFoundError):
            ArduinoInput(port=COM)


class TestFindArduinoPort:
    """find_arduino_port() scans list_ports by VID/PID."""

    def test_finds_leonardo_by_vid(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        assert find_arduino_port() == COM

    def test_pid_filter(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM, pid=PID_LEONARDO)
        assert find_arduino_port(pid=PID_LEONARDO) == COM
        assert find_arduino_port(pid=0xBEEF) is None

    def test_returns_none_when_absent(self, fake_serial: types.SimpleNamespace) -> None:
        assert find_arduino_port() is None

    def test_returns_first_match(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port("COM3")
        fake_serial.add_port("COM4")
        assert find_arduino_port() == "COM3"


class TestWireProtocol:
    """ArduinoInput must send exactly the documented wire lines."""

    def test_press_combo_sends_K(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:
            ard.press_combo(COMBO)
            assert command_lines(dev) == [f"K {COMBO}"]

    def test_hold_combo_sends_D(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:
            ard.hold_combo(COMBO)
            assert command_lines(dev) == [f"D {COMBO}"]

    def test_release_combo_sends_U(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:
            ard.release_combo(COMBO)
            assert command_lines(dev) == [f"U {COMBO}"]

    def test_mouse_move_sends_M(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:
            ard.mouse_move(12, -7)
            assert command_lines(dev) == ["M 12 -7"]

    def test_click_sends_C(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:
            ard.click(ClickSide.LEFT)
            ard.click(ClickSide.RIGHT)
            assert command_lines(dev) == ["C L", "C R"]

    def test_release_all_sends_X(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:
            ard.release_all()
            assert command_lines(dev) == ["X"]

    def test_ping_returns_true_on_pong(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        with make_backend(heartbeat_interval=60.0) as ard:
            assert ard.ping() is True

    def test_out_of_range_move_raises_and_sends_nothing(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:
            with pytest.raises(ValueError):
                ard.mouse_move(-128, 0)
            assert command_lines(dev) == []

    def test_device_err_reply_raises(self, fake_serial: types.SimpleNamespace) -> None:
        """A non-OK device reply must surface as an error (e.g. kill switch)."""
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:
            dev.reply_hook = lambda _line: "ERR"
            ard.press_combo(COMBO)  # P6: fire-and-forget, the ACK is checked later
            with pytest.raises(ArduinoInputError, match="rejected"):
                ard.press_combo(COMBO)
            assert command_lines(dev) == [f"K {COMBO}"]  # nothing sent after the ERR

    def test_silent_device_raises(self, fake_serial: types.SimpleNamespace) -> None:
        """No reply at all (link dying) must surface as an error."""
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:
            dev.reply_hook = lambda _line: None
            ard.press_combo(COMBO)
            with pytest.raises(ArduinoInputError, match="no reply"):
                ard.release_all()

    def test_press_does_not_block_on_slow_ack(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        """P6: a key press returns before its ACK; a sync command waits for it."""
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:

            def slow_ok(line: str) -> None:
                threading.Timer(0.2, dev._enqueue_reply, args=("OK",)).start()

            dev.reply_hook = slow_ok
            t0 = time.monotonic()
            ard.press_combo(COMBO)
            ard.mouse_move(1, 1)
            assert time.monotonic() - t0 < 0.1
            dev.reply_hook = None
            ard.release_all()  # drains both pending ACKs without error
            assert command_lines(dev) == [f"K {COMBO}", "M 1 1", "X"]


class TestHeartbeat:
    """The 1 Hz heartbeat: P sent on an interval, PONG expected."""

    def test_heartbeat_sends_pings_on_interval(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        ard = make_backend(heartbeat_interval=0.05)
        try:
            deadline = time.monotonic() + 2.0
            while count_pings(dev) < 4 and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            ard.close()
        # verify ping at connect + at least 3 heartbeats
        assert count_pings(dev) >= 4

    def test_default_heartbeat_is_one_second(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        fake_serial.add_port(COM)
        with ArduinoInput(port=COM) as ard:
            assert ard.heartbeat_interval == 1.0
            assert ard.max_missed_pongs == 3
            assert ard.baudrate == 115200

    def test_heartbeat_keeps_is_alive(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        with make_backend() as ard:
            time.sleep(0.2)
            assert ard.is_alive


class TestReconnect:
    """Reconnect logic: unplug -> lost -> replug -> back alive."""

    def test_recovers_after_unplug_and_replug(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend() as ard:
            ard.press_combo(COMBO)
            dev.is_open = False  # yank the cable
            assert wait_for(lambda: not ard.is_alive)
            dev.is_open = True  # re-plug
            assert wait_for(lambda: ard.is_alive)
            ard.press_combo(COMBO)  # works again
            assert command_lines(dev).count(f"K {COMBO}") == 2

    def test_reconnect_redetects_new_port(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        """After re-plug the OS may assign a different COM port."""
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend() as ard:
            dev.is_open = False
            assert wait_for(lambda: not ard.is_alive)
            # re-plug: same physical device, new port name
            fake_serial.remove_port(COM)
            dev.port = "COM6"
            fake_serial.devices["COM6"] = dev
            dev.is_open = True
            assert wait_for(lambda: ard.connected_port == "COM6")
            assert ard.ping() is True


class TestLostPongSafety:
    """Safety stop: N consecutive missed pings -> LOST -> refuse commands."""

    def test_silent_device_declared_lost(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend() as ard:
            dev.reply_hook = lambda _line: None  # firmware stops replying
            assert wait_for(lambda: not ard.is_alive)

    def test_lost_device_refuses_commands(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend() as ard:
            dev.is_open = False
            assert wait_for(lambda: not ard.is_alive)
            with pytest.raises(DeviceLostError):
                ard.press_combo(COMBO)
            with pytest.raises(DeviceLostError):
                ard.hold_combo(COMBO)
            with pytest.raises(DeviceLostError):
                ard.mouse_move(1, 1)
            with pytest.raises(DeviceLostError):
                ard.click(ClickSide.LEFT)
            with pytest.raises(DeviceLostError):
                ard.release_all()
            assert ard.ping() is False

    def test_lost_error_is_an_input_error(self) -> None:
        assert issubclass(DeviceLostError, ArduinoInputError)

    def test_recovery_clears_lost_state(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend() as ard:
            dev.reply_hook = lambda _line: None
            assert wait_for(lambda: not ard.is_alive)
            dev.reply_hook = None  # firmware wakes up
            assert wait_for(lambda: ard.is_alive)
            ard.press_combo(COMBO)  # commands work again


class TestThreadSafety:
    """Serialized serial access: 20 Hz-style concurrent calls + heartbeat."""

    def test_concurrent_commands_never_interleave(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend() as ard:  # heartbeat thread runs concurrently
            errors: list[str] = []
            n_threads, n_cmds = 5, 30

            def worker(n: int) -> None:
                try:
                    for i in range(n_cmds):
                        ard.press_combo(f"ctrl+alt+t-{n}-{i}")
                except Exception as exc:  # defensive; must not happen
                    errors.append(f"{n}: {exc!r}")

            threads = [
                threading.Thread(target=worker, args=(n,)) for n in range(n_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors
            assert dev.max_concurrent_writers == 1
            assert len(command_lines(dev)) == n_threads * n_cmds

    def test_ping_concurrent_with_commands(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        fake_serial.add_port(COM)
        with make_backend() as ard:
            results: list[bool] = []

            def pinger() -> None:
                for _ in range(50):
                    results.append(ard.ping())

            t = threading.Thread(target=pinger)
            t.start()
            for _ in range(50):
                ard.press_combo(COMBO)
            t.join()
            assert all(results)  # the link never got corrupted


class TestShutdown:
    """close(): release-all + close port + stop heartbeat, idempotently."""

    def test_close_stops_heartbeat(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        ard = make_backend()
        assert wait_for(lambda: count_pings(dev) >= 2, timeout=2.0)
        ard.close()
        pings_at_close = count_pings(dev)
        time.sleep(0.3)
        # at most one ping could have been in flight during the join
        assert count_pings(dev) - pings_at_close <= 1

    def test_close_releases_all_and_closes_port(
        self, fake_serial: types.SimpleNamespace
    ) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        ard = make_backend(heartbeat_interval=60.0)
        ard.close()
        assert dev.closed is True
        assert command_lines(dev)[-1] == "X"

    def test_close_is_idempotent(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        ard = make_backend(heartbeat_interval=60.0)
        ard.close()
        ard.close()  # must not raise
        assert not ard.is_alive

    def test_close_when_device_lost(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        ard = make_backend()
        dev.is_open = False
        assert wait_for(lambda: not ard.is_alive)
        ard.close()  # must not raise
        assert not ard.is_alive

    def test_context_manager_closes(self, fake_serial: types.SimpleNamespace) -> None:
        fake_serial.add_port(COM)
        dev = fake_serial.devices[COM]
        with make_backend(heartbeat_interval=60.0) as ard:
            ard.ping()
        assert dev.closed is True
        assert not ard.is_alive


class TestImportWithoutSerial:
    """The module must import cleanly with no serial module at all."""

    def test_import_does_not_load_serial(self) -> None:
        code = (
            "import sys, importlib.abc\n"
            "class Blocker(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'serial' or name.startswith('serial.'):\n"
            "            raise ImportError('serial blocked by test')\n"
            "        return None\n"
            "sys.meta_path.insert(0, Blocker())\n"
            "import moonlight.input as m\n"
            "assert m.ArduinoInput is not None\n"
            "assert not any(k == 'serial' or k.startswith('serial.')\n"
            "               for k in sys.modules)\n"
            "print('import-ok')\n"
        )
        repo_root = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=str(repo_root.parent))
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
