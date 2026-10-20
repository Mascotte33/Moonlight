"""Battle list reading (T19).

Extracts the creatures from the Tibia client's battle/combat list widget
(bottom-right of the window) and provides the data for
``WorldState.monsters``, ``selected_target`` and ``players_on_screen``.

Layout facts (``docs/ui_layout.md`` section 3, measured from the tibiaeye /
PyTibia production bots):

* one creature per 22 px slot (:data:`SLOT_HEIGHT`), max ~20 rows
  (:data:`MAX_SLOTS`);
* content width 156 px (:data:`CONTENT_WIDTH`);
* the first slot starts 11 px below the widget top (:data:`TOP_PADDING`);
* the creature name starts at x=23 (:data:`NAME_START_X`) and spans ~115 px;
* text is bright pixels on a dark background: grayscale > 150 = text pixel
  (:data:`TEXT_THRESHOLD`; real text grays are ~192 / 247);
* every creature row has an HP bar under its name whose black outline is a
  long dark run on slot rows 14..21 (:data:`HP_BAR_ROWS`); a text row
  without it is some other panel's text, not a creature (T28: on the golden
  frames whose layout puts another widget at the calibrated rect, every
  phantom row lacked it and every real entry had it);
* the HP fill is the row under the outline's top edge, inside the
  outline's run: HP-coloured pixels (green/yellow/red, channel spread >
  :data:`HP_FILL_MIN_SPREAD`) are HP left, grey panel pixels HP lost
  (F-16, measured on the golden frames: yellow ``c0c000`` fill, ``~4a4a4a``
  lost);
* the selected target's creature icon is framed by a pure-red 20x20 px
  outline at slot x 1..20, y 0..19 (:data:`SELECTED_RGB`; measured on the
  T08 golden frames in T28 -- the older grey-corner rule (76/166) matched
  the client's stone panel background and flagged every row).

A row counts as a creature entry when its name area contains at least
``min_text_pixels`` text pixels and an HP-bar outline. Everything is vectorised with NumPy, so a
full read of a 20-row list is well under the 15 ms per-read budget (T30
timing requirement; see ``perception/README.md``).

Name resolution (template matching or OCR) is pluggable via
:class:`NameResolver`. Until the golden dataset ships (T08) the default
reader returns empty names and never fabricates OCR output. ``is_player``
is derived from the resolved name when a monster-name set is provided
(unknown name = likely a player; ui_layout.md section 8, technique 3).

Frames are RGB ``(H, W, 3)`` ``uint8`` arrays as produced by
``moonlight.capture``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from moonlight.state import Monster

__all__ = [
    "Rect",
    "SLOT_HEIGHT",
    "CONTENT_WIDTH",
    "TOP_PADDING",
    "NAME_START_X",
    "NAME_WIDTH",
    "MAX_SLOTS",
    "TEXT_THRESHOLD",
    "SELECTED_RGB",
    "HP_BAR_ROWS",
    "BattleEntry",
    "NameResolver",
    "BattleReader",
    "entries_to_state",
]

#: A rectangle as ``(x, y, width, height)`` (matches ``calibrate.Rect``).
Rect = tuple[int, int, int, int]

#: Battle-list slot height in pixels (ui_layout.md #3).
SLOT_HEIGHT = 22

#: Battle-list content width in pixels (ui_layout.md #3).
CONTENT_WIDTH = 156

#: Vertical padding above the first slot (ui_layout.md #3).
TOP_PADDING = 11

#: X offset of the creature name within a slot (ui_layout.md #3).
NAME_START_X = 23

#: Approximate name width in pixels within a slot (ui_layout.md #3).
NAME_WIDTH = 115

#: Maximum rows the battle-list widget can show (156x440 / 22 ~ 20).
#: T33: recalibrate on server -- moonlight_user.json's battle_list is only
#: 58 px tall (TOP_PADDING + 2 slots, the 2nd bar's fill cut off; F-16), so
#: at most one creature reads with HP. Kept as-is: no live client to measure.
MAX_SLOTS = 20

#: Grayscale threshold: pixels strictly greater are text (ui_layout.md #3).
TEXT_THRESHOLD = 150

#: Slot rows (start, stop) that hold the HP bar's outline (T28).
HP_BAR_ROWS: tuple[int, int] = (14, 22)

#: Grayscale below which a pixel belongs to the HP bar's black outline.
HP_BAR_DARK = 30

#: Minimum dark run (px) of the outline; real bars span ~130 px.
HP_BAR_MIN_RUN = 80
#: Channel spread (max - min) above which a fill pixel is HP colour
#: (green/yellow/red); the lost part shows the grey panel (spread ~0).
HP_FILL_MIN_SPREAD = 60

#: Attack-target outline color around the selected row's icon (T28).
SELECTED_RGB: tuple[int, int, int] = (255, 0, 0)

#: Red pixels each horizontal edge of the 20 px outline must carry (of 20).
SELECTED_MIN_EDGE_PIXELS = 15

#: Per-channel tolerance for the outline color (compression artifacts).
DEFAULT_HIGHLIGHT_TOLERANCE = 30

#: Minimum text pixels in the name area for a row to be a creature entry.
DEFAULT_MIN_TEXT_PIXELS = 3


@dataclass(frozen=True)
class BattleEntry:
    """One creature row of the battle list.

    Attributes:
        name: Creature name (via :class:`NameResolver`); empty string when
            no resolver is configured or the name is unknown.
        index: 0-based row position in the battle list.
        is_player: True when the name is not a known monster name.
        is_selected: True when this row is the currently attacked target.
        hp_percent: Creature HP percent when visible; None otherwise.
    """

    name: str
    index: int = 0
    is_player: bool = False
    is_selected: bool = False
    hp_percent: float | None = None


class NameResolver(Protocol):
    """Resolves the creature name of one battle-list slot.

    Implementations perform template matching or OCR on the slot's
    grayscale crop. Return an empty string for unknown names.
    """

    def resolve(self, row: int, slot_gray: np.ndarray) -> str:  # pragma: no cover
        """Return the creature name for *row* (empty string if unknown)."""


class BattleReader:
    """Read the battle list from captured frames using calibrated regions.

    Construct from a :class:`moonlight.calibrate.CalibrationProfile` (which
    stores the ``battle_list`` region) and/or an explicit region tuple. The
    stored region is the default used when :meth:`read_battle_list` is
    called without an explicit region.

    Example:
        >>> profile = CalibrationProfile.load("profiles/default.json")
        >>> reader = BattleReader(profile, monster_names={"wolf", "rat"})
        >>> frame = capture.get_frame()            # RGB (H, W, 3)
        >>> entries = reader.read_battle_list(frame)
        >>> monsters, selected, players = entries_to_state(entries)
    """

    def __init__(
        self,
        profile: object | None = None,
        name_resolver: NameResolver | None = None,
        monster_names: set[str] | None = None,
        *,
        battle_region: Rect | None = None,
        slot_height: int = SLOT_HEIGHT,
        top_padding: int = TOP_PADDING,
        content_width: int = CONTENT_WIDTH,
        text_threshold: int = TEXT_THRESHOLD,
        highlight_tolerance: int = DEFAULT_HIGHLIGHT_TOLERANCE,
        min_text_pixels: int = DEFAULT_MIN_TEXT_PIXELS,
    ) -> None:
        """Create a reader.

        Args:
            profile: Optional ``CalibrationProfile``. Its ``battle_list``
                region is used as the default region.
            name_resolver: Optional resolver for creature names.
            monster_names: Known monster names; resolved names not in this
                set are classified as players (None disables the check).
            battle_region: Explicit battle-list region; overrides the
                profile.
            slot_height: Pixel height of one battle-list slot.
            top_padding: Pixels between the region top and the first slot.
            content_width: Pixel width of the slot content.
            text_threshold: Grayscale threshold for text pixels.
            highlight_tolerance: Per-channel tolerance for the red
                target outline.
            min_text_pixels: Minimum text pixels for a row to be an entry.

        Raises:
            ValueError: If a region is not a 4-element tuple.
        """
        self._slot_height = int(slot_height)
        self._top_padding = int(top_padding)
        self._content_width = int(content_width)
        self._text_threshold = int(text_threshold)
        self._highlight_tolerance = int(highlight_tolerance)
        self._min_text_pixels = int(min_text_pixels)
        self._name_resolver = name_resolver
        self._monster_names = monster_names
        self._battle_region: Rect | None = None

        if profile is not None:
            regions = getattr(profile, "regions", None)
            if regions:
                battle = regions.get("battle_list")
                if battle is not None:
                    self._battle_region = self._as_rect(battle.to_tuple)

        # Explicit region takes priority.
        if battle_region is not None:
            self._battle_region = self._as_rect(battle_region)

    # -- public API ---------------------------------------------------------

    def read_battle_list(
        self,
        frame: np.ndarray,
        region: Rect | None = None,
    ) -> list[BattleEntry]:
        """Return the creature entries visible in the battle list.

        Args:
            frame: RGB ``(H, W, 3)`` ``uint8`` capture.
            region: Battle-list region ``Rect``; defaults to the region
                stored in the constructor.

        Returns:
            The creature entries in list order (empty when no region is
            known, the region is out of bounds, or no text is detected).
        """
        region = region if region is not None else self._battle_region
        if region is None:
            return []
        crop = self._crop(frame, region)
        if crop is None:
            return []
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        entries: list[BattleEntry] = []
        height, width = gray.shape
        for row in range(MAX_SLOTS):
            top = self._top_padding + row * self._slot_height
            if top >= height:
                break
            bottom = min(top + self._slot_height, height)
            cols = min(self._content_width, width)
            slot = gray[top:bottom, :cols]
            if slot.size == 0:
                continue
            bar = self._hp_bar(slot) if self._has_text(slot) else None
            if bar is None:
                continue
            name = self._resolve_name(row, slot)
            slot_rgb = crop[top:bottom, :cols]
            entries.append(
                BattleEntry(
                    name=name,
                    index=row,
                    is_player=self._is_player(name),
                    is_selected=self._is_selected(slot_rgb),
                    hp_percent=self._hp_percent(slot_rgb, bar),
                )
            )
        return entries

    # -- helpers ------------------------------------------------------------

    def _has_text(self, slot: np.ndarray) -> bool:
        """Return True when the slot's name area holds enough text pixels."""
        width = slot.shape[1]
        x0 = min(NAME_START_X, width)
        x1 = min(width, NAME_START_X + NAME_WIDTH)
        if x1 <= x0:
            return False
        text = slot[:, x0:x1]
        pixels = int(np.count_nonzero(text > self._text_threshold))
        return pixels >= self._min_text_pixels

    @staticmethod
    def _hp_bar(slot: np.ndarray) -> tuple[int, int, int] | None:
        """The HP-bar outline under the name as ``(row, x0, x1)``, or None.

        *row* is the outline's top edge and ``x0:x1`` its longest dark run
        (slot coordinates); None means no bar, so not a creature row.
        """
        bar = slot[HP_BAR_ROWS[0] : HP_BAR_ROWS[1], NAME_START_X:] < HP_BAR_DARK
        for i, row in enumerate(bar):
            # Runs of True: [start, stop) pairs from the 0/1 edges.
            edges = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
            if not edges.size:
                continue
            k = int(np.argmax(edges[1::2] - edges[::2]))
            x0, x1 = int(edges[2 * k]), int(edges[2 * k + 1])
            if x1 - x0 >= HP_BAR_MIN_RUN:
                return HP_BAR_ROWS[0] + i, NAME_START_X + x0, NAME_START_X + x1
        return None

    @staticmethod
    def _hp_percent(slot_rgb: np.ndarray, bar: tuple[int, int, int]) -> float | None:
        """HP percent from the fill row inside the outline (F-16).

        None when no fill pixel is HP-coloured: a listed creature has HP
        left, so an empty fill row means a misread, never "0 % HP".
        """
        row, x0, x1 = bar
        if row + 1 >= slot_rgb.shape[0] or x1 - x0 < 3:
            return None
        fill = slot_rgb[row + 1, x0 + 1 : x1 - 1, :3].astype(np.int16)
        left = int(np.count_nonzero(fill.max(axis=1) - fill.min(axis=1) > HP_FILL_MIN_SPREAD))
        return 100.0 * left / len(fill) if left else None

    def _is_selected(self, slot_rgb: np.ndarray) -> bool:
        """Detect the red target outline around the slot's creature icon.

        Both horizontal edges (rows 0 and 19, left of the name) must carry
        at least :data:`SELECTED_MIN_EDGE_PIXELS` outline-red pixels; the
        red HP bar (rows ~16-18, right of x=23) and red icons do not.
        """
        if slot_rgb.shape[0] < 20:
            return False
        icon = slot_rgb[:20, :NAME_START_X, :3].astype(np.int16)
        ref = np.array(SELECTED_RGB, dtype=np.int16)
        red = (np.abs(icon - ref) <= self._highlight_tolerance).all(axis=2)
        return bool(
            red[0].sum() >= SELECTED_MIN_EDGE_PIXELS
            and red[19].sum() >= SELECTED_MIN_EDGE_PIXELS
        )

    def _resolve_name(self, row: int, slot: np.ndarray) -> str:
        """Resolve the slot's creature name via the configured resolver."""
        if self._name_resolver is None:
            return ""
        return str(self._name_resolver.resolve(row, slot))

    def _is_player(self, name: str) -> bool:
        """Classify a resolved name as player when it is not a monster."""
        if not name or self._monster_names is None:
            return False
        return name not in self._monster_names

    @staticmethod
    def _crop(frame: np.ndarray, region: Rect) -> np.ndarray | None:
        """Clip *region* to the frame bounds and return a view of the crop.

        Returns ``None`` if the region is empty or lies entirely outside
        the frame.
        """
        h, w = frame.shape[:2]
        x, y, rw, rh = region
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(w, int(x) + int(rw))
        y1 = min(h, int(y) + int(rh))
        if x1 <= x0 or y1 <= y0:
            return None
        return frame[y0:y1, x0:x1, :3]

    @staticmethod
    def _as_rect(rect: object) -> Rect:
        """Validate and coerce a region to a ``Rect`` (as in vitals.py)."""
        if not isinstance(rect, Sequence) or isinstance(rect, (str, bytes)):
            raise ValueError(f"Region must be a 4-element sequence, got {rect!r}")
        if len(rect) != 4:
            raise ValueError(f"Region must have 4 values, got {len(rect)}")
        return (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))


def entries_to_state(
    entries: Sequence[BattleEntry],
) -> tuple[list[Monster], Monster | None, int]:
    """Convert battle-list entries into WorldState combat fields.

    Args:
        entries: Battle-list entries in list order.

    Returns:
        ``(monsters, selected_target, players_on_screen)`` where
        ``monsters`` holds the non-player entries (``hp_percent`` defaults
        to 100.0 when the entry has none), ``selected_target`` is the
        Monster of the selected entry (None when nothing is selected), and
        ``players_on_screen`` counts the player entries.
    """
    monsters: list[Monster] = []
    selected: Monster | None = None
    players_on_screen = 0
    for entry in entries:
        hp = 100.0 if entry.hp_percent is None else entry.hp_percent
        if entry.is_player:
            players_on_screen += 1
            if entry.is_selected and selected is None:
                selected = Monster(name=entry.name, hp_percent=hp, is_player=True)
            continue
        monster = Monster(name=entry.name, hp_percent=hp)
        monsters.append(monster)
        if entry.is_selected and selected is None:
            selected = monster
    return monsters, selected, players_on_screen
