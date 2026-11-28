"""The loop's claims and the shared hand (who holds the mouse and the walk)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from moonlight.core.hand import Hand
from moonlight.core.loop import BotState, DecisionModules, GameLoop, LoopConfig
from moonlight.core.state import WorldState


class FakeHand:
    def __init__(self, **kw: Any) -> None:
        self.busy = kw.get("busy", False)
        self.near_floor_change = kw.get("near_floor_change", False)
        self.walking_on = kw.get("walking_on", False)
        self.moving = kw.get("moving", False)
        self._loot = kw.get("loot", False)
        self.calls: list[str] = []

    def loot_pending(self, _snap: WorldState) -> bool:
        return bool(self._loot)

    def release_walk_key(self) -> None:
        self.calls.append("release")

    def resume(self) -> None:
        self.calls.append("resume")

    def walk(self, _snap: WorldState) -> None:
        self.calls.append("walk")


def drop(hand: FakeHand | None, *, due: bool = True, in_round: bool = False) -> Any:
    from moonlight.looting.drop_items import DropItems

    d = DropItems.__new__(DropItems)
    d.hand = hand  # type: ignore[assignment]
    d.in_round = in_round
    d._sends = []
    d.due = lambda _snap: due  # type: ignore[method-assign]
    d.step = lambda _snap: None  # type: ignore[method-assign]
    return d


SNAP = WorldState()


def test_drop_waits_while_the_mouse_is_busy_or_a_ring_waits() -> None:
    assert drop(FakeHand()).wants(SNAP)
    assert not drop(FakeHand(busy=True)).wants(SNAP)
    assert not drop(FakeHand(loot=True)).wants(SNAP)
    assert drop(FakeHand(busy=True), in_round=True).wants(SNAP)  # a round runs on
    assert not drop(FakeHand(), due=False).wants(SNAP)


def test_drop_waits_past_a_rope_spot_unless_mid_drag() -> None:
    assert not drop(FakeHand(near_floor_change=True)).wants(SNAP)
    d = drop(FakeHand(near_floor_change=True), in_round=True)
    d._sends = [lambda: None]
    assert d.wants(SNAP)


def test_drop_waits_instead_of_stopping_the_walk() -> None:
    assert not drop(FakeHand(moving=True)).wants(SNAP)
    assert not drop(FakeHand(moving=True), in_round=True).wants(SNAP)  # round on hold
    assert drop(FakeHand(moving=True, walking_on=True)).wants(SNAP)
    d = drop(FakeHand(moving=True), in_round=True)
    d._sends = [lambda: None]
    assert d.wants(SNAP)  # the half-sent drag is finished
    assert drop(FakeHand(moving=False)).wants(SNAP)  # fight or standing anyway


def test_drop_walks_on_during_plain_walking_else_holds_the_walk() -> None:
    sent: list[object] = []
    hand = FakeHand(walking_on=True)
    drop(hand).act(SNAP, lambda a: sent.append(a) or True)
    assert hand.calls == ["walk"] and len(sent) == 1
    hand = FakeHand()
    drop(hand).act(SNAP, lambda a: True)
    assert hand.calls == ["release", "resume"]


def test_the_claims_keep_their_order() -> None:
    m = DecisionModules(
        cavebot=SimpleNamespace(release_walk_key=lambda: None, inputs=None),
        looting=SimpleNamespace(loot_first=lambda s: False, should_loot=lambda s: False),
        scripts=SimpleNamespace(wants=lambda s: False, hand=None, targeting_turned_off=False),
        drop_items=SimpleNamespace(wants=lambda s: False, hand=None),
        extras=SimpleNamespace(),
    )  # type: ignore[arg-type]
    gl = GameLoop(LoopConfig(), SimpleNamespace(), modules=m)  # type: ignore[arg-type]
    assert [c.name for c in gl._claims()] == [
        "loot first", "attack", "loot", "script", "drop", "extras", "move",
    ]  # fmt: skip
    assert [c.state for c in gl._claims()][:3] == [BotState.LOOT, BotState.ATTACK, BotState.LOOT]


def test_hand_reads_the_modules_of_this_tick() -> None:
    cavebot = SimpleNamespace(
        hand_busy=True, near_floor_change=True, walking_waypoint=True, refill_now=False
    )
    modules = DecisionModules(cavebot=cavebot)  # type: ignore[arg-type]
    hand = Hand(lambda: modules)
    assert hand.busy and hand.near_floor_change
    hand.moving = True
    assert not hand.walking_on  # the cavebot needs the mouse
    hand.request_refill()
    assert cavebot.refill_now
    assert not Hand(lambda: DecisionModules()).busy


def test_the_walk_stops_while_the_button_comes_up() -> None:
    hand = FakeHand(walking_on=True)
    d = drop(hand, in_round=True)
    d._sends = [d._release]
    d.act(SNAP, lambda a: True)
    assert hand.calls == ["release"]  # no walk this tick


def test_the_button_waits_for_the_step_to_end() -> None:
    from dataclasses import replace

    hand, sent = FakeHand(walking_on=True), []
    d = drop(hand, in_round=True)
    d._sends = [d._release]
    d.act(replace(SNAP, game_view_sliding=True), lambda a: sent.append(a) or True)
    assert hand.calls == ["release"] and sent == []
    d.act(SNAP, lambda a: sent.append(a) or True)
    assert len(sent) == 1


def test_a_battle_list_click_holds_the_fight_until_it_answers() -> None:
    # owner 2026-11-28: the route walked off right after the click; the monster was left.
    m = DecisionModules(targeting=SimpleNamespace(is_unreachable=lambda s: False))  # type: ignore[arg-type]
    gl = GameLoop(LoopConfig(), SimpleNamespace(), modules=m)  # type: ignore[arg-type]
    assert not gl._wants_attack(WorldState())
    gl._select_click = (2, 1.0)
    assert gl._wants_attack(WorldState())
