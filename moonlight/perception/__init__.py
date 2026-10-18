"""Perception layer: OpenCV screen-region readers (see README.md).

Readers:
    - VitalsReader:    HP/mana/supremes (vitals.py)
    - BattleReader:    loot, monsters, deaths (battle.py)
    - MinimapReader:   player position x, y, z (minimap.py)
    - FloorReader:     current z-level / floor index (floor.py)
    - SpriteDetector:  monster sprite positions (sprites.py)
    - CursorReader:    mouse-cursor position for closed-loop clicking (cursor.py)

Example:
    >>> profile = CalibrationProfile.load("profiles/default.json")
    >>> vitals = VitalsReader(profile)
    >>> battle = BattleReader(profile)
    >>> floor = FloorReader(profile)
    >>> minimap = MinimapReader(profile, floor_reader=floor)
    >>> frame = capture.get_frame()            # RGB (H, W, 3)
    >>> stats = vitals.read_vitals(frame)
"""

from __future__ import annotations

from moonlight.perception.battle import (
    BattleEntry,
    BattleReader,
    NameResolver,
    Rect,
    entries_to_state,
)
from moonlight.perception.cursor import (
    CursorReader,
    CursorTemplates,
)
from moonlight.perception.floor import (
    FLOOR_LEVELS,
    GROUND_FLOOR,
    FloorReader,
    TemplateSet,
)
from moonlight.perception.minimap import (
    MapMatcher,
    MinimapReader,
)
from moonlight.perception.sprites import (
    MonsterTemplates,
    SpriteDetection,
    SpriteDetector,
)
from moonlight.perception.vitals import (
    RGB,
    VitalsReader,
)

__all__ = [
    "BattleEntry",
    "BattleReader",
    "CursorReader",
    "CursorTemplates",
    "FLOOR_LEVELS",
    "FloorReader",
    "GROUND_FLOOR",
    "MapMatcher",
    "MinimapReader",
    "MonsterTemplates",
    "NameResolver",
    "RGB",
    "Rect",
    "SpriteDetection",
    "SpriteDetector",
    "TemplateSet",
    "VitalsReader",
    "entries_to_state",
]
