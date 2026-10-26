"""Monsters seen in the battle list, and the tesseract guess of their names.

# doc: docs/prs/targeting.md#seen-monsters

The tick only looks a name hash up (:meth:`SeenMonsters.name_for`). A daemon
thread runs tesseract once per new hash, so the tick never blocks (P4). Each
hash also gets a PNG of its battle-list slot (icon and name), so the owner can
see which monster it is.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

__all__ = ["SeenMonster", "SeenMonsters"]

logger = logging.getLogger(__name__)

#: Factor the name crop is enlarged by before tesseract reads it.
GUESS_SCALE = 3


@dataclass
class SeenMonster:
    """One name hash: tesseract's guess and the owner's correction."""

    name_hash: str
    guessed_name: str = ""
    name: str = ""

    @property
    def display_name(self) -> str:
        return self.name or self.guessed_name


class SeenMonsters:
    """The seen-monsters file of one profile, with a background name guesser."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        #: ``p.seen_monsters.json`` keeps its pictures in ``p.seen_monsters/``.
        self._images_dir = self._path.with_suffix("")
        self._hashes_with_image: set[str] = set()
        self._lock = threading.Lock()
        self._monsters: dict[str, SeenMonster] = {}
        self._crops: queue.Queue[tuple[str, np.ndarray]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._guessing_stopped = False
        if self._path.exists():
            self._load()

    def name_for(self, name_hash: str, name_crop: np.ndarray, clue_crop: np.ndarray) -> str:
        """The owner's name, else the guess, else ``""``. Never blocks.

        *clue_crop* (icon and name) is saved once per hash as its picture.
        """
        if name_hash not in self._hashes_with_image:
            self._save_image(name_hash, clue_crop)
        with self._lock:
            monster = self._monsters.get(name_hash)
            if monster is not None:
                return monster.display_name
            self._monsters[name_hash] = SeenMonster(name_hash)
            self._save()
            if not self._guessing_stopped:
                self._crops.put((name_hash, name_crop.copy()))
                if self._worker is None:
                    self._worker = threading.Thread(target=self._guess_loop, daemon=True)
                    self._worker.start()
        return ""

    def rename(self, name_hash: str, name: str) -> None:
        """Store the owner's name for *name_hash*."""
        with self._lock:
            self._monsters.setdefault(name_hash, SeenMonster(name_hash)).name = name
            self._save()

    def image_path(self, name_hash: str) -> Path:
        """Where the slot picture of *name_hash* is (it may not exist yet)."""
        return self._images_dir / f"{name_hash}.png"

    def _save_image(self, name_hash: str, clue_crop: np.ndarray) -> None:
        import cv2

        path = self.image_path(name_hash)
        if not path.exists():
            self._images_dir.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(path), cv2.cvtColor(clue_crop, cv2.COLOR_RGB2BGR)):
                raise OSError(f"could not write {path}")
        self._hashes_with_image.add(name_hash)

    def entries(self) -> list[SeenMonster]:
        """A copy of every seen monster, for the tab."""
        with self._lock:
            return [
                SeenMonster(m.name_hash, m.guessed_name, m.name)
                for m in self._monsters.values()
            ]

    def _guess(self, name_crop: np.ndarray) -> str:
        import cv2
        import pytesseract

        big = cv2.resize(
            name_crop, None, fx=GUESS_SCALE, fy=GUESS_SCALE, interpolation=cv2.INTER_CUBIC
        )
        return str(pytesseract.image_to_string(big, config="--psm 7")).strip().lower()

    def _guess_loop(self) -> None:
        while True:
            name_hash, name_crop = self._crops.get()
            try:
                guess = self._guess(name_crop)
            except Exception as error:  # noqa: BLE001 - no tesseract: stop guessing, bot goes on
                logger.warning("name guessing stopped, the owner can still type names: %s", error)
                with self._lock:
                    self._guessing_stopped = True
                self._crops.task_done()
                return
            with self._lock:
                self._monsters[name_hash].guessed_name = guess
                self._save()
            self._crops.task_done()

    def _load(self) -> None:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        try:
            for name_hash, fields in raw.items():
                self._monsters[name_hash] = SeenMonster(
                    name_hash, str(fields["guessed_name"]), str(fields["name"])
                )
        except (AttributeError, KeyError, TypeError) as error:
            raise ValueError(f"broken seen-monsters file {self._path}: {error!r}") from error

    def _save(self) -> None:
        """Write a temp file, then replace the real one with it (caller holds the lock)."""
        data = {
            m.name_hash: {"guessed_name": m.guessed_name, "name": m.name}
            for m in self._monsters.values()
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(self._path.name + ".tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temp, self._path)
