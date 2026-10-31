"""Extras: food, haste, a buff potion, a spell buff, ring and amulet, player timer, PM alarm.

Each extra has its own switch and presses a client hotkey. They look for
pictures the owner cuts from a screenshot into ``data/templates/icons/``
(see :data:`ICONS`), anywhere in the client window, once a second. An extra
whose picture is missing never presses. The PM alarm needs no picture: it
looks for the light blue of a received private message (:data:`PM_RGB`) or
the red name of an unread private message tab (:data:`NPCS_TAB`).

# doc: docs/prs/extras.md#extras
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from moonlight.core.state import WorldState
from moonlight.healing.healing import check_hotkey

__all__ = [
    "ICONS", "ICONS_DIR", "Extras", "ExtrasConfig", "locate", "pm_shown", "save_screenshot"
]

logger = logging.getLogger(__name__)

ICONS_DIR = Path("data/templates/icons")
#: Picture name -> what it shows.
ICONS = {
    "hungry": "the hungry icon in the status icon row",
    "haste": "the haste icon in the status icon row",
    "pz": "the protection zone icon in the status icon row",
    "buff": "the buff potion's icon in the status icon row",
    "spell_buff": "the spell buff's icon in the status icon row",
    "ring_empty": "the empty ring slot",
    "amulet_empty": "the empty amulet slot",
}
MATCH = 0.9
#: A received private message's text colour, in the chat and the game view
#: (2026-10-30 grab: nothing else in town, depot or cave is drawn in it).
PM_RGB, PM_TOLERANCE, PM_PIXELS = (96, 248, 248), 8, 50
#: The black outline of the NPCs chat tab's name (the same in any name colour).
#: The owner's client opens a new tab for each private message, right of the NPCs
#: tab, so a red (unread) name right of it is an unread PM.
NPCS_TAB = Path("data/templates/npcs_tab.png")
TAB_RED, PM_TAB_PIXELS = (247, 95, 95), 30
SCAN_GAP = 1.0  # s between picture scans
#: Around a picture's first spot: the status row shifts sideways as icons come and go.
SPOT_PAD_X, SPOT_PAD_Y = 100, 10
PRESS_GAP = 2.0  # s between two presses of one key: the icon takes a moment to change
PLAYER_GONE = 60.0  # s out of the player list before a player's timer resets
SCREENSHOTS_DIR = Path("screenshots")
KEEP_SCREENSHOTS = 20


@dataclass(frozen=True)
class ExtrasConfig:
    """One switch and one hotkey per extra, plus the player limits."""

    food: bool = False
    food_hotkey: str = ""
    haste: bool = False
    haste_hotkey: str = ""
    buff: bool = False
    buff_hotkey: str = ""
    spell_buff: bool = False
    spell_buff_hotkey: str = ""
    ring: bool = False
    ring_hotkey: str = ""
    amulet: bool = False
    amulet_hotkey: str = ""
    #: A player in the players-only battle list longer than this is "staying" (0 = at once).
    player_minutes: float = 5.0
    #: A staying player sends the character to refill (the next ``check`` jumps).
    player_refill: bool = False
    #: A staying player sounds the alarm and saves a screenshot.
    player_alarm: bool = False
    #: A private message sounds the alarm and saves a screenshot.
    pm_alarm: bool = False

    def validate(self) -> ExtrasConfig:
        for name in ("food", "haste", "buff", "spell_buff", "ring", "amulet"):
            if getattr(self, name):
                check_hotkey(getattr(self, f"{name}_hotkey"), f"extras.{name}")
        if self.player_minutes < 0:
            raise ValueError("extras.player_minutes must be >= 0")
        return self


def load_icons(directory: Path = ICONS_DIR) -> dict[str, np.ndarray]:
    """The :data:`ICONS` pictures found in *directory*, RGB."""
    import cv2

    out = {}
    for name in ICONS:
        image = cv2.imread(str(directory / f"{name}.png"), cv2.IMREAD_COLOR)
        if image is not None:
            out[name] = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return out


def locate(frame: np.ndarray, picture: np.ndarray) -> tuple[int, int] | None:
    """Top-left (x, y) of *picture* in *frame*, or None (same colour order, or both gray)."""
    import cv2

    if frame.shape[0] < picture.shape[0] or frame.shape[1] < picture.shape[1]:
        return None
    scores = cv2.matchTemplate(frame, picture, cv2.TM_CCOEFF_NORMED)
    _, best, _, spot = cv2.minMaxLoc(scores)
    return (int(spot[0]), int(spot[1])) if best >= MATCH else None


def save_screenshot(
    frame: np.ndarray, directory: Path = SCREENSHOTS_DIR, keep: int = KEEP_SCREENSHOTS
) -> Path:
    """Save *frame* (RGB) as a PNG; only the newest *keep* screenshots stay."""
    import cv2

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / time.strftime("%Y%m%d-%H%M%S.png")
    cv2.imwrite(str(path), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    for old in sorted(directory.glob("*.png"))[:-keep]:
        old.unlink()
    return path


# doc: docs/prs/extras.md#extras
def pm_shown(frame: np.ndarray, npcs_tab: np.ndarray | None = None) -> bool:
    """A received private message shows in *frame* (RGB): its text, or its unread tab
    right of *npcs_tab* (:data:`NPCS_TAB`'s outline, gray)."""
    if _pixels(frame, PM_RGB) >= PM_PIXELS:
        return True
    if npcs_tab is None:
        return False
    outline = ((frame.max(axis=2) < 16) * 255).astype(np.uint8)
    spot = locate(outline, npcs_tab)
    if spot is None:
        return False
    x, y = spot
    h, w = npcs_tab.shape
    return _pixels(frame[y : y + h, x + w + 30 :], TAB_RED) >= PM_TAB_PIXELS


def _pixels(frame: np.ndarray, rgb: tuple[int, int, int]) -> int:
    off = np.abs(frame.astype(np.int16) - np.array(rgb, np.int16)).max(axis=2)
    return int((off <= PM_TOLERANCE).sum())


class Extras:
    """Decides the extras' presses, times the players and raises their alarms."""

    def __init__(
        self,
        config: ExtrasConfig,
        *,
        grab: Callable[[], np.ndarray] | None = None,
        clock: Callable[[], float] = time.monotonic,
        pictures: dict[str, np.ndarray] | None = None,
    ) -> None:
        self.config = config.validate()
        self._grab = grab
        self._clock = clock
        if pictures is None:
            pictures = load_icons() if grab else {}
        self._pictures = pictures
        self._npcs_tab = None
        if grab and config.pm_alarm:
            import cv2

            self._npcs_tab = cv2.imread(str(NPCS_TAB), cv2.IMREAD_GRAYSCALE)
        for name in ("food", "haste", "buff", "spell_buff", "ring", "amulet"):
            picture = {
                "food": "hungry",
                "ring": "ring_empty",
                "amulet": "amulet_empty",
            }.get(name, name)
            if getattr(config, name) and picture not in self._pictures:
                logger.warning(
                    "extras: %s is on but %s/%s.png is missing", name, ICONS_DIR, picture
                )
        #: The pictures seen at the last scan.
        self.icons: frozenset[str] = frozenset()
        self._scanned = float("-inf")
        #: Picture name -> where it was first found; later scans look only there.
        self._spots: dict[str, tuple[int, int]] = {}
        self._search_turn = 0
        self._pressed: dict[str, float] = {}
        #: Player name hash -> (first seen, last seen).
        self.players: dict[str, tuple[float, float]] = {}
        #: True on the tick a player starts staying, with player_refill on.
        self.refill_due = False

    def observe(self, snap: WorldState) -> None:
        """Scan the pictures (once a second) and time the players."""
        now = self._clock()
        scan = self._pictures or self.config.pm_alarm
        if self._grab and scan and now - self._scanned >= SCAN_GAP:
            self._scanned = now
            try:
                self.icons = self._scan(self._grab())
            except Exception:  # noqa: BLE001 - a failed grab only skips this scan
                logger.exception("extras: picture scan failed")
        was_staying = self.staying
        for key in snap.players:
            self.players[key] = (self.players.get(key, (now, now))[0], now)
        self.players = {k: v for k, v in self.players.items() if now - v[1] < PLAYER_GONE}
        self.refill_due = self.config.player_refill and self.staying and not was_staying

    # doc: docs/prs/extras.md#extras
    def _scan(self, frame: np.ndarray) -> frozenset[str]:
        """The pictures shown in *frame*: a found one is checked at its spot only.

        A full-window search costs ~50 ms (gray, 2560x1369) against ~1 ms at the
        spot, so one picture not found yet is searched per scan, in turn.
        """
        import cv2

        found = set()
        for name, picture in self._pictures.items():
            if name in self._spots:
                x, y = self._spots[name]
                window = frame[
                    max(0, y - SPOT_PAD_Y) : y + picture.shape[0] + SPOT_PAD_Y,
                    max(0, x - SPOT_PAD_X) : x + picture.shape[1] + SPOT_PAD_X,
                ]
                if locate(window, picture) is not None:
                    found.add(name)
        not_found_yet = [name for name in self._pictures if name not in self._spots]
        if not_found_yet:
            name = not_found_yet[self._search_turn % len(not_found_yet)]
            self._search_turn += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            spot = locate(gray, cv2.cvtColor(self._pictures[name], cv2.COLOR_RGB2GRAY))
            if spot is not None:
                self._spots[name] = spot
                found.add(name)
        if self.config.pm_alarm and pm_shown(frame, self._npcs_tab):
            found.add("pm")
        return frozenset(found)

    @property
    def staying(self) -> bool:
        """A player has been in the player list longer than ``player_minutes``."""
        limit = self.config.player_minutes * 60.0
        return any(last - first >= limit for first, last in self.players.values())

    def alarm(self) -> str | None:
        """The alarm to raise (private message, staying player), or None."""
        if self.config.pm_alarm and "pm" in self.icons:
            return "private message"
        if self.config.player_alarm and self.staying:
            return f"player in sight over {self.config.player_minutes:g} min"
        return None

    def due(self, walking: bool) -> str | None:
        """The hotkey to press now, or None. *walking*: the route walks, no fight."""
        c, icons, seen = self.config, self.icons, self._pictures
        wanted = (
            (c.food, c.food_hotkey, "hungry" in icons),
            (c.ring, c.ring_hotkey, "ring_empty" in icons),
            (c.amulet, c.amulet_hotkey, "amulet_empty" in icons),
            (
                c.haste,
                c.haste_hotkey,
                walking and "haste" in seen and not icons & {"haste", "pz"},
            ),
            (c.buff, c.buff_hotkey, "buff" in seen and "buff" not in icons),
            (
                c.spell_buff,
                c.spell_buff_hotkey,
                "spell_buff" in seen and "spell_buff" not in icons,
            ),
        )
        now = self._clock()
        for on, key, need in wanted:
            if on and need and now - self._pressed.get(key, float("-inf")) >= PRESS_GAP:
                return key
        return None

    def pressed(self, key: str) -> None:
        self._pressed[key] = self._clock()

    def screenshot(self) -> None:
        """Save a screenshot of the client window (never raises)."""
        if self._grab is None:
            return
        try:
            logger.warning("screenshot saved: %s", save_screenshot(self._grab()))
        except Exception:  # noqa: BLE001
            logger.exception("screenshot failed")
