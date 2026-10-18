# tests/perf/ - golden-frame accuracy + performance tests

Validates `perception/` readers against human-labelled golden screenshots
(T08 dataset) and checks the per-read < 10 ms perception budget (T30).

## Files

| File | Module under test | Tests |
|------|-------------------|-------|
| `test_vitals_golden.py` | `perception/vitals.py` | 3 |
| `test_battle_golden.py` | `perception/battle.py` | 4 |
| `test_minimap_golden.py` | `perception/minimap.py` | 3 |
| `test_floor_golden.py` | `perception/floor.py` | 2 |

## Data (T08, pending)

Each test looks for:
- frames: `data/golden/*.png` (preferred) or `tests/golden/*.png`
- labels: `tests/golden/labels.json` or `data/golden/labels.json`
- regions: the calibrated rects from `profiles/<name>.json`

Labels are produced by `tools/label_golden.py`. Until the dataset exists the
tests **skip cleanly** (12 skips) — they never fabricate golden images or
labels.

## Run

```bash
cd <repo-root>
python -m pytest tests/perf/ -rs    # -rs shows why each test skipped
```
