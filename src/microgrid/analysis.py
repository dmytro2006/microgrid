"""Analizy wielokrotnych uruchomień: Monte Carlo (niepewność) i dobór mocy PV / pojemności BESS.

- ``monte_carlo``  – N powtórzeń z różnymi ziarnami (inne przyjazdy EV, a w trybie syntetycznym
  także inne profile) → średnia, odchylenie i 95% przedział ufności (rozkład t-Studenta) KPI.
- ``sizing_grid``  – przegląd siatki (P_PV, E_BESS) dla wybranej strategii; koszt eksploatacji
  z taryfy + annuitetowy koszt inwestycji przeliczony na horyzont symulacji.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from scipy import stats

from .config import SimConfig, SiteConfig
from .metrics_evaluator import kpis
from .simulation import parallel_map, run


@dataclass(frozen=True)
class InvestmentCosts:
    pv_pln_kwp: float = 3500.0      # instalacja PV (komercyjna, dach / wiata)
    pv_life_y: float = 25.0
    bess_pln_kwh: float = 1500.0    # magazyn LFP z PCS
    bess_life_y: float = 15.0
    discount_rate: float = 0.06

    @staticmethod
    def crf(r: float, n: float) -> float:
        """Współczynnik odzysku kapitału (annuitet)."""
        return r * (1 + r) ** n / ((1 + r) ** n - 1) if r > 0 else 1.0 / n

    def per_day(self, pv_kwp: float, bess_kwh: float) -> float:
        pv = pv_kwp * self.pv_pln_kwp * self.crf(self.discount_rate, self.pv_life_y)
        bess = bess_kwh * self.bess_pln_kwh * self.crf(self.discount_rate, self.bess_life_y)
        return (pv + bess) / 365.0


def _kpi_task(args) -> dict:
    site, strategy, cfg = args
    return kpis(run(site, strategy, cfg))


def _map(tasks: list, n_jobs: int | None) -> list:
    return parallel_map(_kpi_task, tasks, n_jobs)


def monte_carlo(site: SiteConfig, cfg: SimConfig, strategies=("A", "B", "C", "D"), n: int = 10,
                n_jobs: int | None = None) -> pd.DataFrame:
    """Zwraca ramkę: jeden wiersz na (ziarno, strategia) z kompletem KPI."""
    tasks, keys = [], []
    for i in range(n):
        c = replace(cfg, seed=cfg.seed + i)
        for st in strategies:
            tasks.append((site, st, c))
            keys.append((cfg.seed + i, st))
    rows = _map(tasks, n_jobs)
    df = pd.DataFrame(rows)
    df.insert(0, "Scenariusz", [k[1] for k in keys])
    df.insert(0, "seed", [k[0] for k in keys])
    return df


def summarize(mc: pd.DataFrame, metrics: list[str], conf: float = 0.95) -> pd.DataFrame:
    """Średnia ± półszerokość przedziału ufności dla każdej strategii."""
    out = []
    for st, g in mc.groupby("Scenariusz"):
        n = len(g)
        tq = stats.t.ppf(0.5 + conf / 2, n - 1) if n > 1 else np.nan
        for m in metrics:
            x = g[m].astype(float)
            sd = x.std(ddof=1) if n > 1 else 0.0
            out.append({"Scenariusz": st, "KPI": m, "średnia": x.mean(), "odch. std": sd,
                        "± 95% CI": tq * sd / np.sqrt(n) if n > 1 else np.nan,
                        "min": x.min(), "max": x.max()})
    return pd.DataFrame(out).set_index(["KPI", "Scenariusz"]).sort_index()


def sizing_grid(site: SiteConfig, cfg: SimConfig, strategy: str, pv_values, bess_values,
                c_rate: float = 0.5, inv: InvestmentCosts = InvestmentCosts(),
                n_jobs: int | None = None) -> pd.DataFrame:
    """Przegląd (P_PV × E_BESS); moc BESS = c_rate · pojemność."""
    tasks, keys = [], []
    for pv in pv_values:
        for e in bess_values:
            s = site.with_(pv_kwp=float(pv), bess_kwh=float(e), bess_p_kw=float(e) * c_rate)
            tasks.append((s, strategy, cfg))
            keys.append((pv, e))
    rows = _map(tasks, n_jobs)
    df = pd.DataFrame(rows)
    df.insert(0, "E_BESS [kWh]", [k[1] for k in keys])
    df.insert(0, "P_PV [kWp]", [k[0] for k in keys])
    df["Inwestycja [PLN]"] = [inv.per_day(pv, e) * cfg.days for pv, e in keys]
    df["Koszt łączny [PLN]"] = df["Koszt całk. [PLN]"] + df["Inwestycja [PLN]"]
    return df


def contract_sweep(site: SiteConfig, cfg: SimConfig, strategies, values, n_jobs: int | None = None) -> pd.DataFrame:
    """Przegląd mocy umownej (≤ zdolność przyłącza): opłata stała ↔ kary ↔ niedoładowanie EV.

    EMS pilnuje min(P_umowna, P_max), więc niższa moc umowna obniża składnik stały, ale
    ogranicza ładowanie (rośnie UDR) i – w scenariuszu A – generuje kary.
    """
    tasks, keys = [], []
    for v in values:
        s = site.with_(p_contract_kw=float(v))
        for st in strategies:
            tasks.append((s, st, cfg))
            keys.append((v, st))
    rows = _map(tasks, n_jobs)
    df = pd.DataFrame(rows)
    df.insert(0, "Scenariusz", [k[1] for k in keys])
    df.insert(0, "P_umowna [kW]", [k[0] for k in keys])
    return df


def best_contract(df: pd.DataFrame, udr_penalty_pln: float = 0.0) -> pd.DataFrame:
    """Najtańsza moc umowna dla każdej strategii (opcjonalnie z karą za każdy p.p. UDR)."""
    d = df.assign(cel=df["Koszt całk. [PLN]"] + udr_penalty_pln * df["UDR wykonalne [%]"])
    return d.loc[d.groupby("Scenariusz")["cel"].idxmin()].set_index("Scenariusz")


def _pierwsze_ograniczenie(ts: pd.DataFrame) -> str:
    """Typ pierwszego naruszonego elementu (kolejność: transformator, linia SN, napięcie)."""
    from .cosim import LOADING_MAX, V_MAX_PU, V_MIN_PU
    if (ts["trafo_loading"] > LOADING_MAX).any():
        return "transformator"
    if (ts["feeder_loading"] > LOADING_MAX).any():
        return "linia SN"
    if ((ts["v_node"] > V_MAX_PU) | (ts["v_node"] < V_MIN_PU)).any():
        return "napięcie"
    return "napięcie"


def hosting_capacity(site: SiteConfig, cfg: SimConfig, strategy: str = "A", pv_max_kwp: float = 5000.0,
                     tol_kwp: float = 25.0) -> dict:
    """Zdolność przyłączeniowa PV węzła: największa moc PV bez naruszenia ograniczeń sieci.

    Bisekcja po ``site.pv_kwp`` (jedna doba, Δt = 15 min, bez koordynatora sieciowego, bez farmy
    wiatrowej i awarii; liczą się tylko naruszenia w krokach bez naruszeń przy PV = 0). ``ograniczenie`` – pierwszy naruszony element na granicy.
    """
    from .cosim import run_network
    c = replace(cfg, network=True, network_control=False, days=1, dt_min=15, wind_farm="none",
                outage_start_h=None)

    def run_ts(kwp: float) -> pd.DataFrame:
        res, _ = run_network([site.with_(pv_kwp=float(kwp))], strategy, c)
        return res[site.name].ts

    # kroki, w których sieć jest przeciążona już bez PV (np. samym poborem EV), nie są winą PV
    base = run_ts(0.0)["net_violation"].astype(bool).to_numpy()

    def violated(kwp: float):
        ts = run_ts(kwp)
        new = ts["net_violation"].astype(bool).to_numpy() & ~base
        return bool(new.any()), ts[new]

    bad, ts = violated(pv_max_kwp)
    if not bad:
        return {"kWp": float(pv_max_kwp), "ograniczenie": "brak (≥ maks.)"}
    lo, hi, ts_hi = 0.0, float(pv_max_kwp), ts
    while hi - lo > tol_kwp:
        mid = 0.5 * (lo + hi)
        bad, ts = violated(mid)
        if bad:
            hi, ts_hi = mid, ts
        else:
            lo = mid
    return {"kWp": lo, "ograniczenie": _pierwsze_ograniczenie(ts_hi)}


def _hosting_task(args) -> dict:
    site, cfg, strategy, pv_max_kwp = args
    return hosting_capacity(site, cfg, strategy, pv_max_kwp=pv_max_kwp)


def hosting_capacity_table(sites: list[SiteConfig], cfg: SimConfig, strategy: str = "A",
                           pv_max_kwp: float = 5000.0, n_jobs: int | None = None) -> pd.DataFrame:
    """``hosting_capacity`` dla wielu węzłów (równolegle) → tabela: węzeł, GPZ, PV obecnie, zdolność, ograniczenie."""
    from .grid_osm import load_power_data
    from .grid_topology import site_gpz
    data = load_power_data()
    out = parallel_map(_hosting_task, [(s, cfg, strategy, pv_max_kwp) for s in sites], n_jobs)
    return pd.DataFrame([{
        "Węzeł": s.name, "GPZ": site_gpz(s, data).name if data is not None else "",
        "PV obecnie [kWp]": s.pv_kwp, "Zdolność PV [kWp]": o["kWp"], "Ograniczenie": o["ograniczenie"],
    } for s, o in zip(sites, out)]).set_index("Węzeł")
