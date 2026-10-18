# data/waypoints/ — cavebot routes (T22)

One JSON file per route: `data/waypoints/<route_name>.json`, loaded with
`moonlight.decision.cavebot.RouteFileLoader.load(path)` (validates and
raises `RouteError` on any problem).

## Route

| Key | Type | Required | Meaning |
|-----|------|----------|---------|
| `name` | string | yes | Route name (non-empty) |
| `description` | string | no | Free text |
| `waypoints` | list | yes | Non-empty list of waypoints, executed in order |

## Waypoint

| Key | Type | Meaning |
|-----|------|---------|
| `x`, `y` | int ≥ 0 | Tibia tile coordinates (same space as the minimap position) |
| `z` | int 0..15 | Floor: 0 = sky, 7 = ground, 15 = deepest |
| `action` | string | One of the actions below (default `walk`) |
| `label` | string | Name, for `label` and `goto` only |

| Action | What the bot does once within 1 tile of `(x, y, z)` | Floor after |
|--------|------------------------------------------------------|-------------|
| `walk` | Nothing — waypoint reached | z |
| `rope` | Presses the rope hotkey, clicks the rope spot tile | z − 1 |
| `shovel` | Presses the shovel hotkey, clicks the hole, steps in | z + 1 |
| `ladder` | Right-clicks ("use") the ladder tile | z − 1 |
| `label` | Positionless jump target (`x/y/z` not needed) | — |
| `goto` | Positionless: continues after the label named `label` | — |

Walking is done by clicking the minimap toward the waypoint (1 px = 1 tile).
`rope`/`shovel`/`ladder` waypoints sit **on the tile to use** (the rope
spot, the hole, the ladder); the bot stands on or next to it. The action is
retried until the floor changes; no movement for 5 s stops the route with
alarm `stuck`. A route without a `goto` ends after its last waypoint.

## Example — `data/waypoints/rotworm_loop.json`

```json
{
  "name": "rotworm_loop",
  "description": "Ground floor to the rotworm cave and back, forever.",
  "waypoints": [
    {"action": "label", "label": "start"},
    {"x": 32369, "y": 32241, "z": 7, "action": "walk"},
    {"x": 32372, "y": 32244, "z": 7, "action": "shovel"},
    {"x": 32380, "y": 32250, "z": 8, "action": "walk"},
    {"x": 32390, "y": 32255, "z": 8, "action": "walk"},
    {"x": 32372, "y": 32244, "z": 8, "action": "rope"},
    {"action": "goto", "label": "start"}
  ]
}
```
