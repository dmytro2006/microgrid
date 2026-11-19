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


# Wartości policzone na kodzie sprzed refaktoryzacji pętli (SiteSim) – wynik ma się nie zmienić.
REGRESSION = {
    "A": (15183.082169, 46.809431),
    "B": (16111.210732, 152.189942),
    "C": (15429.118099, 127.084028),
    "D": (15589.11862, 125.12093),
}


@pytest.mark.parametrize("st", list(REGRESSION))
def test_run_unchanged_after_refactor(st):
    cfg = SimConfig(days=2, dt_min=15, seed=3, outage_start_h=17.0, outage_dur_h=4.0, wind_farm="gpz")
    ts = run(KRAKOW_SITES[1], st, cfg).ts
    assert (round(ts["p_grid"].sum(), 6), round(ts["soc_bess"].sum(), 6)) == REGRESSION[st]


def test_pv_multiplier():
    from microgrid.simulation import make_inputs
    base, evs0, _ = make_inputs(KRAKOW_SITES[0], SimConfig(days=1, dt_min=15, seed=5))
    dbl, evs1, _ = make_inputs(KRAKOW_SITES[0], SimConfig(days=1, dt_min=15, seed=5, pv_multiplier=2.0))
    assert base["pv"].sum() > 0
    assert dbl["pv"].sum() == pytest.approx(2.0 * base["pv"].sum(), rel=1e-9)
    for key in base:
        if key != "pv":
            assert (dbl[key] == base[key]).all(), key
    assert [(e.t_arr, e.cap_kwh) for e in evs0] == [(e.t_arr, e.cap_kwh) for e in evs1]


def test_outage_only_selected_gpz():
    cfg = SimConfig(days=1, dt_min=15, seed=3, outage_start_h=17.0, outage_dur_h=4.0, outage_gpz=("GPZ AGH",))
    agh = run(KRAKOW_SITES[0], "C", cfg).ts
    kaz = run(KRAKOW_SITES[2], "C", cfg).ts
    assert (~agh["grid_ok"]).sum() == 16
    assert agh["grid_ok"].iloc[0]
    assert kaz["grid_ok"].all()


def test_site_sim_commit_curt_net_and_extra():
    import simpy

    from microgrid.ev_generator import TICK_EPS
    from microgrid.simulation import SiteSim

    cfg = SimConfig(days=1, dt_min=15, seed=3)
    env = simpy.Environment()
    sim = SiteSim(env, KRAKOW_SITES[0], "C", cfg)

    def ticker():
        yield env.timeout(TICK_EPS)
        for k in range(cfg.n_steps):
            prop = sim.propose(k)
            prop.curt_net = 0.5 * (prop.pv + prop.wind)
            sim.commit(prop, extra={"v_pu": 1.0})
            yield env.timeout(cfg.dt_min)

    sim.start()
    env.process(ticker())
    env.run(until=cfg.horizon_min + 2 * TICK_EPS)
    r = sim.results()
    assert r.ts["p_curt_net"].sum() > 0
    assert (r.ts["v_pu"] == 1.0).all()
    for name, v in energy_balance_residuals(r).items():
        assert abs(v) < 1e-6, (name, v)
