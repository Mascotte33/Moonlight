from moonlight.gui import theme
from moonlight.gui.sysinfo import cpu_percent, memory_text, ram_percent
from moonlight.gui.tabs.base import dot_color


def test_dot_color() -> None:
    assert dot_color(True) == theme.LIFE
    assert dot_color(False) == theme.BLOOD


def test_memory_text() -> None:
    assert memory_text().endswith("MB") or memory_text() == "—"


def test_cpu_percent_is_whole_pc_busy_share() -> None:
    assert cpu_percent((100, 1000), (150, 1100)) == 50.0
    assert cpu_percent((100, 1000), (100, 1000)) == 0.0


def test_ram_percent_is_a_share_or_unknown() -> None:
    ram = ram_percent()
    assert ram is None or 0.0 <= ram <= 100.0
