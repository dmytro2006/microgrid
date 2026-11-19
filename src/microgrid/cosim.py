"""Wspólna symulacja wszystkich węzłów mikrosieci w sieci 110/15 kV Krakowa.

Krok k (jeden przebieg na strategię, wspólne środowisko SimPy):
  1. ``propose`` każdego węzła (lokalne EMS bez zmian),
  2. moc PCC węzłów → ``GridModel.set_step`` → rozpływ mocy (pomijany przy awarii wszystkich węzłów),
  3. (``network_control``) koordynator sieciowy: węzeł naruszający ograniczenia (linia 15 kV lub
     transformator > 100%, napięcie nN poza 0,90–1,10 p.u.) dostaje żądanie zmiany mocy PCC;
     eksport: ładowanie BESS → ładowanie EV → ograniczenie PV/wiatru, pobór: rozładowanie BESS →
     dławienie ładowania EV; ponowny rozpływ (maks. ``MAX_COORD_ITER`` razy, tylko przy naruszeniu),
  4. ``commit`` z kolumnami sieciowymi (napięcie, obciążenia, ``net_violation``).

Naruszenia na 110 kV i w GPZ są tylko raportowane (domena OSD).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import simpy

from .config import SimConfig, SiteConfig
from .ev_generator import TICK_EPS
from .grid_environment import _shift, pcc_balance
from .grid_topology import Branch
from .network import GridModel, StepResult
from .simulation import Proposal, Results, SiteSim, parallel_map

V_MIN_PU, V_MAX_PU = 0.90, 1.10   # dopuszczalne napięcie szyny nN węzła
LOADING_MAX = 100.0               # dopuszczalne obciążenie linii 15 kV i transformatora węzła [%]
COORD_MARGIN = 1.02               # zapas żądanej zmiany mocy
MAX_COORD_ITER = 3                # maks. liczba ponownych rozpływów w kroku


@dataclass
class NetworkResults:
    strategy: str
    times: pd.Series
    branches: list[Branch]
    hv_loading: np.ndarray            # obciążenie linii 110 kV [%] (n_steps × n_branches)
    gpz_loading: pd.DataFrame         # obciążenie transformatorów GPZ [%] (kolumny = GPZ)
    losses_kw: pd.Series              # straty w sieci [kW]
    pse_import_kw: pd.Series          # pobór z sieci krajowej [kW]
    chp_kw: pd.Series                 # produkcja Elektrociepłowni Łęg [kW]
    pf_ok: pd.Series                  # rozpływ policzony i zbieżny
    internal_exchange_kw: pd.Series   # min(Σ eksportów, Σ poborów) węzłów [kW]
    site_gpz: dict[str, str]


def pcc_kw(prop: Proposal) -> float:
    """Moc PCC propozycji (> 0 pobór) – tak, jak policzy ją ``SiteSim.commit`` przy dostępnej sieci."""
    if not prop.grid_ok:
        return 0.0
    gen = prop.pv + prop.wind
    curt = min(prop.curt_net, gen) if prop.curt_net > 0 and gen > 0 else 0.0
    return pcc_balance(prop.load, sum(prop.p_ev.values()), prop.p_bess, gen - curt, 0.0)


def node_violation(res: StepResult, name: str) -> bool:
    """Naruszenie ograniczeń na poziomie węzła (NaN – węzeł odłączony / brak rozpływu – to nie naruszenie)."""
    v = res.v_node[name]
    return (res.feeder_loading[name] > LOADING_MAX or res.trafo_loading[name] > LOADING_MAX
            or v > V_MAX_PU or v < V_MIN_PU)


def coordinate(props: dict[str, Proposal], res: StepResult, model: GridModel) -> dict[str, float]:
    """Wymagana zmiana mocy PCC węzłów naruszających ograniczenia [kW] (ujemna = mniej poboru/eksportu
    w kierunku przepływu). Obciążenie L > 100% → |P|·(1 − 100/L)·1,02; V > 1,10 przy eksporcie →
    |P|·(V − 1,10)/(V − 1)·1,02; V < 0,90 przy poborze → |P|·(0,90 − V)/(1 − V)·1,02."""
    out: dict[str, float] = {}
    for name in model.site_names:
        prop = props.get(name)
        if prop is None or not prop.grid_ok or not node_violation(res, name):
            continue
        p = pcc_kw(prop)
        a = abs(p)
        need = 0.0
        load_pct = float(np.fmax(res.feeder_loading[name], res.trafo_loading[name]))
        if load_pct > LOADING_MAX:
            need = max(need, a * (1.0 - LOADING_MAX / load_pct) * COORD_MARGIN)
        v = res.v_node[name]
        if v > V_MAX_PU and p < 0:
            need = max(need, a * (v - V_MAX_PU) / (v - 1.0) * COORD_MARGIN)
        if v < V_MIN_PU and p > 0:
            need = max(need, a * (V_MIN_PU - v) / (1.0 - v) * COORD_MARGIN)
        if need > 0:
            out[name] = -need
    return out


def apply_correction(prop: Proposal, delta_kw: float) -> float:
    """Koryguje propozycję o ``|delta_kw|`` w stronę mniejszego przepływu przez PCC (w granicach fizyki).
    Zwraca niezrealizowaną część żądania [kW]."""
    need = abs(delta_kw)
    if need <= 0 or not prop.grid_ok:
        return need
    if pcc_kw(prop) < 0:  # eksport → więcej poboru: BESS ładowanie → EV ładowanie → ograniczenie OZE
        c = min(need, max(0.0, prop.b_ch - prop.p_bess))
        prop.p_bess += c
        need -= c
        need -= _shift(prop.p_ev, {i: prop.ch_max[i] - v for i, v in prop.p_ev.items()
                                   if prop.ch_max[i] - v > 0}, need)
        gen = prop.pv + prop.wind
        if need > 0 and gen > 0:
            c = min(need, gen - prop.curt_net)
            prop.curt_net += c
            need -= c
    else:  # pobór → mniej poboru: BESS rozładowanie → dławienie ładowania EV
        d = min(need, max(0.0, prop.p_bess + prop.b_dis))
        prop.p_bess -= d
        need -= d
        need -= _shift(prop.p_ev, {i: -v for i, v in prop.p_ev.items() if v > 0}, need)
    # zabezpieczenie przed błędami zaokrągleń (commit nie sprawdza granic)
    prop.p_bess = min(max(prop.p_bess, -prop.b_dis), prop.b_ch)
    for i, v in prop.p_ev.items():
        prop.p_ev[i] = min(max(v, -prop.dis_max[i]), prop.ch_max[i])
    return max(need, 0.0)


class NetworkSim:
    """Wszystkie węzły jednej strategii w jednym środowisku SimPy, sprzężone rozpływem mocy."""

    def __init__(self, sites: list[SiteConfig], strategy: str, cfg: SimConfig, data=None):
        from .grid_osm import load_power_data
        from .grid_profiles import background_shape, chp_profile, ztpo_profile

        data = data if data is not None else load_power_data()
        if data is None:
            raise RuntimeError("Brak danych sieci WN (OSM) – symulacja sieci niemożliwa")
        self.cfg, self.strategy = cfg, strategy
        self.env = simpy.Environment()
        self.sims = {s.name: SiteSim(self.env, s, strategy, cfg) for s in sites}
        self.model = GridModel(sites, data, farm=cfg.wind_farm != "none")
        self.bg = background_shape(cfg)
        self.outage = ~cfg.grid_available()  # kroki awarii (pełnej lub wybranych GPZ)
        self.chp = chp_profile(cfg)
        self.ztpo = ztpo_profile(cfg)
        if cfg.wind_farm != "none":
            from .windfarm import farm_profile
            self.farm = farm_profile(cfg)
        else:
            self.farm = np.zeros(cfg.n_steps)
        n, nb = cfg.n_steps, len(self.model.branches)
        self.hv = np.full((n, nb), np.nan)
        self.gpz = np.full((n, len(self.model.gpz_names)), np.nan)
        self.losses = np.full(n, np.nan)
        self.pse = np.full(n, np.nan)
        self.pf_ok = np.zeros(n, dtype=bool)
        self.exchange = np.zeros(n)

    def _solve(self, k: int, props: dict[str, Proposal], gpz_out: frozenset[str]) -> StepResult:
        p_node = {name: pcc_kw(p) for name, p in props.items()}
        self.model.set_step(p_node, bg=float(self.bg[k]), chp_kw=float(self.chp[k]),
                            ztpo_kw=float(self.ztpo[k]), farm_kw=float(self.farm[k]), gpz_out=gpz_out)
        return self.model.solve()

    def step(self, k: int) -> None:
        cfg, model = self.cfg, self.model
        props = {name: sim.propose(k) for name, sim in self.sims.items()}
        out_nodes = [name for name, p in props.items() if not p.grid_ok]
        res: StepResult | None = None
        if len(out_nodes) < len(props):  # pełny blackout → rozpływ pominięty
            # awaria wybranych GPZ wg maski czasu (także GPZ bez symulowanego węzła) + GPZ węzłów wyspowych
            gpz_out = frozenset(cfg.outage_gpz) if cfg.outage_gpz and self.outage[k] else frozenset()
            gpz_out |= {model.site_gpz[n] for n in out_nodes}
            res = self._solve(k, props, gpz_out)
            if cfg.network_control and res.pf_ok:
                for _ in range(MAX_COORD_ITER):
                    corr = coordinate(props, res, model)
                    if not corr:
                        break
                    for name, d in corr.items():
                        apply_correction(props[name], d)
                    res = self._solve(k, props, gpz_out)
                    if not res.pf_ok:
                        break
            self.pf_ok[k] = res.pf_ok
            self.hv[k] = res.hv_loading
            self.gpz[k] = [res.gpz_loading[g] for g in model.gpz_names]
            self.losses[k] = res.losses_kw
            self.pse[k] = res.pse_import_kw

        p_grid = []
        for name, sim in self.sims.items():
            if res is None:
                extra = {"v_node": math.nan, "feeder_loading": math.nan, "trafo_loading": math.nan,
                         "net_violation": False}
            else:
                extra = {"v_node": res.v_node[name], "feeder_loading": res.feeder_loading[name],
                         "trafo_loading": res.trafo_loading[name],
                         "net_violation": bool(props[name].grid_ok and node_violation(res, name))}
            p_grid.append(sim.commit(props[name], extra))
        p = np.asarray(p_grid)
        self.exchange[k] = min(np.clip(-p, 0, None).sum(), np.clip(p, 0, None).sum())

    def run(self) -> tuple[dict[str, Results], NetworkResults]:
        cfg, env = self.cfg, self.env

        def ticker():
            yield env.timeout(TICK_EPS)
            for k in range(cfg.n_steps):
                self.step(k)
                yield env.timeout(cfg.dt_min)

        for sim in self.sims.values():
            sim.start()
        env.process(ticker())
        env.run(until=cfg.horizon_min + 2 * TICK_EPS)

        times = pd.Series(pd.Timestamp(cfg.start) + pd.to_timedelta(np.arange(cfg.n_steps) * cfg.dt_min,
                                                                    unit="min"))
        idx = pd.RangeIndex(cfg.n_steps)
        net = NetworkResults(
            strategy=self.strategy, times=times, branches=self.model.branches, hv_loading=self.hv,
            gpz_loading=pd.DataFrame(self.gpz, columns=self.model.gpz_names, index=idx),
            losses_kw=pd.Series(self.losses, index=idx), pse_import_kw=pd.Series(self.pse, index=idx),
            chp_kw=pd.Series(self.chp, index=idx), pf_ok=pd.Series(self.pf_ok, index=idx),
            internal_exchange_kw=pd.Series(self.exchange, index=idx), site_gpz=dict(self.model.site_gpz),
        )
        return {name: sim.results() for name, sim in self.sims.items()}, net


def run_network(sites: list[SiteConfig], strategy: str, cfg: SimConfig) -> tuple[dict[str, Results], NetworkResults]:
    """Wspólna symulacja węzłów ``sites`` (jedna strategia) z rozpływem mocy w każdym kroku."""
    return NetworkSim(sites, strategy, cfg).run()


def _run_network_task(args: tuple[list[SiteConfig], str, SimConfig]):
    return run_network(*args)


def run_all_network(sites: list[SiteConfig], cfg: SimConfig, strategies=("A", "B", "C", "D"),
                    n_jobs: int | None = None) -> tuple[dict[tuple[str, str], Results], dict[str, NetworkResults]]:
    """``run_network`` dla każdej strategii (równolegle po strategiach)."""
    if cfg.wind_farm != "none":  # farma dzielona tylko między symulowane węzły
        from .windfarm import assign_shares
        sites = assign_shares(sites)
    tasks = [(sites, st, cfg) for st in strategies]
    out = parallel_map(_run_network_task, tasks, n_jobs)
    results: dict[tuple[str, str], Results] = {}
    nets: dict[str, NetworkResults] = {}
    for st, (res, net) in zip(strategies, out):
        nets[st] = net
        for name, r in res.items():
            results[(name, st)] = r
    return results, nets
