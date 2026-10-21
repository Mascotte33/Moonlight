"""T6.F1.K1: attack spells pick AoE vs single, keep the mana reserve and cooldown."""

from __future__ import annotations

from dataclasses import replace

from moonlight.bot.state import Monster, WorldState
from moonlight.decision.attack_spells import AttackSpells, AttackSpellsConfig
from moonlight.hardware.input import FakeInput

COMBOS = {"exori": "F5", "exori gran": "F6"}
CFG = AttackSpellsConfig(single_spell="exori", aoe_spell="exori gran", aoe_min_monsters=3)


def _state(monsters: int, *, mp: float = 80.0, target: bool = True) -> WorldState:
    state = WorldState.default()
    state.monsters = [Monster(name=f"Rat{i}") for i in range(monsters)]
    state.selected_target = state.monsters[0] if target and monsters else None
    state.mp_percent = mp
    return state


def _spells(now: list[float]) -> AttackSpells:
    return AttackSpells(CFG, COMBOS, clock=lambda: now[0])


def test_aoe_with_enough_monsters_else_single() -> None:
    spells = _spells([0.0])
    assert spells.decide(_state(3)) == "exori gran"
    assert spells.decide(_state(2)) == "exori"


def test_mana_reserve_blocks_cast() -> None:
    spells = _spells([0.0])
    assert spells.decide(_state(1, mp=20.0)) is None
    assert spells.decide(_state(1, mp=0.0)) is None
    assert spells.decide(_state(1, mp=21.0)) == "exori"


def test_cooldown_between_casts() -> None:
    now = [0.0]
    spells, backend = _spells(now), FakeInput()
    assert spells.cast(_state(1), backend)
    now[0] = 1.9
    assert not spells.cast(_state(1), backend)
    now[0] = 2.0
    assert spells.cast(_state(1), backend)


def test_no_target_no_cast() -> None:
    assert _spells([0.0]).decide(_state(4, target=False)) is None


def test_blank_spell_not_used() -> None:
    spells = AttackSpells(AttackSpellsConfig(aoe_spell="exori gran"), COMBOS)
    assert spells.decide(_state(1)) is None
    assert spells.decide(_state(3)) == "exori gran"


def test_config_without_attack_spells_section_loads_off() -> None:
    from moonlight.setup.config import BotConfig

    data = BotConfig.default().to_dict()
    del data["modules"]["attack_spells"]
    assert BotConfig.from_dict(data).attack_spells is None


def test_tab_writes_spells_and_hotkeys() -> None:
    import pytest

    from moonlight.gui.tabs.targeting import apply_attack_spells
    from moonlight.setup.config import BotConfig, ConfigError

    values = {
        "attack_spells.single_spell": "exori",
        "attack_spells.aoe_min_monsters": 4.0,
    }
    on = replace(BotConfig.default(), attack_spells=AttackSpellsConfig())
    cfg = apply_attack_spells(on, values, {"attack_spells.single_spell": "F5"})
    assert cfg.attack_spells == AttackSpellsConfig(single_spell="exori", aoe_min_monsters=4)
    assert cfg.spell_combos["exori"] == "F5"
    with pytest.raises(ConfigError, match="no hotkey"):
        apply_attack_spells(on, values, {"attack_spells.single_spell": ""})
