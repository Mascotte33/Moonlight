# moonlight/decision/ - priority state machine

Decision modules implement: HEAL > ALARM > ATTACK > LOOT > WALK.
screen_ok = false or kill switch -> stop everything (HEAL continues only in
ALARM mode). Rate limit 10 actions/s; never emit input unless client_focused.

| Module | Task | Responsibility | Status |
|--------|------|----------------|--------|
| healing.py | T14 | Emergency/normal heal, mana, cures, cooldowns | ✅ done |
| targeting.py | T20 | Target selection: attack/switch/flee, player safety | ✅ done |
| buffs.py | T26 | Buff recast tracking (missing / expiring soon) | ✅ done |
| cavebot.py | T22 | Waypoint execution (walk/rope/shovel/ladder/label/goto), stuck detection | ⬜ stub |
| looting.py | T23 | Open corpse, move items by templates, cap check | ⬜ stub |
| alarms.py | T25 | Player on screen / PM / fast HP drop -> alarm mode + sound | ✅ done |

targeting.py emits `TargetingAction` intents (attack/switch/flee) read from
WorldState; it never sends input itself. Safety rules are hard-coded in
decide(): players_on_screen > 0 -> flee; monsters > max_monsters_to_engage
-> flee; players are never targeted.

Tests: unit (tests/unit/). Replay (tests/replay/, T28) and HIL
(tests/hil/, T18) suites are planned — not yet created.
