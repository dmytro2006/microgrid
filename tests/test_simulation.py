import pandas as pd
import pytest

from microgrid.config import KRAKOW_SITES, SimConfig
from microgrid.map_view import build_deck
from microgrid.metrics_evaluator import energy_balance_residuals, kpis
from microgrid.simulation import run, run_all

CFG = SimConfig(days=2, dt_min=5, seed=42)


@pytest.fixture(scope="module")
def results():
    return run_all(KRAKOW_SITES, CFG)


def test_energy_balance_closes(results):
    for r in results.values():
        for name, v in energy_balance_residuals(r).items():
            assert abs(v) < 1e-6, (r.site.name, r.strategy, name, v)


def test_bess_soc_within_limits(results):
    for r in results.values():
        assert r.ts["soc_bess"].min() >= r.site.bess_soc_min - 1e-9
        assert r.ts["soc_bess"].max() <= r.site.bess_soc_max + 1e-9


def test_baseline_overloads_and_controlled_scenarios_do_not(results):
    assert any(kpis(results[(s.name, "A")])["T_over [min/doba]"] > 0 for s in KRAKOW_SITES)
    for s in KRAKOW_SITES:
        for st in ("B", "C", "D"):
            assert kpis(results[(s.name, st)])["T_over [min/doba]"] == 0, (s.name, st)


def test_ev_soc_and_vehicles(results):
    for r in results.values():
        ses = r.sessions
        assert len(ses) > 0
        assert ses["soc_final"].between(0, 1).all()
        # nikt nie jest ładowany powyżej celu
        assert (ses["soc_final"] <= ses[["soc_target", "soc_init"]].max(axis=1) + 1e-9).all()


def test_smart_is_cheaper_than_baseline(results):
    for s in KRAKOW_SITES:
        assert kpis(results[(s.name, "C")])["Koszt [PLN]"] < kpis(results[(s.name, "A")])["Koszt [PLN]"]


def test_deterministic():
    a = run(KRAKOW_SITES[0], "C", CFG)
    b = run(KRAKOW_SITES[0], "C", CFG)
    pd.testing.assert_frame_equal(a.ts, b.ts)


@pytest.mark.parametrize("dt", [1, 15])
def test_other_time_steps(dt):
    r = run(KRAKOW_SITES[1], "B", SimConfig(days=1, dt_min=dt))
    assert len(r.ts) == 24 * 60 // dt
    assert max(abs(v) for v in energy_balance_residuals(r).values()) < 1e-6


@pytest.mark.parametrize("forecast", ["perfect", "persistence"])
def test_mpc_forecast_modes(forecast):
    r = run(KRAKOW_SITES[2], "D", SimConfig(days=2, dt_min=15, forecast=forecast))
    assert kpis(r)["T_over [min/doba]"] == 0
    assert max(abs(v) for v in energy_balance_residuals(r).values()) < 1e-6


def test_map_builds(results):
    rs = [results[(s.name, "A")] for s in KRAKOW_SITES]
    deck = build_deck(rs, 100)
    assert len(deck.layers) >= 3


def test_mpc_beats_rule_based_on_total_cost(results):
    for s in KRAKOW_SITES:
        cost = {st: kpis(results[(s.name, st)])["Koszt całk. [PLN]"] for st in "ABCD"}
        assert cost["D"] < cost["C"] < cost["A"], (s.name, cost)


def test_tariff_penalises_overrun(results):
    for s in KRAKOW_SITES:
        a = kpis(results[(s.name, "A")])
        if a["T_over [min/doba]"] > 0:
            assert a["Kara moc umowna [PLN]"] > 0
        assert kpis(results[(s.name, "D")])["Kara moc umowna [PLN]"] == 0
