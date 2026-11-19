"""Starzenie BESS, moc umowna, prognoza przyjazdów w MPC, nowe węzły i animacje stacji."""

import numpy as np
import pytest

from microgrid.aging import calendar_loss, cycle_loss, rainflow, turning_points
from microgrid.analysis import best_contract, contract_sweep
from microgrid.animation import sankey_html, station_html
from microgrid.config import ALL_SITES, KRAKOW_SITES_EXTRA, SimConfig
from microgrid.ev_generator import expected_arrivals
from microgrid.metrics_evaluator import energy_balance_residuals, kpis
from microgrid.simulation import run

CFG = SimConfig(days=1, dt_min=15)


def test_turning_points_and_rainflow():
    assert turning_points(np.array([0, 1, 1, 2, 1, 0, 0, 3])).tolist() == [0, 2, 0, 3]
    full = [r for r, c in rainflow(np.array([0.5, 0.2, 0.4, 0.3, 0.9])) if c == 1.0]
    assert full == [pytest.approx(0.1)]  # mały cykl 0.4→0.3 wewnątrz dużego


def test_cycle_loss_wohler():
    shallow = cycle_loss(np.tile([0.4, 0.6], 200))   # 200 cykli DoD 0.2
    deep = cycle_loss(np.tile([0.1, 0.9], 50))        # 50 cykli DoD 0.8
    assert shallow < deep  # ta sama energia, płytkie cykle zużywają mniej (k > 1)


def test_calendar_loss_temperature_and_soc():
    n = 24 * 30
    base = calendar_loss(np.full(n, 0.5), np.full(n, 25.0), 1.0)
    assert calendar_loss(np.full(n, 0.5), np.full(n, 40.0), 1.0) > base
    assert calendar_loss(np.full(n, 0.9), np.full(n, 25.0), 1.0) > base
    assert base == pytest.approx(0.02 * 30 / 365, rel=1e-3)


def test_contract_power_limits_ems_and_cost():
    site = KRAKOW_SITES_EXTRA[3]
    df = contract_sweep(site, CFG, ("A", "C"), [0.6 * site.p_max_kw, site.p_max_kw], n_jobs=1)
    low = df[(df["Scenariusz"] == "C") & (df["P_umowna [kW]"] < site.p_max_kw)].iloc[0]
    high = df[(df["Scenariusz"] == "C") & (df["P_umowna [kW]"] == site.p_max_kw)].iloc[0]
    assert low["Opł. stała [PLN]"] < high["Opł. stała [PLN]"]
    # EMS ogranicza EV i BESS, ale nie odbiory bazowe – szczyt spada, choć nie zawsze poniżej limitu
    assert low["P_grid_peak [kW]"] < high["P_grid_peak [kW]"]
    assert set(best_contract(df).index) == {"A", "C"}


def test_expected_arrivals_sum():
    site = ALL_SITES[0]
    lam = expected_arrivals(site, SimConfig(days=2, dt_min=5))
    assert lam.sum() == pytest.approx(2 * site.ev_per_day, rel=1e-2)


def test_mpc_with_ev_forecast_runs():
    for f in (True, False):
        r = run(ALL_SITES[1], "D", SimConfig(days=1, dt_min=15, mpc_ev_forecast=f))
        assert kpis(r)["T_over [min/doba]"] == 0


@pytest.mark.parametrize("site", KRAKOW_SITES_EXTRA, ids=lambda s: s.name)
def test_new_sites_real_data(site):
    r = run(site, "C", SimConfig(days=1, dt_min=15, data_source="real", ev_fleet="real"))
    assert max(abs(v) for v in energy_balance_residuals(r).values()) < 1e-6
    assert r.ts["t_amb"].between(-30, 45).all()


def test_ev_trace_and_station_animations():
    r = run(ALL_SITES[0], "D", CFG)
    tr = r.ev_trace
    assert len(tr) and tr.groupby("k").size().max() <= r.site.n_chargers
    assert (tr.groupby("k")["id"].count().reindex(range(len(r.ts)), fill_value=0).to_numpy()
            == r.ts["n_active"].to_numpy()).all()
    a = run(ALL_SITES[0], "A", CFG)
    assert "makePlayer" in station_html([a, r]) and "<svg" in sankey_html(r)
