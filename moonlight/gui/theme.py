"""Moonlight GUI look: colors, fonts, spacing and radii (S.F6.K1).

The only place in ``moonlight/gui`` that holds raw colors. Widgets use the
names below; :func:`apply` restyles customtkinter's defaults so widgets that
pass no color still get the Look. Dark only.
"""

from __future__ import annotations

import customtkinter as ctk

__all__ = [
    "AMBER",
    "AMBER_HOVER",
    "BLOOD",
    "BLOOD_HOVER",
    "FONT",
    "GAP",
    "HOVER",
    "LIFE",
    "LIFE_HOVER",
    "MANA",
    "MANA_HOVER",
    "MONO",
    "NAV_ACTIVE",
    "NIGHT",
    "PAD",
    "PANEL",
    "RADIUS_CARD",
    "RADIUS_CONTROL",
    "RAISED",
    "SECTION_GAP",
    "SIZE_BODY",
    "SIZE_HEADING",
    "SIZE_HERO",
    "SIZE_TITLE",
    "TEXT",
    "TEXT_DIM",
    "TEXT_MUTED",
    "UNIT",
    "apply",
    "button_colors",
]

NIGHT = "#0d1117"
PANEL = "#171b22"
RAISED = "#22262c"
HOVER = "#2c313a"
NAV_ACTIVE = "#1e2332"
TEXT = "#e7edf2"
TEXT_DIM = "#b6b8bd"
TEXT_MUTED = "#7c8186"
LIFE = "#73c9a5"
MANA = "#6ba4f8"
AMBER = "#c99b3e"
BLOOD = "#e55d51"
LIFE_HOVER = "#86d4b3"
MANA_HOVER = "#82b3fa"
AMBER_HOVER = "#d6ab52"
BLOOD_HOVER = "#ec7166"

FONT = "Segoe UI"
MONO = "Cascadia Mono"
SIZE_TITLE = 11
SIZE_BODY = 13
SIZE_HEADING = 16
SIZE_HERO = 26

UNIT = 4
PAD = 12
GAP = 12
SECTION_GAP = 24
RADIUS_CONTROL = 6
RADIUS_CARD = 10


def apply() -> None:
    """Switch customtkinter to dark mode and the Moonlight defaults.

    Call once, before the first widget is created.
    """
    ctk.set_appearance_mode("dark")
    control = {"corner_radius": RADIUS_CONTROL, "text_color": TEXT}
    disabled = {"text_color_disabled": TEXT_MUTED}
    overrides: dict[str, dict[str, object]] = {
        "CTk": {"fg_color": NIGHT},
        "CTkToplevel": {"fg_color": NIGHT},
        "CTkFrame": {
            "fg_color": NIGHT,
            "top_fg_color": NIGHT,
            "border_color": RAISED,
        },
        "CTkButton": {
            **control,
            **disabled,
            "fg_color": RAISED,
            "hover_color": HOVER,
            "border_color": RAISED,
        },
        "CTkLabel": {"text_color": TEXT_DIM},
        "CTkEntry": {
            **control,
            "border_width": 1,
            "fg_color": RAISED,
            "border_color": RAISED,
            "placeholder_text_color": TEXT_MUTED,
        },
        "CTkCheckBox": {
            **disabled,
            "corner_radius": RADIUS_CONTROL,
            "border_width": 2,
            "fg_color": MANA,
            "hover_color": MANA_HOVER,
            "border_color": TEXT_MUTED,
            "checkmark_color": NIGHT,
            "text_color": TEXT_DIM,
        },
        "CTkSwitch": {
            **disabled,
            "fg_color": RAISED,
            "progress_color": MANA,
            "button_color": TEXT,
            "button_hover_color": TEXT_DIM,
            "text_color": TEXT_DIM,
        },
        "CTkRadioButton": {
            **disabled,
            "fg_color": MANA,
            "hover_color": MANA_HOVER,
            "border_color": TEXT_MUTED,
            "text_color": TEXT_DIM,
        },
        "CTkProgressBar": {"fg_color": RAISED, "progress_color": MANA},
        "CTkSlider": {
            "fg_color": RAISED,
            "progress_color": MANA,
            "button_color": MANA,
            "button_hover_color": MANA_HOVER,
        },
        "CTkOptionMenu": {
            **control,
            **disabled,
            "fg_color": RAISED,
            "button_color": RAISED,
            "button_hover_color": HOVER,
        },
        "CTkComboBox": {
            **control,
            **disabled,
            "border_width": 1,
            "fg_color": RAISED,
            "border_color": RAISED,
            "button_color": RAISED,
            "button_hover_color": HOVER,
        },
        "CTkScrollbar": {"button_color": RAISED, "button_hover_color": HOVER},
        "CTkSegmentedButton": {
            **control,
            **disabled,
            "border_width": 2,
            "fg_color": RAISED,
            "selected_color": MANA,
            "selected_hover_color": MANA_HOVER,
            "unselected_color": RAISED,
            "unselected_hover_color": HOVER,
        },
        "CTkTextbox": {
            **control,
            "fg_color": RAISED,
            "border_color": RAISED,
            "scrollbar_button_color": PANEL,
            "scrollbar_button_hover_color": HOVER,
        },
        "CTkScrollableFrame": {"label_fg_color": PANEL},
        "DropdownMenu": {"fg_color": RAISED, "hover_color": HOVER, "text_color": TEXT},
        "CTkFont": {"family": FONT, "size": SIZE_BODY},
    }
    for widget, values in overrides.items():
        ctk.ThemeManager.theme.setdefault(widget, {}).update(values)


def button_colors(enabled: bool, color: str, hover: str) -> dict[str, str]:
    """Fill and hover for a colored button: its own colors, or RAISED when disabled.

    customtkinter only greys a disabled button's text, so a colored button
    would keep its fill.
    """
    if enabled:
        return {"fg_color": color, "hover_color": hover}
    return {"fg_color": RAISED, "hover_color": RAISED}
