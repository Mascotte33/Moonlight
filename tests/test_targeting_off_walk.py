"""With targeting turned off by a script the bot walks past monsters and fights
only what leaves it no way past (owner 2026-11-26: off the hunt, like the rings).

# doc: docs/prs/near-targeting.md#targeting-script
"""

from __future__ import annotations

from moonlight.cavebot.route import Waypoint
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


def test_a_resume_mid_route_replays_the_last_targeting_switch_before_it() -> None:
    off = Waypoint(0, 0, 0, "script", "targeting_off")
    on = Waypoint(0, 0, 0, "script", "targeting_on")
    scripts = ScriptRunner()
    sim = Sim([off, walk(110, 100), on, walk(130, 100), off, walk(150, 100)],
              Position(150, 100, 7), scripts=scripts)  # fmt: skip
    sim.bot.start(nearest=True)  # resumes at waypoint 5: after the second targeting_off
    assert sim.route.current == 5 and scripts.targeting_turned_off
    sim.pos = Position(130, 100, 7)
    sim.bot.start(nearest=True)  # waypoint 3: after targeting_on
    assert sim.route.current >= 3 and not scripts.targeting_turned_off


def test_a_resume_in_the_hunt_loop_follows_the_goto_back_into_it() -> None:
    """2026-11-27 Yalahar elves: targeting_on sits on the walk back, right
    before the ramp into the hunt; a resume in the loop read waypoint 0's
    targeting_off and walked the hunt without attacking."""
    off = Waypoint(0, 0, 0, "script", "targeting_off")
    on = Waypoint(0, 0, 0, "script", "targeting_on")
    start = Waypoint(110, 100, 7, "label", "StartHunt")
    goto = Waypoint(0, 0, 0, "goto", "StartHunt")
    scripts = ScriptRunner()
    route = [off, start, walk(110, 100), walk(120, 100), goto,  # 0-4: loop
             Waypoint(0, 0, 0, "label", "GoToRefill"), off, walk(200, 100),  # 5-7: to town
             walk(150, 100), on, walk(140, 100), goto]  # 8-11: back, on at the ramp  # fmt: skip
    sim = Sim(route, Position(120, 100, 7), scripts=scripts)
    sim.bot.start(nearest=True)  # waypoint 3, in the loop
    assert sim.route.current == 3 and not scripts.targeting_turned_off
    sim.pos = Position(200, 100, 7)
    sim.bot.start(nearest=True)  # waypoint 7, on the way to town
    assert sim.route.current == 7 and scripts.targeting_turned_off


def test_a_start_with_no_position_yet_resumes_on_the_first_one() -> None:
    """2026-11-26: Tibia not in front at start, no position, waypoint 0 ran
    targeting_off and go_to_city in the cave."""
    off = Waypoint(0, 0, 0, "script", "targeting_off")
    on = Waypoint(0, 0, 0, "script", "targeting_on")
    scripts = ScriptRunner()
    sim = Sim([off, walk(110, 100), on, walk(130, 100)], Position(130, 100, 7), scripts=scripts)
    sim.pos = None
    sim.bot.start(nearest=True)
    sim.step()
    assert not scripts.targeting_turned_off  # waypoint 0 did not run
    sim.pos = Position(130, 100, 7)
    sim.step()
    assert sim.route.current >= 3 and not scripts.targeting_turned_off
