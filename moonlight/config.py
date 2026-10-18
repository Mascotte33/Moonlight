"""Central bot configuration (T40).

:class:`BotConfig` aggregates everything needed to build the bot: the
calibration profile name, the serial port, the loop parameters, the
spell -> hotkey mapping and the per-module decision configs. The module
configs are the existing dataclasses from ``moonlight.decision`` (and
:class:`moonlight.loop.LoopConfig`); nothing is re-invented here.

A module is **enabled** when its config is not ``None``.

JSON format (``moonlight_config.json``)::

    {
      "profile": "default",
      "serial_port": null,                 # null = auto-detect the Arduino
      "profiles_dir": null,                # null = moonlight/profiles
      "templates_dir": "data/templates",   # cursor/*.png, loot/*.png
      "routes_dir": "data/waypoints",
      "loop": {"tick_rate": 20.0},         # any LoopConfig field
      "hotkeys": {"exura gran": "F1", "exura": "F2"},   # spell -> combo
      "modules": {
        "healing":   {"enabled": true, "normal_hp_percent": 70.0},
        "targeting": {"enabled": true},
        "buffs":     {"enabled": false},
        "alarms":    {"enabled": true},
        "cavebot":   {"enabled": false, "route": "rotworms"},
        "looting":   {"enabled": false},
        "refill":    {"enabled": false}
      }
    }

Fail closed: ``profile``, ``modules`` and every module's ``enabled`` flag
are required; unknown keys, wrong types, invalid values, a spell without a
hotkey, a missing cavebot route file, or refill without cavebot+looting
all raise :class:`ConfigError`. Fields left out of a module section take
the dataclass defaults.
"""

from __future__ import annotations

import json
import types
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from moonlight.decision.alarms import AlarmConfig
from moonlight.decision.buffs import BuffManagerConfig
from moonlight.decision.cavebot import Route, RouteError, RouteFileLoader
from moonlight.decision.healing import HealingConfig
from moonlight.decision.looting import LootingConfig
from moonlight.decision.refill import RefillConfig
from moonlight.decision.targeting import TargetingConfig
from moonlight.loop import GameLoop, LoopConfig

__all__ = [
    "DEFAULT_CONFIG_FILE",
    "MODULES",
    "BotConfig",
    "CavebotConfig",
    "ConfigError",
]

#: Default config file name (looked up in the current directory).
DEFAULT_CONFIG_FILE = "moonlight_config.json"

#: Module section names, in ``modules`` of the JSON file.
MODULES = ("healing", "targeting", "buffs", "alarms", "cavebot", "looting", "refill")

_TOP_KEYS = {
    "profile",
    "serial_port",
    "profiles_dir",
    "templates_dir",
    "routes_dir",
    "loop",
    "hotkeys",
    "modules",
}


class ConfigError(ValueError):
    """The bot config is missing, unreadable or invalid."""


@dataclass(frozen=True)
class CavebotConfig:
    """Cavebot settings: the loaded :class:`Route` plus its tool hotkeys."""

    route: Route
    rope_hotkey: str = "F9"
    shovel_hotkey: str = "F10"


@dataclass(frozen=True)
class BotConfig:
    """Everything needed to build and run the bot (see the module docstring)."""

    profile: str
    serial_port: str | None = None
    profiles_dir: Path | None = None
    templates_dir: Path = Path("data/templates")
    routes_dir: Path = Path("data/waypoints")
    loop: LoopConfig = LoopConfig()
    hotkeys: dict[str, str] | None = None
    healing: HealingConfig | None = None
    targeting: TargetingConfig | None = None
    buffs: BuffManagerConfig | None = None
    alarms: AlarmConfig | None = None
    cavebot: CavebotConfig | None = None
    looting: LootingConfig | None = None
    refill: RefillConfig | None = None

    @property
    def spell_combos(self) -> dict[str, str]:
        """Spell -> key combo mapping for healing and buffs."""
        return dict(self.hotkeys or {})

    @classmethod
    def default(cls) -> BotConfig:
        """Safe defaults: healing, targeting and alarms on; the rest off."""
        cfg = cls(
            profile="default",
            hotkeys={"exura gran": "F1", "exura": "F2"},
            healing=HealingConfig(),
            targeting=TargetingConfig(),
            alarms=AlarmConfig(),
        )
        cfg.validate()
        return cfg

    @classmethod
    def load(cls, path: str | Path) -> BotConfig:
        """Load and validate a config file.

        Raises:
            ConfigError: If the file is missing, not JSON, or invalid.
        """
        p = Path(path)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot read config {p}: {exc}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: object) -> BotConfig:
        """Build and validate a config from decoded JSON.

        Raises:
            ConfigError: On any missing, unknown or malformed field.
        """
        data = _require_dict(raw, "config")
        _reject_unknown(data, _TOP_KEYS, "config")
        hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        if "profile" not in data:
            raise ConfigError("config: missing required key 'profile'")
        for key in ("profile", "serial_port", "profiles_dir", "templates_dir", "routes_dir"):
            if key in data:
                kwargs[key] = _coerce(data[key], hints[key], key)
        if not kwargs["profile"]:
            raise ConfigError("profile must be a non-empty string")
        if "loop" in data:
            kwargs["loop"] = _build(LoopConfig, data["loop"], "loop")
        if "hotkeys" in data:
            kwargs["hotkeys"] = _coerce(data["hotkeys"], dict[str, str], "hotkeys")

        modules = _require_dict(data.get("modules"), "modules")
        _reject_unknown(modules, set(MODULES), "modules")
        routes_dir = kwargs.get("routes_dir", Path("data/waypoints"))
        for name in MODULES:
            if name not in modules:
                raise ConfigError(f"modules: missing required section {name!r}")
            section = dict(_require_dict(modules[name], f"modules.{name}"))
            enabled = section.pop("enabled", None)
            if not isinstance(enabled, bool):
                raise ConfigError(f"modules.{name}.enabled must be true or false")
            if not enabled:
                continue
            if name == "cavebot":
                kwargs[name] = _cavebot(section, routes_dir)
            else:
                kwargs[name] = _coerce(section, hints[name], f"modules.{name}")

        cfg = cls(**kwargs)
        cfg.validate()
        return cfg

    def validate(self) -> BotConfig:
        """Cross-field checks; return self.

        Raises:
            ConfigError: If a value or module combination is invalid.
        """
        try:
            GameLoop._validate_config(self.loop)
            for cfg in (self.healing, self.targeting, self.buffs, self.alarms):
                if cfg is not None:
                    cfg.validate()
            for spell, combo in self.spell_combos.items():
                if not combo or any(ch.isspace() for ch in combo):
                    raise ValueError(f"hotkey for {spell!r} must be a combo without spaces")
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        # An unmapped spell would be decided every tick but never cast.
        spells: list[str] = []
        if self.healing is not None:
            h = self.healing
            spells += [h.emergency_spell, h.normal_spell]
            if h.cure_conditions:
                spells.append(h.cure_spell)
        if self.buffs is not None:
            spells += [b.spell for b in self.buffs.buffs]
        missing = sorted({s for s in spells if s not in self.spell_combos})
        if missing:
            raise ConfigError(f"no hotkey configured for spell(s): {missing}")
        if self.refill is not None and (self.cavebot is None or self.looting is None):
            raise ConfigError("refill needs the cavebot and looting modules enabled")
        return self


# -- parsing helpers ----------------------------------------------------------


def _require_dict(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: must be a JSON object")
    return value


def _reject_unknown(data: dict[str, Any], known: set[str], where: str) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}")


def _cavebot(section: dict[str, Any], routes_dir: Path) -> CavebotConfig:
    name = section.pop("route", None)
    if not isinstance(name, str) or not name:
        raise ConfigError("modules.cavebot.route must be a non-empty route name")
    try:
        route = RouteFileLoader.load(routes_dir / f"{name}.json")
    except RouteError as exc:
        raise ConfigError(f"modules.cavebot: {exc}") from exc
    _reject_unknown(section, {"rope_hotkey", "shovel_hotkey"}, "modules.cavebot")
    keys = {k: _coerce(v, str, f"modules.cavebot.{k}") for k, v in section.items()}
    return CavebotConfig(route=route, **keys)


def _build(cls: Any, raw: object, where: str) -> Any:
    """Instantiate dataclass *cls* from *raw*, type-checking every given field."""
    data = _require_dict(raw, where)
    names = {f.name for f in fields(cls)}
    _reject_unknown(data, names, where)
    hints = get_type_hints(cls)
    kwargs = {k: _coerce(v, hints[k], f"{where}.{k}") for k, v in data.items()}
    try:
        obj = cls(**kwargs)
        validate = getattr(obj, "validate", None)
        if validate is not None:
            validate()
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: {exc}") from exc
    return obj


def _coerce(value: object, hint: Any, where: str) -> Any:
    """Check *value* against type *hint* (JSON -> Python), or raise ConfigError."""
    origin = get_origin(hint)
    args = get_args(hint)
    if origin in (Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        (inner,) = [a for a in args if a is not type(None)]
        return _coerce(value, inner, where)
    if hint is float and isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if hint in (int, str, bool):
        if isinstance(value, hint) and (hint is bool or not isinstance(value, bool)):
            return value
    elif hint is Path and isinstance(value, str):
        return Path(value)
    elif origin in (list, tuple) and isinstance(value, list):
        items = [_coerce(v, args[0], f"{where}[{i}]") for i, v in enumerate(value)]
        return items if origin is list else tuple(items)
    elif origin is dict and isinstance(value, dict):
        return {
            _coerce(k, args[0], where): _coerce(v, args[1], f"{where}.{k}")
            for k, v in value.items()
        }
    elif is_dataclass(hint) and isinstance(value, dict):
        return _build(hint, value, where)
    raise ConfigError(f"{where}: expected {getattr(hint, '__name__', hint)}, got {value!r}")
