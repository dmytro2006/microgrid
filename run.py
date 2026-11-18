"""CLI: uruchamia scenariusze A/B/C dla mikrosieci w Krakowie, drukuje KPI, zapisuje wykresy i mapę.

Przykład:
    uv run python run.py --days 2 --dt 5 --no-show
    uv run python run.py --data real --start 2026-01-12 --days 7 --fleet real --no-show
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
    ap = argparse.ArgumentParser(description="Symulacja mikrosieci EV + BESS + OZE (dane syntetyczne lub rzeczywiste)")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--dt", type=int, default=5, choices=[1, 5, 15], help="krok Δt [min]")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--data", default="synthetic", choices=["synthetic", "real"],
                    help="profile wejściowe: syntetyczne albo Open-Meteo + PSE (RCE, KSE)")
    ap.add_argument("--start", default="2026-06-15", help="data startu (RRRR-MM-DD, czas lokalny)")
    ap.add_argument("--fleet", default="synthetic", choices=["synthetic", "real"],
                    help="flota EV: syntetyczna albo rzeczywiste modele + ładowarki AC/DC")
    ap.add_argument("--anim", action="store_true", help="zapisz animacje HTML (mapa + schemat węzła)")
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
    cfg = SimConfig(days=args.days, dt_min=args.dt, seed=args.seed, start=f"{args.start} 00:00",
                    data_source=args.data, ev_fleet=args.fleet)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Symulacja: {len(sites)} lokalizacji × 3 scenariusze, {cfg.days} dni od {args.start}, "
          f"Δt = {cfg.dt_min} min, seed = {cfg.seed}, dane = {cfg.data_source}, flota = {cfg.ev_fleet}")
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
    if args.anim:
        from microgrid.animation import animated_map_html, flow_diagram_html
        for strategy in ("A", "C"):
            rs = [results[(s.name, strategy)] for s in sites]
            (out / f"anim_map_{strategy}.html").write_text(animated_map_html(rs, start_k=k_peak), encoding="utf-8")
            for r in rs:
                (out / f"anim_{slug(r.site.name)}_{strategy}.html").write_text(flow_diagram_html(r), encoding="utf-8")
        print(f"Animacje: {out}/anim_*.html")
    if cfg.data_source == "real":
        from microgrid.plots import plot_real_inputs
        for s in sites:
            fig = plot_real_inputs(results[(s.name, "A")])
            fig.savefig(out / f"inputs_{slug(s.name)}.{args.format}", dpi=args.dpi, bbox_inches="tight")
            if args.no_show:
                plt.close(fig)
    t_peak = results[(sites[0].name, "A")].ts["time"].iloc[k_peak]
    print(f"Mapy przepływów: {out}/map_A.html (szczyt {t_peak:%d.%m %H:%M}), {out}/map_C.html")
    print(f"Wyniki zapisane w: {out.resolve()}")

    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
