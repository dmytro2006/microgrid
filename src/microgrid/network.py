"""Model rozpływu mocy AC sieci 110/15 kV Krakowa (pandapower) z węzłami mikrosieci.

Budowa (raz, w ``GridModel.__init__``):
  - szyna 110 kV każdej stacji WN z OSM, linie 110 kV z ``hv_branches`` (``circuits`` → ``parallel``),
  - stacje ``pse`` = sieć krajowa (``ext_grid``, u = ``EXT_GRID_VM_PU``),
  - GPZ dzielnicowy (``kind == "gpz"``, nazwa „GPZ …”): transformator 2 × 25 MVA 110/15 kV, szyna 15 kV
    i obciążenie tła dzielnicy,
  - węzeł mikrosieci: kabel 15 kV GPZ → środek (½ długości) → węzeł (½), w środku obciążenie tła linii,
    transformator 15/0,4 kV o mocy przyłącza i szyna nN (PCC),
  - Elektrociepłownia Łęg i ZTPO jako ``sgen`` na szynach 110 kV, farma wiatrowa na 15 kV najbliższego GPZ.

W kolejnych krokach (``set_step``) zmieniane są tylko moce i stan transformatorów GPZ – bez przebudowy sieci;
``solve`` startuje z wyników poprzedniego kroku.
"""

from __future__ import annotations

import importlib.util
import math
import warnings
from dataclasses import dataclass

import numpy as np
import pandapower as pp
from pandapower.powerflow import LoadflowNotConverged

from .config import (
    BG_COS_PHI,
    EXT_GRID_VM_PU,
    FEEDER_BG_PEAK_KW,
    GPZ_BG_PEAK_KW,
    ROUTE_FACTOR,
    WIND_FARM_SITE,
    SiteConfig,
)
from .grid_osm import PowerData
from .grid_topology import Branch, hv_branches, km, nearest_district_gpz
from .grid_topology import site_gpz as _site_gpz

LINE_OH_110 = "243-AL1/39-ST1A 110.0"
LINE_CABLE_110 = "N2XS(FL)2Y 1x240 RM/35 64/110 kV"
LINE_CABLE_15 = "NA2XS2Y 1x240 RM/25 12/20 kV"
CHP_STATION = "GPZ Łęg"
ZTPO_STATION = "GPZ ZTPO"
# transformator GPZ: 2 × 25 MVA 110/15 kV, uk = 11%, przekładnia 1,0 (straty: typowe dla 25 MVA)
GPZ_TRAFO = dict(sn_mva=25.0, vn_hv_kv=110.0, vn_lv_kv=15.0, vk_percent=11.0, vkr_percent=0.41,
                 pfe_kw=14.0, i0_percent=0.07, parallel=2)
# transformator węzła: Sn = P_max / 0,95, 15/0,4 kV, uk = 6%
NODE_TRAFO = dict(vn_hv_kv=15.0, vn_lv_kv=0.4, vk_percent=6.0, vkr_percent=1.2, pfe_kw=0.0, i0_percent=0.0)
NODE_COS_PHI = 0.95
_MIN_KM = 0.01
_TAN_BG = math.tan(math.acos(BG_COS_PHI))
_NUMBA = importlib.util.find_spec("numba") is not None  # bez numby – runpp(numba=False), bez ostrzeżenia


def is_district_gpz(kind: str, name: str) -> bool:
    """GPZ dzielnicowy 110/15 kV (stacje trakcyjne i PSE nie mają tła ani szyny 15 kV)."""
    return kind == "gpz" and name.startswith("GPZ ")


@dataclass
class StepResult:
    pf_ok: bool
    v_node: dict[str, float]           # napięcie szyny nN węzła [p.u.]
    feeder_loading: dict[str, float]   # maks. obciążenie odcinków linii 15 kV węzła [%]
    trafo_loading: dict[str, float]    # obciążenie transformatora węzła [%]
    hv_loading: np.ndarray             # obciążenie linii 110 kV [%], kolejność ``GridModel.branches``
    gpz_loading: dict[str, float]      # obciążenie transformatora GPZ [%]
    losses_kw: float                   # straty w liniach i transformatorach [kW]
    pse_import_kw: float               # pobór z sieci krajowej (stacje PSE) [kW]


class GridModel:
    """Sieć 110/15 kV budowana raz; ``set_step`` zmienia moce, ``solve`` liczy rozpływ."""

    def __init__(self, sites: list[SiteConfig], data: PowerData, farm: bool):
        self.branches: list[Branch] = hv_branches(data)
        self.site_gpz: dict[str, str] = {s.name: _site_gpz(s, data).name for s in sites}
        subs = list(data.substations)
        self.gpz_names: list[str] = [s.name for s in subs if is_district_gpz(s.kind, s.name)]
        self.site_names: list[str] = [s.name for s in sites]
        self.farm = farm

        net = pp.create_empty_network(sn_mva=1.0)
        self._bus110 = {s.name: pp.create_bus(net, vn_kv=110.0, name=s.name) for s in subs}
        for s in subs:
            if s.kind == "pse":
                pp.create_ext_grid(net, self._bus110[s.name], vm_pu=EXT_GRID_VM_PU, name=s.name)
        self._hv_lines = np.array([
            pp.create_line(net, self._bus110[b.a], self._bus110[b.b], length_km=max(b.length_km, _MIN_KM),
                           std_type=LINE_CABLE_110 if b.cable else LINE_OH_110, parallel=b.circuits,
                           name=f"{b.a} – {b.b}")
            for b in self.branches], dtype=int)

        # GPZ dzielnicowe: trafo, szyna 15 kV, tło
        self._bus15: dict[str, int] = {}
        gpz_trafos = []
        for name in self.gpz_names:
            b15 = pp.create_bus(net, vn_kv=15.0, name=f"{name} 15 kV")
            self._bus15[name] = b15
            gpz_trafos.append(pp.create_transformer_from_parameters(
                net, self._bus110[name], b15, name=name, **GPZ_TRAFO))
        self._gpz_trafos = np.array(gpz_trafos, dtype=int)
        self._gpz_bg_loads = np.array([pp.create_load(net, self._bus15[n], p_mw=0.0, name=f"tło {n}")
                                       for n in self.gpz_names], dtype=int)

        # węzły mikrosieci: linia 15 kV z tłem w połowie, transformator, szyna nN
        by_name = {s.name: s for s in subs}
        lines_a, lines_b, node_trafos, node_buses, feeder_loads, node_loads = [], [], [], [], [], []
        for site in sites:
            g = by_name[self.site_gpz[site.name]]
            if g.name not in self._bus15:
                raise KeyError(f"GPZ {g.name!r} węzła {site.name!r} nie jest GPZ dzielnicowym 110/15 kV")
            half = max(km((site.lat, site.lon), (g.lat, g.lon)) * ROUTE_FACTOR, 2 * _MIN_KM) / 2
            mid = pp.create_bus(net, vn_kv=15.0, name=f"{site.name} – linia 15 kV")
            mv = pp.create_bus(net, vn_kv=15.0, name=f"{site.name} 15 kV")
            lv = pp.create_bus(net, vn_kv=0.4, name=f"{site.name} nN")
            lines_a.append(pp.create_line(net, self._bus15[g.name], mid, length_km=half, std_type=LINE_CABLE_15,
                                          name=f"{g.name} – {site.name} (1)"))
            lines_b.append(pp.create_line(net, mid, mv, length_km=half, std_type=LINE_CABLE_15,
                                          name=f"{g.name} – {site.name} (2)"))
            node_trafos.append(pp.create_transformer_from_parameters(
                net, mv, lv, sn_mva=site.p_max_kw / NODE_COS_PHI / 1000, name=site.name, **NODE_TRAFO))
            node_buses.append(lv)
            feeder_loads.append(pp.create_load(net, mid, p_mw=0.0, name=f"tło linii {site.name}"))
            node_loads.append(pp.create_load(net, lv, p_mw=0.0, name=site.name))
        self._feeder_a = np.array(lines_a, dtype=int)
        self._feeder_b = np.array(lines_b, dtype=int)
        self._node_trafos = np.array(node_trafos, dtype=int)
        self._node_buses = np.array(node_buses, dtype=int)
        self._feeder_bg_loads = np.array(feeder_loads, dtype=int)
        self._node_loads = np.array(node_loads, dtype=int)

        # źródła: elektrociepłownia, ZTPO (110 kV), farma wiatrowa (15 kV najbliższego GPZ)
        self._chp = pp.create_sgen(net, self._bus110[CHP_STATION], p_mw=0.0, name="Elektrociepłownia Łęg")
        self._ztpo = pp.create_sgen(net, self._bus110[ZTPO_STATION], p_mw=0.0, name="ZTPO")
        farm_xy = (WIND_FARM_SITE["lat"], WIND_FARM_SITE["lon"])
        self.farm_gpz: str = nearest_district_gpz(*farm_xy, data).name
        self._farm = pp.create_sgen(net, self._bus15[self.farm_gpz], p_mw=0.0, name=WIND_FARM_SITE["name"],
                                    in_service=farm)

        self.net = net
        # pozycje wierszy w tabelach (dostęp tablicowy w każdym kroku)
        self._load_pos = {k: net.load.index.get_indexer(v) for k, v in (
            ("gpz", self._gpz_bg_loads), ("feeder", self._feeder_bg_loads), ("node", self._node_loads))}
        self._sgen_pos = net.sgen.index.get_indexer([self._chp, self._ztpo, self._farm])
        self._gpz_trafo_pos = net.trafo.index.get_indexer(self._gpz_trafos)
        self._init = "auto"  # pierwszy krok / po braku zbieżności

    def set_step(self, p_node_kw: dict[str, float], bg: float, chp_kw: float, ztpo_kw: float,
                 farm_kw: float, gpz_out: frozenset[str]) -> None:
        """Ustawia moce kroku: ``p_node_kw`` > 0 pobór węzła (< 0 oddawanie), ``bg`` – mnożnik tła (0–1),
        ``gpz_out`` – GPZ, których transformatory są wyłączone (awaria)."""
        net = self.net
        p = np.empty(len(net.load))
        q = np.zeros(len(net.load))
        p[self._load_pos["gpz"]] = GPZ_BG_PEAK_KW * bg / 1000
        p[self._load_pos["feeder"]] = FEEDER_BG_PEAK_KW * bg / 1000
        p[self._load_pos["node"]] = [p_node_kw.get(n, 0.0) / 1000 for n in self.site_names]
        q[self._load_pos["gpz"]] = p[self._load_pos["gpz"]] * _TAN_BG
        q[self._load_pos["feeder"]] = p[self._load_pos["feeder"]] * _TAN_BG
        net.load["p_mw"] = p
        net.load["q_mvar"] = q

        ps = np.zeros(len(net.sgen))
        ps[self._sgen_pos] = [chp_kw / 1000, ztpo_kw / 1000, farm_kw / 1000 if self.farm else 0.0]
        net.sgen["p_mw"] = ps

        ins = net.trafo["in_service"].to_numpy(copy=True)
        ins[self._gpz_trafo_pos] = [n not in gpz_out for n in self.gpz_names]
        net.trafo["in_service"] = ins

    def _runpp(self, init: str) -> None:
        with warnings.catch_warnings():
            # pandapower ostrzega m.in. o braku numby i o FutureWarning pandas – nie zaśmiecamy wyjścia
            warnings.filterwarnings("ignore", module=r"pandapower(\.|$)")
            warnings.filterwarnings("ignore", category=FutureWarning, module=r"pandas(\.|$)")
            pp.runpp(self.net, init=init, numba=_NUMBA, calculate_voltage_angles=True)

    def solve(self) -> StepResult:
        """Rozpływ mocy AC; brak zbieżności → ``pf_ok=False`` i wartości NaN (bez wyjątku)."""
        tries = [self._init] if self._init != "results" else ["results", "flat"]
        for init in tries:
            try:
                self._runpp(init)
            except (LoadflowNotConverged, np.linalg.LinAlgError,
                    ValueError, ZeroDivisionError):
                continue
            if self.net.converged:
                self._init = "results"
                return self._result()
        self._init = "auto"
        return self._nan_result()

    def _result(self) -> StepResult:
        net = self.net
        vm = net.res_bus["vm_pu"].to_numpy()
        bus_pos = net.bus.index.get_indexer(self._node_buses)
        ll = net.res_line["loading_percent"].to_numpy()
        line_pos = net.line.index
        fa = ll[line_pos.get_indexer(self._feeder_a)]
        fb = ll[line_pos.get_indexer(self._feeder_b)]
        tl = net.res_trafo["loading_percent"].to_numpy()
        tr_pos = net.trafo.index
        node_tl = tl[tr_pos.get_indexer(self._node_trafos)]
        gpz_tl = tl[self._gpz_trafo_pos]
        losses = (net.res_line["pl_mw"].sum() + net.res_trafo["pl_mw"].sum()) * 1000
        return StepResult(
            pf_ok=True,
            v_node=dict(zip(self.site_names, vm[bus_pos].tolist())),
            feeder_loading=dict(zip(self.site_names, np.fmax(fa, fb).tolist())),
            trafo_loading=dict(zip(self.site_names, node_tl.tolist())),
            hv_loading=ll[line_pos.get_indexer(self._hv_lines)].copy(),
            gpz_loading=dict(zip(self.gpz_names, gpz_tl.tolist())),
            losses_kw=float(losses),
            pse_import_kw=float(net.res_ext_grid["p_mw"].sum() * 1000),
        )

    def _nan_result(self) -> StepResult:
        nan = float("nan")
        nodes = dict.fromkeys(self.site_names, nan)
        return StepResult(
            pf_ok=False, v_node=dict(nodes), feeder_loading=dict(nodes), trafo_loading=dict(nodes),
            hv_loading=np.full(len(self.branches), nan), gpz_loading=dict.fromkeys(self.gpz_names, nan),
            losses_kw=nan, pse_import_kw=nan,
        )
