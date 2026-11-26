"""With targeting turned off by a script the bot walks past monsters and fights
only what leaves it no way past (owner 2026-11-26: off the hunt, like the rings).

# doc: docs/prs/near-targeting.md#targeting-script
"""

from __future__ import annotations

from moonlight.core.loop import BotState, DecisionModules, GameLoop, LoopConfig
from moonlight.core.state import Monster, Position
from moonlight.scripts.scripts import ScriptRunner
from tests.test_cavebot import Sim, walk


def test_targeting_off_holds_attacks_unless_creatures_block_the_way() -> None:
    sim = Sim([walk(110, 100)], Position(100, 100, 7))
    sim.bot.start()
    assert not sim.bot.holds_attacks()
    sim.bot.walk_past_monsters = True
    assert sim.bot.holds_attacks()
    sim.bot.stuck_on_creatures = True  # no way past: fight through
    assert not sim.bot.holds_attacks()


def test_with_targeting_off_the_loop_walks_on_past_a_selected_monster() -> None:
    sim = Sim([walk(110, 100), walk(120, 100)], Position(100, 100, 7))
    sim.state.selected_target = Monster(name="Wolf")
    scripts = ScriptRunner()
    loop = GameLoop(
        sim.state,
        sim.fake,
        LoopConfig(enable_f12_monitor=False),
        modules=DecisionModules(cavebot=sim.bot, scripts=scripts),
    )
    sim.bot.start()
    scripts.targeting_turned_off = True  # script targeting_off ran
    loop.tick()
    assert sim.bot.walk_past_monsters
    assert _state(loop) is BotState.MOVE  # not ATTACK: walks on
    scripts.targeting_turned_off = False  # script targeting_on ran
    loop.tick()
    assert _state(loop) is BotState.ATTACK


def _state(loop: GameLoop) -> BotState:
    return loop.current_state
