# instructions.md — `perception/` (screen region readers)

**Read this before touching anything in this folder.**
Last verified: 2026-10-15.

## What this is
OpenCV-based screen-region readers. Each module reads ONE region of the
captured client frame and returns a typed value into `WorldState`
(`moonlight.state`). OCR is used only when unavoidable (prefer templates /
contour matching).

## Status
**`vitals.py`, `battle.py`, `minimap.py`, `floor.py`, `sprites.py`, and
`cursor.py`, `loot.py` are DONE (T21/T17/T23); golden accuracy for all is pending
T08 data.**

| Module | Task | Reads (region) | Returns | Status |
|--------|------|----------------|---------|--------|
| `vitals.py` | T12 | HP/MP bars | hp, mp (0-100) | ✅ done (T25 golden pending T08) |
| `battle.py` | T19 | battle list | entries (name, is_player, is_selected) → monsters/selected/players | ✅ done (golden pending T08) |
| `loot.py` | T23 | `loot_window` region | `LootWindow` (open, items by slot) via title + item templates; None without templates | ✅ done (T08 templates pending) |
| `minimap.py` | T21 | minimap/radar | world (x, y), player-centered | ✅ done (golden pending T08) |
| `floor.py` | T21/T23 | floor indicator | z (floor level), 16 levels | ✅ done (golden pending T08) |
| `sprites.py` | T17/T23 | monster sprites on field | sprite (x, y) positions via template match + NMS | ✅ done (T08 templates pending) |
| `cursor.py` | T17 | cursor region | cursor (x, y) center via template match (fail-closed: None) | ✅ done (T08 templates pending) |

## How to test / check
```bash
cd <repo-root>
python -m pytest tests/unit/test_vitals.py tests/unit/test_battle.py   # offline, synthetic frames
python -m pytest tests/perf/                                           # golden tests (skip until T08 data)
ruff check perception/ && mypy perception/
```

## Agent notes
- **Golden-frame tests:** perception modules are validated against the golden
  frame dataset + labels (`tools/label_golden.py`, `tests/golden/labels.json`
  — see `tools/instructions.md`). Add golden-frame tests per module.
- Depends on `capture.py` for frames and `calibrate.py` for region rectangles
  (from `profiles/*.json`). Those are DONE.
- Implement one region at a time. Use synthetic frames in unit tests (no live
  client needed).
- Update `perception/README.md` and this file when a module goes stub → done.
