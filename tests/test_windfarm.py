"""Wspólna farma wiatrowa poza miastem: warianty PPA i przyłączenie do GPZ."""

import dataclasses

import numpy as np
import pytest

from microgrid.animation import animated_map_html
from microgrid.config import KRAKOW_SITES, SimConfig
from microgrid.map_view import build_deck
from microgrid.metrics_evaluator import energy_balance_residuals, kpis
from microgrid.simulation import run, run_all
from microgrid.windfarm import assign_shares, farm_power_curve, farm_profile, site_share

BASE = SimConfig(days=2, dt_min=15, seed=7, outage_start_h=17.0, outage_dur_h=5.0)
SITE = KRAKOW_SITES[0]


def cfg(variant: str, **kw) -> SimConfig:
    return dataclasses.replace(BASE, wind_farm=variant, **kw)


def test_power_curve():
    p = farm_power_curve(np.array([0, 2.9, 3, 7.5, 12, 20, 25, 30]), 3000)
    assert p[0] == p[1] == p[2] == 0 and 0 < p[3] < 3000
    assert p[4] == p[5] == 3000 and p[6] == p[7] == 0


def test_profile_and_shares():
    f = farm_profile(cfg("ppa"))
    assert len(f) == BASE.n_steps and 0 <= f.min() and f.max() <= 3000 + 1e-9
    assert 0.05 < f.mean() / 3000 < 0.6
    shared = assign_shares(KRAKOW_SITES)
    assert sum(s.farm_share for s in shared) == pytest.approx(1.0)
    assert 0 < site_share(SITE) < 1


def test_ppa_changes_only_billing():
    r0, r1 = run(SITE, "C", BASE), run(SITE, "C", cfg("ppa"))
    # te same przepływy fizyczne, inny rachunek
    assert np.allclose(r0.ts["p_grid"], r1.ts["p_grid"])
    k0, k1 = kpis(r0), kpis(r1)
    assert k0["Farma: energia [kWh]"] == 0 and k1["Farma: energia [kWh]"] > 0
    assert k1["Farma PPA [PLN]"] > 0 and k1["Energia [PLN]"] < k0["Energia [PLN]"]
    assert k1["Dystrybucja [PLN]"] == pytest.approx(k0["Dystrybucja [PLN]"])
    assert k1["ENS [kWh]"] == pytest.approx(k0["ENS [kWh]"])  # farma przez sieć OSD nie pomaga przy awarii


def test_gpz_farm_supplies_island():
    for st in ("A", "C", "D"):
        r_ppa, r_gpz = run(SITE, st, cfg("ppa")), run(SITE, st, cfg("gpz"))
        out = ~r_gpz.ts["grid_ok"]
        ts = r_gpz.ts
        assert (ts.loc[out, "p_grid"] >= -1e-6).all()
        assert (ts.loc[out, "p_grid"] <= ts.loc[out, "p_farm"] + 1e-6).all()
        assert kpis(r_gpz)["ENS [kWh]"] <= kpis(r_ppa)["ENS [kWh]"] + 1e-6, st
        for name, v in energy_balance_residuals(r_gpz).items():
            assert abs(v) < 1e-6, (st, name, v)


def test_announced_outage_with_farm_mpc():
    r = run(SITE, "D", cfg("gpz", outage_announced=True))
    out = ~r.ts["grid_ok"]
    assert (r.ts.loc[out, "p_grid"] <= r.ts.loc[out, "p_farm"] + 1e-6).all()


def test_maps_show_farm():
    res = run_all(KRAKOW_SITES[:2], cfg("gpz"), strategies=("C",), n_jobs=1)
    rs = list(res.values())
    k = int((~rs[0].ts["grid_ok"]).idxmax())
    deck = build_deck(rs, k).to_json()
    assert "Farma wiatrowa" in deck
    assert '"farm": {' in animated_map_html(rs, start_k=k)
