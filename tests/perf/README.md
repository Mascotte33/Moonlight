# tests/perf/ - golden-frame accuracy and timing tests

Checks the `moonlight/perception/` readers against the labelled golden
screenshots in `tests/golden/`, and checks the 20 Hz (50 ms) tick budget.

## Files

| File | What it checks | Tests |
|------|----------------|-------|
| `conftest.py` | Collects one line per test into a "golden accuracy" summary at the end of the run | - |
| `test_vitals_golden.py` | `moonlight/perception/vitals.py` against labelled HP/MP | 3 |
| `test_battle_golden.py` | `moonlight/perception/battle.py` against labelled monsters and target | 4 |
| `test_minimap_golden.py` | `moonlight/perception/minimap.py` against labelled positions | 3 |
| `test_floor_golden.py` | `moonlight/perception/floor.py` against labelled floors | 2 |
| `test_golden_smoke.py` | Every reader runs on every frame without crashing (sprites, cursor, loot and log have no labels) | 7 |
| `test_timing_report.py` | Per-module latency table (`-s` to see it) and the production tick within budget | 2 |

## Data

- Frames: `tests/golden/*.png` (`data/golden/` is also searched).
- Labels: `tests/golden/labels.json`, made with `tools/label_golden.py`.
- Regions: the calibrated profile in `moonlight/profiles/`.

All of this is local-only (R7). When it is missing the tests skip and say why.
Floor tests also need the level icons in `data/templates/floorLevels/`.

## Run

```bash
python -m pytest tests/perf -rs    # -rs shows why each test skipped
```
