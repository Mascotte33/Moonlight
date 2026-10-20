"""Unit tests for moonlight.decision.targeting (TargetingDecision, T20).

Fully hermetic: synthetic WorldState snapshots only. No hardware, no
client, no input backend (targeting emits intents, it never sends input).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from moonlight.decision.targeting import (
    TargetingAction,
    TargetingActionKind,
    TargetingConfig,
    TargetingDecision,
)
from moonlight.state import Monster, WorldState


def make_state(
    *,
    monsters: list[Monster] | None = None,
    selected_target: Monster | None = None,
    players_on_screen: int = 0,
) -> WorldState:
    """One consistent world-state frame (reader-style, like the loop gets)."""
    state = WorldState.default()
    state.client_focused = True
    state.screen_ok = True
    state.monsters = list(monsters) if monsters is not None else []
    state.selected_target = selected_target
    state.players_on_screen = players_on_screen
    state.last_update = 1.0
    return state


def make_targeting(config: TargetingConfig | None = None) -> TargetingDecision:
    return TargetingDecision(config)


# -- decide(): idle -------------------------------------------------------------


class TestDecideIdle:
    def test_empty_battle_list_is_idle(self) -> None:
        assert make_targeting().decide(make_state()) is None

    def test_only_players_in_battle_list_is_idle(self) -> None:
        state = make_state(monsters=[Monster(name="Bob", is_player=True)])
        assert make_targeting().decide(state) is None

    def test_only_players_with_protection_disabled_is_idle(self) -> None:
        config = TargetingConfig(player_protection=False)
        state = make_state(
            monsters=[Monster(name="Bob", is_player=True)],
            players_on_screen=1,
        )
        assert make_targeting(config).decide(state) is None

    def test_nameless_entries_never_auto_attacked(self) -> None:
        """F-01: an entry without a name (no resolver wired) is UNKNOWN.

        It may be a player, so it is never a candidate and targeting stays
        idle rather than auto-attacking it.
        """
        state = make_state(monsters=[Monster(name="", hp_percent=10.0)])
        assert make_targeting().decide(state) is None

    def test_nameless_entry_is_skipped_in_favour_of_named(self) -> None:
        """F-01: a nameless entry is skipped; only the named monster is targeted."""
        state = make_state(
            monsters=[
                Monster(name="", hp_percent=1.0),
                Monster(name="Wolf", hp_percent=100.0),
            ]
        )
        action = make_targeting().decide(state)
        assert action is not None
        assert action.target_name == "Wolf"

    def test_nameless_selection_is_not_kept(self) -> None:
        """F-01: a nameless current selection is abandoned, never attacked."""
        state = make_state(
            monsters=[Monster(name="", hp_percent=1.0)],
            selected_target=Monster(name="", hp_percent=1.0),
        )
        assert make_targeting().decide(state) is None


# -- decide(): basic attack -----------------------------------------------------


class TestDecideAttack:
    def test_single_monster_no_selection_attacks_it(self) -> None:
        wolf = Monster(name="Wolf", hp_percent=100.0)
        action = make_targeting().decide(
            make_state(monsters=[wolf], selected_target=None)
        )
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK
        assert action.target_name == "Wolf"

    def test_action_tokens_are_stable(self) -> None:
        wolf = Monster(name="Wolf")
        action = make_targeting().decide(make_state(monsters=[wolf]))
        assert action is not None
        assert action.action.value in {"attack", "switch", "flee"}
        assert action.action.value == "attack"
        assert isinstance(action, TargetingAction)
        assert isinstance(action.reason, str) and action.reason

    def test_existing_valid_selection_kept(self) -> None:
        wolf = Monster(name="Wolf", hp_percent=80.0)
        action = make_targeting().decide(
            make_state(monsters=[wolf], selected_target=wolf)
        )
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK
        assert action.target_name == "Wolf"
        assert "Wolf" in action.reason

    def test_selection_match_is_case_insensitive(self) -> None:
        wolf = Monster(name="Wolf", hp_percent=80.0)
        selected = Monster(name="wolf", hp_percent=80.0)
        action = make_targeting().decide(
            make_state(monsters=[wolf], selected_target=selected)
        )
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK
        assert action.target_name == "Wolf"


# -- decide(): prefer_low_hp ------------------------------------------------------


class TestPreferLowHp:
    # These scenarios intentionally hold several monsters, so they need a
    # higher engage limit than the default of 1.
    _config = TargetingConfig(max_monsters_to_engage=5)

    def test_picks_weakest_monster(self) -> None:
        state = make_state(
            monsters=[
                Monster(name="Wolf", hp_percent=90.0),
                Monster(name="Rat", hp_percent=25.0),
                Monster(name="Spider", hp_percent=50.0),
            ],
            selected_target=None,
        )
        action = make_targeting(self._config).decide(state)
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK
        assert action.target_name == "Rat"
        assert "Rat" in action.reason

    def test_hp_tie_keeps_battle_list_order(self) -> None:
        state = make_state(
            monsters=[
                Monster(name="Rat", hp_percent=40.0),
                Monster(name="Wolf", hp_percent=40.0),
            ],
            selected_target=None,
        )
        action = make_targeting(self._config).decide(state)
        assert action is not None
        assert action.target_name == "Rat"

    def test_first_monster_when_prefer_low_hp_disabled(self) -> None:
        config = TargetingConfig(prefer_low_hp=False, max_monsters_to_engage=5)
        state = make_state(
            monsters=[
                Monster(name="Wolf", hp_percent=90.0),
                Monster(name="Rat", hp_percent=25.0),
            ],
            selected_target=None,
        )
        action = make_targeting(config).decide(state)
        assert action is not None
        assert action.target_name == "Wolf"

    def test_valid_current_selection_beats_lower_hp(self) -> None:
        # A still-valid selected target is kept, even if another monster
        # is weaker (selection is sticky until it stops being a monster).
        wolf = Monster(name="Wolf", hp_percent=95.0)
        rat = Monster(name="Rat", hp_percent=10.0)
        action = make_targeting(self._config).decide(
            make_state(monsters=[wolf, rat], selected_target=wolf)
        )
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK
        assert action.target_name == "Wolf"


# -- decide(): switch away from invalid selections ---------------------------------


class TestSwitchAway:
    def test_player_selected_switches_to_monster(self) -> None:
        player = Monster(name="Bob", is_player=True)
        wolf = Monster(name="Wolf", hp_percent=100.0)
        action = make_targeting().decide(
            make_state(monsters=[wolf], selected_target=player)
        )
        assert action is not None
        assert action.action is TargetingActionKind.SWITCH
        assert action.target_name == "Wolf"
        assert "Bob" in action.reason

    def test_stale_selection_switches_to_monster(self) -> None:
        # Selected target no longer in the battle list (it died / left).
        stale = Monster(name="Rat", hp_percent=10.0)
        wolf = Monster(name="Wolf", hp_percent=100.0)
        action = make_targeting().decide(
            make_state(monsters=[wolf], selected_target=stale)
        )
        assert action is not None
        assert action.action is TargetingActionKind.SWITCH
        assert action.target_name == "Wolf"

    def test_switch_reason_mentions_previous_selection(self) -> None:
        player = Monster(name="Bob", is_player=True)
        wolf = Monster(name="Wolf", hp_percent=100.0)
        action = make_targeting().decide(
            make_state(monsters=[wolf], selected_target=player)
        )
        assert action is not None
        assert "Bob" in action.reason
        assert "Wolf" in action.reason


# -- safety: players ---------------------------------------------------------------


class TestPlayerSafety:
    def test_players_on_screen_flee(self) -> None:
        wolf = Monster(name="Wolf", hp_percent=100.0)
        action = make_targeting().decide(
            make_state(monsters=[wolf], players_on_screen=1)
        )
        assert action is not None
        assert action.action is TargetingActionKind.FLEE
        assert action.target_name == ""

    def test_flee_wins_over_target_selection(self) -> None:
        wolf = Monster(name="Wolf", hp_percent=100.0)
        action = make_targeting().decide(
            make_state(
                monsters=[wolf],
                selected_target=wolf,
                players_on_screen=2,
            )
        )
        assert action is not None
        assert action.action is TargetingActionKind.FLEE
        assert "2" in action.reason

    def test_players_on_screen_ignored_when_protection_disabled(self) -> None:
        config = TargetingConfig(player_protection=False)
        wolf = Monster(name="Wolf", hp_percent=100.0)
        action = make_targeting(config).decide(
            make_state(monsters=[wolf], players_on_screen=1)
        )
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK
        assert action.target_name == "Wolf"

    def test_player_never_targeted_even_if_listed_as_monster(self) -> None:
        # Perception mislabels a player as a monster entry: safety filter
        # must exclude it from targeting.
        player = Monster(name="Bob", is_player=True)
        wolf = Monster(name="Wolf", hp_percent=100.0)
        action = make_targeting().decide(
            make_state(monsters=[wolf, player], selected_target=None)
        )
        assert action is not None
        assert action.target_name == "Wolf"

    @pytest.mark.parametrize("players", [1, 2, 5])
    def test_any_player_count_flees(self, players: int) -> None:
        action = make_targeting().decide(
            make_state(monsters=[Monster(name="Wolf")], players_on_screen=players)
        )
        assert action is not None
        assert action.action is TargetingActionKind.FLEE


# -- max_monsters_to_engage ----------------------------------------------------------


class TestMaxMonstersToEngage:
    def test_single_monster_engaged_with_default_config(self) -> None:
        action = make_targeting().decide(make_state(monsters=[Monster(name="Wolf")]))
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK

    def test_crowd_keeps_attacking_and_warns_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # F-08: no flee route exists yet, so a crowd only logs a warning.
        state = make_state(monsters=[Monster(name=n) for n in "ABCDE"], selected_target=None)
        targeting = make_targeting()
        with caplog.at_level("WARNING"):
            for _ in range(3):
                action = targeting.decide(state)
                assert action is not None
                assert action.action is TargetingActionKind.ATTACK
        assert sum("exceed max_monsters_to_engage" in r.message for r in caplog.records) == 1

    def test_crowd_keeps_existing_selection(self) -> None:
        wolf = Monster(name="Wolf")
        state = make_state(
            monsters=[wolf, *(Monster(name=n) for n in "ABCD")],
            selected_target=wolf,
        )
        action = make_targeting().decide(state)
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK
        assert action.target_name == "Wolf"

    def test_at_limit_engaged(self) -> None:
        config = TargetingConfig(max_monsters_to_engage=3)
        state = make_state(
            monsters=[Monster(name="A"), Monster(name="B"), Monster(name="C")]
        )
        action = make_targeting(config).decide(state)
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK

    def test_above_limit_still_attacks(self) -> None:
        config = TargetingConfig(max_monsters_to_engage=3)
        state = make_state(
            monsters=[
                Monster(name="A"),
                Monster(name="B"),
                Monster(name="C"),
                Monster(name="D"),
            ]
        )
        action = make_targeting(config).decide(state)
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK

    def test_player_entries_do_not_count_towards_limit(self) -> None:
        # Players are not monsters; they must not block engagement.
        state = make_state(
            monsters=[Monster(name="Wolf"), Monster(name="Bob", is_player=True)]
        )
        action = make_targeting().decide(state)
        assert action is not None
        assert action.action is TargetingActionKind.ATTACK
        assert action.target_name == "Wolf"


# -- config validation ------------------------------------------------------------------


class TestConfigValidation:
    def test_default_config_is_valid(self) -> None:
        TargetingConfig().validate()
        TargetingDecision()  # must not raise

    def test_default_values_match_spec(self) -> None:
        config = TargetingConfig()
        assert config.max_monsters_to_engage == 4
        assert config.prefer_low_hp is True
        assert config.player_protection is True

    @pytest.mark.parametrize("value", [0, -1, -100])
    def test_nonpositive_max_monsters_rejected(self, value: int) -> None:
        with pytest.raises(ValueError):
            TargetingDecision(TargetingConfig(max_monsters_to_engage=value))

    def test_bool_max_monsters_rejected(self) -> None:
        with pytest.raises(ValueError):
            TargetingDecision(TargetingConfig(max_monsters_to_engage=True))


# -- TargetingAction structure ----------------------------------------------------------


class TestTargetingActionStructure:
    def test_action_kind_values(self) -> None:
        assert TargetingActionKind.ATTACK.value == "attack"
        assert TargetingActionKind.SWITCH.value == "switch"
        assert TargetingActionKind.FLEE.value == "flee"

    def test_frozen(self) -> None:
        action = TargetingAction(
            target_name="Wolf",
            action=TargetingActionKind.ATTACK,
            reason="first monster in battle list: 'Wolf'",
        )
        with pytest.raises(FrozenInstanceError):
            action.target_name = "Rat"  # type: ignore[misc]

    def test_flee_action_has_empty_target_name(self) -> None:
        action = make_targeting().decide(make_state(players_on_screen=1))
        assert action is not None
        assert action.action is TargetingActionKind.FLEE
        assert action.target_name == ""
