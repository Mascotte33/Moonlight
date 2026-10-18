# Tibia Client UI Layout — Calibration Reference

**Purpose:** Pixel positions and properties for a screen-capture automation bot (OTS servers that allow bots). Used to calibrate screen regions and train OpenCV perception modules.
**Hardware context:** RTX 4090, 24GB VRAM.
**Method:** Values below are extracted from three production pixel-based bots (tibia_12_bot, tibiaeye, PyTibia) and cross-checked against official TibiaWiki. Values are **measured from code, not eyeballed**.

> ⚠️ **CRITICAL CALIBRATION NOTE:** Absolute pixel coordinates **cannot be hard-coded** for a general bot because:
> 1. The game window can be moved/resized by the user.
> 2. Different client versions (C++ 1.x classic vs. 1.2+ Flash-style) have different layouts.
> 3. The 1.2+ client has multiple status-bar styles (Default/Compact/Parallel/Large).
>
> **All mature bots use template-matching or landmark-relative detection**, not fixed coordinates. This document provides (a) the landmark/anchor strategy and (b) the **relative dimensions, colors, and ratios** that ARE stable. Capture real screenshots of YOUR target OTS server's client version to fill in the final absolute offsets.

---

## 0. Client Version & Layout Model

Two UI paradigms exist. **Identify which your OTS server uses first.**

### C++ Client (classic 1.x)
- Right sidebar holds: **Status Bar** (HP red, MP blue) → **Action Bar** → **Inventory** → **Minimap** → **VIP** → **Battle List**.
- **Chat/Console** runs across the bottom.
- HP bar = **RED**.

### 1.2+ / Flash-Style Client (modern, most common for current OTS)
- Status bar is a **floating widget** (draggable) showing HP (green by default) / MP (blue) / skill progress / conditions.
- Multiple styles:
  - **Default** — HP & MP stacked on one row, progress middle, conditions bottom.
  - **Compact** — HP, conditions, MP across the top row; progress below.
  - **Parallel** — four rows (HP, MP, progress, conditions).
  - **Large** — like Compact but HP/MP bars thicker (20px → 35px).
- Chat window has **tabs** (Chat / Loot / Trade / Party / Guild / PM).

> 📌 **Recommendation:** Target the **1.2+ Flash-style client** (what tibia_12_bot, tibiaeye, and PyTibia all read). Capture a clean screenshot of the default layout from your specific server, then use the relative measurements below to calibrate.

---

## 1. HP Bar

**Role:** Shows character health. Bot uses it to detect low HP and trigger healing.

### Measured properties (from code)

| Property | Value | Source |
|---|---|---|
| Bar width (1.2+ widget) | ~94–100 px | PyTibia (94), tibiaflask (100) |
| Bar height (widget) | ~23 px (tibiaflask) | tibiaflask |
| Scan row (HP) | y = 5 (tibiaflask) / y = 6 (tibia_12_bot) | both |
| HP fill color (1.2+ widget) | **RGB (241, 97, 97)** | tibiaflask `HealthPixelColor` |
| HP empty/background color | **RGB (95, 95, 95)** gray | tibia_12_bot `getHealth()` |
| Color tolerance | ≤ 15 (Euclidean) | both |

### Percentage algorithm (proven in production)
```
HP% = (filled_pixels / total_bar_width) * 100
```
- **tibia_12_bot:** count pixels matching empty-gray (95,95,95) → `hp% = 100 * (width - empty_count) / width`.
- **PyTibia:** count fill pixels → `hp% = filled / 94 * 100`.
- **tibiaflask:** count pixels matching (241,97,97) → ratio to width.

All three scan a **single horizontal row** through the bar (not the whole region) for speed.

### Creature HP bars (in-game world, above monsters/players)
These are **different** from the status bar — they're the small bars floating over every creature.

| Property | Value | Source |
|---|---|---|
| Bar size | **31 × 4 px** (w × h) | tibia_12_bot `detect_monsters.py` |
| Border | black frame around bar | tibia_12_bot |
| HP colors (RGB) | green (0,192,0), (96,192,96), yellow (192,192,0), red (192,0,0), (192,48,48), dark-red (96,0,0), gray (192,192,192) | tibia_12_bot `HP_COLORS` |
| Low-HP colors (RGB) | (192,192,0), (192,0,0), (192,48,48), (96,0,0) | tibia_12_bot `LOW_HP_COLORS` |
| Creature feet offset (from bar) | +32 px (monster), +46 px (player) | tibia_12_bot |

**How HP% is displayed:** as a **filled portion** of a fixed-width bar. Full bar = 100%, partially empty = reduced. Color shifts green→yellow→red as HP drops.

> ✅ **Verify with screenshot:** confirm exact HP-bar red shade and width on your client. The (241,97,97) is from one specific build; your OTS client may vary slightly.

---

## 2. MP Bar

**Role:** Shows character mana. Bot uses it to detect low MP.

### Measured properties

| Property | Value | Source |
|---|---|---|
| Bar width | ~94–100 px (same as HP) | PyTibia, tibiaflask |
| Bar height | ~23 px (widget) | tibiaflask |
| Scan row (MP) | y = 17 (tibiaflask) / y = 6 (tibia_12_bot) | both |
| MP fill color | **RGB (83, 80, 218)** blue | tibiaflask `ManaPixelColor` |
| MP empty/background | RGB (95, 95, 95) gray (same as HP) | tibia_12_bot |
| Color tolerance | ≤ 15 | both |

### Position relative to HP bar
In the 1.2+ **Default/Compact** style, MP sits **directly below HP** (or adjacent in Compact). In **Parallel** style, MP is on its own row. **Use the HP bar as the anchor**, then offset down.

### Percentage algorithm
Same as HP: `MP% = (filled_pixels / total_bar_width) * 100`.

> ✅ **Verify with screenshot:** confirm the exact blue shade and the vertical offset between HP and MP bars on your client.

---

## 3. Battle List (Combat List)

**Role:** Lists nearby creatures. Bot reads it to identify the **selected target** (highlighted creature) and monster names.

### Measured properties

| Property | Value | Source |
|---|---|---|
| Slot height | **22 px** | tibiaeye, PyTibia |
| Content width | **156 px** | tibiaeye `CONTENT_WIDTH` |
| Top padding (slot start Y) | **11 px** | tibiaeye `SLOT_START_Y` |
| Name start X | **23 px** | tibiaeye `NAME_START_X` |
| Name width | **115 px** | tibiaeye `NAME_WIDTH` |
| Max slots (rows) | 20 (156×22 content / 22 = ~20 rows) | derived |
| Text pixel (grayscale) | 192, 247 (bright text) | tibiaeye |
| Text threshold | > 150 = text pixel | tibiaeye |
| Skull icon region | x: -38 to -2, y: +2 to +13 within a slot | PyTibia |

### How monsters are listed
- One creature per row (22px tall).
- Format: `[skull icon] Creature Name`.
- Text is bright pixels on a dark background → threshold > 150 to find text.
- **Skull icons** (if present) appear to the left of the name (x -38 to -2).

### Selected target indicator
- The creature **currently being attacked** is **highlighted with a colored border/box**.
- **PyTibia / tibiaeye:** detect the highlight by checking **corner pixels** (0,0), (19,0), (0,19), (19,19) of a 20px slot box for the attack-highlight color (**76/166** grayscale values).
- The highlighted row = your target.

> ✅ **Verify with screenshot:** the highlight color/box is the key detection. Capture a screenshot with a creature selected to get the exact highlight color on your client.

---

## 4. Minimap (Radar)

**Role:** Top-down view of terrain around the player. Bot uses it for **position tracking** and floor-level detection.

### Measured properties

| Property | Value | Source |
|---|---|---|
| Minimap size | **106 × 109 px** | PyTibia, tibiaeye |
| Half-width | **53 px** | both |
| Half-height | **54 px** | both |
| Player position | **CENTER of minimap** (53, 54) | both |
| Floor levels | 0–15 (16 levels) | PyTibia `floors` |
| Ground floor | level **7** | PyTibia `FLOOR_GROUND` |

### How position is displayed
- The **player is always at the center** of the minimap.
- Surrounding terrain scrolls around the player.
- **Floor level** is shown via a **template-matched indicator** (PyTibia uses `floorLevels/0-15.png` templates).

### Terrain colors (grayscale values)
| Terrain | Gray |
|---|---|
| accessPoint | 226 |
| caveFloor | 111 |
| caveWall | 76 |
| commonFloorOrStreet | 1 |
| grassOrRockyGround | 120 |
| ice | 240 |
| lava | 136 |
| mountainOrStone | 102 |
| sand | 213 |
| snow | 255 |
| swamp | 207 |
| treesOrBushes | 60 |
| wall | 106 |
| water | 93 |
| vacuumOrUndiscovered | 0 |

### Position tracking technique (closed-loop mouse control)
1. Capture the 106×109 minimap region.
2. Convert to grayscale.
3. Compare terrain pattern frame-to-frame.
4. Displacement of the terrain pattern = player's movement → used to compute direction/position for mouse-control feedback.
5. Use coordinate offsets: `COORDINATE_OFFSET_X = 31744`, `COORDINATE_OFFSET_Y = 30976` (Tibia world coordinates).

> ✅ **Verify with screenshot:** capture the minimap at a known location to confirm the 106×109 size and center-player assumption on your client.

---

## 5. Floor Indicator

**Role:** Shows the current z-level (floor).

### Measured properties
- **Location:** embedded in the minimap area (PyTibia) OR a separate small indicator.
- **Format:** 16 possible floor levels, indexed 0–15.
- **Convention:**
  - `0` = highest (sky/roof)
  - `7` = **ground level** (street level)
  - `15` = deepest underground
- **Display method:** a small **template image** (`floorLevels/<level>.png`) is matched against the minimap corner/indicator region. The matching template = current floor.

### Detection algorithm (PyTibia)
1. Extract the floor-indicator sub-region from the minimap.
2. Match against 16 reference templates.
3. Highest-confidence match = current floor index.

> ✅ **Verify with screenshot:** capture the minimap on different floors (ground vs. underground) to collect the 16 reference templates for your client.

---

## 6. Status Icons (Buffs / Conditions)

**Role:** Shows active buffs, debuffs, and special conditions (haste, invisibility, fire shield, etc.).

### Measured properties
- **Location:** within the status bar widget (1.2+ client) — in **Default** style, conditions are on the **bottom row**; in **Compact**, they're on the **top row between HP and MP**.
- **Detection method:** **template matching** against known condition icons (PyTibia has a `skills` module with locators).
- **Size:** each icon is a small fixed-size square (~20×20 px, to confirm).

### Available conditions (common in Tibia)
Haste, invisibility, fire shield, ice shield, energy shield, holy shield, protection, focus, paralyze, poison, burn, drain, etc.

> ✅ **Verify with screenshot:** capture each condition icon on your client and build a template library. This is the most version-sensitive element — icon sets change between client versions.

---

## 7. Containers / Loot Window

**Role:** Shows item slots when a container (backpack, chest, inventory) is open.

### Measured properties
- **Type:** separate **draggable window** (not fixed).
- **Detection:** **template match** on the container window's title bar or frame (tibiaeye uses chat/container tab templates: `loot_selected`, `loot_unselected`, `loot_new_message`).
- **Item grid:** items displayed in a grid of **equal-size slots** (~30–45 px each, to confirm).
- **Slot detection:** each slot is a bordered cell; detect by grid lines or slot-border template matching.

### How items are displayed
- Grid layout (rows × columns depending on container type).
- Each item = sprite in a cell.
- Item count/name readable via the cell sprite.

### Loot tab (chat window)
- The chat window's **Loot tab** shows loot messages (e.g., "You found X gold coins").
- Detect the tab via template match on the tab icon.

> ✅ **Verify with screenshot:** open a backpack and a chest, capture the window, and measure the slot grid dimensions and window frame color.

---

## 8. Player Detection (Detecting another player on screen)

**Role:** Identify when another player is on screen (for safety, party detection, etc.).

### Detection techniques (from code)

**1. Height difference (tibia_12_bot):**
- Players are **taller** than monsters in the game window.
- Feet offset from HP bar: **player +46 px**, **monster +32 px**.
- Use this to classify a creature sprite as player vs. monster.

**2. HP bar color (tibia_12_bot):**
- Players/monsters both have HP bars, but combined with height and name you can classify.

**3. Combat list name (PyTibia/tibiaeye):**
- Read the creature name from the battle list.
- **Unknown names** (not in the monster list) are likely players.

**4. Game window grid (PyTibia/tibiaeye):**
- The game window is a **15 × 11 grid**.
- Player is at slot **(7, 5)**.
- Other entities occupy other grid slots.

**5. Name text above sprite:**
- Creature name appears **above** the HP bar (NAME_HEIGHT = 13 px, offset x -50 to +90).
- Read the name text to identify players.

> ✅ **Verify with screenshot:** capture a scene with a player vs. a monster to confirm the height offset and name-placement on your client.

---

## 9. PM Window (Private Messages)

**Role:** The chat console where private messages appear. Bot must detect if a PM is open/active.

### Measured properties
- **Location:** **chat console at the bottom of the screen** (all client versions).
- **Structure (1.2+):** the chat window has a **tab bar** (top-right) with tabs: Chat, Loot, Trade, Party, Guild, and a **"file card"** button that switches chat channels.
- **PM channel:** one of the selectable channels.

### How to detect PM is open/active
1. **Template match** the chat tab bar / "file card" icon.
2. **Match the currently-selected channel** icon against known PM-channel icons.
3. **Watch for incoming message highlight** (new-message indicator, e.g., `loot_new_message` template).

### Detection algorithm (tibiaeye)
```
1. Locate chat window via tab templates (loot_selected/loot_unselected).
2. Find the active tab.
3. If active tab = PM channel → PM is open.
4. Check new-message indicator for unread PMs.
```

> ✅ **Verify with screenshot:** capture the chat console with the tab bar visible, and open a PM to confirm the PM-tab icon and highlight color on your client.

---

## 10. Game Window (Core Play Area) — Calibration Anchor

This is the **master reference region** that most other elements are positioned relative to.

### Measured properties

| Property | Value | Source |
|---|---|---|
| Grid | **15 (wide) × 11 (tall)** slots | PyTibia, tibiaeye, tibia_12_bot |
| Player slot | **(7, 5)** (center) | PyTibia, tibiaeye |
| Game window size @720p | **480 × 352 px**, slot = 32 px | tibiaeye |
| Game window size @1080p | **960 × 704 px**, slot = 64 px | tibiaeye |
| Tile size (dynamic) | width/15 × height/11 | tibia_12_bot |
| Creature bar size @720p | 27 × 4 px | PyTibia/tibiaeye |
| Creature bar size @1080p | 54 × 8 px (2×) | PyTibia/tibiaeye |
| Creature bar size @1.2 (tibia_12_bot) | 31 × 4 px | tibia_12_bot |
| Bar black threshold | 30 (dark pixel = border) | PyTibia |
| Min vertical spacing between bars | 16 px | PyTibia |

### Calibration strategy (recommended for your bot)
1. **Locate the game window** via template match on a stable landmark (e.g., the top-left corner, the minimap, or the "stop" button).
2. **Derive all other regions** as offsets from the game window origin.
3. **Compute tile size dynamically:** `tile_w = window_width / 15`, `tile_h = window_height / 11`.
4. **Locate HP/MP bars** relative to the status-bar anchor (e.g., the "stop" button).
5. **Locate battle list** relative to the game window.

> 📌 **Why not hard-code?** The game window moves when the user drags it. The **ratios and relative offsets are stable**; absolute positions are not.

---

## 11. Master Landmark Map (Relative Offsets)

From **tibia_12_bot**, all regions are defined as **relative offsets from the "stop" button** (a stable, always-present landmark in the game window). Format: `(left, top, right, bottom)`.

| Element | Offset from stop button | Notes |
|---|---|---|
| **Map** | (-118, -259, -52, -161) | minimap region |
| **Health** (HP) | (-103, +18, -53, +14) | HP bar region |
| **Mana** (MP) | (-103, +31, -53, +27) | MP bar region (below HP) |
| **Buffs** | (-118, 0, -53, -1) | status icons |
| **Bless** | (-104, -144, -135, -147) | blessing icons |
| **Capacity** | (-45, -13, -52, -15) | capacity indicator |
| **WindowButtons** | (-118, 71, -52, 164) | window button region |

**Usage:**
1. Find the "stop" button via template match → get its region.
2. Apply each offset to compute the HP bar, MP bar, minimap, etc.
3. This gives you **all regions from one landmark**.

> ✅ **This is the recommended calibration approach.** Capture a screenshot, locate the stop button, and measure the actual offsets for your client.

---

## 12. Verification Checklist (Screenshots Needed)

The following **require actual screenshots of YOUR target OTS server's client** to finalize:

- [ ] **Absolute pixel coordinates** for all regions (after locating the game window).
- [ ] **Exact HP bar red shade** (code says RGB 241,97,97 — confirm on your client).
- [ ] **Exact MP bar blue shade** (code says RGB 83,80,218 — confirm).
- [ ] **Selected-target highlight color** in the battle list (code says grayscale 76/166 — confirm).
- [ ] **Minimap size** (code says 106×109 — confirm).
- [ ] **Floor-level indicator templates** (16 templates for floors 0–15).
- [ ] **Condition/buff icon set** (icons change between client versions).
- [ ] **Container window slot grid** dimensions.
- [ ] **Chat tab bar + PM tab icon** positions.
- [ ] **Client version confirmation** (C++ classic vs. 1.2+ Flash-style).

### Screenshot capture protocol
1. Set the client to the **default layout** (don't drag windows).
2. Use a **known resolution** (e.g., 1920×1080 or the client's native).
3. Capture:
   - Clean default view (no containers, no PM).
   - With a creature selected (battle list highlight).
   - With a backpack open (container window).
   - With a PM open (chat tab bar).
   - On different floors (ground + underground, for floor templates).
4. Run the template-match calibration script on these captures to extract the final absolute offsets.

---

## 13. Source Repositories (Proven Production Bots)

| Repo | What it provides | Key files |
|---|---|---|
| `mateogon/tibia_12_bot` | Tibia 1.2 pixel bot (HP/MP/monster detection) | `src/config/constants.py` (offsets), `src/core/bot_runtime.py` (HP/MP/monster logic), `src/vision/detect_monsters.py`, `src/vision/screen_elements.py` |
| `GGotha/tibiaeye` | Modern pixel bot (game window, battle list, radar) | `src/repositories/gamewindow/config.py`, `src/repositories/battlelist/config.py`, `src/repositories/radar/config.py`, `src/repositories/radar/extractors.py` |
| `lucasmonstrox/PyTibia` | PyTibia (status bar, battle list, radar, floor detection) | `src/repositories/statusBar/{config,core}.py`, `src/repositories/battleList/{config,core}.py`, `src/repositories/radar/{config,core}.py`, `src/repositories/gameWindow/config.py` |
| `DennysOliveira/tibiaflask` | 1.2+ client HP/MP pixel reader | `main.py` (HealthPixelColor, ManaPixelColor, bar geometry) |

**Wiki references:**
- TibiaWiki: "Status Bar", "Action Bar", "Combat List", "Automap (Minimap)", "Console/Chat", "Game Window".

---

## 14. Quick-Reference Color Table (for OpenCV masks)

| Element | Color (RGB) | Usage |
|---|---|---|
| HP fill (1.2+ widget) | (241, 97, 97) | HP bar detection |
| MP fill (1.2+ widget) | (83, 80, 218) | MP bar detection |
| Bar empty/background | (95, 95, 95) | HP/MP empty region |
| Creature HP green | (0, 192, 0) | Full HP bar in-world |
| Creature HP yellow | (192, 192, 0) | Medium HP in-world |
| Creature HP red | (192, 0, 0) | Low HP in-world |
| Creature HP gray | (192, 192, 192) | Empty/dead bar in-world |
| Battle list text (gray) | 192, 247 | Text detection (threshold >150) |
| Target highlight (gray) | 76, 166 | Selected target detection |
| Minimap player center | — | (53, 54) in 106×109 minimap |

> **Note:** OpenCV uses **BGR** by default. Convert these RGB values when building masks (or use `cv2.cvtColor(..., cv2.COLOR_BGR2RGB)`).

---

*End of UI Layout Calibration Reference.*
