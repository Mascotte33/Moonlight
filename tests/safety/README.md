# tests/safety/ — safety invariant suite (T29)

The full safety suite: the invariants the bot must never violate, independent of
feature behavior.

## Contents
- `test_safety_suite.py` — 12 invariant tests: kill-switch (F12) stops exactly one
  tick after activation; fail-closed on lost client focus / bad screen state;
  stuck + lost (floor desync) detection; release-all on stop/exit; HP/MP emergency
  paths; the 10/s input rate limit; IDLE sends no input; loot-window focus gate;
  refill cap gate.

## Run
```bash
python -m pytest tests/safety -q        # ~0.15 s, fully offline
```
