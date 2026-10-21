"""S.F6.K1: raw colors live only in moonlight/gui/theme.py."""

from __future__ import annotations

import re
from pathlib import Path

import customtkinter as ctk

from moonlight.gui import theme

GUI = Path(__file__).resolve().parents[2] / "moonlight" / "gui"
HEX = re.compile(r"""["']#[0-9a-fA-F]{3,8}["']""")


def test_no_raw_colors_outside_theme() -> None:
    hits = [
        f"{path.relative_to(GUI)}:{n}: {line.strip()}"
        for path in GUI.rglob("*.py")
        if path.name != "theme.py"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if HEX.search(line)
    ]
    assert hits == []


def test_apply_sets_look_defaults() -> None:
    theme.apply()
    assert ctk.ThemeManager.theme["CTk"]["fg_color"] == theme.NIGHT
    assert ctk.ThemeManager.theme["CTkButton"]["fg_color"] == theme.RAISED
    assert ctk.ThemeManager.theme["CTkFont"]["family"] == theme.FONT


def test_disabled_button_is_raised() -> None:
    assert theme.button_colors(False, theme.LIFE, theme.LIFE_HOVER) == {
        "fg_color": theme.RAISED,
        "hover_color": theme.RAISED,
    }
    assert theme.button_colors(True, theme.LIFE, theme.LIFE_HOVER) == {
        "fg_color": theme.LIFE,
        "hover_color": theme.LIFE_HOVER,
    }
