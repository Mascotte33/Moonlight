"""Tiny example user script: taps F1 once per tick while it is running."""

from moonlight.core.state import WorldState
from moonlight.hardware.input import InputBackend
from moonlight.scripts.scripts import BotScript


class Example(BotScript):
    def tick(self, state: WorldState, input: InputBackend) -> None:  # noqa: A002
        input.press_combo("f1")
