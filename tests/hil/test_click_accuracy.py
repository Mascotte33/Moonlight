"""HIL scenario 2 -- click accuracy (offline: exact wire commands)."""

import pytest
from mock_serial import MockBus

from moonlight.input import ClickSide, FakeInput, ProtocolEncoder


@pytest.mark.parametrize(("side", "line"), [(ClickSide.LEFT, "C L"), (ClickSide.RIGHT, "C R")])
def test_fake_click_emits_exact_wire_command(side: ClickSide, line: str) -> None:
    fake = FakeInput()
    fake.click(side)
    assert fake.sent_lines == [line]
    assert fake.last_reply == "OK"
    assert ProtocolEncoder().click(side) == line


def test_move_then_click_lands_on_target() -> None:
    fake = FakeInput(cursor=(100, 100))
    fake.mouse_move(50, -20)
    fake.click(ClickSide.LEFT)
    assert fake.sent_lines == ["M 50 -20", "C L"]
    assert fake.cursor_position == (150, 80)


def test_serial_click_wire_and_device_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = MockBus()
    bus.install(monkeypatch)
    ard = bus.connect()
    try:
        ard.click(ClickSide.LEFT)  # raises unless the device answered OK
        assert bus.current.lines_written[-1] == "C L"
    finally:
        ard.close()
