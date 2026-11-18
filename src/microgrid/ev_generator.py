"""Generator sesji EV i procesy DES stacji ładowania (SimPy).

Czas symulacji w SimPy jest w minutach. Zdarzenia przyjazdu i odjazdu są
wyrównane do siatki kroku Δt, a krok bilansowy (ticker) wykonuje się tuż po
węzłach siatki (przesunięcie TICK_EPS), dzięki czemu wszystkie zdarzenia DES
z chwili t są obsłużone, zanim EMS policzy bilans dla przedziału [t, t+Δt).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import simpy

from .config import EV_MODELS, SimConfig, SiteConfig

TICK_EPS = 1e-6


@dataclass
class EV:
    """Agent pojazdu: x_EV = <t_arr, t_dep, C_bat, SoC_init, SoC_target, P_ch_max>."""

    id: int
    t_arr: float  # [min]
    t_dep: float  # [min]
    cap_kwh: float
    soc_init: float
    soc_target: float
    p_max_kw: float
    v2g: bool
    model: str = ""
    soc: float = field(init=False)
    t_plug: float | None = None
    energy_in_kwh: float = 0.0
    energy_out_kwh: float = 0.0
    status: str = "pending"

    def __post_init__(self) -> None:
        self.soc = self.soc_init

    # --- ograniczenia fizyczne w kroku dt ---
    def max_charge_kw(self, dt_h: float) -> float:
        need = max(0.0, (self.soc_target - self.soc) * self.cap_kwh)
        return min(self.p_max_kw, need / dt_h)

    def max_discharge_kw(self, t_min: float, dt_h: float, soc_floor: float) -> float:
        """Maks. moc V2G, która nadal pozwala osiągnąć SoC_target do odjazdu."""
        if not self.v2g:
            return 0.0
        remaining_h = (self.t_dep - t_min) / 60.0 - dt_h
        if remaining_h <= 0:
            return 0.0
        e_needed_at_end = self.soc_target * self.cap_kwh - self.p_max_kw * remaining_h
        e_min = max(soc_floor * self.cap_kwh, e_needed_at_end)
        e_avail = self.soc * self.cap_kwh - e_min
        return max(0.0, min(self.p_max_kw, e_avail / dt_h))

    def laxity_deadline(self) -> float:
        """d_i = t_dep - (SoC_target - SoC)·C_bat / P_ch_max  [min] (kryterium EDF)."""
        need_h = max(0.0, self.soc_target - self.soc) * self.cap_kwh / self.p_max_kw
        return self.t_dep - need_h * 60.0

    def apply(self, p_kw: float, dt_h: float) -> None:
        e = p_kw * dt_h
        self.soc = min(1.0, max(0.0, self.soc + e / self.cap_kwh))
        if e >= 0:
            self.energy_in_kwh += e
        else:
            self.energy_out_kwh += -e


def _arrival_rate_shape(h: np.ndarray) -> np.ndarray:
    def bump(mu, s):
        d = np.minimum(np.abs(h - mu), 24.0 - np.abs(h - mu))
        return np.exp(-0.5 * (d / s) ** 2)

    return 0.08 + 1.0 * bump(8.0, 1.5) + 1.3 * bump(17.5, 2.0)


def generate_evs(site: SiteConfig, cfg: SimConfig, rng: np.random.Generator) -> list[EV]:
    """Niejednorodny proces Poissona (metoda thinning) + losowanie atrybutów pojazdu."""
    hh = np.linspace(0, 24, 24 * 60, endpoint=False)
    shape = _arrival_rate_shape(hh)
    lam_scale = site.ev_per_day / (shape.mean() * 24.0)  # [poj./h] na jednostkę kształtu
    lam_max = lam_scale * shape.max()

    p = cfg.ev
    weights = np.array([m.weight for m in EV_MODELS])
    weights = weights / weights.sum()
    evs: list[EV] = []
    t_h = 0.0
    horizon_h = cfg.horizon_min / 60.0
    dt = cfg.dt_min
    while True:
        t_h += rng.exponential(1.0 / lam_max)
        if t_h >= horizon_h:
            break
        if rng.random() > lam_scale * _arrival_rate_shape(np.array([t_h % 24.0]))[0] / lam_max:
            continue
        hod = t_h % 24.0
        median = p.park_median_evening_h if hod >= 17.0 else p.park_median_h
        if cfg.ev_fleet == "real":
            m = EV_MODELS[rng.choice(len(EV_MODELS), p=weights)]
            dc = rng.random() < p.dc_share
            cap, model = m.battery_kwh, m.name
            p_max = min(m.dc_kw, p.dc_charger_kw) if dc else min(m.ac_kw, p.ac_charger_kw)
            if dc:
                median = p.park_median_dc_h
        else:
            cap, model, dc = float(rng.choice(p.battery_kwh)), "", False
            p_max = float(rng.choice(p.p_ch_max_kw, p=p.p_ch_weights))
        park_h = float(np.clip(rng.lognormal(math.log(median), p.park_sigma), p.park_min_h, p.park_max_h))
        t_arr = math.ceil(t_h * 60.0 / dt) * dt
        if t_arr >= cfg.horizon_min:
            break
        t_dep = max(t_arr + dt, round((t_arr + park_h * 60.0) / dt) * dt)
        evs.append(
            EV(
                id=len(evs),
                t_arr=float(t_arr),
                t_dep=float(t_dep),
                cap_kwh=float(cap),
                soc_init=float(rng.uniform(*p.soc_init)),
                soc_target=float(rng.uniform(*p.soc_target)),
                p_max_kw=float(p_max),
                v2g=bool(rng.random() < site.v2g_share) and not dc,  # V2G tylko na AC (ISO 15118-20)
                model=model,
            )
        )
    return evs


def expected_arrivals(site: SiteConfig, cfg: SimConfig) -> np.ndarray:
    """Oczekiwana liczba przyjazdów w każdym kroku Δt (intensywność procesu Poissona × Δt)."""
    hh = np.linspace(0, 24, 24 * 60, endpoint=False)
    lam_scale = site.ev_per_day / (_arrival_rate_shape(hh).mean() * 24.0)
    h = (np.arange(cfg.n_steps) * cfg.dt_min + cfg.dt_min / 2) / 60.0 % 24.0
    return lam_scale * _arrival_rate_shape(h) * cfg.dt_h


def fleet_means(cfg: SimConfig) -> dict[str, float]:
    """Średnie parametry sesji: potrzebna energia [kWh], moc ładowania [kW], udział sesji DC."""
    p = cfg.ev
    need = (np.mean(p.soc_target) - np.mean(p.soc_init))
    if cfg.ev_fleet == "real":
        w = np.array([m.weight for m in EV_MODELS])
        w = w / w.sum()
        cap = float(np.dot(w, [m.battery_kwh for m in EV_MODELS]))
        p_ac = float(np.dot(w, [min(m.ac_kw, p.ac_charger_kw) for m in EV_MODELS]))
        p_dc = float(np.dot(w, [min(m.dc_kw, p.dc_charger_kw) for m in EV_MODELS]))
        dc = p.dc_share
        return {"e_need": need * cap, "p": (1 - dc) * p_ac + dc * p_dc, "dc": dc}
    cap = float(np.mean(p.battery_kwh))
    return {"e_need": need * cap, "p": float(np.dot(p.p_ch_max_kw, p.p_ch_weights)), "dc": 0.0}


def mean_parking_h(hour_of_day: np.ndarray, cfg: SimConfig) -> np.ndarray:
    """Oczekiwany czas postoju [h] (średnia rozkładu log-normalnego) w zależności od godziny przyjazdu."""
    p = cfg.ev
    f = math.exp(p.park_sigma**2 / 2)
    ac = np.where(hour_of_day >= 17.0, p.park_median_evening_h, p.park_median_h) * f
    ac = np.clip(ac, p.park_min_h, p.park_max_h)
    if cfg.ev_fleet == "real":
        return (1 - p.dc_share) * ac + p.dc_share * max(p.park_min_h, p.park_median_dc_h * f)
    return ac


class Station:
    """Stacja ładowania: N punktów (simpy.Resource) + ograniczona kolejka FIFO."""

    def __init__(self, env: simpy.Environment, site: SiteConfig):
        self.env = env
        self.site = site
        self.chargers = simpy.Resource(env, capacity=site.n_chargers)
        self.active: dict[int, EV] = {}
        self.finished: list[EV] = []

    @property
    def queue_len(self) -> int:
        return len(self.chargers.queue)

    def ev_process(self, ev: EV):
        if self.queue_len >= self.site.queue_cap and self.chargers.count >= self.site.n_chargers:
            ev.status = "balked"  # kolejka pełna – kierowca odjeżdża od razu
            self.finished.append(ev)
            return
        req = self.chargers.request()
        patience = self.env.timeout(max(0.0, ev.t_dep - self.env.now))
        result = yield req | patience
        if req not in result:
            req.cancel()
            ev.status = "reneged"  # czas postoju minął w kolejce
            self.finished.append(ev)
            return
        ev.t_plug = self.env.now
        ev.status = "charging"
        self.active[ev.id] = ev
        yield self.env.timeout(max(0.0, ev.t_dep - self.env.now))
        del self.active[ev.id]
        self.chargers.release(req)
        ev.status = "served"
        self.finished.append(ev)

    def arrivals(self, evs: list[EV]):
        """Proces generatora zdarzeń przyjazdu."""
        for ev in evs:
            yield self.env.timeout(max(0.0, ev.t_arr - self.env.now))
            self.env.process(self.ev_process(ev))
