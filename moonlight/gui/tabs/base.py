"""Tab base class and small shared widget helpers (T39).

Every tab:

* receives a :class:`moonlight.gui.state_bridge.GuiContext` in its
  constructor (dependency injection — no globals, no display assumptions),
* builds its widgets in :meth:`build` (called once by the app shell),
* updates them in :meth:`on_poll` (called on every state poll, ~10-20 Hz),
* lists its config options in ``options`` (Rule 2 of GUI_REMAKE.MD):
  :meth:`build_options` makes one control per :class:`Option` and
  :meth:`apply` writes them all into ``moonlight_config.json``.

``on_poll`` must stay fast (label/progress-bar updates only) — it runs on
the Tk thread while the bot loop thread keeps ticking in the background.
"""

from __future__ import annotations

import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Literal,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import customtkinter as ctk

from moonlight.gui import theme
from moonlight.gui.loop_factory import load_real_config
from moonlight.setup.config import DEFAULT_CONFIG_FILE, MODULES, BotConfig, ConfigError

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState
    from moonlight.gui.state_bridge import GuiContext

__all__ = ["Option", "Tab", "read_option", "section", "stat_label", "stat_num", "write_option"]


@dataclass(frozen=True)
class Option:
    """One config option shown as one control.

    *field* is a dotted path into :class:`BotConfig`, e.g.
    ``"healing.normal_hp_percent"``. ``"<module>.enabled"`` turns a module
    on or off; writing any other field never changes that.
    """

    label: str
    field: str
    control: Literal["slider", "switch", "entry", "dropdown"]
    min: float | None = None
    max: float | None = None
    choices: list[str] | None = None


def _unwrap(hint: Any) -> Any:
    if get_origin(hint) in (Union, types.UnionType):
        (inner,) = [a for a in get_args(hint) if a is not type(None)]
        return inner
    return hint


def _field_type(path: list[str]) -> Any:
    hint: Any = BotConfig
    for name in path:
        try:
            hint = _unwrap(get_type_hints(hint)[name])
        except (KeyError, TypeError):
            raise ConfigError(f"unknown option field {'.'.join(path)!r}") from None
    return hint


def _cast(value: object, hint: Any, field: str) -> object:
    """Fit a control's value to the field's type (sliders give floats)."""
    try:
        if hint is int and isinstance(value, float):
            return round(value)
        if hint in (int, float) and isinstance(value, str):
            return hint(value.strip())
    except ValueError:
        raise ConfigError(f"{field}: {value!r} is not a number") from None
    return value


def read_option(cfg: BotConfig, field: str) -> object:
    """The value *field* has in *cfg*, using a turned-off module's kept values.

    ``None`` when an off module has no kept values.
    """
    head, *rest = field.split(".")
    if head in MODULES and rest == ["enabled"]:
        return getattr(cfg, head) is not None
    _field_type([head, *rest])
    node: Any = cfg
    if head in MODULES:
        node = getattr(cfg, head) or cfg.off.get(head)
        if node is None:
            return None
    else:
        rest = [head, *rest]
    for name in rest:
        node = getattr(node, name)
    return node


def write_option(cfg: BotConfig, option: Option, value: object) -> BotConfig:
    """A new config with *value* written into *option*'s field.

    Goes through :meth:`BotConfig.from_dict`, so the result is checked like a
    loaded file. A turned-off module keeps the value in ``BotConfig.off``.

    Raises:
        ConfigError: For an unknown field, a value outside *option*'s range,
            or a config that fails validation.
    """
    path = option.field.split(".")
    head = path[0]
    if not (head in MODULES and path[1:] == ["enabled"]):
        value = _cast(value, _field_type(path), option.field)
    if isinstance(value, int | float) and not isinstance(value, bool):
        low, high = option.min, option.max
        if (low is not None and value < low) or (high is not None and value > high):
            raise ConfigError(f"{option.field}: {value} is outside {low}..{high}")
    data = cfg.to_dict()
    node: dict[str, Any] = data["modules"] if head in MODULES else data
    for name in path[:-1]:
        node = node[name]
    node[path[-1]] = value
    return BotConfig.from_dict(data)


def section(parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
    """A titled, inset frame (consistent look across tabs)."""
    box = ctk.CTkFrame(
        parent,
        fg_color=theme.PANEL,
        border_width=1,
        border_color=theme.RAISED,
        corner_radius=theme.RADIUS_CARD,
    )
    ctk.CTkLabel(
        box,
        text=title.upper(),
        anchor="w",
        font=ctk.CTkFont(size=theme.SIZE_TITLE, weight="bold"),
        text_color=theme.TEXT_MUTED,
    ).pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 0))
    return box


def stat_label(parent: ctk.CTkFrame, title: str) -> ctk.CTkLabel:
    """A right-aligned stat label (value updated in on_poll)."""
    return ctk.CTkLabel(
        parent,
        text=f"{title}: --",
        anchor="w",
        font=ctk.CTkFont(family=theme.MONO, size=theme.SIZE_BODY),
    )


def stat_num(stats: dict[str, object], key: str, default: float = 0.0) -> float:
    """Read a numeric field from a loop-stats dict (values are typed object)."""
    value = stats.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


class Tab:
    """Base class for all 10 GUI tabs.

    Subclasses set :attr:`title` and implement :meth:`build`;
    :meth:`on_poll` is a no-op by default.
    """

    title: str = "Tab"
    #: The tab's config options; a tab file sets this to its ``OPTIONS``.
    options: ClassVar[Sequence[Option]] = ()

    def __init__(self, ctx: GuiContext) -> None:
        self.ctx = ctx
        self._readers: dict[str, Callable[[], object]] = {}

    def build(self, parent: ctk.CTkFrame) -> None:
        """Create this tab's widgets inside *parent* (called once)."""
        raise NotImplementedError

    def on_poll(self, snap: WorldState) -> None:
        """Update widgets from a state snapshot (default: no-op)."""
        return None

    def build_options(self, parent: ctk.CTkFrame) -> None:
        """One labelled control per entry in ``options``, filled from the config."""
        cfg = load_real_config()
        for option in self.options:
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=theme.PAD, pady=(theme.UNIT, 0))
            ctk.CTkLabel(
                row,
                text=option.label,
                anchor="w",
                font=ctk.CTkFont(size=theme.SIZE_BODY),
                text_color=theme.TEXT_DIM,
            ).pack(side="left")
            value = read_option(cfg, option.field)
            self._readers[option.field] = _control(row, option, value)

    def apply(self) -> BotConfig:
        """Write every option into the config and save it (Rule 2).

        Raises:
            ConfigError: When a value is refused; nothing is saved then.
        """
        cfg = load_real_config()
        for option in self.options:
            if option.field in self._readers:
                cfg = write_option(cfg, option, self._readers[option.field]())
        cfg.save(Path(DEFAULT_CONFIG_FILE))
        return cfg


def _control(row: ctk.CTkFrame, option: Option, value: object) -> Callable[[], object]:
    """Pack *option*'s control into *row*; return a function reading its value."""
    if option.control == "switch":
        switch = ctk.CTkSwitch(row, text="", progress_color=theme.LIFE)
        if value:
            switch.select()
        switch.pack(side="right")
        return lambda: bool(switch.get())
    if option.control == "dropdown":
        choices = option.choices or []
        menu = ctk.CTkOptionMenu(
            row,
            values=choices,
            fg_color=theme.RAISED,
            button_color=theme.RAISED,
            button_hover_color=theme.HOVER,
        )
        menu.set(str(value) if value is not None else (choices[0] if choices else ""))
        menu.pack(side="right")
        return lambda: menu.get()
    if option.control == "slider":
        low = option.min if option.min is not None else 0.0
        high = option.max if option.max is not None else 100.0
        shown = ctk.CTkLabel(
            row, width=48, anchor="e", font=ctk.CTkFont(family=theme.MONO, size=theme.SIZE_BODY)
        )
        slider = ctk.CTkSlider(
            row,
            from_=low,
            to=high,
            button_color=theme.MANA,
            command=lambda v: shown.configure(text=f"{v:g}"),
        )
        start = value if isinstance(value, int | float) else low
        slider.set(start)
        shown.configure(text=f"{start:g}")
        shown.pack(side="right")
        slider.pack(side="right", padx=theme.UNIT)
        return lambda: slider.get()
    entry = ctk.CTkEntry(row, fg_color=theme.NIGHT, border_color=theme.RAISED)
    entry.insert(0, "" if value is None else str(value))
    entry.pack(side="right")
    return lambda: entry.get()
