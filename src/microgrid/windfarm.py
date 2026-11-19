"""Wspólna farma wiatrowa poza miastem, dzielona między mikrosieci.

Warianty (``SimConfig.wind_farm``):
  - ``"ppa"`` – farma przyłączona do sieci OSD gdzie indziej; energia płynie przez sieć publiczną, więc fizycznie
    nie zmienia przepływów w PCC węzłów. Każdy węzeł ma udział w produkcji i rozlicza go umową PPA
    (``tariff.cost_breakdown``). W czasie awarii GPZ farma nie pomaga.
  - ``"gpz"`` – farma przyłączona do tego samego GPZ. Rozliczenie jak w PPA, a w czasie awarii GPZ pracuje
    wyspowo z farmą: węzeł może pobrać z GPZ do wysokości swojego udziału w bieżącej produkcji
    (przydział stały, proporcjonalny – bez wymiany nadwyżek między węzłami).

Turbiny: krzywa mocy typowej turbiny wielomegawatowej (start 3 m/s, moc znamionowa od 12 m/s,
wyłączenie 25 m/s), wiatr z Open-Meteo przeliczony na wysokość piasty profilem potęgowym.
"""

from __future__ import annotations

import numpy as np

from .config import ALL_SITES, WIND_FARM_SITE, SimConfig, SiteConfig

V_CUT_IN, V_RATED, V_CUT_OUT = 3.0, 12.0, 25.0
SYNTH_MEAN_V = 6.0  # średnia prędkość wiatru na wysokości piasty w trybie syntetycznym [m/s]
SYNTH_SD_V = 2.0    # odchylenie standardowe prędkości [m/s]


def farm_power_curve(v: np.ndarray, rated_kw: float) -> np.ndarray:
    """Moc farmy [kW] z prędkości wiatru na wysokości piasty (przybliżenie sześcienne do mocy znamionowej)."""
    v = np.asarray(v, dtype=float)
    p = np.clip((v**3 - V_CUT_IN**3) / (V_RATED**3 - V_CUT_IN**3), 0.0, 1.0) * rated_kw
    return np.where((v < V_CUT_IN) | (v >= V_CUT_OUT), 0.0, p)


def farm_profile(cfg: SimConfig) -> np.ndarray:
    """Produkcja całej farmy w każdym kroku [kW] (wspólna dla wszystkich węzłów i strategii)."""
    rated_kw = 1000.0 * cfg.wind_farm_mw
    if cfg.data_source == "real":
        from .real_data import _days, fetch_weather, hub_wind_speed, resample, sim_grid_utc

        d0, d1 = _days(cfg)
        w = resample(fetch_weather(WIND_FARM_SITE["lat"], WIND_FARM_SITE["lon"], d0, d1), sim_grid_utc(cfg))
        v = hub_wind_speed(w["v10"].to_numpy(), w["v100"].to_numpy(), cfg.wind_farm_hub_m)
    else:
        from .synthetic import _ar1

        rng = np.random.default_rng(cfg.seed * 7919 + 17)  # osobny strumień – nie zmienia floty EV ani profili
        n_h = int(np.ceil(cfg.n_steps * cfg.dt_h)) + 2
        # godzinowy proces AR(1) (odch. stacjonarne ≈ SYNTH_SD_V, korelacja ~ doba) interpolowany na siatkę Δt
        v_h = SYNTH_MEAN_V + _ar1(rng, n_h, 0.96, SYNTH_SD_V * np.sqrt(1 - 0.96**2))
        v = np.clip(np.interp(np.arange(cfg.n_steps) * cfg.dt_h, np.arange(n_h), v_h), 0.0, 30.0)
    return farm_power_curve(v, rated_kw)


def site_share(site: SiteConfig) -> float:
    """Udział węzła w farmie: jawny ``farm_share`` albo proporcjonalnie do mocy umownej wśród ALL_SITES."""
    if site.farm_share > 0:
        return site.farm_share
    return site.p_contract / sum(s.p_contract for s in ALL_SITES)


def assign_shares(sites: list[SiteConfig]) -> list[SiteConfig]:
    """Podział farmy między symulowane węzły proporcjonalnie do mocy umownej (suma udziałów = 1)."""
    total = sum(s.p_contract for s in sites)
    return [s.with_(farm_share=s.p_contract / total) for s in sites]
