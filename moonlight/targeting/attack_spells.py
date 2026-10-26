"""Attack spells (T6.F1.K1): cast an attack spell on the selected target.

Each tick with a selected target and MP above the reserve, cast the AoE
spell when at least ``aoe_min_monsters`` monsters are in the battle list,
else the single-target spell. A blank spell is not used. Spells map to
key combos through ``spell_combos`` (``BotConfig.hotkeys``), like healing.

Unknown MP (0.0, never read) never casts: the reserve is a floor, and an
unread bar cannot prove it is met. Focus / screen gating and the rate
limit are the loop's job.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from moonlight.core.state import WorldState
from moonlight.hardware.input import InputBackend

__all__ = ["AttackSpells", "AttackSpellsConfig"]

logger = logging.getLogger("moonlight.targeting.attack_spells")


@dataclass(frozen=True)
class AttackSpellsConfig:
    """Attack spell settings."""

    single_spell: str = ""
    aoe_spell: str = ""
    aoe_min_monsters: int = 3
    mana_reserve_percent: float = 20.0
    cooldown_ms: int = 2000

    def validate(self) -> AttackSpellsConfig:
        """Check ranges and return self.

        Raises:
            ValueError: For a value out of range.
        """
        if self.aoe_min_monsters < 1:
            raise ValueError("aoe_min_monsters must be >= 1")
        if not 0.0 <= self.mana_reserve_percent <= 100.0:
            raise ValueError("mana_reserve_percent must be within 0..100")
        if self.cooldown_ms < 0:
            raise ValueError("cooldown_ms must be >= 0")
        return self


class AttackSpells:
    """Picks and casts the attack spell for one tick."""

    def __init__(
        self,
        config: AttackSpellsConfig,
        spell_combos: Mapping[str, str],
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config.validate()
        self._combos = dict(spell_combos)
        self._clock = clock or time.monotonic
        self._last_cast: float | None = None

    def decide(self, state: WorldState) -> str | None:
        """The spell to cast now, or ``None``."""
        cfg = self.config
        if state.selected_target is None or state.mp_percent <= cfg.mana_reserve_percent:
            return None
        now = self._clock()
        if self._last_cast is not None and (now - self._last_cast) * 1000 < cfg.cooldown_ms:
            return None
        monsters = sum(1 for m in state.monsters if not m.is_player)
        if cfg.aoe_spell and monsters >= cfg.aoe_min_monsters:
            return cfg.aoe_spell
        return cfg.single_spell or None

    def cast(self, state: WorldState, backend: InputBackend) -> bool:
        """:meth:`decide` and press the spell's combo; False when nothing was sent."""
        spell = self.decide(state)
        combo = self._combos.get(spell) if spell else None
        if combo is None:
            return False
        backend.press_combo(combo)
        self._last_cast = self._clock()
        logger.info("attack spell: %s (combo %s)", spell, combo)
        return True
