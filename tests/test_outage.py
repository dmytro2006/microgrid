"""Awaria GPZ (blackout): praca wyspowa węzłów."""

import dataclasses

import pytest

from microgrid.config import KRAKOW_SITES, SimConfig
from microgrid.grid_environment import island_dispatch
from microgrid.metrics_evaluator import energy_balance_residuals, kpis
from microgrid.simulation import run

# awaria wieczorem pierwszej doby (szczyt odbiorów, brak PV)
CFG = SimConfig(days=2, dt_min=15, seed=7, outage_start_h=17.0, outage_dur_h=5.0)
SITE = KRAKOW_SITES[0]


@pytest.fixture(scope="module")
def results():
    return {st: run(SITE, st, CFG) for st in ("A", "B", "C", "D")}


def test_grid_mask():
    ok = CFG.grid_available()
    assert (~ok).sum() * CFG.dt_h == pytest.approx(5.0)
    assert SimConfig().grid_available().all()


def test_no_grid_exchange_during_outage(results):
    for st, r in results.items():
        out = ~r.ts["grid_ok"]
        assert out.sum() == 20
        assert r.ts.loc[out, "p_grid"].abs().max() < 1e-6, st
        assert r.ts.loc[~out, "p_shed"].max() == 0 and r.ts.loc[~out, "p_curt"].max() == 0


def test_balance_closes_with_outage(results):
    for st, r in results.items():
        for name, v in energy_balance_residuals(r).items():
            assert abs(v) < 1e-6, (st, name, v)
        ts = r.ts
        assert (ts["load"] <= ts["load_dem"] + 1e-9).all()
        assert (ts["load_dem"] - ts["load"] - ts["p_shed"]).abs().max() < 1e-9


def test_outage_kpis(results):
    for r in results.values():
        k = kpis(r)
        assert k["Awaria GPZ [h]"] == pytest.approx(5.0)
        assert 0 <= k["ENS [%]"] <= 100


def test_announced_outage_helps_mpc():
    sudden = kpis(run(SITE, "D", CFG))["ENS [kWh]"]
    announced = kpis(run(SITE, "D", dataclasses.replace(CFG, outage_announced=True)))["ENS [kWh]"]
    assert announced <= sudden + 1e-6


def test_island_dispatch_priorities():
    # deficyt 100 kW: BESS 30 kW, potem ładowanie EV (40 kW) do zera, V2G 10 kW, reszta odciążenie
    p_ev, p_bess, shed, curt = island_dispatch(
        load=100, gen=40, p_ev={1: 40.0, 2: 0.0}, ev_ch_max={1: 40.0, 2: 10.0}, ev_dis_max={1: 0.0, 2: 10.0},
        p_bess=0.0, bess_ch_max=50.0, bess_dis_max=30.0)
    assert p_bess == pytest.approx(-30) and p_ev[1] == pytest.approx(0) and p_ev[2] == pytest.approx(-10)
    assert shed == pytest.approx(100 - 40 - 30 - 10) and curt == 0
    # nadwyżka 80 kW: BESS 50 kW, EV +10 kW, reszta ograniczenie OZE
    p_ev, p_bess, shed, curt = island_dispatch(
        load=20, gen=100, p_ev={1: 0.0}, ev_ch_max={1: 10.0}, ev_dis_max={1: 0.0},
        p_bess=0.0, bess_ch_max=50.0, bess_dis_max=30.0)
    assert p_bess == pytest.approx(50) and p_ev[1] == pytest.approx(10) and curt == pytest.approx(20)
    assert shed == 0
