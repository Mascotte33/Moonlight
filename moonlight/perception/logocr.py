"""Server-log OCR (T38) — the one approved OCR exception.

Crops the calibrated ``log_region`` from a frame, runs Tesseract (via
``pytesseract``) and classifies each new text line as LOOT / DAMAGE / KILL /
OTHER with simple keyword heuristics. Fail closed: disabled, bad region,
low OCR confidence or any OCR error yields ``LogFrame(ok=False, ...)``;
``read`` never raises.

* **Cost (P4).** One Tesseract run per read (``image_to_data`` gives text
  and confidence together), on an Otsu-thresholded crop upscaled with
  INTER_LINEAR; with ``line_height`` calibrated only the bottom
  ``max_lines`` lines are cropped. :class:`LogOCRWorker` runs reads off
  the tick on a daemon thread at <= 1 Hz, so the tick never waits on OCR.
* **Failures (F-21).** The first OCR failure is logged once with its
  exception (a missing Tesseract binary is otherwise indistinguishable
  from an empty log); :func:`health` is what ``tools/check_setup.py`` reports.
* **Dedupe (F-21).** By position, not text: the new read is aligned with
  the previous one (the log scrolls up), and only lines below the overlap
  are new -- so two identical "You loot nothing" lines are two events.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np

__all__ = [
    "EventKind",
    "LogEvent",
    "LogFrame",
    "LogOCR",
    "LogOCRConfig",
    "LogOCRWorker",
    "health",
]

logger = logging.getLogger(__name__)


class EventKind(Enum):
    LOOT = "loot"
    DAMAGE = "damage"
    KILL = "kill"
    OTHER = "other"


@dataclass(frozen=True)
class LogOCRConfig:
    """Calibration + heuristics. Disabled until calibrated (fail closed)."""

    log_region: tuple[int, int, int, int]  # x, y, w, h in client pixels
    min_confidence: int = 60  # Tesseract mean word confidence, 0-100
    enabled: bool = False
    min_size: int = 8  # smallest usable crop edge in px
    scale: int = 2  # upscale before OCR; small log font reads badly at 1x
    max_lines: int = 3  # only the newest lines are read (P4)
    # Pixel height of one log line; crops the bottom max_lines lines before
    # OCR. None = OCR the whole region. T33: recalibrate on server.
    line_height: int | None = None
    kill_keywords: tuple[str, ...] = ("died", "killed", "dies")
    loot_keywords: tuple[str, ...] = ("loot of", "got", "found")
    damage_keywords: tuple[str, ...] = ("hit you for", "took you down", "you lose", "hitpoints")


@dataclass(frozen=True)
class LogEvent:
    kind: EventKind
    text: str
    confidence: float  # 0-1


@dataclass
class LogFrame:
    lines: list[str] = field(default_factory=list)
    events: list[LogEvent] = field(default_factory=list)
    ok: bool = False
    confidence: float = 0.0  # 0-1


class LogOCR:
    """Read the server log. Stateful: remembers the previous read's lines."""

    def __init__(self) -> None:
        self._prev: list[str] = []
        #: The most recent OCR failure (None after a successful read).
        self.last_error: Exception | None = None
        self._failure_logged = False

    def read(self, frame: np.ndarray, config: LogOCRConfig) -> LogFrame:
        try:
            result = self._read(frame, config)
        except Exception as exc:  # noqa: BLE001 - perception must never raise
            self.last_error = exc
            if not self._failure_logged:
                self._failure_logged = True
                logger.warning("log OCR failed (Tesseract installed?): %r", exc)
            return LogFrame()
        self.last_error = None
        return result

    def _read(self, frame: np.ndarray, config: LogOCRConfig) -> LogFrame:
        if not config.enabled:
            return LogFrame()
        crop = self._crop(frame, config)
        if crop is None:
            return LogFrame()
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
        _, gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        if config.scale > 1:
            gray = cv2.resize(
                gray, None, fx=config.scale, fy=config.scale, interpolation=cv2.INTER_LINEAR
            )
        raw, conf = self._ocr_lines(gray)
        if conf < config.min_confidence:
            return LogFrame()
        lines = [s.strip() for s in raw if s.strip()][-config.max_lines :]
        new = lines[_overlap(self._prev, lines) :]
        self._prev = lines
        events = [LogEvent(self._classify(line, config), line, conf / 100.0) for line in new]
        return LogFrame(lines=lines, events=events, ok=True, confidence=conf / 100.0)

    @staticmethod
    def _crop(frame: np.ndarray, config: LogOCRConfig) -> np.ndarray | None:
        """Region clipped to the frame; ``None`` if too small."""
        h, w = frame.shape[:2]
        x, y, rw, rh = config.log_region
        x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + rw), min(h, y + rh)
        if config.line_height is not None:
            y0 = max(y0, y1 - config.line_height * config.max_lines)
        if x1 - x0 < config.min_size or y1 - y0 < config.min_size:
            return None
        return frame[y0:y1, x0:x1]

    @staticmethod
    def _classify(line: str, config: LogOCRConfig) -> EventKind:
        low = line.lower()
        for kind, words in (
            (EventKind.KILL, config.kill_keywords),
            (EventKind.LOOT, config.loot_keywords),
            (EventKind.DAMAGE, config.damage_keywords),
        ):
            if any(k in low for k in words):
                return kind
        return EventKind.OTHER

    def _ocr_lines(self, image: np.ndarray) -> tuple[list[str], float]:
        """One Tesseract run: ``(lines, mean word confidence 0-100)``.

        Swappable (tests mock this). Confidence is 0.0 when nothing was read.
        """
        import pytesseract

        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        lines: dict[tuple[int, int, int], list[str]] = {}
        confs: list[float] = []
        for i, word in enumerate(data["text"]):
            word, conf = str(word).strip(), float(data["conf"][i])
            if not word or conf < 0:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append(word)
            confs.append(conf)
        text = [" ".join(words) for words in lines.values()]
        return text, (sum(confs) / len(confs) if confs else 0.0)


def _overlap(prev: list[str], cur: list[str]) -> int:
    """Lines of *cur* already seen: the longest tail of *prev* heading *cur*."""
    for k in range(min(len(prev), len(cur)), 0, -1):
        if prev[-k:] == cur[:k]:
            return k
    return 0


def health() -> tuple[bool, str]:
    """Is OCR usable here? ``(ok, detail)`` for ``tools/check_setup.py`` (F-21)."""
    try:
        import pytesseract

        return True, f"tesseract {pytesseract.get_tesseract_version()}"
    except Exception as exc:  # noqa: BLE001 - missing module or binary
        return False, f"tesseract unavailable: {exc}"


class LogOCRWorker:
    """Run :meth:`LogOCR.read` off the tick on a daemon thread (P4).

    Reads *frame_provider* at most once per *interval* seconds; the tick
    only collects results with :meth:`drain` (never blocks on Tesseract).
    """

    def __init__(
        self,
        reader: LogOCR,
        config: LogOCRConfig,
        frame_provider: Callable[[], np.ndarray],
        *,
        interval: float = 1.0,
    ) -> None:
        if interval < 1.0:
            raise ValueError(f"interval must be >= 1.0 s (<= 1 Hz), got {interval}")
        self._reader = reader
        self._config = config
        self._frames = frame_provider
        self._interval = interval
        self._lock = threading.Lock()
        self._events: list[LogEvent] = []
        self._latest = LogFrame()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="logocr", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)

    @property
    def latest(self) -> LogFrame:
        """The most recent read."""
        with self._lock:
            return self._latest

    def drain(self) -> list[LogEvent]:
        """Events read since the last drain."""
        with self._lock:
            events, self._events = self._events, []
        return events

    def poll(self) -> None:
        """One read (the thread body; callable directly in tests)."""
        try:
            frame = self._frames()
        except Exception as exc:  # noqa: BLE001 - capture hiccup: skip this read
            logger.debug("log OCR: no frame: %r", exc)
            return
        result = self._reader.read(frame, self._config)
        with self._lock:
            self._latest = result
            self._events.extend(result.events)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self._interval)
