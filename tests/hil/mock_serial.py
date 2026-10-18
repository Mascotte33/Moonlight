"""In-memory serial device for the HIL suite (no hardware, no pyserial).

``MockSerial`` satisfies ``moonlight.input.SerialPortLike`` and answers with
the firmware replies emulated by ``FakeInput``. ``MockBus`` stands in for the
USB bus: it patches ``moonlight.input.open_serial_port`` /
``find_arduino_port`` and can unplug / re-plug the device.
"""

import pytest

from moonlight.input import ArduinoInput, FakeInput

PORT = "COM5"


class MockSerial:
    """One open connection to the simulated Leonardo."""

    def __init__(self, port: str = PORT) -> None:
        self.port = port
        self.timeout = 0.5
        self.dropped = False
        self.closed = False
        self.lines_written: list[str] = []
        self._firmware = FakeInput()
        self._rx: list[bytes] = []

    def drop(self) -> None:
        """Simulate yanking the USB cable: every later I/O raises."""
        self.dropped = True

    def write(self, data: bytes | bytearray | memoryview | str) -> int:
        if self.dropped:
            raise OSError("device disconnected")
        raw = data.encode() if isinstance(data, str) else bytes(data)
        for line in raw.decode().splitlines():
            self.lines_written.append(line)
            self._rx.append(f"{self._firmware.write(line)}\n".encode())
        return len(raw)

    def readline(self, size: int = -1) -> bytes:
        if self.dropped:
            raise OSError("device disconnected")
        return self._rx.pop(0) if self._rx else b""

    def reset_input_buffer(self) -> None:
        if self.dropped:
            raise OSError("device disconnected")
        self._rx.clear()

    def close(self) -> None:
        self.closed = True


class MockBus:
    """Fake USB bus: opens ``MockSerial`` ports until unplugged."""

    def __init__(self) -> None:
        self.plugged = True
        self.opened: list[MockSerial] = []

    @property
    def current(self) -> MockSerial:
        return self.opened[-1]

    def open_port(self, port: str, baudrate: int, read_timeout: float) -> MockSerial:
        if not self.plugged:
            raise OSError(f"could not open {port}")
        ser = MockSerial(port)
        ser.timeout = read_timeout
        self.opened.append(ser)
        return ser

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("moonlight.input.open_serial_port", self.open_port)
        monkeypatch.setattr("moonlight.input.find_arduino_port", lambda *a, **k: None)

    def connect(self, heartbeat_interval: float = 60.0) -> ArduinoInput:
        """Open an ``ArduinoInput`` on this bus (slow heartbeat: tests drive I/O)."""
        return ArduinoInput(
            PORT, heartbeat_interval=heartbeat_interval, reconnect_backoff=0.01
        )
