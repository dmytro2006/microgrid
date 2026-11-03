"""Wykresy matplotlib (CLI i Streamlit)."""

from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams.update({"font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8,
                     "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7})

from .controller import CONTROLLERS
from .simulation import Results

COLORS = {
    "load": "#6c757d", "ev": "#e8590c", "pv": "#f2b705", "wind": "#3bb2d0",
    "bess": "#2f9e44", "grid": "#1c4c78", "limit": "#c92a2a",
    "A": "#c92a2a", "B": "#f08c00", "C": "#2f9e44",
}


def _fmt_time(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    ax.grid(alpha=0.3)


def plot_timeseries(r: Results) -> plt.Figure:
    ts = r.ts
    t = ts["time"]
    fig, axes = plt.subplots(4, 1, figsize=(12, 6.2), sharex=True,
                             gridspec_kw={"height_ratios": [2.6, 1, 1, 0.9]})
    fig.suptitle(f"{r.site.name} – {CONTROLLERS[r.strategy].label}", fontsize=10, fontweight="bold")

    ax = axes[0]
    ax.fill_between(t, 0, ts["pv"], color=COLORS["pv"], alpha=0.35, label="PV")
    ax.fill_between(t, ts["pv"], ts["pv"] + ts["wind"], color=COLORS["wind"], alpha=0.35, label="Wiatr")
    ax.plot(t, ts["load"], color=COLORS["load"], lw=1.2, label="Odbiory bazowe")
    ax.plot(t, ts["p_ev"], color=COLORS["ev"], lw=1.2, label="Σ EV (netto)")
    ax.plot(t, ts["p_bess"], color=COLORS["bess"], lw=1.2, label="BESS (+ład./−rozł.)")
    ax.plot(t, ts["p_grid"], color=COLORS["grid"], lw=2.0, label="P_grid (PCC)")
    p_max = r.site.p_max_kw
    ax.axhline(p_max, color=COLORS["limit"], ls="--", lw=1.2, label="P_max")
    over = ts["p_grid"].abs() > p_max
    ax.fill_between(t, p_max, ts["p_grid"], where=over, color=COLORS["limit"], alpha=0.3,
                    interpolate=True, label="Przeciążenie")
    # zakres osi dopasowany do danych (−P_max rysowane tylko przy dużym eksporcie)
    y_min = min(ts[["p_grid", "p_ev", "p_bess"]].min().min(), 0.0)
    y_max = max(ts["p_grid"].max(), ts["load"].max(), p_max)
    if y_min < -0.8 * p_max:
        ax.axhline(-p_max, color=COLORS["limit"], ls="--", lw=1.2)
    pad = 0.08 * (y_max - y_min)
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_ylabel("Moc [kW]")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=8, fontsize=7, frameon=False,
              borderaxespad=0.1)
    _fmt_time(ax)

    ax = axes[1]
    ax.plot(t, 100 * ts["soc_bess"], color=COLORS["bess"], lw=1.5)
    ax.set_ylabel("SoC BESS\n[%]")
    ax.set_ylim(0, 100)
    _fmt_time(ax)

    ax = axes[2]
    ax.step(t, ts["n_active"], where="post", color=COLORS["ev"], label="Podłączone EV")
    ax.step(t, ts["n_queue"], where="post", color=COLORS["grid"], label="Kolejka")
    ax.axhline(r.site.n_chargers, color="k", ls=":", lw=1, label="Liczba ładowarek")
    ax.set_ylabel("Pojazdy")
    ax.set_ylim(-0.5, max(r.site.n_chargers, ts["n_queue"].max()) * 1.5)
    ax.legend(loc="upper left", ncol=3, fontsize=7, frameon=False)
    _fmt_time(ax)

    ax = axes[3]
    ax.plot(t, ts["price"], color="#7048e8", lw=1.2)
    ax.set_ylabel("Cena\n[PLN/MWh]")
    _fmt_time(ax)

    fig.tight_layout(h_pad=0.4)
    return fig


def plot_grid_comparison(results: dict, site_name: str) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(12, 3.3))
    p_max = None
    for (name, strategy), r in results.items():
        if name != site_name:
            continue
        p_max = r.site.p_max_kw
        ax.plot(r.ts["time"], r.ts["p_grid"], color=COLORS[strategy], lw=1.5, label=CONTROLLERS[strategy].label)
    if p_max is not None:
        ax.axhline(p_max, color="k", ls="--", lw=1, label="P_max")
    ax.set_title(f"{site_name} – moc w węźle PCC: porównanie scenariuszy", fontweight="bold")
    ax.set_ylabel("P_grid [kW]")
    ax.legend(fontsize=8)
    _fmt_time(ax)
    fig.tight_layout()
    return fig


def plot_kpi_bars(table: pd.DataFrame, metrics=("T_over [min/doba]", "dP_over_max [kW]", "SSR_import [%]",
                                               "UDR [%]", "Koszt [PLN]", "EFC [-]")) -> plt.Figure:
    fig, axes = plt.subplots(2, 3, figsize=(12, 5))
    df = table.reset_index()
    sites = list(dict.fromkeys(df["Lokalizacja"]))
    strategies = list(dict.fromkeys(df["Scenariusz"]))
    width = 0.8 / len(strategies)
    for ax, m in zip(axes.flat, metrics):
        for j, st in enumerate(strategies):
            vals = [df[(df["Lokalizacja"] == s) & (df["Scenariusz"] == st)][m].iloc[0] for s in sites]
            ax.bar([i + j * width for i in range(len(sites))], vals, width, color=COLORS[st], label=st)
        ax.set_title(m, fontsize=10)
        ax.set_xticks([i + width * (len(strategies) - 1) / 2 for i in range(len(sites))])
        ax.set_xticklabels([s.split(" ")[0] for s in sites], fontsize=8)
        ax.grid(axis="y", alpha=0.3)
    axes.flat[0].legend(title="Scenariusz", fontsize=8)
    fig.suptitle("KPI – porównanie scenariuszy A/B/C", fontweight="bold")
    fig.tight_layout()
    return fig
