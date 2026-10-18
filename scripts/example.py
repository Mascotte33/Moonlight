"""Tiny example user script: taps F1 once per tick while it is running."""

from moonlight.input import InputBackend
from moonlight.scripts import BotScript
from moonlight.state import WorldState


class Example(BotScript):
    def tick(self, state: WorldState, input: InputBackend) -> None:  # noqa: A002
        input.press_combo("f1")
