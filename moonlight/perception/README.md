# moonlight/perception/

OpenCV readers over captured frames. Each module reads one region and returns a typed value for `WorldState`.
Prefer templates and contour matching. Use OCR only when unavoidable.
Region rectangles come from a profile, see [../profiles/README.md](../profiles/README.md).

| Module | Reads | Returns | Status |
|--------|-------|---------|--------|
| `capture.py` | The client window (mss, optional dxcam), DPI aware, 20 Hz | frames | done |
| `vitals.py` | HP and MP bars | hp, mp as % | done |
| `battle.py` | Battle list (22 px slots) | entries (name, is_player, is_selected). `entries_to_state` gives monsters, target, players. Names come from a pluggable `NameResolver` and stay empty until templates exist | done |
| `minimap.py` | Minimap | world (x, y), player-centered | done |
| `floor.py` | Floor indicator | z level (16 levels) by template match | done |
| `sprites.py` | Monster sprites on the field | (x, y) list by template match + NMS | done |
| `cursor.py` | Cursor region | (x, y) center, `None` if not found (fail-closed) | done |
| `loot.py` | `loot_window` region | `LootWindow` via title template + 5x5 grid of 34 px slots. `None` without templates or region | done |
| `logocr.py` | Server log (Tesseract) | loot, damage, kill events. Off until calibrated | done |

Item and sprite templates and golden accuracy data are still pending, so some readers return empty results today.

## How to test

```bash
python -m pytest tests/unit -q -k "vitals or battle or minimap or floor or cursor or sprites or logocr or capture"
python -m pytest tests/perf tests/replay -q    # golden frames, skip when data is missing
```

Use synthetic frames in unit tests (no live client). The per-read budget is under 10 ms (`tests/perf/test_timing_report.py`).
Golden labels come from `tools/label_golden.py`, see [../../tools/README.md](../../tools/README.md).
