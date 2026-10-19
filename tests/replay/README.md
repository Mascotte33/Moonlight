# tests/replay/ — perception replay on real frames (T28)

Replays the 200 human-labeled golden frames (see `tests/golden/`) through every
perception module and checks the results against the labels.

## Contents
- `test_replay_perception.py` — covers all 8 perception modules
  (vitals, battle, minimap, floor, cursor, sprites, logocr, loot):
  a fast 10-frame subset + the full 200-frame replay.

## Baseline (2026-10-19)
- Vitals: 199/199 frames within ±2 pp of labels (HP mean abs err 0.00, MP 0.06).
- Battle: 80/200 frames have entries, 67 have a selected target.
- Minimap: player marker detected on 173/200 frames.
- LogOCR: fail-closed when the Tesseract binary is missing.

## Run
```bash
python -m pytest tests/replay -q
```
