"""Central bot configuration (T40).

:class:`BotConfig` aggregates everything needed to build the bot: the
calibration profile name, the serial port, the loop parameters, the
spell -> hotkey mapping and the per-module decision configs. The module
configs are the existing dataclasses from ``moonlight.decision`` (and
:class:`moonlight.bot.loop.LoopConfig`); nothing is re-invented here.

A module is **enabled** when its config is not ``None``.

JSON format (``moonlight_config.json``)::

    {
      "profile": "moonlight_user",         # default profile (F-11)
      "serial_port": null,                 # null = auto-detect the Arduino
      "profiles_dir": null,                # null = moonlight/profiles
      "templates_dir": "data/templates",   # cursor/*.png, loot/*.png
      "routes_dir": "data/waypoints",
      "minimap_start": [x, y, z],          # optional absolute seed (F-03)
      "loop": {"tick_rate": 20.0},         # any LoopConfig field
      "clicker": {"open_loop": false},     # any ClickerConfig field (F-14)
      "hotkeys": {"exura gran": "F1", "exura": "F2"},   # spell -> combo
      "monster_name_source": false,        # F-09: looting needs this true (T34)
      "capacity_source": false,            # F-09: refill needs this true (T34)
      "buff_icon_source": false,           # F-10: buffs needs this true (T34)
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
all raise :class:`ConfigError`. Looting, refill and buffs additionally
require their perception source (``monster_name_source`` /
``capacity_source`` / ``buff_icon_source``) to be true (F-09 / F-10).
Fields left out of a module section take the dataclass defaults.

The default profile is ``moonlight_user`` (F-11); the GUI and
``tools/check_setup.py`` use it when no profile is given.
"""

from __future__ import annotations

import json
import types
from dataclasses import asdict, dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from moonlight.bot.loop import GameLoop, LoopConfig
from moonlight.decision.alarms import AlarmConfig
from moonlight.decision.buffs import BuffManagerConfig
from moonlight.decision.cavebot import Route, RouteError, RouteFileLoader
from moonlight.decision.healing import HealingConfig
from moonlight.decision.looting import LootingConfig
from moonlight.decision.refill import RefillConfig
from moonlight.decision.targeting import TargetingConfig
from moonlight.hardware.clicker import ClickerConfig

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
    "minimap_start",
    "monster_name_source",
    "capacity_source",
    "buff_icon_source",
    "loop",
    "clicker",
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
    #: Clicker mode (F-14): closed loop by default; open loop if T33 HIL
    #: shows the rendered cursor cannot be captured.
    clicker: ClickerConfig = ClickerConfig()
    hotkeys: dict[str, str] | None = None
    healing: HealingConfig | None = None
    targeting: TargetingConfig | None = None
    buffs: BuffManagerConfig | None = None
    alarms: AlarmConfig | None = None
    cavebot: CavebotConfig | None = None
    looting: LootingConfig | None = None
    refill: RefillConfig | None = None
    #: Absolute ``(x, y, z)`` seed for minimap position tracking (F-03).
    #: When set, the minimap reader reports *absolute* world positions
    #: (seed + accumulated relative offset) instead of positions relative to
    #: wherever the bot started. ``None`` keeps the legacy relative behavior.
    minimap_start: tuple[int, int, int] | None = None
    #: Perception sources that are actually wired up (F-09 / F-10). The
    #: capacity reader and the monster-name resolver are deferred (T34), so
    #: the corresponding modules would run against *unpopulated* state. Set
    #: these to true only once a real reader populates the state; ``validate``
    #: refuses to enable looting / refill / buffs without their source.
    monster_name_source: bool = False
    capacity_source: bool = False
    buff_icon_source: bool = False

    @property
    def spell_combos(self) -> dict[str, str]:
        """Spell -> key combo mapping for healing and buffs."""
        return dict(self.hotkeys or {})

    @classmethod
    def default(cls) -> BotConfig:
        """Safe defaults: healing, targeting and alarms on; the rest off."""
        cfg = cls(
            profile="moonlight_user",
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
        for key in (
            "profile",
            "serial_port",
            "profiles_dir",
            "templates_dir",
            "routes_dir",
            "minimap_start",
            "monster_name_source",
            "capacity_source",
            "buff_icon_source",
        ):
            if key in data:
                kwargs[key] = _coerce(data[key], hints[key], key)
        if not kwargs["profile"]:
            raise ConfigError("profile must be a non-empty string")
        if "loop" in data:
            kwargs["loop"] = _build(LoopConfig, data["loop"], "loop")
        if "clicker" in data:
            kwargs["clicker"] = _build(ClickerConfig, data["clicker"], "clicker")
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

    def to_dict(self) -> dict[str, Any]:
        """The JSON :meth:`from_dict` reads; cavebot is saved by its route name."""
        data: dict[str, Any] = {
            f.name: getattr(self, f.name) for f in fields(self) if f.name not in MODULES
        }
        if self.hotkeys is None:
            del data["hotkeys"]
        data["loop"] = asdict(self.loop)
        data["clicker"] = asdict(self.clicker)
        modules: dict[str, Any] = {}
        for name in MODULES:
            cfg = getattr(self, name)
            if cfg is None:
                modules[name] = {"enabled": False}
            elif isinstance(cfg, CavebotConfig):
                modules[name] = {
                    "enabled": True,
                    "route": cfg.route.name,
                    "rope_hotkey": cfg.rope_hotkey,
                    "shovel_hotkey": cfg.shovel_hotkey,
                }
            else:
                modules[name] = {"enabled": True, **asdict(cfg)}
        data["modules"] = modules
        # Round-trip through JSON so Paths become str and tuples become lists.
        result: dict[str, Any] = json.loads(json.dumps(data, default=str))
        return result

    def save(self, path: str | Path) -> None:
        """Write :meth:`to_dict` to *path* as JSON."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

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
            if self.minimap_start is not None:
                start = self.minimap_start
                if len(start) != 3:
                    raise ValueError(f"minimap_start must be (x, y, z), got {start!r}")
                for i, value in enumerate(start):
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        raise ValueError(
                            f"minimap_start[{i}] must be a non-negative int, got {value!r}"
                        )
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
        # F-09: unreachable gates must be honest. Looting and refill read
        # state that no reader populates yet (monster names, capacity). Refuse
        # to enable them until a perception source is wired (T34).
        if self.looting is not None and not self.monster_name_source:
            raise ConfigError(
                "looting is enabled but no monster-name perception source is "
                "configured: set 'monster_name_source' to true once a name "
                "resolver is wired (T34), or disable the looting module"
            )
        if self.refill is not None and not self.capacity_source:
            raise ConfigError(
                "refill is enabled but no capacity perception source is "
                "configured: set 'capacity_source' to true once a capacity "
                "reader is wired (T34), or disable the refill module"
            )
        # F-10: the buffs module reads buff-icon state that no reader
        # populates yet; refuse to enable it until one is wired.
        if self.buffs is not None and not self.buff_icon_source:
            raise ConfigError(
                "buffs is enabled but no buff-icon perception source is "
                "configured: set 'buff_icon_source' to true once buff-icon "
                "perception is wired (T34), or disable the buffs module"
            )
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
