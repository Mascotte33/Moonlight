# tests/safety/ - safety invariant suite (T29)

The rules the bot must never break, independent of any feature. Fully offline:
`FakeInput` records every wire line, the world state is set by hand and the
clock is fake.

## Files

| File | What it checks | Tests |
|------|----------------|-------|
| `test_safety_suite.py` | Tests 1-12 drive the loop directly. F12 kill switch stops and releases; fail closed on an unfocused window or a bad screen; stuck and lost-floor alarms; release-all on stop; HP emergency heal; no spells on low MP; input rate limit; no input while idle; loot input only in an open, focused window; refill only above the cap. Tests 13-18 do the same through the production `build_loop`: healing from a golden frame, kill while unfocused, pause on a bad screen, a module or perception exception releases and stops, pause and resume on focus. | 18 |

## Run

```bash
python -m pytest tests/safety -q
```
