"""Input backend for MOONLIGHT.

This module is the only layer that talks to the Arduino Leonardo
(ATmega32U4) HID device. It is built so that everything except the real
transport can be developed and tested without hardware:

* :class:`InputBackend` -- abstract, logical input interface. Methods express
  *what* to do (press a combo, move the mouse), never *how* it is encoded.
* :class:`ProtocolEncoder` -- encodes logical commands into the Arduino wire
  protocol (one command per line, 115200 baud, device replies OK/ERR).
* :class:`FakeInput` -- hardware-free :class:`InputBackend` for unit tests:
  it records every wire line it would send and simulates the device replies.
* :class:`ArduinoInput` -- the real transport (task T16): opens the Arduino
  COM port with pyserial, runs a 1 Hz heartbeat (``P`` -> ``PONG``),
  auto-reconnects with backoff, and declares the device LOST after repeated
  missed pings so the bot loop can halt safely.

Wire protocol (see ``firmware/README.md``)::

    K <combo>    press + release combo
    D <combo>    hold combo down
    U <combo>    release combo
    M <dx> <dy>  relative mouse move; EACH axis must be -4000..4000 per command
    A <x> <y>    absolute mouse move; EACH axis 0..32767 across the screen
    C L | C R    mouse click (left / right)
    P            ping; the device replies PONG
    X            release all inputs

Empty or unknown lines -> the device replies ERR.

The real serial transport (``ArduinoInput``) is built on top of
:class:`ProtocolEncoder` with pyserial. pyserial is imported lazily inside
functions (never at module level), so this module -- and the whole test
suite -- imports cleanly without pyserial or without an Arduino attached.
"""

import logging
import threading
from abc import ABC, abstractmethod
from collections import deque
from enum import StrEnum
from types import TracebackType
from typing import Protocol

__all__ = [
    "ABS_MAX",
    "ARDUINO_LEONARDO_VID",
    "MAX_MOUSE_DELTA",
    "ArduinoInput",
    "ArduinoInputError",
    "ClickSide",
    "DeviceLostError",
    "FakeInput",
    "InputBackend",
    "NoArduinoFoundError",
    "ProtocolEncoder",
    "find_arduino_port",
]

#: Inclusive per-axis bound for a single ``M <dx> <dy>`` command. The
#: firmware splits it into 127-count HID reports, so a whole trip is one
#: command and one rate-limit token (F-20).
MAX_MOUSE_DELTA = 4000

#: Inclusive per-axis bound for an ``A <x> <y>`` command (HID absolute range).
ABS_MAX = 32767

#: Fire-and-forget commands allowed in flight before a send waits for
#: their ACKs (P6); at 10 commands/s the heartbeat drains far sooner.
MAX_PENDING_ACKS = 8

#: USB Vendor ID of the standard Arduino Leonardo (ATmega32U4).
ARDUINO_LEONARDO_VID = 0x2341

logger = logging.getLogger(__name__)


class ClickSide(StrEnum):
    """Mouse button side; values are the wire-protocol tokens (L / R)."""

    LEFT = "L"
    RIGHT = "R"


class InputBackend(ABC):
    """Abstract logical interface for sending input to the game.

    Implementations translate these logical calls into their transport
    (e.g. the Arduino wire protocol); callers never deal with wire details.

    Methods:
        press_combo(combo):
            Tap a key combo (press + release), e.g. ``"ctrl+alt+t"``.
        hold_combo(combo):
            Hold a key combo down until :meth:`release_combo` is called.
        release_combo(combo):
            Release a combo held with :meth:`hold_combo`.
        mouse_move(dx, dy):
            Move the mouse by a relative pixel delta (signs allowed).
        click(side):
            Click the left or right mouse button.
        mouse_down(side) / mouse_up(side):
            Hold a mouse button down / let it go (drag).
        ping():
            Return True if the device is alive and replies PONG.
        release_all():
            Emergency release of every key/button currently held.
    """

    @abstractmethod
    def press_combo(self, combo: str) -> None:
        """Press and immediately release a key combo (tap)."""
        ...

    @abstractmethod
    def hold_combo(self, combo: str) -> None:
        """Hold a key combo down until :meth:`release_combo` is called."""
        ...

    @abstractmethod
    def release_combo(self, combo: str) -> None:
        """Release a previously held key combo."""
        ...

    @abstractmethod
    def mouse_move(self, dx: int, dy: int) -> None:
        """Move the mouse by a relative delta in pixels (may be negative)."""
        ...

    def mouse_to(self, x: int, y: int) -> None:
        """Jump the cursor to an absolute spot, each axis 0..ABS_MAX."""
        raise NotImplementedError(f"{type(self).__name__} has no absolute mouse")

    @abstractmethod
    def click(self, side: ClickSide) -> None:
        """Click the left or right mouse button."""
        ...

    def mouse_down(self, side: ClickSide) -> None:
        """Hold a mouse button down until :meth:`mouse_up`."""
        raise NotImplementedError(f"{type(self).__name__} cannot hold a mouse button")

    def mouse_up(self, side: ClickSide) -> None:
        """Let go of a mouse button held with :meth:`mouse_down`."""
        raise NotImplementedError(f"{type(self).__name__} cannot hold a mouse button")

    @abstractmethod
    def ping(self) -> bool:
        """Check device liveness; True if it replies PONG."""
        ...

    @abstractmethod
    def release_all(self) -> None:
        """Release all keys and mouse buttons (safety / kill path)."""
        ...

    def close(self) -> None:
        """Release the transport (default: safety-release, nothing to close)."""
        self.release_all()


class ProtocolEncoder:
    """Encodes logical commands into Arduino wire-protocol lines.

    Pure and stateless: each method validates its input and returns exactly
    one wire line *without* a trailing newline (the transport appends it).
    Raises ValueError on protocol-invalid input (e.g. an M axis outside
    -4000..4000, or a blank/whitespace combo that would break line framing).
    """

    def press_combo(self, combo: str) -> str:
        """Encode a tap: ``K <combo>``."""
        return f"K {self._validate_combo(combo)}"

    def hold_combo(self, combo: str) -> str:
        """Encode a hold: ``D <combo>``."""
        return f"D {self._validate_combo(combo)}"

    def release_combo(self, combo: str) -> str:
        """Encode a release: ``U <combo>``."""
        return f"U {self._validate_combo(combo)}"

    def mouse_move(self, dx: int, dy: int) -> str:
        """Encode a relative mouse move: ``M <dx> <dy>`` (each axis -4000..4000)."""
        x = self._validate_mouse_delta(dx, "dx")
        y = self._validate_mouse_delta(dy, "dy")
        return f"M {x} {y}"

    def mouse_to(self, x: int, y: int) -> str:
        """Encode an absolute mouse move: ``A <x> <y>`` (each axis 0..32767)."""
        for value in (x, y):
            if not 0 <= value <= ABS_MAX:
                raise ValueError(f"absolute mouse {value} out of range 0..{ABS_MAX}")
        return f"A {x} {y}"

    def click(self, side: ClickSide) -> str:
        """Encode a mouse click: ``C L`` or ``C R``."""
        return f"C {side.value}"

    def mouse_down(self, side: ClickSide) -> str:
        """Encode a mouse button hold: ``H L`` or ``H R``."""
        return f"H {side.value}"

    def mouse_up(self, side: ClickSide) -> str:
        """Encode a mouse button release: ``R L`` or ``R R``."""
        return f"R {side.value}"

    def ping(self) -> str:
        """Encode a ping: ``P`` (device replies PONG)."""
        return "P"

    def release_all(self) -> str:
        """Encode a release-all: ``X``."""
        return "X"

    @staticmethod
    def _validate_combo(combo: str) -> str:
        """Ensure a combo is non-empty and cannot break line framing."""
        if not combo:
            raise ValueError("combo must be a non-empty string")
        if any(ch.isspace() for ch in combo):
            raise ValueError(
                f"combo {combo!r} must not contain whitespace "
                "(the wire protocol is one command per line)"
            )
        return combo

    @staticmethod
    def _validate_mouse_delta(value: int, axis: str) -> int:
        """Ensure one M-command axis stays within -MAX_MOUSE_DELTA..MAX_MOUSE_DELTA."""
        if not -MAX_MOUSE_DELTA <= value <= MAX_MOUSE_DELTA:
            raise ValueError(
                f"mouse delta {value} ({axis}) out of range "
                f"-{MAX_MOUSE_DELTA}..{MAX_MOUSE_DELTA}"
            )
        return value


class FakeInput(InputBackend):
    """In-memory :class:`InputBackend` that emulates the Arduino. No hardware.

    State for tests:
        sent_lines:
            Every wire line sent to the simulated device, in order.
        replies:
            The simulated device's one-line replies, in order
            (OK / ERR / PONG).
        last_reply:
            The most recent reply, or None if nothing was sent yet.
        cursor_position:
            The position of a *virtual cursor* the fake tracks, starting
            at the ``cursor`` given to the constructor. Each ``M`` command
            moves it, optionally imprecisely: ``move_scale`` scales the
            requested delta before it is applied (1.0 = exact; 0.9/1.1
            simulate imprecise USB HID moves), and ``move_snap`` snaps the
            resulting position to a grid (1 = no snapping; larger values
            simulate the game snapping the cursor to its own grid). The
            virtual cursor is what the closed-loop click tests render back
            to a :class:`moonlight.perception.cursor.CursorReader` (T17).

    ``write(line)`` is the single write path (all logical methods funnel
    through it) and also the way tests probe the device emulation directly
    with arbitrary, including malformed, lines.

    Intended for unit tests and offline development; it never opens a serial
    port or touches real hardware.
    """

    def __init__(
        self,
        encoder: ProtocolEncoder | None = None,
        *,
        cursor: tuple[int, int] = (0, 0),
        move_scale: float = 1.0,
        move_snap: int = 1,
    ) -> None:
        """Create a fake backend.

        Args:
            encoder: Encoder used to produce wire lines (defaults to the
                production encoder, so sent lines are byte-identical to
                what the Arduino would receive).
            cursor: Starting position of the virtual cursor (see
                :attr:`cursor_position`).
            move_scale: Multiplier applied to each requested move delta
                before the virtual cursor moves (1.0 = exact; 0.9/1.1
                simulate imprecise USB HID moves, T17).
            move_snap: Grid size (px) the virtual cursor position snaps to
                after each move (1 = no snapping; larger values simulate
                the game snapping the cursor to its grid).
        """
        self.encoder = encoder if encoder is not None else ProtocolEncoder()
        self.sent_lines: list[str] = []
        self.replies: list[str] = []
        self.cursor_position: tuple[int, int] = (int(cursor[0]), int(cursor[1]))
        if move_scale <= 0:
            raise ValueError(f"move_scale must be > 0, got {move_scale}")
        self._move_scale = float(move_scale)
        if move_snap < 1:
            raise ValueError(f"move_snap must be >= 1, got {move_snap}")
        self._move_snap = int(move_snap)

    @property
    def last_reply(self) -> str | None:
        """Most recent simulated device reply, or None if nothing was sent."""
        return self.replies[-1] if self.replies else None

    def write(self, line: str) -> str:
        """Send one raw wire line to the simulated device; return its reply."""
        self.sent_lines.append(line)
        reply = self._device_reply(line)
        self.replies.append(reply)
        return reply

    # -- InputBackend: logical methods (encode -> write -> simulated reply) --

    def press_combo(self, combo: str) -> None:
        """Tap a key combo. Wire: ``K <combo>``."""
        self.write(self.encoder.press_combo(combo))

    def hold_combo(self, combo: str) -> None:
        """Hold a key combo down. Wire: ``D <combo>``."""
        self.write(self.encoder.hold_combo(combo))

    def release_combo(self, combo: str) -> None:
        """Release a held key combo. Wire: ``U <combo>``."""
        self.write(self.encoder.release_combo(combo))

    def mouse_move(self, dx: int, dy: int) -> None:
        """Relative mouse move. Wire: ``M <dx> <dy>``.

        Also advances the virtual cursor as the game would apply the
        move (scaled by ``move_scale``, snapped to ``move_snap``).
        """
        self.write(self.encoder.mouse_move(dx, dy))
        self._advance_cursor(dx, dy)

    def mouse_to(self, x: int, y: int) -> None:
        """Absolute mouse move. Wire: ``A <x> <y>``."""
        self.write(self.encoder.mouse_to(x, y))

    def click(self, side: ClickSide) -> None:
        """Mouse click. Wire: ``C L`` / ``C R``."""
        self.write(self.encoder.click(side))

    def mouse_down(self, side: ClickSide) -> None:
        """Hold a mouse button. Wire: ``H L`` / ``H R``."""
        self.write(self.encoder.mouse_down(side))

    def mouse_up(self, side: ClickSide) -> None:
        """Release a mouse button. Wire: ``R L`` / ``R R``."""
        self.write(self.encoder.mouse_up(side))

    def ping(self) -> bool:
        """Ping; True iff the simulated device replies PONG."""
        return self.write(self.encoder.ping()) == "PONG"

    def release_all(self) -> None:
        """Release all inputs. Wire: ``X``."""
        self.write(self.encoder.release_all())

    # -- simulated game cursor --------------------------------------------

    def _advance_cursor(self, dx: int, dy: int) -> None:
        """Advance the virtual cursor as the game would apply ``(dx, dy)``."""
        nx = self.cursor_position[0] + int(round(dx * self._move_scale))
        ny = self.cursor_position[1] + int(round(dy * self._move_scale))
        self.cursor_position = (self._snap(nx), self._snap(ny))

    def _snap(self, value: int) -> int:
        """Snap *value* to the ``move_snap`` grid (the game's cursor grid)."""
        if self._move_snap == 1:
            return value
        return int(round(value / self._move_snap) * self._move_snap)

    # -- simulated Arduino ----------------------------------------------------

    def _device_reply(self, line: str) -> str:
        """Emulate the firmware line parser (see ``firmware/README.md``)."""
        tokens = line.split()
        if not tokens:
            return "ERR"
        cmd, args = tokens[0], tokens[1:]
        if cmd in {"K", "D", "U"}:
            return "OK" if args else "ERR"
        if cmd == "M":
            return "OK" if self._valid_mouse_args(args) else "ERR"
        if cmd == "A":
            ok = len(args) == 2 and all(a.isdigit() and int(a) <= ABS_MAX for a in args)
            return "OK" if ok else "ERR"
        if cmd in {"C", "H", "R"}:
            return "OK" if len(args) == 1 and args[0] in {"L", "R"} else "ERR"
        if cmd == "P":
            return "PONG" if not args else "ERR"
        if cmd == "X":
            return "OK" if not args else "ERR"
        return "ERR"

    @staticmethod
    def _valid_mouse_args(args: list[str]) -> bool:
        """True if ``args`` are two integers each within -4000..4000."""
        if len(args) != 2:
            return False
        try:
            dx = int(args[0])
            dy = int(args[1])
        except ValueError:
            return False
        return (
            -MAX_MOUSE_DELTA <= dx <= MAX_MOUSE_DELTA
            and -MAX_MOUSE_DELTA <= dy <= MAX_MOUSE_DELTA
        )


# =============================================================================
# ArduinoInput -- the real pyserial transport (task T16)
# =============================================================================


class ArduinoInputError(RuntimeError):
    """Base class for errors raised by the Arduino serial backend."""


class NoArduinoFoundError(ArduinoInputError):
    """No Arduino Leonardo could be found or verified on the serial bus."""


class DeviceLostError(ArduinoInputError):
    """The Arduino was declared LOST; commands are refused (safety stop).

    Raised by the logical methods once the heartbeat missed
    :attr:`ArduinoInput.max_missed_pongs` pings in a row, or after a serial
    I/O error dropped the link. The bot loop must treat this as "halt".
    """


class SerialPortLike(Protocol):
    """The slice of pyserial's ``Serial`` that :class:`ArduinoInput` uses.

    Declared as a Protocol (instead of importing pyserial) so this module
    has no static dependency on it; unit tests satisfy it with an in-memory
    fake.
    """

    port: str
    timeout: float

    @property
    def in_waiting(self) -> int:
        """Bytes already received and not yet read."""
        ...

    def write(self, data: bytes | bytearray | memoryview | str) -> int:
        ...

    def readline(self, size: int = -1) -> bytes:
        ...

    def reset_input_buffer(self) -> None:
        ...

    def close(self) -> None:
        ...


def find_arduino_port(
    vid: int = ARDUINO_LEONARDO_VID,
    pid: int | None = None,
) -> str | None:
    """Find a serial port belonging to an Arduino Leonardo.

    Scans ``serial.tools.list_ports.comports()`` for a port whose USB VID
    equals *vid* (and whose PID equals *pid*, when given).

    Returns:
        The device path (``"COM5"`` on Windows, ``/dev/ttyACM0`` on Linux),
        or None when no matching port is present.

    pyserial is imported lazily so this module stays importable without it.
    """
    from serial.tools import list_ports  # type: ignore[import-untyped]

    for info in list_ports.comports():
        if info.vid == vid and (pid is None or info.pid == pid):
            device: str | None = info.device
            return device
    return None


def open_serial_port(port: str, baudrate: int, read_timeout: float) -> SerialPortLike:
    """Open *port* at *baudrate* with pyserial (lazy import)."""
    import serial  # type: ignore[import-untyped]

    ser: SerialPortLike = serial.Serial(port=port, baudrate=baudrate)
    ser.timeout = read_timeout
    return ser


class ArduinoInput(InputBackend):
    """Real :class:`InputBackend` that drives the Arduino Leonardo over USB
    serial with pyserial (115200 baud, one wire line per command).

    Behavior (task T16):

    * **Port selection.** Uses *port* when given; otherwise auto-detects the
      Leonardo by scanning for USB VID :data:`ARDUINO_LEONARDO_VID`.
    * **Heartbeat.** A daemon thread sends ``P`` every
      *heartbeat_interval* seconds (default 1.0) and expects ``PONG``.
      This also feeds the firmware watchdog so it never releases inputs
      while the bot is running.
    * **Reconnect.** If the link drops (I/O error or repeated missed pings)
      the heartbeat thread closes the dead port, re-detects the Arduino and
      re-opens it with exponential backoff. Recovery clears the lost state,
      so the bot resumes when the board is re-plugged.
    * **Safety stop.** After *max_missed_pongs* consecutive failed pings (or
      any serial I/O error) the device is declared LOST:
      :attr:`is_alive` turns False, :meth:`ping` returns False and every
      logical command raises :class:`DeviceLostError`, so the bot loop can
      halt instead of silently dropping input.
    * **Thread safety.** All serial I/O is serialized with one lock, so the
      20 Hz bot loop and the heartbeat thread never interleave wire lines.
    * **Fire-and-forget (P6).** ``K``/``D``/``M``/``C`` do not wait for
      their ``OK``: the write returns at once and the ACK is checked
      later, without blocking, before the next command (and by every
      synchronous command / heartbeat, which first drain the pending ACKs).
      A rejected or missing ACK raises :class:`ArduinoInputError` from the
      next command, so a disarmed board still halts the bot, one command
      late. Releases (``U``/``X``) and ``P`` stay synchronous: a stop must
      be confirmed.

    Import-safe: pyserial is imported lazily inside methods, so this module
    imports cleanly without pyserial or hardware.

    Example::

        with ArduinoInput() as ard:
            if not ard.is_alive:
                raise SystemExit("Arduino not responding")
            ard.press_combo("F1")
            ard.mouse_move(50, -20)
            ard.release_all()
    """

    def __init__(
        self,
        port: str | None = None,
        *,
        encoder: ProtocolEncoder | None = None,
        vid: int = ARDUINO_LEONARDO_VID,
        pid: int | None = None,
        baudrate: int = 115200,
        heartbeat_interval: float = 1.0,
        max_missed_pongs: int = 3,
        reconnect_backoff: float = 0.5,
        max_reconnect_backoff: float = 5.0,
        read_timeout: float = 0.5,
    ) -> None:
        """Open the Arduino and start the heartbeat thread.

        Args:
            port: Explicit serial port (e.g. ``"COM5"``). When None, the
                Arduino is auto-detected by USB VID.
            encoder: Optional :class:`ProtocolEncoder` (for tests).
            vid / pid: USB IDs used for auto-detection.
            baudrate: Serial baud rate (the firmware uses 115200).
            heartbeat_interval: Seconds between heartbeat pings.
            max_missed_pongs: Consecutive missed pings before the device is
                declared LOST (safety stop).
            reconnect_backoff: Initial reconnect delay; doubles on each
                failed attempt up to *max_reconnect_backoff*.
            read_timeout: Seconds to wait for a device reply.

        Raises:
            ValueError: On invalid timing parameters.
            NoArduinoFoundError: When no matching Arduino can be opened and
                verified (P -> PONG).
        """
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be > 0")
        if max_missed_pongs < 1:
            raise ValueError("max_missed_pongs must be >= 1")
        if reconnect_backoff <= 0:
            raise ValueError("reconnect_backoff must be > 0")
        if max_reconnect_backoff < reconnect_backoff:
            raise ValueError("max_reconnect_backoff must be >= reconnect_backoff")
        if read_timeout <= 0:
            raise ValueError("read_timeout must be > 0")

        self.encoder = encoder if encoder is not None else ProtocolEncoder()
        self._explicit_port = port
        self._vid = vid
        self._pid = pid
        self.baudrate = baudrate
        self.heartbeat_interval = heartbeat_interval
        self.max_missed_pongs = max_missed_pongs
        self.reconnect_backoff = reconnect_backoff
        self.max_reconnect_backoff = max_reconnect_backoff
        self.read_timeout = read_timeout

        self._lock = threading.Lock()
        self._port: SerialPortLike | None = None
        #: Fire-and-forget commands still awaiting their ACK (P6).
        self._pending: deque[str] = deque()
        #: First bad ACK seen while draining; raised by the next command.
        self._ack_error: str | None = None
        self._device_lost = False
        self._consecutive_missed = 0
        self._closed = False
        self._stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

        # An explicit port is honored exclusively; auto-detect otherwise.
        if not self._open_and_verify(allow_detected=port is None):
            where = (
                f"on port {port!r}" if port is not None else f"with VID 0x{vid:04x}"
            )
            raise NoArduinoFoundError(
                f"no Arduino found {where} "
                "(is it plugged in and flashed? see firmware/README.md)"
            )

        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="moonlight-arduino-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        logger.info("ArduinoInput connected on %s", self.connected_port)

    # -- InputBackend: logical methods ------------------------------------------

    def press_combo(self, combo: str) -> None:
        """Tap a key combo. Wire: ``K <combo>``."""
        self._send_async("press_combo", self.encoder.press_combo(combo))

    def hold_combo(self, combo: str) -> None:
        """Hold a key combo down. Wire: ``D <combo>``."""
        self._send_async("hold_combo", self.encoder.hold_combo(combo))

    def release_combo(self, combo: str) -> None:
        """Release a held combo. Wire: ``U <combo>``."""
        self._send_command("release_combo", self.encoder.release_combo(combo))

    def mouse_move(self, dx: int, dy: int) -> None:
        """Relative mouse move. Wire: ``M <dx> <dy>``."""
        self._send_async("mouse_move", self.encoder.mouse_move(dx, dy))

    def mouse_to(self, x: int, y: int) -> None:
        """Absolute mouse move. Wire: ``A <x> <y>``."""
        self._send_async("mouse_to", self.encoder.mouse_to(x, y))

    def click(self, side: ClickSide) -> None:
        """Mouse click. Wire: ``C L`` / ``C R``."""
        self._send_async("click", self.encoder.click(side))

    def mouse_down(self, side: ClickSide) -> None:
        """Hold a mouse button. Wire: ``H L`` / ``H R``."""
        self._send_async("mouse_down", self.encoder.mouse_down(side))

    def mouse_up(self, side: ClickSide) -> None:
        """Release a mouse button. Wire: ``R L`` / ``R R``."""
        self._send_command("mouse_up", self.encoder.mouse_up(side))

    def ping(self) -> bool:
        """Send ``P``; True iff the device answers ``PONG`` right now."""
        if self._closed or self._device_lost:
            return False
        return self._send_line(self.encoder.ping()) == "PONG"

    def release_all(self) -> None:
        """Release every key/button. Wire: ``X`` (kill / safety path)."""
        self._send_command("release_all", self.encoder.release_all())

    # -- liveness / state ---------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        """True while the link is up and the device is not declared LOST."""
        return self._port is not None and not self._device_lost

    @property
    def connected_port(self) -> str | None:
        """Currently connected port name, or None when disconnected."""
        with self._lock:
            ser = self._port
        return ser.port if ser is not None else None

    # -- lifecycle ------------------------------------------------------------------

    def close(self) -> None:
        """Stop the heartbeat, release all inputs, and close the port.

        Best-effort and idempotent: safe to call after a loss or twice.
        """
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        thread = self._heartbeat_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=self.read_timeout + 2.0 * self.heartbeat_interval + 1.0)
        self._send_line(self.encoder.release_all())  # best effort; may be None
        with self._lock:
            self._close_port_locked()
        logger.info("ArduinoInput closed")

    def __enter__(self) -> "ArduinoInput":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- internal: command path ------------------------------------------------------

    def _send_command(self, op: str, line: str) -> None:
        """Send one encoded command and require an ``OK`` reply.

        Raises:
            DeviceLostError: When the device is known to be lost (safety).
            ArduinoInputError: On a dropped link or a non-OK reply (e.g. the
                kill switch disarmed the board).
        """
        self._require_alive(op)
        reply = self._send_line(line)
        if reply is None:
            raise ArduinoInputError(f"{op}: no reply from device (link dropped?)")
        if reply != "OK":
            raise ArduinoInputError(
                f"{op}: device rejected the command (reply: {reply!r})"
            )

    def _send_async(self, op: str, line: str) -> None:
        """Write one fire-and-forget command; its ACK is checked later (P6).

        Raises:
            DeviceLostError: When the device is known to be lost (safety).
            ArduinoInputError: On a write failure, or when an earlier
                command's ACK was rejected or never came (nothing is sent).
        """
        self._require_alive(op)
        with self._lock:
            ser = self._port
            if ser is None:
                raise ArduinoInputError(f"{op}: no serial port (link dropped?)")
            try:
                self._drain_acks_locked(ser, block=len(self._pending) >= MAX_PENDING_ACKS)
                error, self._ack_error = self._ack_error, None
                if error is None:
                    ser.write(f"{line}\n".encode())
                    self._pending.append(op)
            except Exception as exc:  # USB yanked, driver error, port busy...
                logger.warning("serial I/O failed on %s: %s", ser.port, exc)
                self._device_lost = True
                self._close_port_locked()
                raise ArduinoInputError(f"{op}: serial I/O failed: {exc}") from exc
        if error is not None:
            raise ArduinoInputError(error)

    def _drain_acks_locked(self, ser: SerialPortLike, *, block: bool) -> None:
        """Consume pending ACKs; remember the first bad one. Hold ``_lock``.

        Non-blocking reads only what has already arrived; *block* waits
        (up to the read timeout per line) for every pending ACK.
        """
        while self._pending:
            if not block and not ser.in_waiting:
                return
            raw = ser.readline()
            op = self._pending.popleft()
            reply = raw.decode(errors="replace").strip() if raw else None
            if reply != "OK" and self._ack_error is None:
                self._ack_error = (
                    f"{op}: no reply from device (link dropped?)"
                    if reply is None
                    else f"{op}: device rejected the command (reply: {reply!r})"
                )
            if reply is None:
                self._pending.clear()  # silent device: don't wait for the rest
                return

    def _require_alive(self, op: str) -> None:
        if self._closed or self._device_lost:
            raise DeviceLostError(
                f"{op}: Arduino is lost (link down or too many missed pings); "
                "the bot loop must halt safely"
            )

    # -- internal: raw I/O -------------------------------------------------------------

    def _send_line(self, line: str) -> str | None:
        """Send one wire line; return the device reply, or None on failure.

        None is returned when the port is gone or the device never replied.
        A failed write marks the device LOST (safety).
        """
        with self._lock:
            ser = self._port
            if ser is None:
                return None
            try:
                self._drain_acks_locked(ser, block=True)
                ser.reset_input_buffer()
                ser.write(f"{line}\n".encode())
                raw = ser.readline()
            except Exception as exc:  # USB yanked, driver error, port busy...
                logger.warning("serial I/O failed on %s: %s", ser.port, exc)
                self._device_lost = True
                self._close_port_locked()
                return None
        if not raw:
            return None
        return raw.decode(errors="replace").strip()

    def _close_port_locked(self) -> None:
        """Close and forget the current port. Caller must hold ``_lock``."""
        ser, self._port = self._port, None
        self._pending.clear()
        if ser is not None:
            self._quiet_close(ser)

    @staticmethod
    def _quiet_close(ser: SerialPortLike) -> None:
        try:
            ser.close()
        except Exception:
            pass

    # -- internal: heartbeat + reconnect -----------------------------------------------

    def _heartbeat_loop(self) -> None:
        """Heartbeat thread: ping every interval; recover when the link dies."""
        while not self._stop_event.is_set():
            if self._device_lost:
                self._recover_until_alive()
                continue
            if self._heartbeat_tick():
                self._consecutive_missed = 0
            else:
                self._consecutive_missed += 1
                if self._consecutive_missed < self.max_missed_pongs:
                    self._stop_event.wait(self.heartbeat_interval)
                    continue
                self._device_lost = True
                logger.warning(
                    "Arduino declared LOST after %d missed pings; reconnecting",
                    self._consecutive_missed,
                )
                self._recover_until_alive()
                continue
            self._stop_event.wait(self.heartbeat_interval)

    def _heartbeat_tick(self) -> bool:
        """One heartbeat: send ``P``; report whether ``PONG`` arrived."""
        return self._send_line(self.encoder.ping()) == "PONG"

    def _recover_until_alive(self) -> None:
        """Re-open the Arduino (re-detected) with backoff.

        Blocks on the heartbeat thread until the link is alive again or the
        backend is being closed.
        """
        with self._lock:
            self._close_port_locked()
        backoff = self.reconnect_backoff
        while not self._stop_event.is_set():
            # On reconnect also allow the VID-detected port: after a re-plug
            # the OS may have assigned a different COM port.
            if self._open_and_verify(allow_detected=True):
                with self._lock:
                    self._device_lost = False
                    self._consecutive_missed = 0
                logger.info("Arduino reconnected on %s", self.connected_port)
                return
            self._stop_event.wait(backoff)
            backoff = min(backoff * 2.0, self.max_reconnect_backoff)

    def _open_and_verify(self, allow_detected: bool) -> bool:
        """Open a candidate port and verify it answers P -> PONG.

        Tries the candidates in priority order. On success the connection is
        kept open and True is returned.
        """
        for candidate in self._candidate_ports(allow_detected):
            try:
                ser = open_serial_port(candidate, self.baudrate, self.read_timeout)
            except ImportError:
                raise  # pyserial missing: fail loudly, don't retry forever
            except Exception as exc:
                logger.debug("cannot open %s: %s", candidate, exc)
                continue
            if self._verify_serial(ser):
                with self._lock:
                    self._port = ser
                return True
            self._quiet_close(ser)
        return False

    def _verify_serial(self, ser: SerialPortLike) -> bool:
        """Send P over a fresh (unshared) port; True iff it answers PONG."""
        try:
            ser.reset_input_buffer()
            ser.write(b"P\n")
            raw = ser.readline()
        except Exception:
            return False
        return raw.strip() == b"PONG"

    def _candidate_ports(self, allow_detected: bool) -> list[str]:
        """Ports to try, in priority order.

        The explicit port comes first (and alone, when given); the
        VID-detected port is an extra candidate only when *allow_detected*
        (i.e. during auto-detect or reconnect).
        """
        candidates: list[str] = []
        if self._explicit_port is not None:
            candidates.append(self._explicit_port)
        if allow_detected:
            detected = find_arduino_port(self._vid, self._pid)
            if detected is not None and detected not in candidates:
                candidates.append(detected)
        return candidates
