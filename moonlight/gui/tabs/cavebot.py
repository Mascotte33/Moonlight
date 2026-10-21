"""Cavebot tab — route data layer + T22 hook (T39).

Data layer only (per T22 split): load/save a JSON waypoint route
(``data/routes/<name>.json``), inspect the waypoint list, and edit the
refill/deposit parameters in :class:`~moonlight.gui.config_bridge.CavebotParams`.

The cavebot engine itself (``moonlight.decision.cavebot``) is still the T22
placeholder, so the tab shows a clear "T22 pending" badge. When the engine
lands, the hook is already in place: :func:`cavebot_ready` detects a real
:class:`~moonlight.decision.cavebot.Cavebot` class and the tab swaps the
badge to "engine available" — no tab rewrite needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from moonlight.gui import routes
from moonlight.gui.config_bridge import BotConfig, CavebotParams
from moonlight.gui.tabs.base import Tab, section

if TYPE_CHECKING:  # pragma: no cover
    from moonlight.bot.state import WorldState

__all__ = ["CavebotTab"]


def cavebot_ready() -> bool:
    """True once ``moonlight.decision.cavebot`` ships the T22 engine.

    The placeholder module has no ``Cavebot`` class; the real one will.
    """
    try:
        import moonlight.decision.cavebot as cb

        return hasattr(cb, "Cavebot")
    except ImportError:  # pragma: no cover
        return False


class CavebotTab(Tab):
    """Route load/save + waypoint list + refill/deposit params (T22 hook)."""

    title = "Cavebot"

    def build(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        self.lbl_t22 = ctk.CTkLabel(
            parent,
            text="Cavebot engine: T22 pending (data layer only)"
            if not cavebot_ready()
            else "Cavebot engine: available",
            anchor="w",
            font=ctk.CTkFont(weight="bold"),
            text_color="#e0a040",
        )
        self.lbl_t22.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 0))

        body = ctk.CTkFrame(parent, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=8)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=3)
        body.grid_rowconfigure(2, weight=2)

        # -- route selection ------------------------------------------------
        route_box = section(body, "Route")
        route_box.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        route_grid = ctk.CTkFrame(route_box, fg_color="transparent")
        route_grid.pack(fill="both", expand=True)
        self.var_route = ctk.StringVar()
        self.cmb_route = ctk.CTkComboBox(
            route_grid, values=self._available_routes(), variable=self.var_route,
            state="readonly",
        )
        self.cmb_route.grid(row=0, column=1, sticky="w", padx=8, pady=6)
        self.btn_refresh = ctk.CTkButton(route_grid, text="Refresh", width=90)
        self.btn_refresh.grid(row=0, column=2, padx=8, pady=6)
        self.btn_save_route = ctk.CTkButton(route_grid, text="Save route", width=100)
        self.btn_save_route.grid(row=0, column=3, padx=8, pady=6)
        self.lbl_route = ctk.CTkLabel(route_grid, text="", anchor="w")
        self.lbl_route.grid(row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 6))
        self.btn_refresh.configure(command=self._on_refresh)
        self.btn_save_route.configure(command=self._on_save_route)
        self.cmb_route.configure(command=self._on_select_route)
        self._refresh_routes()

        # -- waypoint list ---------------------------------------------------
        wp_box = section(body, "Waypoints")
        wp_box.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        self.wp_list = ctk.CTkTextbox(wp_box, height=120, wrap="none")
        self.wp_list.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.wp_list.configure(state="disabled")
        self._set_wp("no route selected")

        # -- refill / deposit params ------------------------------------------
        param_box = section(body, "Refill / deposit")
        param_box.grid(row=3, column=0, sticky="nsew")
        param_grid = ctk.CTkFrame(param_box, fg_color="transparent")
        param_grid.pack(fill="both", expand=True)
        param_grid.grid_columnconfigure(1, weight=1)
        self.var_refill_hp = ctk.StringVar()
        self.var_refill_mp = ctk.StringVar()
        self.var_deposit = ctk.BooleanVar(value=False)
        self.var_loop = ctk.BooleanVar(value=True)
        ctk.CTkLabel(param_grid, text="Refill HP threshold (%)").grid(
            row=0, column=0, sticky="w", padx=8, pady=4
        )
        self.ent_refill_hp = ctk.CTkEntry(param_grid, textvariable=self.var_refill_hp, width=120)
        self.ent_refill_hp.grid(row=0, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(param_grid, text="Refill MP threshold (%)").grid(
            row=1, column=0, sticky="w", padx=8, pady=4
        )
        self.ent_refill_mp = ctk.CTkEntry(param_grid, textvariable=self.var_refill_mp, width=120)
        self.ent_refill_mp.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        self.chk_deposit = ctk.CTkCheckBox(
            param_grid, text="Deposit at full capacity", variable=self.var_deposit
        )
        self.chk_deposit.grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        self.chk_loop = ctk.CTkCheckBox(param_grid, text="Loop route", variable=self.var_loop)
        self.chk_loop.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        self.btn_apply = ctk.CTkButton(param_grid, text="Apply parameters")
        self.btn_apply.grid(row=4, column=0, sticky="w", padx=8, pady=6)
        self.btn_apply.configure(command=self._on_apply_params)
        self.lbl_params = ctk.CTkLabel(param_grid, text="", anchor="w")
        self.lbl_params.grid(row=5, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
        self._load_params(self.ctx.active_config())

        self._route: routes.Route | None = None

    # -- route helpers -------------------------------------------------------

    def _available_routes(self) -> list[str]:
        return routes.list_routes()

    def _set_wp(self, text: str) -> None:
        self.wp_list.configure(state="normal")
        self.wp_list.delete("1.0", "end")
        self.wp_list.insert("end", text)
        self.wp_list.configure(state="disabled")

    def _refresh_routes(self) -> None:
        names = self._available_routes()
        self.cmb_route.configure(values=names)
        if names and not self.var_route.get():
            self.var_route.set(names[0])
            self._load_route_by_name(names[0])

    def _on_refresh(self) -> None:
        self._refresh_routes()
        self.lbl_route.configure(text=f"{len(self._available_routes())} route(s) on disk")

    def _load_route_by_name(self, name: str) -> None:
        try:
            route = routes.load_route(routes.route_path(name))
        except (OSError, ValueError) as exc:
            self._route = None
            self._set_wp(f"failed to load {name}: {exc}")
            return
        self._route = route
        lines = [
            f"{i + 1:>3}. {wp.type:<6} ({wp.x:>4}, {wp.y:>4})  t={wp.ts:.3f}"
            for i, wp in enumerate(route.waypoints)
        ]
        header = f"route '{route.name}': {len(route.waypoints)} waypoints"
        self._set_wp(header + "\n" + "\n".join(lines))

    def _on_select_route(self, _value: object) -> None:
        name = self.var_route.get()
        if name:
            self._load_route_by_name(name)

    def _on_save_route(self) -> None:
        name = self.var_route.get().strip()
        if not name:
            self.lbl_route.configure(text="no route selected")
            return
        if self._route is None:
            self._route = routes.Route(name=name, created_at=routes.now_ts())
        self._route.name = name
        try:
            self._route.validate()
        except ValueError as exc:
            self.lbl_route.configure(text=f"invalid route: {exc}")
            return
        path = routes.save_route(self._route)
        self.lbl_route.configure(
            text=f"saved {len(self._route.waypoints)} waypoints -> {path.name}"
        )
        self._refresh_routes()

    def _load_params(self, cfg: BotConfig) -> None:
        self.var_refill_hp.set(str(cfg.cavebot.refill_hp_percent))
        self.var_refill_mp.set(str(cfg.cavebot.refill_mp_percent))
        self.var_deposit.set(cfg.cavebot.deposit_at_full)
        self.var_loop.set(cfg.cavebot.loop_route)

    def _on_apply_params(self) -> None:
        try:
            hp = float(self.var_refill_hp.get())
            mp = float(self.var_refill_mp.get())
        except ValueError:
            self.lbl_params.configure(text="thresholds must be numbers")
            return

        def mutate(cfg: BotConfig) -> None:
            cfg.cavebot = CavebotParams(
                route_name=cfg.cavebot.route_name,
                refill_hp_percent=hp,
                refill_mp_percent=mp,
                deposit_at_full=bool(self.var_deposit.get()),
                loop_route=bool(self.var_loop.get()),
            )

        try:
            self.ctx.config_store.update(mutate)
        except ValueError as exc:
            self.lbl_params.configure(text=f"invalid: {exc}")
            return
        self.lbl_params.configure(text="applied (cavebot params updated)")

    def on_poll(self, snap: WorldState) -> None:
        # Live capacity readout next to the deposit option (T22 consumes this).
        pct = snap.capacity.percent
        self.chk_deposit.configure(text=f"Deposit at full capacity (cap {pct:.0f}%)")
