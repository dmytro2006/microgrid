"""Silnik symulacji hybrydowej: DES (SimPy) + dyskretny krok bilansowy Δt."""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np
import pandas as pd
import simpy

from .config import SimConfig, SiteConfig
from .controller import CONTROLLERS, SiteState
from .ev_generator import EV, TICK_EPS, Station, generate_evs
from .grid_environment import BESS, pcc_balance
from .synthetic import generate_profiles


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


def site_seed(site: SiteConfig, seed: int) -> int:
    return seed * 1_000_003 + zlib.crc32(site.name.encode())


def make_inputs(site: SiteConfig, cfg: SimConfig) -> tuple[dict[str, np.ndarray], list[EV]]:
    """Wspólne liczby losowe: te same profile i ta sama flota EV dla każdej strategii."""
    rng = np.random.default_rng(site_seed(site, cfg.seed))
    profiles = generate_profiles(site, cfg, rng)
    evs = generate_evs(site, cfg, rng)
    return profiles, evs


def run(site: SiteConfig, strategy: str, cfg: SimConfig | None = None) -> Results:
    cfg = cfg or SimConfig()
    profiles, evs = make_inputs(site, cfg)
    controller = CONTROLLERS[strategy]()

    env = simpy.Environment()
    station = Station(env, site)
    bess = BESS(site)
    dt_h = cfg.dt_h
    steps_per_day = 24 * 60 // cfg.dt_min
    price_sig = (pd.Series(profiles["price"])
                 .rolling(window=max(1, 60 // cfg.dt_min), center=True, min_periods=1).mean().to_numpy())
    rows: list[dict] = []

    def ticker():
        yield env.timeout(TICK_EPS)
        for k in range(cfg.n_steps):
            t = k * cfg.dt_min
            day = slice((k // steps_per_day) * steps_per_day, (k // steps_per_day + 1) * steps_per_day)
            prices_day = price_sig[day]
            state = SiteState(
                t_min=t, dt_h=dt_h,
                load=profiles["load"][k], pv=profiles["pv"][k], wind=profiles["wind"][k],
                price=profiles["price"][k], price_signal=float(price_sig[k]),
                price_low=float(np.percentile(prices_day, 30)),
                price_high=float(np.percentile(prices_day, 80)),
                active=list(station.active.values()), bess=bess, site=site,
                v2g_soc_floor=cfg.ev.v2g_soc_floor,
            )
            p_ev_cmd, p_bess_cmd = controller.decide(state)

            p_ch = p_dis = 0.0
            for ev in state.active:
                p = p_ev_cmd.get(ev.id, 0.0)
                # ochrona przed poleceniami niewykonalnymi fizycznie
                if p >= 0:
                    p = min(p, ev.max_charge_kw(dt_h))
                else:
                    p = -min(-p, ev.max_discharge_kw(t, dt_h, cfg.ev.v2g_soc_floor))
                ev.apply(p, dt_h)
                if p >= 0:
                    p_ch += p
                else:
                    p_dis += -p
            p_bess = bess.step(p_bess_cmd, dt_h)
            p_ev_sum = p_ch - p_dis
            p_grid = pcc_balance(state.load, p_ev_sum, p_bess, state.pv, state.wind)
            rows.append({
                "t_min": t, "load": state.load, "pv": state.pv, "wind": state.wind,
                "price": state.price, "p_ev": p_ev_sum, "p_ev_ch": p_ch, "p_ev_dis": p_dis,
                "p_bess": p_bess, "soc_bess": bess.soc, "p_grid": p_grid,
                "n_active": len(state.active), "n_queue": station.queue_len,
            })
            yield env.timeout(cfg.dt_min)

    env.process(station.arrivals(evs))
    env.process(ticker())
    env.run(until=cfg.horizon_min + 2 * TICK_EPS)

    ts = pd.DataFrame(rows)
    ts["time"] = pd.Timestamp(cfg.start) + pd.to_timedelta(ts["t_min"], unit="min")
    ts["p_max"] = site.p_max_kw

    for ev in station.active.values():
        ev.status = "end_of_sim"
    all_evs = station.finished + list(station.active.values())
    sessions = pd.DataFrame([{
        "id": ev.id, "status": ev.status,
        "t_arr": pd.Timestamp(cfg.start) + pd.Timedelta(minutes=ev.t_arr),
        "t_dep": pd.Timestamp(cfg.start) + pd.Timedelta(minutes=ev.t_dep),
        "wait_min": (ev.t_plug - ev.t_arr) if ev.t_plug is not None else np.nan,
        "cap_kwh": ev.cap_kwh, "p_max_kw": ev.p_max_kw, "v2g": ev.v2g,
        "soc_init": ev.soc_init, "soc_target": ev.soc_target, "soc_final": ev.soc,
        "energy_in_kwh": ev.energy_in_kwh, "energy_out_kwh": ev.energy_out_kwh,
    } for ev in sorted(all_evs, key=lambda e: e.id)])

    return Results(site, strategy, cfg, ts, sessions, bess.losses_kwh, bess.e_init, bess.energy)


def run_all(sites: list[SiteConfig], cfg: SimConfig, strategies=("A", "B", "C")) -> dict[tuple[str, str], Results]:
    return {(s.name, st): run(s, st, cfg) for s in sites for st in strategies}
