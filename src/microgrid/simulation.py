"""Silnik symulacji hybrydowej: DES (SimPy) + dyskretny krok bilansowy Δt."""

from __future__ import annotations

import multiprocessing
import os
import sys
import zlib
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import simpy

from .config import SimConfig, SiteConfig
from .controller import CONTROLLERS, SiteState
from .ev_generator import EV, TICK_EPS, Station, expected_arrivals, fleet_means, generate_evs, mean_parking_h
from .grid_environment import BESS, island_dispatch, pcc_balance
from .synthetic import generate_profiles
from .tariff import DEFAULT_TARIFF, import_price


SYNTH_T_AMB_C = 20.0  # temperatura otoczenia w trybie syntetycznym (model starzenia BESS)


@dataclass
class Results:
    site: SiteConfig
    strategy: str
    cfg: SimConfig
    ts: pd.DataFrame        # szereg czasowy (jeden wiersz na krok Δt)
    sessions: pd.DataFrame  # jeden wiersz na pojazd
    bess_losses_kwh: float
    bess_e_init: float
    bess_e_final: float
    raw: pd.DataFrame | None = None  # surowe dane rzeczywiste (pogoda, RCE, KSE) na siatce Δt
    ev_trace: pd.DataFrame | None = None  # (k, id, soc, p) dla podłączonych pojazdów w każdym kroku


def site_seed(site: SiteConfig, seed: int) -> int:
    return seed * 1_000_003 + zlib.crc32(site.name.encode())


def make_inputs(site: SiteConfig, cfg: SimConfig) -> tuple[dict[str, np.ndarray], list[EV], pd.DataFrame | None]:
    """Wspólne liczby losowe: te same profile i ta sama flota EV dla każdej strategii."""
    rng = np.random.default_rng(site_seed(site, cfg.seed))
    profiles = generate_profiles(site, cfg, rng)  # zawsze losowane – stały strumień dla generatora EV
    raw = None
    if cfg.data_source == "real":
        from .real_data import real_inputs
        profiles, raw = real_inputs(site, cfg)
    elif cfg.data_source != "synthetic":
        raise ValueError(f"Nieznane źródło danych: {cfg.data_source!r}")
    if cfg.pv_multiplier != 1.0:
        profiles = {**profiles, "pv": profiles["pv"] * cfg.pv_multiplier}
    evs = generate_evs(site, cfg, rng)
    return profiles, evs, raw


def site_grid_available(site: SiteConfig, cfg: SimConfig) -> np.ndarray:
    """Maska dostępności GPZ dla węzła: awaria dotyczy wszystkich (``outage_gpz`` puste)
    albo tylko węzłów zasilanych z GPZ wymienionych w ``cfg.outage_gpz``."""
    if not cfg.outage_gpz:
        return cfg.grid_available()
    from .grid_osm import load_power_data
    from .grid_topology import site_gpz
    data = load_power_data()
    if data is None:
        raise RuntimeError("Brak danych sieci WN (OSM) – nie można przypisać węzła do GPZ")
    if site_gpz(site, data).name in cfg.outage_gpz:
        return cfg.grid_available()
    return np.ones(cfg.n_steps, dtype=bool)


@dataclass
class Proposal:
    """Krok węzła po decyzji EMS, przycięciu do fizyki i ewentualnej dyspozycji wyspowej
    (przed zastosowaniem mocy EV/BESS). Współsymulacja sieci może między ``propose`` a ``commit``
    zmienić ``p_ev``/``p_bess`` i zażądać ograniczenia lokalnych OZE (``curt_net``)."""
    k: int
    p_ev: dict[int, float]   # moc EV [kW] (+ ładowanie, − V2G)
    p_bess: float            # polecenie BESS [kW] (+ ładowanie)
    load: float              # zapotrzebowanie [kW]
    pv: float
    wind: float
    ch_max: dict[int, float]
    dis_max: dict[int, float]
    b_ch: float
    b_dis: float
    shed: float              # odciążenie (praca wyspowa) [kW]
    curt: float              # ograniczenie OZE (praca wyspowa) [kW]
    farm_k: float            # moc farmy dostępna dla wyspy [kW]
    grid_ok: bool
    curt_net: float = 0.0    # ograniczenie lokalnych PV+wiatr wymuszone przez sieć [kW] (tylko grid_ok)
    t_min: float = 0.0
    price: float = 0.0
    active: list[EV] = field(default_factory=list)  # pojazdy podłączone w kroku (kolejność jak w EMS)


class SiteSim:
    """Symulacja jednego węzła w (współdzielonym) środowisku SimPy, krok po kroku: ``propose`` → ``commit``."""

    def __init__(self, env: simpy.Environment, site: SiteConfig, strategy: str, cfg: SimConfig,
                 grid_ok: np.ndarray | None = None):
        self.env, self.site, self.strategy, self.cfg = env, site, strategy, cfg
        self.profiles, self.evs, self.raw = make_inputs(site, cfg)
        profiles = self.profiles
        self.controller = CONTROLLERS[strategy]()
        self.station = Station(env, site)
        self.bess = BESS(site)
        self.steps_per_day = steps_per_day = 24 * 60 // cfg.dt_min
        self.price_sig = (pd.Series(profiles["price"])
                          .rolling(window=max(1, 60 // cfg.dt_min), center=True, min_periods=1).mean().to_numpy())
        self.rows: list[dict] = []
        self.trace: list[tuple[int, int, float, float]] = []
        times = pd.Timestamp(cfg.start) + pd.to_timedelta(np.arange(cfg.n_steps) * cfg.dt_min, unit="min")
        fc_src = {key: profiles[key].copy() for key in ("load", "pv", "wind")}
        if cfg.forecast == "persistence":  # prognoza = ten sam kwadrans poprzedniej doby
            for key, arr in fc_src.items():
                arr[steps_per_day:] = profiles[key][:-steps_per_day]
        forecast = {
            "load": fc_src["load"], "net": fc_src["load"] - fc_src["pv"] - fc_src["wind"],
            "p_imp": import_price(profiles["price"], times), "p_exp": profiles["price"],
        }
        self.grid_ok = grid_ok = site_grid_available(site, cfg) if grid_ok is None else np.asarray(grid_ok, bool)
        # wspólna farma wiatrowa: udział węzła w produkcji [kW] (rozliczenie PPA; w wariancie "gpz" też wyspa GPZ)
        if cfg.wind_farm in ("ppa", "gpz"):
            from .windfarm import farm_profile, site_share
            self.p_farm = site_share(site) * farm_profile(cfg)
        elif cfg.wind_farm == "none":
            self.p_farm = np.zeros(cfg.n_steps)
        else:
            raise ValueError(f"Nieznany wariant farmy wiatrowej: {cfg.wind_farm!r}")
        # farma zasila wyspy tylko przy awarii wszystkich GPZ (pusta krotka outage_gpz)
        feeds_islands = cfg.wind_farm == "gpz" and not cfg.outage_gpz
        self.island_farm = island_farm = self.p_farm if feeds_islands else np.zeros(cfg.n_steps)
        fc_farm = island_farm.copy()
        if cfg.forecast == "persistence":
            fc_farm[steps_per_day:] = island_farm[:-steps_per_day]
        forecast["farm"] = fc_farm
        # awaria zapowiedziana → MPC zna okno z wyprzedzeniem; nagła → plan zakłada sprawną sieć
        forecast["grid_ok"] = grid_ok if cfg.outage_announced else np.ones_like(grid_ok)
        if cfg.mpc_ev_forecast:
            hod = (np.arange(cfg.n_steps) * cfg.dt_min / 60.0) % 24.0
            forecast["ev_arrivals"] = expected_arrivals(site, cfg)
            forecast["ev_park_h"] = mean_parking_h(hod, cfg)
            forecast["ev_means"] = fleet_means(cfg)
        self.forecast = forecast

    def start(self) -> None:
        """Rejestruje proces przyjazdów pojazdów w środowisku SimPy."""
        self.env.process(self.station.arrivals(self.evs))

    def propose(self, k: int) -> Proposal:
        """Decyzja EMS w kroku ``k``, przycięcie do fizyki i (przy awarii GPZ) dyspozycja wyspowa."""
        cfg, profiles, bess, dt_h = self.cfg, self.profiles, self.bess, self.cfg.dt_h
        spd = self.steps_per_day
        t = k * cfg.dt_min
        day = slice((k // spd) * spd, (k // spd + 1) * spd)
        prices_day = self.price_sig[day]
        state = SiteState(
            t_min=t, dt_h=dt_h,
            load=profiles["load"][k], pv=profiles["pv"][k], wind=profiles["wind"][k],
            price=profiles["price"][k], price_signal=float(self.price_sig[k]),
            price_low=float(np.percentile(prices_day, 30)),
            price_high=float(np.percentile(prices_day, 80)),
            active=list(self.station.active.values()), bess=bess, site=self.site,
            v2g_soc_floor=cfg.ev.v2g_soc_floor,
            k=k, dt_min=cfg.dt_min, forecast=self.forecast, tariff=DEFAULT_TARIFF,
            p_grid_prev=self.rows[-1]["p_grid"] if self.rows else 0.0,
        )
        p_ev_cmd, p_bess_cmd = self.controller.decide(state)

        # ochrona przed poleceniami niewykonalnymi fizycznie
        ch_max = {ev.id: ev.max_charge_kw(dt_h) for ev in state.active}
        dis_max = {ev.id: ev.max_discharge_kw(t, dt_h, cfg.ev.v2g_soc_floor) for ev in state.active}
        p_ev = {}
        for ev in state.active:
            p = p_ev_cmd.get(ev.id, 0.0)
            p_ev[ev.id] = min(p, ch_max[ev.id]) if p >= 0 else -min(-p, dis_max[ev.id])
        b_ch, b_dis = bess.max_charge_kw(dt_h), bess.max_discharge_kw(dt_h)
        p_bess_cmd = min(p_bess_cmd, b_ch) if p_bess_cmd >= 0 else -min(-p_bess_cmd, b_dis)

        load, pv, wind = state.load, state.pv, state.wind
        farm_k = float(self.island_farm[k])  # moc dostępna z GPZ w czasie awarii (wariant "gpz")
        grid_ok = bool(self.grid_ok[k])
        shed = curt = 0.0
        if not grid_ok:  # awaria GPZ – praca wyspowa (P_grid = 0 albo pobór z farmy przy GPZ)
            p_ev, p_bess_cmd, shed, curt = island_dispatch(
                load, pv + wind + farm_k, p_ev, ch_max, dis_max, p_bess_cmd, b_ch, b_dis)
        return Proposal(k=k, p_ev=p_ev, p_bess=p_bess_cmd, load=load, pv=pv, wind=wind,
                        ch_max=ch_max, dis_max=dis_max, b_ch=b_ch, b_dis=b_dis, shed=shed, curt=curt,
                        farm_k=farm_k, grid_ok=grid_ok, t_min=t, price=state.price, active=state.active)

    def commit(self, prop: Proposal, extra: dict | None = None) -> float:
        """Stosuje moce EV/BESS, domyka bilans węzła, dopisuje wiersz szeregu czasowego
        (z dodatkowymi kolumnami ``extra``) i zwraca P_grid [kW]."""
        k, dt_h, bess = prop.k, self.cfg.dt_h, self.bess
        load, pv, wind, farm_k = prop.load, prop.pv, prop.wind, prop.farm_k
        shed, curt = prop.shed, prop.curt
        curt_net = 0.0
        if prop.grid_ok and prop.curt_net > 0 and pv + wind > 0:  # ograniczenie OZE na żądanie sieci
            curt_net = min(prop.curt_net, pv + wind)
            f = 1.0 - curt_net / (pv + wind)
            pv, wind = pv * f, wind * f

        p_ch = p_dis = 0.0
        for ev in prop.active:
            p = prop.p_ev[ev.id]
            ev.apply(p, dt_h)
            self.trace.append((k, ev.id, ev.soc, p))
            if p >= 0:
                p_ch += p
            else:
                p_dis += -p
        p_bess = bess.step(prop.p_bess, dt_h)
        p_ev_sum = p_ch - p_dis
        if not prop.grid_ok:
            # domknięcie bilansu (resztki numeryczne → odciążenie / ograniczenie OZE)
            resid = pcc_balance(load - shed, p_ev_sum, p_bess, pv + wind + farm_k - curt, 0.0)
            if resid > 0:
                shed = min(load, shed + resid)
            else:
                curt = min(pv + wind + farm_k, curt - resid)
            curt = max(0.0, curt - farm_k)  # najpierw niewykorzystany udział farmy, potem lokalne OZE
            if pv + wind > 0:  # ograniczenie lokalnych OZE proporcjonalnie do produkcji
                f = 1.0 - curt / (pv + wind)
                pv, wind = pv * f, wind * f
            load -= shed
        p_grid = pcc_balance(load, p_ev_sum, p_bess, pv, wind)
        if not prop.grid_ok:
            p_grid = 0.0 if abs(p_grid) < 1e-6 else p_grid
            assert -1e-6 <= p_grid <= farm_k + 1e-6, (k, p_grid, farm_k)
        self.rows.append({
            "t_min": prop.t_min, "load": load, "pv": pv, "wind": wind,
            "load_dem": prop.load, "p_shed": shed, "p_curt": curt, "grid_ok": prop.grid_ok,
            "p_farm": float(self.p_farm[k]),
            "price": prop.price, "p_ev": p_ev_sum, "p_ev_ch": p_ch, "p_ev_dis": p_dis,
            "p_bess": p_bess, "soc_bess": bess.soc, "p_grid": p_grid,
            "n_active": len(prop.active), "n_queue": self.station.queue_len,
            "p_curt_net": curt_net,
            **(extra or {}),
        })
        return p_grid

    def results(self) -> Results:
        cfg, site, station, raw = self.cfg, self.site, self.station, self.raw
        ts = pd.DataFrame(self.rows)
        ts["time"] = pd.Timestamp(cfg.start) + pd.to_timedelta(ts["t_min"], unit="min")
        ts["p_max"] = site.p_max_kw
        ts["t_amb"] = raw["temp"].to_numpy() if raw is not None else SYNTH_T_AMB_C

        for ev in station.active.values():
            ev.status = "end_of_sim"
        all_evs = station.finished + list(station.active.values())
        sessions = pd.DataFrame([{
            "id": ev.id, "status": ev.status,
            "t_arr": pd.Timestamp(cfg.start) + pd.Timedelta(minutes=ev.t_arr),
            "t_dep": pd.Timestamp(cfg.start) + pd.Timedelta(minutes=ev.t_dep),
            "wait_min": (ev.t_plug - ev.t_arr) if ev.t_plug is not None else np.nan,
            "model": ev.model, "cap_kwh": ev.cap_kwh, "p_max_kw": ev.p_max_kw, "v2g": ev.v2g,
            "soc_init": ev.soc_init, "soc_target": ev.soc_target, "soc_final": ev.soc,
            "energy_in_kwh": ev.energy_in_kwh, "energy_out_kwh": ev.energy_out_kwh,
        } for ev in sorted(all_evs, key=lambda e: e.id)])

        ev_trace = pd.DataFrame(self.trace, columns=["k", "id", "soc", "p"])
        bess = self.bess
        return Results(site, self.strategy, cfg, ts, sessions, bess.losses_kwh, bess.e_init, bess.energy,
                       raw, ev_trace)


def run(site: SiteConfig, strategy: str, cfg: SimConfig | None = None) -> Results:
    cfg = cfg or SimConfig()
    env = simpy.Environment()
    sim = SiteSim(env, site, strategy, cfg)

    def ticker():
        yield env.timeout(TICK_EPS)
        for k in range(cfg.n_steps):
            sim.commit(sim.propose(k))
            yield env.timeout(cfg.dt_min)

    sim.start()
    env.process(ticker())
    env.run(until=cfg.horizon_min + 2 * TICK_EPS)
    return sim.results()


def _run_task(args: tuple[SiteConfig, str, SimConfig]) -> Results:
    return run(*args)


def _can_spawn() -> bool:
    """Procesy potomne (forkserver/spawn) importują moduł __main__ – musi to być istniejący plik."""
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    return main_file is None or os.path.exists(main_file)


def parallel_map(fn, tasks: list, n_jobs: int | None = None) -> list:
    """``map`` w puli procesów (gdy się da i opłaca), w przeciwnym razie sekwencyjnie."""
    n_jobs = n_jobs if n_jobs is not None else min(len(tasks), os.cpu_count() or 1)
    in_worker = multiprocessing.parent_process() is not None  # bez zagnieżdżonych pul
    if n_jobs > 1 and len(tasks) > 1 and not in_worker and _can_spawn():
        try:
            with ProcessPoolExecutor(max_workers=n_jobs) as ex:
                return list(ex.map(fn, tasks))
        except (OSError, RuntimeError, BrokenProcessPool):
            pass  # środowisko bez wieloprocesowości → obliczenia sekwencyjne
    return [fn(t) for t in tasks]


def run_all(sites: list[SiteConfig], cfg: SimConfig, strategies=("A", "B", "C", "D"),
            n_jobs: int | None = None) -> dict[tuple[str, str], Results]:
    """Uruchamia wszystkie pary (lokalizacja, strategia); ``n_jobs`` > 1 → procesy równoległe."""
    if cfg.wind_farm != "none":  # farma dzielona tylko między symulowane węzły
        from .windfarm import assign_shares
        sites = assign_shares(sites)
    tasks = [(s, st, cfg) for s in sites for st in strategies]
    out = parallel_map(_run_task, tasks, n_jobs)
    return {(s.name, st): r for (s, st, _), r in zip(tasks, out)}
