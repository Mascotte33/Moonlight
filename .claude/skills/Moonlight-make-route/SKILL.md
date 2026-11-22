---
name: Moonlight-make-route
description: Build a new cavebot route from data/waypoints/route_steps.txt with the repo's route tools, then open the bot on it.
argument-hint: <HuntName> <minLevel>-<maxLevel>
disable-model-invocation: true
---

Make the route `"<HuntName> <min>-<max>"` (the name carries the level range that
`cavebot_rotate` reads). The order of its parts is `data/waypoints/route_steps.txt`:
0 point 0, 1 hunt, 2 check, 3 to town, 4 refill, 5 back, 6 before use.

## 1. Locate the hunt

The owner names the hunt loosely ("venore amazon camp"). Find it yourself:
`python tools/map.py monsters <Monster>`, `data/spawns.json`, `python tools/map.py find depot --near x,y,z`,
`python tools/map.py stairs x,y,z`, `python tools/map.py area x,y,z` (PNG in `logs/`), and the saved routes in
`data/waypoints/` (same city or hunt = reuse their spots and actions). Done when you hold a
spawn area, the nearest depot, bank and shop sqm, and every floor change between them. A
name that matches two places or none goes into the grill as a question.

## 2. Grill the owner

Use `mattpocock-skills:grilling`, in this session. Every question carries your recommended
answer, built from step 1. A question about a place shows a picture instead of
coordinates: `python tools/map.py area x,y,z --size 40 --scale 12 --out logs/grill/<place>.png` (the
× is the spot), sent with `SendUserFile`; name the place in words ("the depot", "the camp's
north ladder") and keep the coordinates in your own notes. Cover:

- city and depot sqm; bank sqm; shop NPC and sqm
- hunt: the loop's walk/stand spots, its floor changes (stairs, ladder, rope, shovel)
- doors, levers, boats, NPC talk on the way (existing `scripts/*.py` such as `door_up` first)
- a needed script that does not exist yet: its steps one by one (what to press, say, wait for, check)
- buy list (`item, percent`)
- special spots: lure, avoid, pz, a place the bot got stuck before (`python tools/stuck_spots.py`)

Done when the owner confirms the whole plan.

## 3. Build

Hand the build to one Sonnet agent with the confirmed plan. It uses only the repo tools:

```
python tools/route.py path A B                      # walk lines between confirmed spots
python tools/route.py block refill CHECK --depot D --bank B --shop S
python tools/route.py new "<Name> <min>-<max>" < route.txt
```

`route.txt` is parts 1-5 of `route_steps.txt` in order: the refill keeps `label Depot`,
`finddepo`, `deposit`, `script account_rotate`, `script cavebot_rotate`, `label Depo_Break`,
`trade`. The agent returns the route's file and every `# guess` line.

Right after `new`, before point 0 is inserted (its walks from every city make the picture
map-sized), show the owner the route:

- the whole route: `python tools/map.py show "<Name> <min>-<max>" --floor Z`, one picture per
  floor it uses (`--out logs/grill/route_<Z>.png`), sent with `SendUserFile`;
- each `# guess` floor change: an `area` picture of its sqm and the question "stairs, ladder,
  rope, shovel or step on it?" with your guess as the recommended answer.

Fix what the owner says with `route.py insert|move|delete` and show the changed floor again.
Done when the owner says the loop and every floor change look right. Then insert point 0:

```
python tools/route.py block travel --depot D > travel.txt
python tools/route.py insert "<Name> <min>-<max>" 0 < travel.txt
```

New scripts go in `scripts/<name>.py` (`def run(bot): yield ...`, see `scripts/door_up.py`)
on branch `claude/route-<slug>`, with `docs/prs/route-<slug>.md` and a `# doc:` link (R9).
A script with logic (a branch, a loop, a check) gets a test in `tests/` (R8); a plain
key/say script gets none. Commit per RULESET R3/R4 and open a PR.

## 4. Check

`python tools/map.py check "<Name> <min>-<max>"` until it reports 0 bad legs; fix with
`route.py insert|move|delete`. Then `python tools/route.py buy "<Name> <min>-<max>" < buy.txt`.

## 5. Open

`python -m moonlight --route "<Name> <min>-<max>"` in the background: Map tab on the route,
bot Stopped.

## 6. Report

Route name, waypoint count, scripts created (PR link), check result, then a numbered test
checklist (R22): setup, what to do, what to expect, for each part 0-5 in the client. Add a
step for each new script (watch it run once, the log line it writes) and one for the first
`cavebot_rotate` (the log names the route it picked for the level it read).
