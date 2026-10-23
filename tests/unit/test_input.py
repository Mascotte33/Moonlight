"""Unit tests for moonlight.hardware.input.

Covers:
* ProtocolEncoder: wire encoding for each command (K/D/U/M/C/P/X) and
  M-axis range validation (-4000..4000 per axis).
* FakeInput: wire lines recorded for every logical call, and the simulated
  device replies (OK / PONG / ERR).

No serial port or hardware is used anywhere in this file.
"""

import pytest

from moonlight.hardware.input import ClickSide, FakeInput, InputBackend, ProtocolEncoder

COMBO = "ctrl+alt+t"


class TestProtocolEncoderCommands:
    """Each logical command must encode to its documented wire line."""

    def setup_method(self) -> None:
        self.enc = ProtocolEncoder()

    def test_press_combo_is_K(self) -> None:
        assert self.enc.press_combo(COMBO) == "K ctrl+alt+t"

    def test_hold_combo_is_D(self) -> None:
        assert self.enc.hold_combo(COMBO) == "D ctrl+alt+t"

    def test_release_combo_is_U(self) -> None:
        assert self.enc.release_combo(COMBO) == "U ctrl+alt+t"

    def test_mouse_move_is_M(self) -> None:
        assert self.enc.mouse_move(12, -7) == "M 12 -7"

    @pytest.mark.parametrize(
        ("side", "expected"),
        [(ClickSide.LEFT, "C L"), (ClickSide.RIGHT, "C R")],
    )
    def test_click_is_C(self, side: ClickSide, expected: str) -> None:
        assert self.enc.click(side) == expected

    def test_ping_is_P(self) -> None:
        assert self.enc.ping() == "P"

    def test_release_all_is_X(self) -> None:
        assert self.enc.release_all() == "X"

    def test_empty_combo_raises(self) -> None:
        with pytest.raises(ValueError):
            self.enc.press_combo("")

    def test_whitespace_combo_raises(self) -> None:
        with pytest.raises(ValueError):
            self.enc.hold_combo("ctrl alt t")


class TestProtocolEncoderMouseRange:
    """M <dx> <dy>: each axis must stay within -4000..4000 per command."""

    def setup_method(self) -> None:
        self.enc = ProtocolEncoder()

    @pytest.mark.parametrize(
        ("dx", "dy"),
        [(0, 0), (-4000, -4000), (4000, 4000), (-127, 127), (4000, -4000), (-1, 99)],
    )
    def test_valid_axes_pass(self, dx: int, dy: int) -> None:
        assert self.enc.mouse_move(dx, dy) == f"M {dx} {dy}"

    @pytest.mark.parametrize(
        ("dx", "dy"),
        [
            (-4001, 0),
            (4001, 0),
            (0, -4001),
            (0, 4001),
            (-4001, -4001),
            (4001, 4001),
            (-9000, 3),
            (3, 9000),
        ],
    )
    def test_out_of_range_axis_raises(self, dx: int, dy: int) -> None:
        with pytest.raises(ValueError):
            self.enc.mouse_move(dx, dy)


class TestFakeInputWireLines:
    """FakeInput must record exactly the documented wire lines."""

    def setup_method(self) -> None:
        self.fake = FakeInput()

    def test_press_combo_sends_K(self) -> None:
        self.fake.press_combo(COMBO)
        assert self.fake.sent_lines == ["K ctrl+alt+t"]

    def test_hold_combo_sends_D(self) -> None:
        self.fake.hold_combo(COMBO)
        assert self.fake.sent_lines == ["D ctrl+alt+t"]

    def test_release_combo_sends_U(self) -> None:
        self.fake.release_combo(COMBO)
        assert self.fake.sent_lines == ["U ctrl+alt+t"]

    def test_mouse_move_sends_M(self) -> None:
        self.fake.mouse_move(-3, 7)
        assert self.fake.sent_lines == ["M -3 7"]

    def test_click_sends_C(self) -> None:
        self.fake.click(ClickSide.LEFT)
        self.fake.click(ClickSide.RIGHT)
        assert self.fake.sent_lines == ["C L", "C R"]

    def test_ping_sends_P(self) -> None:
        self.fake.ping()
        assert self.fake.sent_lines == ["P"]

    def test_release_all_sends_X(self) -> None:
        self.fake.release_all()
        assert self.fake.sent_lines == ["X"]

    def test_sequence_recorded_in_order(self) -> None:
        self.fake.hold_combo(COMBO)
        self.fake.mouse_move(5, -5)
        self.fake.click(ClickSide.LEFT)
        self.fake.release_combo(COMBO)
        self.fake.release_all()
        assert self.fake.sent_lines == [
            "D ctrl+alt+t",
            "M 5 -5",
            "C L",
            "U ctrl+alt+t",
            "X",
        ]

    def test_is_an_input_backend(self) -> None:
        assert isinstance(self.fake, InputBackend)

    def test_out_of_range_move_raises_and_sends_nothing(self) -> None:
        with pytest.raises(ValueError):
            self.fake.mouse_move(-4001, 0)
        assert self.fake.sent_lines == []
        assert self.fake.replies == []


class TestFakeInputDeviceReplies:
    """FakeInput must simulate device replies: OK, PONG, and ERR."""

    def setup_method(self) -> None:
        self.fake = FakeInput()

    def test_ping_replies_pong(self) -> None:
        assert self.fake.ping() is True
        assert self.fake.replies == ["PONG"]
        assert self.fake.last_reply == "PONG"

    def test_valid_commands_reply_ok(self) -> None:
        self.fake.press_combo(COMBO)
        self.fake.hold_combo(COMBO)
        self.fake.release_combo(COMBO)
        self.fake.mouse_move(1, 1)
        self.fake.click(ClickSide.LEFT)
        self.fake.release_all()
        assert self.fake.replies == ["OK"] * 6

    def test_empty_line_replies_err(self) -> None:
        assert self.fake.write("") == "ERR"

    def test_whitespace_line_replies_err(self) -> None:
        assert self.fake.write("   ") == "ERR"

    def test_unknown_commands_reply_err(self) -> None:
        assert self.fake.write("Z 42") == "ERR"
        assert self.fake.write("Q") == "ERR"

    @pytest.mark.parametrize(
        "line",
        [
            "K",  # missing combo
            "M 5",  # missing dy
            "M five 5",  # non-integer axis
            "M -4001 0",  # dx below range
            "M 0 4001",  # dy above range
            "C",  # missing side
            "C M",  # unknown side
            "P extra",  # junk after P
            "X now",  # junk after X
        ],
    )
    def test_malformed_lines_reply_err(self, line: str) -> None:
        assert self.fake.write(line) == "ERR"

    def test_write_records_line_and_reply(self) -> None:
        self.fake.write("P")
        self.fake.write("")
        assert self.fake.sent_lines == ["P", ""]
        assert self.fake.replies == ["PONG", "ERR"]

    def test_last_reply_initially_none(self) -> None:
        assert FakeInput().last_reply is None


def test_mouse_to_is_A_and_checks_range() -> None:
    from moonlight.hardware.input import ABS_MAX, ProtocolEncoder

    enc = ProtocolEncoder()
    assert enc.mouse_to(0, ABS_MAX) == f"A 0 {ABS_MAX}"
    for bad in ((-1, 0), (0, ABS_MAX + 1)):
        with pytest.raises(ValueError):
            enc.mouse_to(*bad)
    fake = FakeInput()
    fake.mouse_to(5, 6)
    assert fake.sent_lines == ["A 5 6"]
    assert fake.write("A 1") == "ERR" and fake.write("A -1 2") == "ERR"
