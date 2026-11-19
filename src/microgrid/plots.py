"""Wykresy matplotlib (CLI i Streamlit)."""

from __future__ import annotations

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams.update({"font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8,
                     "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7})

from .controller import CONTROLLERS
from .simulation import Results

COLORS = {
    "load": "#6c757d", "ev": "#e8590c", "pv": "#f2b705", "wind": "#3bb2d0",
    "bess": "#2f9e44", "grid": "#1c4c78", "limit": "#c92a2a",
    "A": "#c92a2a", "B": "#f08c00", "C": "#2f9e44", "D": "#7048e8",
}


def _fmt_time(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    ax.grid(alpha=0.3)


def _outage_spans(ts: pd.DataFrame, dt_min: int) -> list[tuple]:
    """Przedziały czasu awarii GPZ [(t_od, t_do)]."""
    if "grid_ok" not in ts or ts["grid_ok"].all():
        return []
    out = ~ts["grid_ok"].astype(bool).to_numpy()
    edges = np.flatnonzero(np.diff(np.concatenate([[0], out.astype(int), [0]])))
    t = ts["time"].to_numpy()
    step = pd.Timedelta(minutes=dt_min)
    return [(t[a], t[b - 1] + step) for a, b in zip(edges[::2], edges[1::2])]


def _shade_outage(ax, spans, label: bool = False):
    for i, (a, b) in enumerate(spans):
        ax.axvspan(a, b, color="#495057", alpha=0.13, lw=0, label="Awaria GPZ" if label and i == 0 else None)


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
    spans = _outage_spans(ts, r.cfg.dt_min)
    if spans:
        ax.fill_between(t, ts["load"], ts["load_dem"], where=ts["p_shed"] > 1e-6, color=COLORS["limit"],
                        alpha=0.45, hatch="///", lw=0, step="post", label="Odciążenie (ENS)")
    p_max = r.site.p_max_kw
    ax.axhline(p_max, color=COLORS["limit"], ls="--", lw=1.2, label="P_max")
    over = ts["p_grid"].abs() > p_max
    ax.fill_between(t, p_max, ts["p_grid"], where=over, color=COLORS["limit"], alpha=0.3,
                    interpolate=True, label="Przeciążenie")
    # zakres osi dopasowany do danych (−P_max rysowane tylko przy dużym eksporcie)
    y_min = min(ts[["p_grid", "p_ev", "p_bess"]].min().min(), 0.0)
    y_max = max(ts["p_grid"].max(), ts.get("load_dem", ts["load"]).max(), p_max)
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

    for i, a in enumerate(axes):
        _shade_outage(a, spans, label=i == 0)

    ax = axes[3]
    ax.plot(t, ts["price"], color="#7048e8", lw=1.2)
    ax.set_ylabel("Cena\n[PLN/MWh]")
    _fmt_time(ax)

    if spans:  # legenda z pasem awarii
        axes[0].legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=8, fontsize=7, frameon=False,
                       borderaxespad=0.1)
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
        spans = _outage_spans(r.ts, r.cfg.dt_min)
    if p_max is not None:
        _shade_outage(ax, spans, label=True)
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


def plot_real_inputs(r: Results) -> plt.Figure:
    """Dane rzeczywiste: nasłonecznienie i PV, wiatr i turbina, ceny RCE, kontekst KSE."""
    raw, ts = r.raw, r.ts
    t = ts["time"]
    fig, axes = plt.subplots(4, 1, figsize=(12, 7.2), sharex=True)
    fig.suptitle(f"{r.site.name} – dane rzeczywiste (Open-Meteo, PSE)", fontsize=10, fontweight="bold")

    ax = axes[0]
    ax.fill_between(t, 0, raw["gti"], color=COLORS["pv"], alpha=0.3, label="GTI (35°, S) [W/m²]")
    ax.set_ylabel("GTI [W/m²]")
    ax2 = ax.twinx()
    ax2.plot(t, ts["pv"], color="#e67700", lw=1.3, label="P_PV [kW]")
    ax2.plot(t, raw["temp"], color="#c92a2a", lw=0.9, ls=":", label="T_pow [°C]")
    ax2.set_ylabel("kW / °C")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", ncol=3, frameon=False)
    _fmt_time(ax)

    ax = axes[1]
    ax.plot(t, raw["v10"], color="#99e9f2", lw=0.9, label="v 10 m")
    ax.plot(t, raw["v100"], color="#0b7285", lw=0.9, label="v 100 m")
    ax.plot(t, raw["v_hub"], color=COLORS["wind"], lw=1.4, label=f"v piasta {r.site.wind_hub_m:.0f} m")
    ax.axhline(r.site.wind_v_cut_in, color="k", ls=":", lw=0.8, label="cut-in")
    ax.set_ylabel("Wiatr [m/s]")
    ax2 = ax.twinx()
    ax2.plot(t, ts["wind"], color="#1864ab", lw=1.0, alpha=0.6, label="P_wind [kW]")
    ax2.set_ylabel("kW")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", ncol=5, frameon=False)
    _fmt_time(ax)

    ax = axes[2]
    ax.plot(t, raw["rce"], color="#7048e8", lw=1.2)
    ax.axhline(0, color="k", lw=0.6)
    ax.fill_between(t, raw["rce"], 0, where=raw["rce"] < 0, color="#2f9e44", alpha=0.4, label="cena ujemna")
    ax.set_ylabel("RCE\n[PLN/MWh]")
    _fmt_time(ax)

    ax = axes[3]
    gw = 1000.0
    ax.plot(t, raw["kse_demand"] / gw, color="#495057", lw=1.3, label="zapotrzebowanie KSE")
    ax.stackplot(t, raw["kse_pv"] / gw, raw["kse_wind"] / gw, colors=[COLORS["pv"], COLORS["wind"]],
                 alpha=0.5, labels=["PV (PL)", "wiatr (PL)"])
    ax.set_ylabel("KSE [GW]")
    ax.legend(loc="upper left", ncol=3, frameon=False)
    _fmt_time(ax)
    fig.tight_layout(h_pad=0.4)
    return fig


COST_PARTS = ["Energia [PLN]", "Dystrybucja [PLN]", "Opł. mocowa [PLN]", "Opł. stała [PLN]",
              "Kara moc umowna [PLN]", "Zużycie BESS [PLN]"]
COST_COLORS = ["#1c4c78", "#4dabf7", "#f08c00", "#adb5bd", "#c92a2a", "#2f9e44"]


def plot_cost_breakdown(table: pd.DataFrame, site_name: str) -> plt.Figure:
    """Struktura kosztu całkowitego wg taryfy dla każdej strategii (słupki skumulowane)."""
    df = table.xs(site_name, level="Lokalizacja")
    fig, ax = plt.subplots(figsize=(12, 3.2))
    bottom = pd.Series(0.0, index=df.index)
    for part, col in zip(COST_PARTS, COST_COLORS):
        v = df[part].clip(lower=0)
        ax.barh([CONTROLLERS[s].label for s in df.index], v, left=bottom, color=col, label=part.replace(" [PLN]", ""))
        bottom += v
    for i, (s, tot) in enumerate(df["Koszt całk. [PLN]"].items()):
        ax.text(bottom.iloc[i], i, f"  {tot:,.0f} PLN".replace(",", " "), va="center", fontsize=8, fontweight="bold")
    ax.invert_yaxis()
    ax.set_xlabel("PLN w horyzoncie symulacji")
    ax.set_xlim(0, bottom.max() * 1.18)
    ax.set_title(f"{site_name} – struktura kosztu (taryfa C2x, orientacyjna)", fontweight="bold")
    ax.legend(ncol=6, fontsize=7, loc="lower left", bbox_to_anchor=(0, 1.08), frameon=False)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    return fig


def plot_monte_carlo(mc: pd.DataFrame, metrics: list[str]) -> plt.Figure:
    """Rozrzut KPI między powtórzeniami (pudełka + punkty) dla każdej strategii."""
    fig, axes = plt.subplots(1, len(metrics), figsize=(12, 3.4))
    axes = np.atleast_1d(axes)
    strategies = sorted(mc["Scenariusz"].unique())
    rng = np.random.default_rng(0)
    for ax, m in zip(axes, metrics):
        data = [mc.loc[mc["Scenariusz"] == s, m].to_numpy(dtype=float) for s in strategies]
        bp = ax.boxplot(data, patch_artist=True, widths=0.55, showfliers=False)
        bp_labels = strategies
        ax.set_xticks(range(1, len(bp_labels) + 1), bp_labels)
        for patch, s in zip(bp["boxes"], strategies):
            patch.set_facecolor(COLORS[s])
            patch.set_alpha(0.35)
        for i, (d, s) in enumerate(zip(data, strategies), start=1):
            ax.scatter(i + rng.uniform(-0.12, 0.12, len(d)), d, s=10, color=COLORS[s], zorder=3)
        ax.set_title(m, fontsize=9)
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle(f"Monte Carlo – {mc['seed'].nunique()} powtórzeń", fontweight="bold")
    fig.tight_layout()
    return fig


def plot_sizing_heatmap(df: pd.DataFrame, metric: str = "Koszt łączny [PLN]") -> plt.Figure:
    piv = df.pivot(index="E_BESS [kWh]", columns="P_PV [kWp]", values=metric).sort_index(ascending=False)
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    im = ax.imshow(piv.to_numpy(), cmap="viridis_r", aspect="auto")
    ax.set_xticks(range(piv.shape[1]), [f"{c:g}" for c in piv.columns])
    ax.set_yticks(range(piv.shape[0]), [f"{i:g}" for i in piv.index])
    ax.set_xlabel("P_PV [kWp]")
    ax.set_ylabel("E_BESS [kWh]")
    vals = piv.to_numpy()
    i_best, j_best = np.unravel_index(np.nanargmin(vals), vals.shape)
    for i in range(vals.shape[0]):
        for j in range(vals.shape[1]):
            ax.text(j, i, f"{vals[i, j]:,.0f}".replace(",", " "), ha="center", va="center", fontsize=7,
                    color="white" if (vals[i, j] - np.nanmin(vals)) > 0.5 * np.ptp(vals) else "black",
                    fontweight="bold" if (i, j) == (i_best, j_best) else "normal")
    ax.add_patch(plt.Rectangle((j_best - 0.5, i_best - 0.5), 1, 1, fill=False, ec="#c92a2a", lw=2.5))
    fig.colorbar(im, ax=ax, label=metric)
    ax.set_title(f"Dobór PV × BESS – {metric} (ramka = minimum)", fontweight="bold", fontsize=9)
    fig.tight_layout()
    return fig


def plot_contract_sweep(df: pd.DataFrame) -> plt.Figure:
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.4))
    for st, g in df.groupby("Scenariusz"):
        g = g.sort_values("P_umowna [kW]")
        x = g["P_umowna [kW]"]
        axes[0].plot(x, g["Koszt całk. [PLN]"], "o-", color=COLORS[st], label=st, ms=3)
        axes[1].plot(x, g["Kara moc umowna [PLN]"] + g["Opł. stała [PLN]"], "o-", color=COLORS[st], ms=3)
        axes[2].plot(x, g["UDR wykonalne [%]"], "o-", color=COLORS[st], ms=3)
        i = g["Koszt całk. [PLN]"].idxmin()
        axes[0].scatter([g.loc[i, "P_umowna [kW]"]], [g.loc[i, "Koszt całk. [PLN]"]], s=80,
                        facecolor="none", edgecolor=COLORS[st], lw=2, zorder=4)
    for ax, t in zip(axes, ["Koszt całkowity [PLN] (○ = minimum)", "Opł. stała + kary [PLN]",
                            "UDR wykonalne [%]"]):
        ax.set_title(t, fontsize=9)
        ax.set_xlabel("Moc umowna [kW]")
        ax.grid(alpha=0.3)
    axes[0].legend(title="Scenariusz", fontsize=7)
    fig.suptitle("Dobór mocy umownej", fontweight="bold")
    fig.tight_layout()
    return fig
