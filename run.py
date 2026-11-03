"""CLI: uruchamia scenariusze A/B/C dla mikrosieci w Krakowie, drukuje KPI, zapisuje wykresy i mapę.

Przykład:
    uv run python run.py --days 2 --dt 5 --no-show
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib
import pandas as pd


def slug(s: str) -> str:
    s = s.lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def main() -> None:
    ap = argparse.ArgumentParser(description="Symulacja mikrosieci EV + BESS + OZE (dane syntetyczne)")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--dt", type=int, default=5, choices=[1, 5, 15], help="krok Δt [min]")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sites", nargs="*", help="podciągi nazw lokalizacji (domyślnie wszystkie)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--dpi", type=int, default=200, help="rozdzielczość plików PNG")
    ap.add_argument("--format", default="png", choices=["png", "svg", "pdf"],
                    help="format wykresów (svg/pdf = grafika wektorowa)")
    ap.add_argument("--no-show", action="store_true", help="tylko zapis plików, bez okien wykresów")
    args = ap.parse_args()

    if args.no_show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from microgrid.config import KRAKOW_SITES, SimConfig
    from microgrid.map_view import build_deck
    from microgrid.metrics_evaluator import energy_balance_residuals, kpi_table
    from microgrid.plots import plot_grid_comparison, plot_kpi_bars, plot_timeseries
    from microgrid.simulation import run_all

    sites = KRAKOW_SITES
    if args.sites:
        sites = [s for s in sites if any(q.lower() in s.name.lower() for q in args.sites)]
    cfg = SimConfig(days=args.days, dt_min=args.dt, seed=args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Symulacja: {len(sites)} lokalizacji × 3 scenariusze, {cfg.days} dni, Δt = {cfg.dt_min} min, seed = {cfg.seed}")
    results = run_all(sites, cfg)

    table = kpi_table(results)
    with pd.option_context("display.width", 250, "display.max_columns", 30, "display.float_format", "{:.2f}".format):
        print("\n=== KPI ===")
        print(table)
    table.to_csv(out / "kpi.csv")

    worst = max(abs(v) for r in results.values() for v in energy_balance_residuals(r).values())
    print(f"\nWalidacja bilansu energii: max |rezyduum| = {worst:.2e} kWh")

    for (name, strategy), r in results.items():
        fig = plot_timeseries(r)
        fig.savefig(out / f"ts_{slug(name)}_{strategy}.{args.format}", dpi=args.dpi, bbox_inches="tight")
        r.sessions.to_csv(out / f"sessions_{slug(name)}_{strategy}.csv", index=False)
        if args.no_show:
            plt.close(fig)
    for s in sites:
        fig = plot_grid_comparison(results, s.name)
        fig.savefig(out / f"pcc_{slug(s.name)}.{args.format}", dpi=args.dpi, bbox_inches="tight")
        if args.no_show:
            plt.close(fig)
    fig = plot_kpi_bars(table)
    fig.savefig(out / f"kpi.{args.format}", dpi=args.dpi, bbox_inches="tight")

    # mapa: chwila maksymalnego obciążenia (scenariusz A, suma po lokalizacjach)
    for strategy in ("A", "C"):
        rs = [results[(s.name, strategy)] for s in sites]
        total = sum(r.ts["p_grid"].abs() for r in rs)
        k = int(total.idxmax())
        build_deck(rs, k).to_html(str(out / f"map_{strategy}.html"), open_browser=False, notebook_display=False)
        if strategy == "A":
            k_peak = k
    t_peak = results[(sites[0].name, "A")].ts["time"].iloc[k_peak]
    print(f"Mapy przepływów: {out}/map_A.html (szczyt {t_peak:%d.%m %H:%M}), {out}/map_C.html")
    print(f"Wyniki zapisane w: {out.resolve()}")

    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
