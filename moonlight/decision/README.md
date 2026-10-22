# moonlight/decision/

Decision modules. They read `WorldState` and return intents. The loop in `../bot/loop.py` sends the input.
Priority: HEAL > ALARM > ATTACK > LOOT > WALK. The full state order is in [../README.md](../README.md).

- `screen_ok == false` or kill switch: stop everything.
- Never emit input unless the client is focused. The 10 actions/s limit is enforced at the wire.

| Module | Does | Status |
|--------|------|--------|
| `healing.py` | Emergency and normal heal, mana, cures, cooldowns | done |
| `targeting.py` | `decide(state)` returns attack, switch or flee | done |
| `attack_spells.py` | Cast an attack spell on the selected target | done |
| `buffs.py` | Recast tracking (missing or expiring soon) | done |
| `cavebot.py` | Waypoints: walk, rope, shovel, ladder, label, goto. Stuck and lost detection | done |
| `looting.py` | Open corpse and move all. Per-item drags not built | first pass |
| `refill.py` | Cap full: walk to storage, deposit, walk back. Aborts on cavebot alarms | done |
| `alarms.py` | fast HP drop, low HP, player on screen, PM open | done |

## Rules baked into the code

- `targeting.py` never targets players. `players_on_screen > 0` flees. More monsters than `max_monsters_to_engage` flees.
- `alarms.py` priority: `fast_hp_drop`, `low_hp`, `player_detected`, `pm_open`. The PM check is injected and never fires without one.
- `cavebot.py` sets `alarm = "stuck"` after 5 s without movement and `"lost"` on an unexpected floor.
  Route format: [../../data/waypoints/README.md](../../data/waypoints/README.md).
- `refill.py` raises `deposit_failed` after 5 s without a cap drop.

## How to test

```bash
python -m pytest tests/unit -q -k "healing or targeting or buffs or cavebot or looting or refill or alarms or attack_spells"
```

Tests use `FakeInput` and a synthetic `WorldState`. Other suites: [../../tests/README.md](../../tests/README.md).

## Do not

- No anti-cheat evasion. Decisions are game logic only (heal, attack, loot, walk, alarms).
