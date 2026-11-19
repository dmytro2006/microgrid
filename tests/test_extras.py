"""Taryfa, analizy (Monte Carlo, dobór), OSM i raport."""

import pandas as pd

from microgrid.analysis import InvestmentCosts, monte_carlo, sizing_grid, summarize
from microgrid.config import KRAKOW_SITES, SimConfig
from microgrid.metrics_evaluator import kpi_table
from microgrid.osm import _max_power_kw, parse_overpass
from microgrid.report import build_report
from microgrid.simulation import run_all
from microgrid.tariff import Tariff, capacity_fee_mask, cost_breakdown

CFG = SimConfig(days=1, dt_min=15)


def test_capacity_fee_hours():
    t = pd.to_datetime(["2026-06-15 06:59", "2026-06-15 07:00", "2026-06-15 21:59", "2026-06-15 22:00",
                        "2026-06-13 12:00"])  # pon. … oraz sobota
    assert capacity_fee_mask(t).tolist() == [False, True, True, False, False]


def test_overrun_penalty_uses_15min_average():
    times = pd.date_range("2026-06-15", periods=12, freq="5min")
    ts = pd.DataFrame({"time": times, "p_grid": [100.0] * 12, "price": 500.0, "p_bess": 0.0})
    ts.loc[0, "p_grid"] = 160.0  # chwilowo 160 kW → średnia z kwadransa 120 kW
    c = cost_breakdown(ts, 5, 1.0, p_contract_kw=110.0, bess_kwh=0.0, tariff=Tariff())
    assert abs(c["Kara moc umowna [PLN]"] - 10 * 12.0 * 10.0) < 1e-9


def test_crf():
    assert abs(InvestmentCosts.crf(0.0, 10) - 0.1) < 1e-12
    assert InvestmentCosts.crf(0.06, 15) > 1 / 15


def test_monte_carlo_and_sizing():
    mc = monte_carlo(KRAKOW_SITES[0], CFG, ("A", "C"), n=3, n_jobs=1)
    assert len(mc) == 6 and set(mc["Scenariusz"]) == {"A", "C"}
    summ = summarize(mc, ["Koszt całk. [PLN]"])
    assert summ["± 95% CI"].notna().all()
    sz = sizing_grid(KRAKOW_SITES[0], CFG, "C", [0, 100], [0, 200], n_jobs=1)
    assert len(sz) == 4 and (sz["Koszt łączny [PLN]"] >= sz["Koszt całk. [PLN]"]).all()


def test_osm_parsing():
    js = {"elements": [
        {"type": "node", "id": 1, "lat": 50.06, "lon": 19.94,
         "tags": {"name": "A", "socket:type2:output": "22 kW", "capacity": "2"}},
        {"type": "way", "id": 2, "center": {"lat": 50.07, "lon": 19.95}, "tags": {"socket:ccs:output": "150kW"}},
        {"type": "node", "id": 3, "tags": {}},
    ]}
    df = parse_overpass(js)
    assert len(df) == 2 and df["max_kw"].tolist() == [22.0, 150.0]
    assert _max_power_kw({"socket:type2:output": "11000 W"}) == 11.0


def test_report_builds():
    res = run_all(KRAKOW_SITES[:1], CFG, strategies=("A", "D"), n_jobs=1)
    html = build_report(res, kpi_table(res))
    assert html.startswith("<!doctype html>") and "<svg" in html and "MPC" in html
