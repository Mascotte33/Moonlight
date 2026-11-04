"""Write the bot's hotkeys into the game client as a "Moonlight" hotkey set.

# doc: docs/prs/client-hotkeys.md#moonlight-set

The client keeps its hotkeys in ``conf/clientoptions.json``. The Moonlight
set starts as a copy of the set in use (or the Moonlight set from last
time), so every other key stays. Each key the bot presses is then bound to
its job: a client action (logout, loot, chase), or an action bar button
holding the spell words or item the owner typed for it.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from moonlight.core.config import BotConfig

__all__ = [
    "ITEMS",
    "OFFICIAL_CLIENT_FILE",
    "SET_NAME",     "BotKey",
    "bot_keys",
    "client_combo",
    "write_moonlight_set",
]

SET_NAME = "Moonlight"
#: The official client's hotkey file; other servers' clients keep theirs in their own folder.
OFFICIAL_CLIENT_FILE = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Tibia/packages/Tibia/conf/clientoptions.json"
)

#: Item names the text box understands; any other item goes in as its id number.
ITEMS = {
    "health potion": 266,
    "mana potion": 268,
    "strong health potion": 236,
    "strong mana potion": 237,
    "great health potion": 239,
    "great mana potion": 238,
    "great spirit potion": 7642,
    "ultimate health potion": 7643,
    "ultimate mana potion": 23373,
    "ultimate spirit potion": 23374,
    "supreme health potion": 23375,
    "sudden death rune": 3155,
    "great fireball rune": 3191,
    "avalanche rune": 3161,
    "thunderstorm rune": 3202,
    "stone shower rune": 3175,
    "ultimate healing rune": 3160,
    "rope": 3003,
    "shovel": 3457,
}

_ACTION_BAR = 1
_BUTTONS = range(1, 51)
_CLIENT_KEYS = {"enter": "Return", "backspace": "Backspace", "pgup": "PgUp", "pgdn": "PgDown"}


@dataclass(frozen=True)
class BotKey:
    """One key the bot presses and what the client must do on it."""

    combo: str
    """The bot's combo, e.g. ``ctrl+F1``."""
    purpose: str
    """What the bot presses it for, shown next to the text box."""
    item_use: str = "UseOnYourself"
    """How an item typed for this key is used (the client's ``useType``)."""
    client_action: str = ""
    """A client action bound straight to the key; no text box then."""
    suggested: str = ""
    """What the text box holds before the owner types anything."""


def _module(cfg: BotConfig, name: str) -> Any:
    """A module's config, on or off; None when the config has none."""
    return getattr(cfg, name) or cfg.off.get(name)


def bot_keys(cfg: BotConfig) -> list[BotKey]:
    """Every key *cfg* makes the bot press, once each (the first use wins)."""
    keys: list[BotKey] = []
    healing = _module(cfg, "healing")
    if healing is not None:
        for number, rule in enumerate(healing.rules, start=1):
            keys.append(BotKey(rule.hotkey, f"Healing rule {number} ({rule.kind})"))
        keys += [BotKey(c.hotkey, f"Cure {c.condition}") for c in healing.cures if c.enabled]
    targeting = _module(cfg, "targeting")
    if targeting is not None:
        keys += [
            BotKey(row.hotkey, f"Attack {row.name}", "UseOnTarget")
            for row in targeting.monsters
            if row.hotkey
        ]
        keys += [
            BotKey(rule.hotkey, f"Area spell ({rule.minimum_adjacent_monsters}+)", "UseOnTarget")
            for rule in targeting.area_rules
        ]
        if targeting.toggle_chase_hotkey:
            chase = targeting.toggle_chase_hotkey
            keys.append(BotKey(chase, "Toggle chase", client_action="ToggleChaseOpponents"))
    cavebot = _module(cfg, "cavebot")
    if cavebot is not None:
        keys.append(BotKey(cavebot.rope_hotkey, "Rope", "SelectUseTarget", suggested="rope"))
        keys.append(BotKey(cavebot.shovel_hotkey, "Shovel", "SelectUseTarget", suggested="shovel"))
    looting = _module(cfg, "looting")
    if looting is not None:
        keys.append(BotKey(looting.loot_hotkey, "Loot", client_action="QuickLootAreaAtPlayer"))
    rotation = _module(cfg, "rotation")
    if rotation is not None:
        keys.append(BotKey(rotation.logout_hotkey, "Logout", client_action="Logout"))
    extras = cfg.extras
    for name, purpose, item_use in (
        ("food", "Food", "Use"),
        ("haste", "Haste", "UseOnYourself"),
        ("buff", "Buff potion", "UseOnYourself"),
        ("spell_buff", "Spell buff", "UseOnYourself"),
        ("ring", "Ring", "Equip"),
        ("amulet", "Amulet", "Equip"),
    ):
        if getattr(extras, name):
            keys.append(BotKey(getattr(extras, f"{name}_hotkey"), purpose, item_use))
    seen: set[str] = set()
    unique = []
    for key in keys:
        if key.combo.lower() not in seen:
            seen.add(key.combo.lower())
            unique.append(key)
    return unique


def client_combo(combo: str) -> str:
    """The client's name for a bot combo: ``ctrl+F1`` -> ``Ctrl+F1``, ``alt+q`` -> ``Alt+Q``."""
    *modifiers, key = combo.split("+")
    if len(key) == 1:
        key = key.upper()
    elif not (key[:1] == "F" and key[1:].isdigit()):
        key = _CLIENT_KEYS.get(key, key.capitalize())
    return "+".join([m.capitalize() for m in modifiers] + [key])


def _content(key: BotKey, text: str) -> dict[str, Any] | None:
    """What goes on *key*'s action bar button for *text*; None when blank."""
    text = text.strip()
    if not text:
        return None
    item = int(text) if text.isdigit() else ITEMS.get(text.lower())
    if item is not None:
        return {"useObject": item, "useType": key.item_use}
    return {"chatText": text, "sendAutomatically": True, "spellCastType": 0}


def _works_while_chatting(sequence: str) -> bool:
    """Letters typed with chat on are text, so only F keys and Ctrl/Alt combos go there."""
    *modifiers, key = sequence.split("+")
    return bool({"Ctrl", "Alt"} & set(modifiers)) or (key[:1] == "F" and key[1:].isdigit())


def _bind(hotkey_set: dict[str, Any], sequence: str, setting: dict[str, Any]) -> None:
    """Bind *sequence* to *setting*, dropping what it did before."""
    for mode in ("chatOff", "chatOn"):
        mappings = [m for m in hotkey_set.setdefault(mode, []) if m["keysequence"] != sequence]
        if mode == "chatOff" or _works_while_chatting(sequence):
            mappings.append({"actionsetting": setting, "keysequence": sequence})
        hotkey_set[mode] = mappings


def _button(hotkey_set: dict[str, Any], sequence: str) -> tuple[int, int]:
    """The action bar button *sequence* triggers now, else the first free one on bar 1.

    Raises:
        ValueError: When bar 1 has no free button left.
    """
    triggered = set()
    for mapping in hotkey_set.get("chatOff", []):
        action = mapping["actionsetting"].get("action", "")
        if action.startswith("TriggerActionButton_"):
            bar, button = (int(n) for n in action.removeprefix("TriggerActionButton_").split("."))
            if mapping["keysequence"] == sequence:
                return bar, button
            triggered.add((bar, button))
    bars = hotkey_set.setdefault("actionBarOptions", {}).setdefault("mappings", [])
    used = triggered | {(m["actionBar"], m["actionButton"]) for m in bars}
    for button in _BUTTONS:
        if (_ACTION_BAR, button) not in used:
            return _ACTION_BAR, button
    raise ValueError(f"action bar {_ACTION_BAR} has no free button for {sequence}")


def write_moonlight_set(path: Path, keys: list[BotKey], texts: dict[str, str]) -> list[str]:
    """Write the Moonlight set into the client file *path* and make it the one in use.

    *texts* maps a bot combo to the spell words or item typed for it. The
    old file is kept next to it as ``clientoptions.json.bak``.

    Returns:
        One line per key: what it was bound to, or why it was skipped.

    Raises:
        ValueError: When *path* is not a client hotkey file, or bar 1 is full.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    options = data.get("hotkeyOptions")
    if not isinstance(options, dict) or not options.get("hotkeySets"):
        raise ValueError(f"{path} has no client hotkey sets")
    sets = options["hotkeySets"]
    hotkey_set = copy.deepcopy(sets.get(SET_NAME) or sets[options["currentHotkeySetName"]])
    report = []
    for key in keys:
        sequence = client_combo(key.combo)
        if key.client_action:
            _bind(hotkey_set, sequence, {"action": key.client_action})
            report.append(f"{sequence}: {key.purpose}")
            continue
        content = _content(key, texts.get(key.combo, key.suggested))
        if content is None:
            report.append(f"{sequence}: skipped, nothing typed for {key.purpose}")
            continue
        bar, button = _button(hotkey_set, sequence)
        bars = hotkey_set["actionBarOptions"]["mappings"]
        bars[:] = [m for m in bars if (m["actionBar"], m["actionButton"]) != (bar, button)]
        bars.append({"actionBar": bar, "actionButton": button, "actionsetting": content})
        _bind(hotkey_set, sequence, {"action": f"TriggerActionButton_{bar}.{button}"})
        report.append(f"{sequence}: {key.purpose} on bar {bar} button {button}")
    sets[SET_NAME] = hotkey_set
    options["currentHotkeySetName"] = SET_NAME
    # The client would switch to the character's own set at login.
    options["autoSwitchHotkeyPreset"] = False
    shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return report
