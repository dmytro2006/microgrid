"""Generatory syntetycznych profili wejściowych (zamienniki danych rzeczywistych).

Każda funkcja zwraca wektor numpy o długości ``cfg.n_steps`` (wartość średnia
w przedziale [k*dt, (k+1)*dt)). Docelowo zostaną podmienione na:
  - load  -> profile standardowe OSD / pomiary,
  - pv    -> PVGIS dla współrzędnych węzła,
  - wind  -> Open-Meteo / IMGW (Kraków-Balice),
  - price -> TGE RDN.
"""

from __future__ import annotations

import numpy as np

from .config import RHO_AIR, SimConfig, SiteConfig


def hours_of_day(cfg: SimConfig) -> np.ndarray:
    t_min = np.arange(cfg.n_steps) * cfg.dt_min + cfg.dt_min / 2
    return (t_min / 60.0) % 24.0


def _bump(h: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    """Gaussowski garb na dobie (z zawinięciem przez północ), wartość max = 1."""
    d = np.minimum(np.abs(h - mu), 24.0 - np.abs(h - mu))
    return np.exp(-0.5 * (d / sigma) ** 2)


def _ar1(rng: np.random.Generator, n: int, phi: float, sigma: float) -> np.ndarray:
    x = np.empty(n)
    x[0] = rng.normal(0, sigma / np.sqrt(1 - phi**2))
    eps = rng.normal(0, sigma, n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + eps[i]
    return x


def base_load(site: SiteConfig, cfg: SimConfig, rng: np.random.Generator) -> np.ndarray:
    h = hours_of_day(cfg)
    shape = 0.35 + 0.30 * _bump(h, 8.0, 1.5) + 0.65 * _bump(h, 19.0, 2.0)
    noise = 1.0 + _ar1(rng, cfg.n_steps, 0.95, 0.015)
    return np.clip(site.base_load_peak_kw * shape * noise, 0.0, None)


def pv_power(site: SiteConfig, cfg: SimConfig, rng: np.random.Generator) -> np.ndarray:
    h = hours_of_day(cfg)
    bell = np.clip(np.sin(np.pi * (h - 6.0) / 13.0), 0.0, None) ** 1.3
    bell[(h < 6.0) | (h > 19.0)] = 0.0
    cloud = np.clip(0.75 + _ar1(rng, cfg.n_steps, 0.97, 0.06), 0.3, 1.0)
    return site.pv_kwp * site.inverter_eff * bell * cloud


def wind_speed(site: SiteConfig, cfg: SimConfig, rng: np.random.Generator) -> np.ndarray:
    v = site.wind_mean_speed + _ar1(rng, cfg.n_steps, 0.98, 0.5)
    return np.clip(v, 0.0, 30.0)


def wind_power_curve(site: SiteConfig, v: np.ndarray) -> np.ndarray:
    """Krzywa mocy turbiny z dokumentacji (cut-in / ½·Cp·ρ·A·v³ / rated / cut-out) [kW]."""
    p = 0.5 * site.wind_cp * RHO_AIR * site.wind_rotor_area_m2 * v**3 / 1000.0
    p = np.where(v >= site.wind_v_rated, site.wind_rated_kw, p)
    p = np.where((v < site.wind_v_cut_in) | (v >= site.wind_v_cut_out), 0.0, p)
    return p


def wind_power(site: SiteConfig, cfg: SimConfig, rng: np.random.Generator) -> np.ndarray:
    v = wind_speed(site, cfg, rng)
    if site.wind_rated_kw <= 0:
        return np.zeros(cfg.n_steps)
    return wind_power_curve(site, v)


def price(cfg: SimConfig, rng: np.random.Generator) -> np.ndarray:
    """Syntetyczna cena energii [PLN/MWh] – zamiennik RDN."""
    h = hours_of_day(cfg)
    p = 380 + 180 * _bump(h, 8.0, 1.5) + 380 * _bump(h, 19.0, 2.0) - 150 * _bump(h, 13.0, 2.0) - 80 * _bump(h, 3.0, 2.5)
    return np.clip(p + _ar1(rng, cfg.n_steps, 0.9, 15.0), 50.0, None)


def generate_profiles(site: SiteConfig, cfg: SimConfig, rng: np.random.Generator) -> dict[str, np.ndarray]:
    return {
        "load": base_load(site, cfg, rng),
        "pv": pv_power(site, cfg, rng),
        "wind": wind_power(site, cfg, rng),
        "price": price(cfg, rng),
    }
