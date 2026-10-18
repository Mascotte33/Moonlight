# instructions.md — `decision/` (priority state machine)

**Read this before touching anything in this folder.**
Last verified: 2026-10-14.

## What this is
Decision modules. Priority order: **HEAL > ALARM > ATTACK > LOOT > WALK**.
- `screen_ok == false` or kill switch → stop everything.
- Rate limit ~10 actions/s; never emit input unless `client_focused`.
- Reads from `WorldState` (`moonlight.state`); emits intents for `loop.py` →
  `input.py` to execute.

## Status
`healing.py`, `buffs.py` and `targeting.py` are implemented; the rest are
still docstring placeholders.

| Module | Task | Responsibility | Status |
|--------|------|----------------|--------|
| `healing.py` | T14 | Emergency/normal heal, mana, cures, cooldowns | ✅ done (2026-10-15) |
| `targeting.py` | T20 | Target priority list, selection, attack rotation | ✅ done (2026-10-15) |
| `buffs.py` | T26 | Buff recast tracking (missing / expiring soon) | ✅ done (2026-10-15) |
| `cavebot.py` | T22 | Waypoint execution (walk/rope/shovel/ladder), stuck detection | ⬜ stub |
| `looting.py` | T23 | Open corpse, move items by templates, cap check | ⬜ stub |
| `alarms.py` | T25 | Player on screen / PM / fast HP drop → alarm + sound | ✅ done (2026-10-15) |

## How to test / check
```bash
cd <repo-root>
python -m pytest tests/unit/ -k "healing or targeting or buffs"  # 138 tests
ruff check . && mypy .
```

## Agent notes
- **Blocked upstream:** most decision logic depends on `perception/` filling
  `WorldState`. Check `perception/instructions.md` status first.
- Implement one module at a time (one task per agent). Add `tests/unit/test_<module>.py`
  using `FakeInput` and a synthetic `WorldState`.
- Update `decision/README.md` and this file when a module goes from stub → done.
- **No evasion:** decisions are game logic only (heal/attack/loot/walk/alarms).
  Nothing here may target anti-cheat evasion.
