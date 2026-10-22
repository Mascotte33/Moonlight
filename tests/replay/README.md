# tests/replay/ - perception replay on real frames (T28)

Runs every perception reader over the labelled golden frames in
`tests/golden/` and checks the results against `tests/golden/labels.json`.

## Files

| File | What it checks | Tests |
|------|----------------|-------|
| `test_replay_perception.py` | All 8 readers (vitals, battle, minimap, floor, cursor, sprites, logocr, loot): label coverage, a fast 10-frame subset, and the full replay | 3 |

The frames are local-only (R7), so the tests skip on other machines. The log
reader fails closed when the Tesseract binary is missing.

## Run

```bash
python -m pytest tests/replay -q
```
