# moonlight/perception/ - screen region readers

OpenCV-based readers over captured frames. Read only pixels from capture.py;
each module returns structured data consumed by state.py.

| Module | Task | Reads | Returns | Status |
|--------|------|-------|---------|--------|
| vitals.py | T12 | HP/MP bars (bar colors) | hp, mp as % (+/-2% on golden) | ✅ done (T25 golden pending T08) |
| battle.py | T19 | Battle list | entries (name, is_player, is_selected, hp) → monsters/selected/players via `entries_to_state` | ✅ done (golden pending T08) |
| minimap.py | T21 | Minimap | world (x, y) via radar map match, player-centered (106x109) | ✅ done (golden pending T08) |
| floor.py | T21 | Floor indicator icon | z (floor level) via template match, 16 levels | ✅ done (golden pending T08) |
| sprites.py | T17/T23 | Monster sprites on field | sprite (x, y) positions via template match + NMS | ✅ done (T08 templates pending) |
| cursor.py | T17 | Cursor region | cursor (x, y) center via template match (None = not found) | ✅ done (T08 templates pending) |
| logocr.py | T38 | Server log | OCR (Tesseract) → loot/damage/kill events; disabled until calibrated | ✅ done |
| loot.py | T23 | Corpse/container | items by templates | ⬜ stub |

Requirements: perception step < 10 ms (T30 timing report); OCR only when
unavoidable. Accuracy tests: tests/golden/ (golden frames).
