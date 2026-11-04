"""The Client hotkeys button: the Moonlight set in clientoptions.json."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from moonlight.core.client_hotkeys import (
    SET_NAME,
    BotKey,
    bot_keys,
    client_combo,
    write_moonlight_set,
)
from moonlight.core.config import BotConfig
from moonlight.looting.looting import LootingConfig


def _client_file(tmp_path: Path) -> Path:
    mine = {
        "actionBarOptions": {
            "mappings": [
                {"actionBar": 1, "actionButton": 1, "actionsetting": {"chatText": "exura"}},
                {"actionBar": 1, "actionButton": 2, "actionsetting": {"useObject": 1}},
            ]
        },
        "chatOff": [
            {"actionsetting": {"action": "TriggerActionButton_1.1"}, "keysequence": "F1"},
            {"actionsetting": {"action": "TriggerActionButton_1.2"}, "keysequence": "F2"},
            {"actionsetting": {"action": "GoNorth"}, "keysequence": "W"},
            {"actionsetting": {"action": "Logout"}, "keysequence": "Ctrl+Q"},
        ],
        "chatOn": [{"actionsetting": {"action": "TriggerActionButton_1.1"}, "keysequence": "F1"}],
    }
    data = {
        "options": {"keep": True},
        "hotkeyOptions": {
            "autoSwitchHotkeyPreset": True,
            "currentHotkeySetName": "Knight",
            "hotkeySets": {"Knight": mine},
        },
    }
    path = tmp_path / "clientoptions.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_client_combo_names() -> None:
    assert client_combo("ctrl+F1") == "Ctrl+F1"
    assert client_combo("alt+q") == "Alt+Q"
    assert client_combo("shift+pgdn") == "Shift+PgDown"
    assert client_combo("F10") == "F10"


def test_bot_keys_lists_each_key_once() -> None:
    cfg = BotConfig.default()
    cfg = replace(cfg, looting=LootingConfig(loot_hotkey="F1"))
    keys = bot_keys(cfg)
    combos = [k.combo for k in keys]
    assert combos[:4] == ["F2", "F1", "F3", "F4"]  # the default healing rules
    assert combos.count("F1") == 1  # healing used F1 first, loot is dropped
    assert all(k.client_action != "QuickLootAreaAtPlayer" for k in keys)


def test_write_keeps_the_old_set_and_binds_the_bot_keys(tmp_path: Path) -> None:
    path = _client_file(tmp_path)
    keys = [
        BotKey("F1", "Healing rule 1"),
        BotKey("F5", "Healing rule 2"),
        BotKey("F3", "Attack", "UseOnTarget"),
        BotKey("F6", "Nothing typed"),
        BotKey("ctrl+l", "Logout", client_action="Logout"),
    ]
    texts = {"F1": "exura vita", "F5": "Great Mana Potion", "F3": "3155"}
    report = write_moonlight_set(path, keys, texts)
    data = json.loads(path.read_text(encoding="utf-8"))
    hotkeys = data["hotkeyOptions"]
    assert hotkeys["currentHotkeySetName"] == SET_NAME
    assert hotkeys["autoSwitchHotkeyPreset"] is False
    assert hotkeys["hotkeySets"]["Knight"] == json.loads(
        (tmp_path / "clientoptions.json.bak").read_text(encoding="utf-8")
    )["hotkeyOptions"]["hotkeySets"]["Knight"]
    moonlight = hotkeys["hotkeySets"][SET_NAME]
    buttons = {(m["actionBar"], m["actionButton"]): m["actionsetting"]
               for m in moonlight["actionBarOptions"]["mappings"]}
    # F1 keeps its button; F5 and F3 take the first free ones (3, then 4).
    assert buttons[(1, 1)] == {"chatText": "exura vita", "sendAutomatically": True,
                               "spellCastType": 0}
    assert buttons[(1, 2)] == {"useObject": 1}
    assert buttons[(1, 3)] == {"useObject": 238, "useType": "UseOnYourself"}
    assert buttons[(1, 4)] == {"useObject": 3155, "useType": "UseOnTarget"}
    off = {m["keysequence"]: m["actionsetting"] for m in moonlight["chatOff"]}
    assert off["F5"] == {"action": "TriggerActionButton_1.3"}
    assert off["Ctrl+L"] == {"action": "Logout"}
    assert off["W"] == {"action": "GoNorth"}  # the owner's other keys stay
    assert "F6" not in off
    assert "Ctrl+L" in {m["keysequence"] for m in moonlight["chatOn"]}
    assert data["options"] == {"keep": True}
    assert report[3] == "F6: skipped, nothing typed for Nothing typed"


def test_letters_are_not_bound_while_chatting(tmp_path: Path) -> None:
    path = _client_file(tmp_path)
    write_moonlight_set(path, [BotKey("q", "Loot", client_action="QuickLootAreaAtPlayer")], {})
    sets = json.loads(path.read_text(encoding="utf-8"))["hotkeyOptions"]["hotkeySets"]
    moonlight = sets[SET_NAME]
    assert "Q" in {m["keysequence"] for m in moonlight["chatOff"]}
    assert "Q" not in {m["keysequence"] for m in moonlight["chatOn"]}


def test_a_file_without_hotkeys_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "clientoptions.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="no client hotkey sets"):
        write_moonlight_set(path, [], {})


def test_config_keeps_the_client_fields() -> None:
    cfg = replace(BotConfig.default(), client_options_file=Path("c.json"),
                  client_hotkeys={"F1": "exura vita"})
    again = BotConfig.from_dict(cfg.to_dict())
    assert again.client_options_file == Path("c.json")
    assert again.client_hotkeys == {"F1": "exura vita"}
