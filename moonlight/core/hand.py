"""The hand: who holds the mouse and the walk this tick, shared by the loop's claims.

The loop picks one :class:`Claim` per tick from a fixed list (see
:meth:`moonlight.core.loop.GameLoop._claims`). A claim never asks another
module whether the mouse is free: it asks the :class:`Hand`, the one place
that knows the clicker, the cavebot and looting. The loop also writes the
cavebot's per-tick inputs here (:class:`TickInputs`) instead of setting
fields on the cavebot.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from moonlight.core.state import WorldState

if TYPE_CHECKING:
    from moonlight.core.loop import BotState, DecisionModules

#: Gate one input action (focus, screen and rate limit); True when it was sent.
Send = Callable[[Callable[[], object]], bool]


@dataclass
class TickInputs:
    """What the loop decided this tick that the cavebot walks by."""

    #: A lone unreachable target paused targeting: walk on.
    targeting_paused: bool = False
    #: Targeting turned off by a script: walk past monsters.
    walk_past_monsters: bool = False
    #: Targeting's "a fight is on in range"; None: any battle-list monster.
    fight_in_range: bool | None = None


@dataclass(frozen=True)
class Claim:
    """One bid for the tick. The loop runs the first claim whose *wants* is True.

    *act* returning ``False`` means it did nothing after all: the next claim
    runs. *state* is the loop state the claim puts the bot in; ``None`` is the
    walking tier (MOVE while the route walks, else IDLE).
    """

    name: str
    wants: Callable[[WorldState], bool]
    act: Callable[[WorldState, Send], bool | None]
    state: BotState | None = None


class Hand:
    """The mouse and the walk, read through one interface.

    *modules* returns the modules running this tick (paused ones left out).
    """

    def __init__(self, modules: Callable[[], DecisionModules]) -> None:
        self._modules = modules
        self.inputs = TickInputs()
        #: The loop's state is MOVE this tick (the route walks).
        self.moving = False
        #: The loop's walk step; a drop round calls it to walk on while dropping.
        self.walk: Callable[[WorldState], None] = lambda _snap: None

    @property
    def busy(self) -> bool:
        """A click in flight, or a cavebot drag, typing or NPC talk."""
        m = self._modules()
        return (m.clicker is not None and m.clicker.busy) or (
            m.cavebot is not None and m.cavebot.hand_busy
        )

    @property
    def near_floor_change(self) -> bool:
        """Next to a rope spot or stairs."""
        cavebot = self._modules().cavebot
        return cavebot is not None and cavebot.near_floor_change

    @property
    def walking_on(self) -> bool:
        """Plain walking that may go on during a drop round: the route walks a
        walk/node waypoint and the cavebot does not need the mouse."""
        cavebot = self._modules().cavebot
        return (
            cavebot is not None
            and self.moving
            and not cavebot.hand_busy
            and cavebot.walking_waypoint
        )

    @property
    def has_legs(self) -> bool:
        """A cavebot runs this tick."""
        return self._modules().cavebot is not None

    def loot_pending(self, snap: WorldState) -> bool:
        """A corpse ring still to loot."""
        looting = self._modules().looting
        return looting is not None and (looting.should_loot(snap) or looting.loot_first(snap))

    def request_refill(self) -> None:
        """The next refill check goes to refill."""
        cavebot = self._modules().cavebot
        if cavebot is not None:
            cavebot.refill_now = True

    def release_walk_key(self) -> None:
        cavebot = self._modules().cavebot
        if cavebot is not None:
            cavebot.release_walk_key()

    def resume(self) -> None:
        """Time spent on something else is not "stuck"."""
        cavebot = self._modules().cavebot
        if cavebot is not None:
            cavebot.resume()

    @property
    def stuck_on_creatures(self) -> bool:
        cavebot = self._modules().cavebot
        return cavebot is not None and cavebot.stuck_on_creatures

    def holds_attacks(self) -> bool:
        """A lure waypoint, or targeting off by a script: walk past monsters."""
        cavebot = self._modules().cavebot
        return cavebot is not None and cavebot.holds_attacks()

    def creature_click_pending(self, now: float) -> bool:
        """The cavebot right-clicked a creature in the way; its click may still select it."""
        cavebot = self._modules().cavebot
        return cavebot is not None and now < cavebot.creature_in_the_way_clicked_until
