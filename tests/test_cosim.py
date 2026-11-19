"""Wspólna symulacja węzłów w sieci 110/15 kV z rozpływem mocy i koordynatorem sieciowym."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from microgrid.config import ALL_SITES, KRAKOW_SITES, SimConfig
from microgrid.cosim import run_network
from microgrid.metrics_evaluator import energy_balance_residuals
from microgrid.simulation import run

AGH = ALL_SITES[0]
CFG = SimConfig(days=1, dt_min=15, network=True)
CFG_STRESS = SimConfig(days=1, dt_min=15, network=True, pv_multiplier=8)  # czerwiec, dane syntetyczne
OUT_STEPS = slice(40, 56)  # 10:00–14:00 przy Δt = 15 min


@pytest.fixture(scope="module")
def all_c():
    return run_network(ALL_SITES, "C", CFG)


@pytest.fixture(scope="module")
def stress():
    on = run_network([AGH], "A", CFG_STRESS)
    off = run_network([AGH], "A", dataclasses.replace(CFG_STRESS, network_control=False))
    return on, off


def test_network_matches_independent_when_no_violations(all_c):
    res, net = all_c
    cfg0 = dataclasses.replace(CFG, network=False)
    for site in ALL_SITES:
        ts = res[site.name].ts
        assert not ts["net_violation"].any(), site.name
        ref = run(site, "C", cfg0).ts
        assert np.allclose(ts["p_grid"], ref["p_grid"], atol=1e-6), site.name
        assert ts["p_curt_net"].max() == 0
    assert net.pf_ok.all()
    assert {"v_node", "feeder_loading", "trafo_loading", "p_curt_net", "net_violation"} <= set(ts.columns)
    assert net.hv_loading.shape == (CFG.n_steps, len(net.branches))
    assert (net.losses_kw > 0).all()


def test_stress_pv_control_removes_violations(stress):
    (on, _), (off, _) = stress
    n_off = int(off[AGH.name].ts["net_violation"].sum())
    n_on = int(on[AGH.name].ts["net_violation"].sum())
    assert n_off > 0
    assert n_on == 0 or n_on <= 0.1 * n_off, (n_on, n_off)
    assert on[AGH.name].ts["p_curt_net"].max() > 0
    assert off[AGH.name].ts["p_curt_net"].max() == 0


def test_energy_balance_with_network(stress):
    for res, _ in stress:
        for r in res.values():
            for name, v in energy_balance_residuals(r).items():
                assert abs(v) < 1e-6, (r.site.name, name, v)


def test_full_blackout_skips_pf():
    cfg = dataclasses.replace(CFG, outage_start_h=10.0, outage_dur_h=4.0)
    res, net = run_network(KRAKOW_SITES, "C", cfg)
    out = ~cfg.grid_available()
    assert out.sum() == 16
    assert not net.pf_ok[out].any()
    assert net.pf_ok[~out].all()
    assert np.isnan(net.losses_kw[out]).all() and np.isnan(net.hv_loading[out]).all()
    cfg0 = dataclasses.replace(cfg, network=False)
    for site in KRAKOW_SITES:
        ts, ref = res[site.name].ts, run(site, "C", cfg0).ts
        assert not ts["net_violation"].any()
        for col in ("p_grid", "p_shed", "p_curt", "soc_bess"):
            assert np.allclose(ts[col], ref[col], atol=1e-6), (site.name, col)
        assert ts["v_node"][out].isna().all()


def test_partial_outage():
    cfg = dataclasses.replace(CFG, outage_start_h=10.0, outage_dur_h=4.0, outage_gpz=("GPZ AGH",))
    res, net = run_network(KRAKOW_SITES, "C", cfg)
    out = ~cfg.grid_available()
    assert net.pf_ok.all()
    agh = res[AGH.name].ts
    assert (~agh["grid_ok"][out]).all()
    assert agh["p_grid"][out].abs().max() < 1e-6
    assert agh["v_node"][out].isna().all()
    assert not np.isnan(net.gpz_loading["GPZ AGH"][~out]).any()
    for site in KRAKOW_SITES[1:]:
        ts = res[site.name].ts
        assert ts["grid_ok"].all()
        assert ts["v_node"].notna().all(), site.name


def test_internal_exchange_nonnegative(all_c):
    res, net = all_c
    p = np.column_stack([r.ts["p_grid"].to_numpy() for r in res.values()])
    exp = np.clip(-p, 0, None).sum(axis=1)
    imp = np.clip(p, 0, None).sum(axis=1)
    x = net.internal_exchange_kw.to_numpy()
    assert (x >= 0).all()
    assert (x <= np.minimum(exp, imp) + 1e-9).all()
    assert np.allclose(x, np.minimum(exp, imp))


def test_outage_of_gpz_without_node():
    cfg = dataclasses.replace(CFG, outage_start_h=10.0, outage_dur_h=4.0, outage_gpz=("GPZ Politechnika",))
    res, net = run_network([AGH], "C", cfg)
    out = ~cfg.grid_available()
    g = net.gpz_loading["GPZ Politechnika"]
    assert ((g[out] == 0) | g[out].isna()).all()
    assert (g[~out] > 0).all()
    assert net.pf_ok.all() and res[AGH.name].ts["grid_ok"].all()


NET_KPIS = ["U min [p.u.]", "U max [p.u.]", "Obc. linii SN max [%]", "Obc. transf. max [%]",
            "Ograniczenie PV przez sieć [kWh]", "Naruszenia sieci [kroki]"]


def test_network_kpis_present_and_nan_without_network(all_c):
    from microgrid.metrics_evaluator import kpis, network_summary
    res, net = all_c
    k = kpis(res[AGH.name])
    for name in NET_KPIS:
        assert np.isfinite(k[name]), name
    assert 0.9 < k["U min [p.u.]"] <= k["U max [p.u.]"] < 1.1
    s = network_summary(net, CFG.dt_h)
    assert set(s) == {"Obc. linii 110 kV max [%]", "Straty sieci [MWh]", "Wymiana wewnętrzna [kWh]",
                      "Pobór z PSE max [MW]", "Produkcja EC Łęg [MWh]"}
    assert all(np.isfinite(v) for v in s.values())
    assert s["Straty sieci [MWh]"] > 0
    k0 = kpis(run(AGH, "C", dataclasses.replace(CFG, network=False)))
    for name in NET_KPIS:
        assert np.isnan(k0[name]), name


def test_hosting_capacity_agh():
    from microgrid.analysis import hosting_capacity
    a = hosting_capacity(AGH, CFG, "A")
    b = hosting_capacity(AGH, CFG, "B")
    for h in (a, b):
        assert h["kWp"] > AGH.pv_kwp
        assert h["ograniczenie"] in {"transformator", "linia SN", "napięcie", "brak (≥ maks.)"}
    assert a["kWp"] <= b["kWp"] + 25.0  # BESS zjada część nadwyżki
