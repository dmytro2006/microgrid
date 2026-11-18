"""Testy trybu danych rzeczywistych (korzysta z dołączonego cache `data/cache`, bez sieci)."""

import numpy as np
import pytest

from microgrid.animation import animated_map_html, flow_diagram_html
from microgrid.config import EV_MODELS, KRAKOW_SITES, SimConfig
from microgrid.metrics_evaluator import energy_balance_residuals
from microgrid.real_data import data_dir, hub_wind_speed, pv_from_weather, sim_grid_utc
from microgrid.simulation import run

CFG = SimConfig(days=2, dt_min=15, start="2026-06-15 00:00", data_source="real", ev_fleet="real")


def _have_cache() -> bool:
    return any(data_dir().glob("pse_rce_2026-06.csv"))


needs_cache = pytest.mark.skipif(not _have_cache(), reason="brak danych w data/cache")


def test_pv_model_physics():
    site = KRAKOW_SITES[0]
    p = pv_from_weather(site, np.array([0.0, 1000.0, 1000.0]), np.array([10.0, 0.0, 35.0]))
    assert p[0] == 0
    assert p[1] > p[2]  # wyższa temperatura → niższa moc
    assert p.max() <= site.pv_kwp * site.inverter_eff


def test_hub_wind_between_measurement_heights():
    v = hub_wind_speed(np.array([3.0]), np.array([6.0]), 30.0)
    assert 3.0 < v[0] < 6.0


def test_sim_grid_is_utc_and_local_start():
    g = sim_grid_utc(SimConfig(days=1, dt_min=60, start="2026-06-15 00:00"))
    assert str(g.tz) == "UTC" and len(g) == 24
    assert g[0].hour == 22  # 00:30 CEST = 22:30 UTC poprzedniego dnia


def test_ev_models_valid():
    assert all(m.battery_kwh > 0 and m.ac_kw > 0 and m.dc_kw >= m.ac_kw for m in EV_MODELS)


@needs_cache
@pytest.mark.parametrize("strategy", ["A", "C"])
def test_real_run_balance_and_ranges(strategy):
    r = run(KRAKOW_SITES[0], strategy, CFG)
    assert r.raw is not None and not r.raw.isna().any().any()
    assert max(abs(v) for v in energy_balance_residuals(r).values()) < 1e-6
    assert r.ts["pv"].max() > 0.3 * r.site.pv_kwp  # czerwiec – realne nasłonecznienie
    assert (r.ts["pv"] >= 0).all() and (r.ts["wind"] <= r.site.wind_rated_kw + 1e-9).all()
    assert r.sessions["model"].ne("").all()


@needs_cache
def test_animations_render():
    rs = [run(s, "C", CFG) for s in KRAKOW_SITES]
    m = animated_map_html(rs)
    f = flow_diagram_html(rs[0])
    assert "deck.Deck" in m and KRAKOW_SITES[0].name in m
    assert "<svg" in f and "makePlayer" in f
