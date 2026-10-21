from moonlight.gui import theme
from moonlight.gui.app import cpu_percent, dot_color, memory_text


def test_dot_color() -> None:
    assert dot_color(True) == theme.LIFE
    assert dot_color(False) == theme.BLOOD


def test_memory_text() -> None:
    assert memory_text().endswith("MB") or memory_text() == "—"


def test_cpu_percent_is_whole_pc_busy_share() -> None:
    assert cpu_percent((100, 1000), (150, 1100)) == 50.0
    assert cpu_percent((100, 1000), (100, 1000)) == 0.0
