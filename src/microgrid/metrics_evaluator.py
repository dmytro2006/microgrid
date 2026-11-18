"""Wskaźniki KPI i walidacja bilansowa."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .simulation import Results
from .aging import AgingParams, aging_kpis
from .tariff import DEFAULT_TARIFF, cost_breakdown


def kpis(r: Results) -> dict[str, float]:
    ts, ses, dt_h = r.ts, r.sessions, r.cfg.dt_h
    p_max = r.site.p_max_kw
    demand = ts["load"] + ts["p_ev_ch"]
    gen = ts["pv"] + ts["wind"]

    over = ts["p_grid"].abs() > p_max + 1e-9
    # UDR liczony dla pojazdów, które zostały podłączone i odjechały w horyzoncie T;
    # pojazdy odrzucone (kolejka pełna / czas minął w kolejce) raportowane osobno.
    done = ses[ses["status"] == "served"]
    unmet = (done["soc_final"] < done["soc_target"] - 1e-3)
    served = ses[ses["status"].isin(["served", "end_of_sim"])]
    # UDR wykonalne: tylko pojazdy, które fizycznie dało się naładować do celu w czasie postoju
    park_h = (done["t_dep"] - done["t_arr"]).dt.total_seconds() / 3600 - done["wait_min"].fillna(0) / 60
    feasible = done["p_max_kw"] * park_h >= (done["soc_target"] - done["soc_init"]) * done["cap_kwh"] - 1e-6

    costs = cost_breakdown(ts, r.cfg.dt_min, r.cfg.days, r.site.p_contract, r.site.bess_kwh, DEFAULT_TARIFF)
    efc = (ts["p_bess"].abs().sum() * dt_h) / (2 * r.site.bess_kwh) if r.site.bess_kwh else 0.0
    t = DEFAULT_TARIFF
    aging = aging_kpis(ts["soc_bess"].to_numpy(), ts["t_amb"].to_numpy(), dt_h, r.site.bess_kwh,
                       t.bess_capex_pln_kwh, AgingParams(cycle_life_ref=t.bess_cycle_life, dod_ref=t.bess_dod))
    # koszt zużycia BESS z modelu starzenia (zamiast liniowego przybliżenia na kWh przepływu)
    costs["Koszt całk. [PLN]"] += aging["Zużycie BESS [PLN]"] - costs["Zużycie BESS [PLN]"]
    costs["Zużycie BESS [PLN]"] = aging["Zużycie BESS [PLN]"]
    return {
        "SSR [%]": 100 * np.minimum(demand, gen).sum() / demand.sum(),
        "SSR_import [%]": 100 * (1 - ts["p_grid"].clip(lower=0).sum() / demand.sum()),
        "T_over [min/doba]": over.sum() * r.cfg.dt_min / r.cfg.days,
        "dP_over_max [kW]": float(max(0.0, (ts["p_grid"].abs() - p_max).max())),
        "P_grid_peak [kW]": float(ts["p_grid"].max()),
        "UDR [%]": 100 * unmet.mean() if len(done) else 0.0,
        "UDR wykonalne [%]": 100 * unmet[feasible].mean() if feasible.any() else 0.0,
        "EFC [-]": efc,
        "Starzenie cykl. [%]": aging["Starzenie cykl. [%]"],
        "Starzenie kal. [%]": aging["Starzenie kal. [%]"],
        "Utrata SoH BESS [%]": aging["Utrata SoH BESS [%]"],
        "Koszt [PLN]": float((ts["p_grid"] * ts["price"] / 1000.0).sum() * dt_h),
        **costs,
        "E_EV [kWh]": float(ts["p_ev_ch"].sum() * dt_h),
        "E_V2G [kWh]": float(ts["p_ev_dis"].sum() * dt_h),
        "EV łącznie": float(len(ses)),
        "EV odrzucone": float(ses["status"].isin(["balked", "reneged"]).sum()),
        "Śr. oczekiwanie [min]": float(served["wait_min"].mean()) if len(served) else 0.0,
    }


def energy_balance_residuals(r: Results) -> dict[str, float]:
    """Walidacja bilansowa (prawo zachowania energii) w horyzoncie T [kWh].

    - ``pcc``:  ∫(P_grid + P_PV + P_Wind − P_load − ΣP_EV) dt − ∫P_BESS dt
    - ``bess``: ∫P_BESS dt − (ΔE_BESS + straty)
    - ``ev``:   ∫ΣP_EV dt − Σ_i (E_in,i − E_out,i)
    """
    ts, dt_h = r.ts, r.cfg.dt_h
    lhs = ((ts["p_grid"] + ts["pv"] + ts["wind"] - ts["load"] - ts["p_ev"]).sum()) * dt_h
    e_bess_grid = ts["p_bess"].sum() * dt_h
    return {
        "pcc": float(lhs - e_bess_grid),
        "bess": float(e_bess_grid - ((r.bess_e_final - r.bess_e_init) + r.bess_losses_kwh)),
        "ev": float(ts["p_ev"].sum() * dt_h - (r.sessions["energy_in_kwh"] - r.sessions["energy_out_kwh"]).sum()),
    }


def kpi_table(results: dict) -> pd.DataFrame:
    rows = []
    for (site, strategy), r in results.items():
        rows.append({"Lokalizacja": site, "Scenariusz": strategy, **kpis(r)})
    return pd.DataFrame(rows).set_index(["Lokalizacja", "Scenariusz"])
