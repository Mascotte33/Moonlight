"""The ten MOONLIGHT GUI tabs (T39) — all extend :class:`Tab`."""

from moonlight.gui.tabs.cavebot import CavebotTab
from moonlight.gui.tabs.dashboard import DashboardTab
from moonlight.gui.tabs.diagnostics import DiagnosticsTab
from moonlight.gui.tabs.hardware import HardwareTab
from moonlight.gui.tabs.healing import HealingTab
from moonlight.gui.tabs.recorder import RecorderTab
from moonlight.gui.tabs.settings import SettingsTab
from moonlight.gui.tabs.spell_attack import SpellAttackTab
from moonlight.gui.tabs.status import StatusTab
from moonlight.gui.tabs.targeting import TargetingTab

__all__ = [
    "CavebotTab",
    "DashboardTab",
    "DiagnosticsTab",
    "HealingTab",
    "HardwareTab",
    "RecorderTab",
    "SettingsTab",
    "SpellAttackTab",
    "StatusTab",
    "TargetingTab",
]
