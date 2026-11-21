"""CursorChaseInput: absolute moves as relative steps against the real cursor."""

from __future__ import annotations

import pytest

from moonlight.hardware.cursor_chase import TRIES, CursorChaseInput
from moonlight.hardware.input import ABS_MAX, FakeInput


class AcceleratedPointer(FakeInput):
    """A relative mouse whose big steps land 1.8x as far, like libinput's."""

    def __init__(self) -> None:
        super().__init__()
        self.at = (1679, 1045)
        self.steps = 0

    def mouse_move(self, dx: int, dy: int) -> None:
        super().mouse_move(dx, dy)
        self.steps += 1
        gain = 1.8 if max(abs(dx), abs(dy)) > 10 else 1.0
        self.at = (self.at[0] + round(dx * gain), self.at[1] + round(dy * gain))


def chase(pointer: AcceleratedPointer) -> CursorChaseInput:
    return CursorChaseInput(pointer, lambda: pointer.at, (0, 0, 1680, 1050), sleep=lambda _s: None)


@pytest.mark.parametrize("spot", [(400, 300), (610, 475), (100, 900), (1500, 100)])
def test_mouse_to_lands_on_the_spot_despite_acceleration(spot: tuple[int, int]) -> None:
    pointer = AcceleratedPointer()
    x, y = (round((v + 0.5) * (ABS_MAX + 1) / n) for v, n in zip(spot, (1680, 1050), strict=True))
    chase(pointer).mouse_to(x, y)
    assert abs(pointer.at[0] - spot[0]) <= 1 and abs(pointer.at[1] - spot[1]) <= 1
    assert pointer.steps <= TRIES


def test_mouse_to_without_a_cursor_fails_loudly() -> None:
    pointer = AcceleratedPointer()
    backend = CursorChaseInput(pointer, lambda: None, (0, 0, 1680, 1050), sleep=lambda _s: None)
    with pytest.raises(RuntimeError, match="cursor position unknown"):
        backend.mouse_to(100, 100)


def test_other_commands_go_straight_through() -> None:
    pointer = AcceleratedPointer()
    chase(pointer).press_combo("F1")
    assert pointer.sent_lines[-1] == "K F1"
