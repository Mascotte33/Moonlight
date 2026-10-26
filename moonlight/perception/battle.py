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
  the client's stone panel background and flagged every row). Its top edge
  sits :data:`SELECTED_BAR_OFFSET` px above the HP bar's outline, so it is
  found from the bar, not from the slot top: a region a few px off still
  reads the bar and must still read the selection.

A row counts as a creature entry when its name area contains at least
``min_text_pixels`` text pixels and an HP-bar outline. Everything is vectorised with NumPy, so a
full read of a 20-row list is well under the 15 ms per-read budget (T30
timing requirement; see ``perception/README.md``).

Each entry carries :func:`name_crop_hash`, a stable identity of its name
pixels (no OCR). ``name`` comes from the optional ``SeenMonsters`` store, looked up by
that hash (empty without one). ``is_player`` is derived from the name when a monster-name set is
provided (unknown name = likely a player; ui_layout.md section 8,
technique 3).

Frames are RGB ``(H, W, 3)`` ``uint8`` arrays as produced by
``moonlight.perception.capture``.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from moonlight.core.state import Monster

if TYPE_CHECKING:
    from moonlight.targeting.seen_monsters import SeenMonsters

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
    "BattleReader",
    "entries_to_state",
    "name_crop_hash",
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

#: Rows from the target outline's top edge down to the HP bar's outline
#: (67 of 67 selected golden entries; the synthetic test frames use 16).
SELECTED_BAR_OFFSET = 15

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
        name: Creature name; empty string until a later task fills it.
        index: 0-based row position in the battle list.
        is_player: True when the name is not a known monster name.
        is_selected: True when this row is the currently attacked target.
        hp_percent: Creature HP percent when visible; None otherwise.
        name_hash: :func:`name_crop_hash` of the name pixels; empty when
            the name area holds no text.
    """

    name: str
    index: int = 0
    is_player: bool = False
    is_selected: bool = False
    hp_percent: float | None = None
    name_hash: str = ""


def name_crop_hash(slot_name_crop: np.ndarray) -> str:
    """Stable identity of a slot's name pixels; ``""`` when there is no text.

    Binarizes the crop (max channel over :data:`TEXT_THRESHOLD`), trims it to
    the bounding box of the set pixels and hashes the bits with the shape, so
    the HP bar, the selection outline and a slightly different background do
    not change it.
    """
    # doc: docs/prs/targeting.md#name-hash
    bright = slot_name_crop if slot_name_crop.ndim == 2 else slot_name_crop.max(axis=2)
    bits = bright > TEXT_THRESHOLD
    rows, cols = np.flatnonzero(bits.any(axis=1)), np.flatnonzero(bits.any(axis=0))
    if not rows.size:
        return ""
    bits = bits[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]
    shape_bytes = np.array(bits.shape, dtype=np.int32).tobytes()
    return hashlib.blake2b(bits.tobytes() + shape_bytes, digest_size=8).hexdigest()


class BattleReader:
    """Read the battle list from captured frames using calibrated regions.

    Construct from a :class:`moonlight.calibration.calibrate.CalibrationProfile` (which
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
        monster_names: set[str] | None = None,
        *,
        battle_region: Rect | None = None,
        slot_height: int = SLOT_HEIGHT,
        top_padding: int = TOP_PADDING,
        content_width: int = CONTENT_WIDTH,
        text_threshold: int = TEXT_THRESHOLD,
        highlight_tolerance: int = DEFAULT_HIGHLIGHT_TOLERANCE,
        min_text_pixels: int = DEFAULT_MIN_TEXT_PIXELS,
        seen_monsters: SeenMonsters | None = None,
    ) -> None:
        """Create a reader.

        Args:
            profile: Optional ``CalibrationProfile``. Its ``battle_list``
                region is used as the default region.
            monster_names: Known monster names; names not in this
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
            seen_monsters: Store that turns a name hash into a name.

        Raises:
            ValueError: If a region is not a 4-element tuple.
        """
        # doc: docs/prs/gui-calibration.md#ui-scale
        scale = float(getattr(profile, "ui_scale", 1.0))

        def px(v: int) -> int:
            return round(v * scale)

        self._slot_height = px(slot_height)
        self._top_padding = px(top_padding)
        self._content_width = px(content_width)
        self._name_x, self._name_w = px(NAME_START_X), px(NAME_WIDTH)
        self._bar_rows = (px(HP_BAR_ROWS[0]), px(HP_BAR_ROWS[1]))
        self._bar_min_run = px(HP_BAR_MIN_RUN)
        self._icon = px(20)
        self._edge_min = px(SELECTED_MIN_EDGE_PIXELS)
        self._bar_offset = px(SELECTED_BAR_OFFSET)
        # A 1 px line spreads over this many rows/cols once enlarged.
        self._line = max(1, math.ceil(scale))
        self._text_threshold = int(text_threshold)
        self._highlight_tolerance = int(highlight_tolerance)
        self._min_text_pixels = int(min_text_pixels)
        self._monster_names = monster_names
        self._seen_monsters = seen_monsters
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
            slot_rgb = crop[top:bottom, :cols]
            name_x1 = min(cols, self._name_x + self._name_w)
            outline_top = top + bar[0] - self._bar_offset
            name_crop = slot_rgb[: bar[0], self._name_x : name_x1]
            name_hash = name_crop_hash(name_crop)
            name = ""
            if self._seen_monsters is not None and name_hash:
                name = self._seen_monsters.name_for(name_hash, name_crop)
            entries.append(
                BattleEntry(
                    name=name,
                    index=row,
                    is_player=self._is_player(name),
                    is_selected=self._is_selected(crop[:, :cols], outline_top),
                    hp_percent=self._hp_percent(slot_rgb, bar),
                    name_hash=name_hash,
                )
            )
        return entries

    # -- helpers ------------------------------------------------------------

    def _has_text(self, slot: np.ndarray) -> bool:
        """Return True when the slot's name area holds enough text pixels."""
        width = slot.shape[1]
        x0 = min(self._name_x, width)
        x1 = min(width, self._name_x + self._name_w)
        if x1 <= x0:
            return False
        text = slot[:, x0:x1]
        pixels = int(np.count_nonzero(text > self._text_threshold))
        return pixels >= self._min_text_pixels

    def _hp_bar(self, slot: np.ndarray) -> tuple[int, int, int] | None:
        """The HP-bar outline under the name as ``(row, x0, x1)``, or None.

        *row* is the outline's top edge and ``x0:x1`` its longest dark run
        (slot coordinates); None means no bar, so not a creature row.
        """
        (r0, r1), nx = self._bar_rows, self._name_x
        bar = slot[r0:r1, nx:] < HP_BAR_DARK
        for i, row in enumerate(bar):
            # Runs of True: [start, stop) pairs from the 0/1 edges.
            edges = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
            if not edges.size:
                continue
            k = int(np.argmax(edges[1::2] - edges[::2]))
            x0, x1 = int(edges[2 * k]), int(edges[2 * k + 1])
            if x1 - x0 >= self._bar_min_run:
                return r0 + i, nx + x0, nx + x1
        return None

    def _hp_percent(self, slot_rgb: np.ndarray, bar: tuple[int, int, int]) -> float | None:
        """HP percent from the fill row inside the outline (F-16).

        None when no fill pixel is HP-coloured: a listed creature has HP
        left, so an empty fill row means a misread, never "0 % HP".
        """
        row, x0, x1 = bar
        k = self._line
        if row + k >= slot_rgb.shape[0] or x1 - x0 < 2 * k + 1:
            return None
        fill = slot_rgb[row + k, x0 + k : x1 - k, :3].astype(np.int16)
        left = int(np.count_nonzero(fill.max(axis=1) - fill.min(axis=1) > HP_FILL_MIN_SPREAD))
        return 100.0 * left / len(fill) if left else None

    def _is_selected(self, crop: np.ndarray, outline_top: int) -> bool:
        """Detect the red target outline around the slot's creature icon.

        *outline_top* is the crop row where the outline's top edge should
        be, from the HP bar. Both horizontal edges (that row and the one
        19 px below, each with ``_line`` rows of slack, left of the name)
        must carry at least :data:`SELECTED_MIN_EDGE_PIXELS` outline-red
        pixels; the red HP bar (right of x=23) and red icons do not.
        """
        # doc: docs/prs/keys-walking.md#target-outline
        k, n = self._line, self._icon
        y0, y1 = outline_top - k, outline_top + n - 1 + k + 1
        if y0 < 0 or y1 > crop.shape[0]:
            return False
        icon = crop[y0:y1, : self._name_x, :3].astype(np.int16)
        ref = np.array(SELECTED_RGB, dtype=np.int16)
        red = (np.abs(icon - ref) <= self._highlight_tolerance).all(axis=2)
        edge = red.sum(axis=1) >= self._edge_min
        return bool(edge[: 2 * k + 1].any() and edge[-(2 * k + 1) :].any())

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
                selected = Monster(
                    name=entry.name,
                    hp_percent=hp,
                    is_player=True,
                    name_hash=entry.name_hash,
                    battle_list_row=entry.index,
                )
            continue
        monster = Monster(
            name=entry.name,
            hp_percent=hp,
            name_hash=entry.name_hash,
            battle_list_row=entry.index,
        )
        monsters.append(monster)
        if entry.is_selected and selected is None:
            selected = monster
    return monsters, selected, players_on_screen
