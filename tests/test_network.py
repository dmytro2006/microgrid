"""Testy modelu sieci 110/15 kV (pandapower) z węzłami mikrosieci."""

from __future__ import annotations

import math
import time

import pytest

from microgrid.config import ALL_SITES, CHP_P_MAX_KW, CHP_P_MIN_KW, ZTPO_KW
from microgrid.grid_osm import load_power_data
from microgrid.network import GridModel

AGH = "AGH / Czarna Wieś"


@pytest.fixture(scope="module")
def model() -> GridModel:
    return GridModel(ALL_SITES, load_power_data(), farm=False)


def _step(m: GridModel, p: dict[str, float] | None = None, chp_kw: float = CHP_P_MIN_KW,
          gpz_out: frozenset[str] = frozenset()):
    loads = {s.name: 50.0 for s in ALL_SITES}
    loads.update(p or {})
    m.set_step(loads, bg=0.8, chp_kw=chp_kw, ztpo_kw=ZTPO_KW, farm_kw=0.0, gpz_out=gpz_out)
    return m.solve()


def test_base_case_converges(model):
    r = _step(model)
    assert r.pf_ok
    assert set(r.v_node) == {s.name for s in ALL_SITES}
    assert all(0.95 <= v <= 1.05 for v in r.v_node.values()), r.v_node
    assert len(r.hv_loading) == len(model.branches)
    assert r.losses_kw > 0 and r.pse_import_kw > 0
    assert model.site_gpz[AGH] == "GPZ AGH"
    assert "GPZ AGH" in model.gpz_names and "GPZ AGH" in r.gpz_loading


def test_node_load_raises_loading(model):
    r0 = _step(model, {AGH: 0.0})
    r1 = _step(model, {AGH: 200.0})
    assert r1.trafo_loading[AGH] > r0.trafo_loading[AGH]
    assert r1.feeder_loading[AGH] > r0.feeder_loading[AGH]


def test_export_raises_voltage(model):
    r_exp = _step(model, {AGH: -200.0})
    r_imp = _step(model, {AGH: 200.0})
    assert r_exp.v_node[AGH] > r_imp.v_node[AGH]


def test_chp_reduces_pse_import(model):
    r_min = _step(model, chp_kw=CHP_P_MIN_KW)
    r_max = _step(model, chp_kw=CHP_P_MAX_KW)
    assert r_min.pse_import_kw - r_max.pse_import_kw > 250_000


def test_gpz_out_isolates_node(model):
    r = _step(model, gpz_out=frozenset({"GPZ AGH"}))
    assert r.pf_ok
    assert math.isnan(r.v_node[AGH])
    assert all(not math.isnan(v) for k, v in r.v_node.items() if k != AGH)
    _step(model)  # powrót do pełnej sieci dla kolejnych testów


def test_non_convergence_flagged(model):
    r = _step(model, {AGH: -1e7})
    assert r.pf_ok is False
    assert math.isnan(r.v_node[AGH]) and math.isnan(r.losses_kw) and math.isnan(r.pse_import_kw)
    assert _step(model).pf_ok  # kolejny krok znów się zbiega


def test_solve_speed(model):
    _step(model)
    t0 = time.perf_counter()
    for k in range(50):
        _step(model, {AGH: 4.0 * k})
    assert time.perf_counter() - t0 < 5.0
