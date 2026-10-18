"""Server-log OCR (T38) — the one approved OCR exception.

Crops the calibrated ``log_region`` from a frame, runs Tesseract (via
``pytesseract``) and classifies each new text line as LOOT / DAMAGE / KILL /
OTHER with simple keyword heuristics. Fail closed: disabled, bad region,
low OCR confidence or any OCR error yields ``LogFrame(ok=False, ...)``;
``read`` never raises. Lines already seen in the previous read are not
re-emitted as events (dedupe by line text).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np

__all__ = ["EventKind", "LogEvent", "LogFrame", "LogOCR", "LogOCRConfig"]


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
        self._seen: set[str] = set()

    def read(self, frame: np.ndarray, config: LogOCRConfig) -> LogFrame:
        try:
            return self._read(frame, config)
        except Exception:  # noqa: BLE001 - perception must never raise
            return LogFrame()

    def _read(self, frame: np.ndarray, config: LogOCRConfig) -> LogFrame:
        if not config.enabled:
            return LogFrame()
        crop = self._crop(frame, config)
        if crop is None:
            return LogFrame()
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
        if config.scale > 1:
            gray = cv2.resize(
                gray, None, fx=config.scale, fy=config.scale, interpolation=cv2.INTER_CUBIC
            )
        text = self._ocr(gray)
        conf = self._ocr_confidence(gray)
        if conf < config.min_confidence:
            return LogFrame()
        lines = [s.strip() for s in text.splitlines() if s.strip()]
        events = [
            LogEvent(self._classify(line, config), line, conf / 100.0)
            for line in lines
            if line not in self._seen
        ]
        self._seen = set(lines)
        return LogFrame(lines=lines, events=events, ok=True, confidence=conf / 100.0)

    @staticmethod
    def _crop(frame: np.ndarray, config: LogOCRConfig) -> np.ndarray | None:
        """Region clipped to the frame; ``None`` if too small."""
        h, w = frame.shape[:2]
        x, y, rw, rh = config.log_region
        x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + rw), min(h, y + rh)
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

    def _ocr(self, image: np.ndarray) -> str:
        """Swappable Tesseract call (tests mock this)."""
        import pytesseract

        return str(pytesseract.image_to_string(image))

    def _ocr_confidence(self, image: np.ndarray) -> float:
        """Mean word confidence 0-100 (swappable); 0.0 when nothing was read."""
        import pytesseract

        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        confs = [
            float(c)
            for c, t in zip(data["conf"], data["text"], strict=False)
            if str(t).strip() and float(c) >= 0
        ]
        return sum(confs) / len(confs) if confs else 0.0
