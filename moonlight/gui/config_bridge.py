"""Config bridge for the GUI (T39).

Bridges the GUI's config tabs and the bot's real config objects:

* :class:`BotConfig` — the GUI-level bot configuration (tick rate, window
  target, input mode, module toggles, healing/targeting/spell/cavebot
  parameters). Mirrors the fields of the existing decision-layer config
  dataclasses (``moonlight.decision.healing.HealingConfig`` and
  ``moonlight.decision.targeting.TargetingConfig``) so a tab edit maps 1:1
  onto the config the bot actually consumes.
* :class:`ConfigStore` — thread-safe holder (RLock) so config tabs can edit
  values while the bot loop thread reads them; ``snapshot()`` returns a copy.
* :func:`load_config` / :func:`save_config` — JSON persistence under
  ``data/config.json`` (the repo's data directory).

The GUI does NOT define new decision logic here — it only edits and persists
configuration and maps it onto the existing dataclasses.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from moonlight.decision.healing import HealingConfig
    from moonlight.decision.targeting import TargetingConfig
    from moonlight.loop import LoopConfig

__all__ = [
    "BotConfig",
    "CavebotParams",
    "ConfigStore",
    "HealingParams",
    "ModuleToggles",
    "SpellAttackParams",
    "TargetingParams",
    "default_config_path",
    "load_config",
    "repo_root",
    "save_config",
]


def repo_root() -> Path:
    """The ``moonlight`` package directory."""
    return Path(__file__).resolve().parents[1]


def default_config_path() -> Path:
    """Default config location: ``<repo>/data/config.json``."""
    return repo_root() / "data" / "config.json"


# ---------------------------------------------------------------------------
# Config dataclasses (GUI view of the bot configuration)
# ---------------------------------------------------------------------------


@dataclass
class ModuleToggles:
    """Which decision modules the bot loop should run.

    Healing and the overlay are the baseline bot behavior; cavebot and loot
    are opt-in (cavebot is T22-pending, loot is T23).
    """

    healing: bool = True
    cavebot: bool = False
    loot: bool = False
    overlay: bool = True


@dataclass
class HealingParams:
    """Healing thresholds/spells — mirrors ``decision.healing.HealingConfig``."""

    emergency_hp_percent: float = 30.0
    normal_hp_percent: float = 70.0
    mana_threshold: float = 20.0
    heal_cooldown_ms: int = 500
    emergency_spell: str = "exura gran"
    normal_spell: str = "exura"
    cure_spell: str = "exura gran"

    def to_healing_config(self) -> HealingConfig:
        """Build the real ``decision.healing.HealingConfig`` (validates)."""
        from moonlight.decision.healing import HealingConfig

        return HealingConfig(
            emergency_hp_percent=self.emergency_hp_percent,
            normal_hp_percent=self.normal_hp_percent,
            emergency_spell=self.emergency_spell,
            normal_spell=self.normal_spell,
            cure_spell=self.cure_spell,
            heal_cooldown_ms=self.heal_cooldown_ms,
            mana_threshold=self.mana_threshold,
        )


@dataclass
class TargetingParams:
    """Targeting rules — mirrors ``decision.targeting.TargetingConfig`` plus
    GUI-level name filters (whitelist/blacklist) pending decision wiring.

    ``attack_combo`` is a GUI-level field (the loop's attack hotkey) that
    feeds :meth:`BotConfig.to_loop_config`; the real ``TargetingConfig``
    does not hold it.
    """

    max_monsters_to_engage: int = 1
    prefer_low_hp: bool = True
    player_protection: bool = True
    attack_combo: str = "r"
    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)

    def to_targeting_config(self) -> TargetingConfig:
        """Build the real ``decision.targeting.TargetingConfig`` (validates)."""
        from moonlight.decision.targeting import TargetingConfig

        return TargetingConfig(
            max_monsters_to_engage=self.max_monsters_to_engage,
            prefer_low_hp=self.prefer_low_hp,
            player_protection=self.player_protection,
        )


@dataclass
class SpellAttackParams:
    """Spell-attack groups (single-target vs AoE) and the mana reserve.

    Part of the targeting/spell configuration; consumed by the spell-attack
    decision module once it lands (T24). The GUI edits + persists it now.
    """

    single_target_enabled: bool = True
    aoe_enabled: bool = False
    single_spell_combo: str = "r"
    aoe_spell_combo: str = "g"
    mana_reserve_percent: float = 20.0

    def validate(self) -> SpellAttackParams:
        """Raise ``ValueError`` on invalid values; return self otherwise."""
        if not (0.0 <= self.mana_reserve_percent <= 100.0):
            raise ValueError(
                f"mana_reserve_percent must be in [0, 100], got {self.mana_reserve_percent}"
            )
        if not self.single_target_enabled and not self.aoe_enabled:
            raise ValueError("at least one spell group must be enabled")
        if not self.single_spell_combo.strip():
            raise ValueError("single_spell_combo must be a non-empty combo")
        if not self.aoe_spell_combo.strip():
            raise ValueError("aoe_spell_combo must be a non-empty combo")
        return self


@dataclass
class CavebotParams:
    """Cavebot parameters (T22 engine pending; data layer only for now)."""

    route_name: str = ""
    refill_hp_percent: float = 90.0
    refill_mp_percent: float = 90.0
    deposit_at_full: bool = False
    loop_route: bool = True

    def validate(self) -> CavebotParams:
        """Raise ``ValueError`` on invalid values; return self otherwise."""
        if not (0.0 <= self.refill_hp_percent <= 100.0):
            raise ValueError(
                f"refill_hp_percent must be in [0, 100], got {self.refill_hp_percent}"
            )
        if not (0.0 <= self.refill_mp_percent <= 100.0):
            raise ValueError(
                f"refill_mp_percent must be in [0, 100], got {self.refill_mp_percent}"
            )
        return self


@dataclass
class BotConfig:
    """The full GUI-level bot configuration."""

    tick_rate: float = 20.0
    window_title: str = "Tibia"
    input_mode: str = "arduino"  # "arduino" | "software"
    serial_port: str | None = None
    serial_baudrate: int = 115200
    profile_name: str | None = None
    # F-13: explicit simulation toggle. When True, the GUI Start uses a
    # FakeInput backend instead of a real Arduino. A missing Arduino with
    # this off is a hard error — there is no silent FakeInput fallback.
    simulation: bool = False
    modules: ModuleToggles = field(default_factory=ModuleToggles)
    healing: HealingParams = field(default_factory=HealingParams)
    targeting: TargetingParams = field(default_factory=TargetingParams)
    spells: SpellAttackParams = field(default_factory=SpellAttackParams)
    cavebot: CavebotParams = field(default_factory=CavebotParams)

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict (nested dataclasses included)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BotConfig:
        """Build a config from a dict, tolerating missing/extra keys.

        Missing keys fall back to defaults; unknown keys are ignored so
        older/newer config files load cleanly.
        """
        data = data if isinstance(data, dict) else {}
        kwargs: dict[str, Any] = {}
        for name in (
            "tick_rate",
            "window_title",
            "input_mode",
            "serial_port",
            "serial_baudrate",
            "profile_name",
            "simulation",
        ):
            if name in data:
                kwargs[name] = data[name]
        cfg = cls(**kwargs)
        if isinstance(data.get("modules"), dict):
            known = {f for f in ModuleToggles.__dataclass_fields__}
            cfg.modules = replace(
                cfg.modules,
                **{k: v for k, v in data["modules"].items() if k in known},
            )
        for section in ("healing", "targeting", "spells", "cavebot"):
            cls_ = type(getattr(cfg, section))
            if isinstance(data.get(section), dict):
                known = {f for f in cls_.__dataclass_fields__}
                setattr(
                    cfg,
                    section,
                    replace(
                        getattr(cfg, section),
                        **{k: v for k, v in data[section].items() if k in known},
                    ),
                )
        return cfg

    # -- mapping onto real bot config ---------------------------------------

    def to_loop_config(self) -> LoopConfig:
        """Build the real ``moonlight.loop.LoopConfig`` for GameLoop.

        Healing thresholds feed the loop's emergency/normal thresholds and
        the attack combo comes from the targeting config.
        """
        from moonlight.loop import LoopConfig

        return LoopConfig(
            tick_rate=self.tick_rate,
            critical_hp=self.healing.emergency_hp_percent,
            normal_hp=self.healing.normal_hp_percent,
            attack_combo=self.targeting.attack_combo,
            emergency_heal_combo=self.healing.emergency_spell,
            normal_heal_combo=self.healing.normal_spell,
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_config(config: BotConfig, path: Path | str | None = None) -> Path:
    """Write *config* as pretty JSON to *path* (default: data/config.json).

    Creates parent directories. Returns the written path.
    """
    path = Path(path) if path is not None else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_config(path: Path | str | None = None) -> BotConfig:
    """Load a config from *path*, falling back to defaults.

    A missing or invalid file yields a fresh :class:`BotConfig` (the GUI
    must never crash on a half-written config file).
    """
    path = Path(path) if path is not None else default_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BotConfig()
    try:
        return BotConfig.from_dict(data)
    except (TypeError, ValueError):
        return BotConfig()


# ---------------------------------------------------------------------------
# Thread-safe store
# ---------------------------------------------------------------------------


class ConfigStore:
    """Thread-safe holder for the live :class:`BotConfig`.

    Config tabs call :meth:`update` (mutating a copy under the lock) while
    the bot loop thread may call :meth:`snapshot` concurrently. ``snapshot``
    always returns an independent copy, so callers can never mutate live
    state through a stale reference.
    """

    def __init__(self, config: BotConfig | None = None, path: Path | str | None = None) -> None:
        self._lock = threading.RLock()
        self._config = config if config is not None else BotConfig()
        self.path = Path(path) if path is not None else default_config_path()

    @property
    def config(self) -> BotConfig:
        """A copy of the current config (thread-safe)."""
        return self.snapshot()

    def snapshot(self) -> BotConfig:
        """Return a deep-ish copy of the current config under the lock."""
        with self._lock:
            return BotConfig.from_dict(self._config.to_dict())

    def update(self, mutate: Callable[[BotConfig], None]) -> BotConfig:
        """Apply *mutate* to the live config under the lock.

        *mutate* receives a working copy; the copy is committed only if the
        callback returns without raising, so a failed validation (e.g. from
        a tab's "Save") never corrupts the live config. Returns the new
        snapshot.
        """
        with self._lock:
            candidate = BotConfig.from_dict(self._config.to_dict())
            mutate(candidate)
            # Round-trip validation: ensure the result serializes cleanly.
            candidate.to_dict()
            self._config = candidate
            return self.snapshot()

    def save(self) -> Path:
        """Persist the current config to disk; returns the path."""
        with self._lock:
            return save_config(self._config, self.path)

    def reload(self) -> BotConfig:
        """Reload the config from disk; returns the new snapshot."""
        with self._lock:
            self._config = load_config(self.path)
            return self.snapshot()
