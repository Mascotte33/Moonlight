# Phase 3 corrections

Owner feedback on PR #5 (Phase 3: Tabs). This file lists only the items that no other phase or task in `GUI_REMAKE.MD` / `docs/prs/gui-calibration.md` already plans.

Left out because they are already planned:
- The Part B checks (healing, cavebot running, recorder auto-add and hotkeys in the game). They wait for calibration (`docs/prs/gui-calibration.md`).
- Refill on potion counts. It is under "Not planned yet" in `GUI_REMAKE.MD` and waits for a potion reader.

## Bugs

- **Settings:** Healing and Targeting were saved as off, but they are on again after a restart.
- **Settings:** Buffs, Refill, Looting and Cavebot can't be turned on.
  - Cavebot needs a route, and `data/waypoints/` has none.
  - Looting, Refill and Buffs are refused by the bot's own check, because their readers (monster names, capacity, buff icons) aren't built yet.
  - The GUI must say why, next to the switch, in plain words.
- **Cavebot:** no route to choose from. Same cause as above: `data/waypoints/` is empty.
- **Cavebot:** "Add current position" does nothing when clicked. It needs a picked route and a known position. With neither, it must still show a message.
- **Cavebot:** the "Not running" waypoint line could not be found.

## Changes

### Settings
- Explain what the **Modules** section is for, in the tab itself. Each switch turns one bot module on or off. An off module keeps its values.
- **Tick rate:** add a box to type the number. The slider moves in whole numbers only.

### Dashboard
- Make **Current task** easier to see (bigger, near the top).

### Cavebot (and Recorder)
- Move the whole **Recorder** into Cavebot, as one route editor.
- Add a way to save waypoints from Cavebot.
- Add more waypoint types: node, stand, rope, walk, ladder, lure, and other actions. The bot today only knows walk, rope, shovel, ladder, label and goto. Node, stand and lure need new bot code first.

### Loot
- **Max items per corpse** (`looting.max_items_per_loot`) is not used by the bot. Either make the bot use it, or remove it from the tab.

### Refill
- Explain in the tab how refill works:
  - When the used capacity reaches the trigger, the bot walks the refill route to the `storage` label.
  - It deposits until capacity is at the stop value.
  - Then it walks back to the `farming` label.

### Healing
- Add a box to type each value. Sliders move in whole numbers only.
- Add potion options (HP and MP potion + hotkey + HP%/MP%). The bot has no potion healing today, so this needs bot code first.
- Group each heal: the HP% and, just under it, its spell or hotkey (Emergency, Normal, Cure).

### Targeting
- Let the user select creatures in the list, then press **Remove** on the selected ones.
- Move the **Attack spells** section into the Targeting tab.
- Set a different attack spell for each creature, plus a **Use for all monsters** option. Needs bot code first.

## Second review

### Cavebot
- Replace the action dropdown and **Add waypoint** with one button per waypoint type (walk, rope, shovel, ladder, label, goto). A button adds that waypoint at the typed X, Y, Z, or at the bot's position when they are blank.

### Healing
- **Cure** had one spell for every condition. In Tibia each condition has its own cure. Give each condition (paralyze, poison, burning, electrified, cursed, bleeding) its own spell and hotkey. A blank spell means that condition is not cured.
