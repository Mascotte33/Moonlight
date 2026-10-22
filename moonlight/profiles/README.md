# moonlight/profiles/

Calibration profiles. Each `<name>.json` stores screen regions keyed to a stable UI landmark, not absolute
coordinates, so a profile survives moving the client window.
Written by [../setup/calibrate.py](../setup/calibrate.py). Loaded by `../setup/profile.py` (`ProfileManager`).

| File | What |
|------|------|
| `moonlight_user.json` | The default profile (`hp_bar`, `mp_bar`, `battle_list`, `minimap`, `floor_indicator`) |
| `<name>.png` | Screenshot the profile was calibrated on, shown as the Calibration tab's summary. Gitignored |

## JSON schema

```json
{
  "landmark": [x, y],
  "window_size": [w, h],
  "ui_scale": 1.0,
  "regions": { "hp_bar": [x, y, w, h], "mp_bar": [x, y, w, h] }
}
```

- `landmark`: absolute `[x, y]` of the stable UI element. Found again at runtime by template match.
- `regions.*`: `[x, y, w, h]` relative to the landmark.
- `window_size` (or `null`) and `ui_scale` (positive number, default 1.0) are optional.
- Region keys known to the calibrator (`calibrate.REGIONS`): `hp_bar`, `mp_bar`, `battle_list`, `minimap`,
  `floor_indicator`, `status_icons`, `game_grid`. A profile may hold only some of them.
- Looting also needs a `loot_window` region (see `../bot/wiring.py`).

## Do not

- Do not commit machine-specific paths or real serial ports. The serial port belongs in `moonlight_config.json`.
