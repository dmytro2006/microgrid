"""Profile czasowe sieci 110/15 kV: tło odbiorów GPZ, Elektrociepłownia Łęg i ZTPO.

  - tło: kształt zapotrzebowania KSE (``real``) lub dobowy profil syntetyczny, znormalizowany do 1,
  - CHP Łęg: produkcja sterowana zapotrzebowaniem na ciepło – zależy od średniej dobowej temperatury,
  - ZTPO: praca ciągła ze stałą mocą.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CHP_P_MAX_KW, CHP_P_MIN_KW, CHP_T_DESIGN_C, CHP_T_START_C, ZTPO_KW, SimConfig
from .grid_osm import load_power_data
from .real_data import TZ, _days, fetch_kse, fetch_weather, resample, sim_grid_utc
from .synthetic import _bump, hours_of_day

CHP_SUBSTATION = "GPZ Łęg"   # lokalizacja pogody dla CHP (współrzędne z danych OSM)
T_DAY_SYNTHETIC_C = 20.0     # tryb syntetyczny: temperatura letnia → minimum CHP


def background_shape(cfg: SimConfig) -> np.ndarray:
    """Kształt obciążenia tła (max = 1), długość ``cfg.n_steps``."""
    if cfg.data_source == "real":
        d0, d1 = _days(cfg)
        demand = resample(fetch_kse(d0, d1), sim_grid_utc(cfg))["demand"].to_numpy()
        return demand / demand.max()
    h = hours_of_day(cfg)
    shape = 0.35 + 0.30 * _bump(h, 8.0, 1.5) + 0.65 * _bump(h, 19.0, 2.0)
    return shape / shape.max()


def _chp_coords() -> tuple[float, float]:
    data = load_power_data()
    if data is not None:
        for s in data.substations:
            if s.name == CHP_SUBSTATION:
                return s.lat, s.lon
    raise LookupError(f"brak stacji „{CHP_SUBSTATION}” w danych OSM")


def chp_profile(cfg: SimConfig) -> np.ndarray:
    """Moc elektryczna Elektrociepłowni Łęg [kW] – zależna od średniej dobowej temperatury."""
    if cfg.data_source == "real":
        lat, lon = _chp_coords()
        d0, d1 = _days(cfg)
        grid = sim_grid_utc(cfg)
        temp = resample(fetch_weather(lat, lon, d0, d1), grid)["temp"].to_numpy()
        loc = grid.tz_convert(TZ)
        t_day = pd.Series(temp, index=loc).groupby(loc.date).transform("mean").to_numpy()
    else:
        t_day = np.full(cfg.n_steps, T_DAY_SYNTHETIC_C)
    x = np.clip((CHP_T_START_C - t_day) / (CHP_T_START_C - CHP_T_DESIGN_C), 0.0, 1.0)
    return CHP_P_MIN_KW + (CHP_P_MAX_KW - CHP_P_MIN_KW) * x


def ztpo_profile(cfg: SimConfig) -> np.ndarray:
    """ZTPO – stała moc [kW]."""
    return np.full(cfg.n_steps, float(ZTPO_KW))
