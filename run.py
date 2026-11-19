"""CLI: uruchamia scenariusze A/B/C dla mikrosieci w Krakowie, drukuje KPI, zapisuje wykresy i mapę.

Przykład:
    uv run python run.py --days 2 --dt 5 --no-show
    uv run python run.py --data real --start 2026-01-12 --days 7 --fleet real --no-show
    uv run python run.py --network --pv-mult 5 --no-show   # sieć 110/15 kV z rozpływem mocy
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
    ap.add_argument("--forecast", default="perfect", choices=["perfect", "persistence"], help="prognoza dla MPC (D)")
    ap.add_argument("--strategies", default="ABCD", help="podzbiór strategii, np. ABC")
    ap.add_argument("--report", action="store_true", help="zapisz raport HTML (raport.html)")
    ap.add_argument("--no-ev-forecast", action="store_true", help="MPC bez prognozy przyjazdów EV")
    ap.add_argument("--network", action="store_true",
                    help="wspólna symulacja węzłów w sieci 110/15 kV z rozpływem mocy (pandapower)")
    ap.add_argument("--no-net-control", action="store_true", help="sieć bez koordynatora sieciowego")
    ap.add_argument("--pv-mult", type=float, default=1.0, help="mnożnik produkcji PV wszystkich węzłów")
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

    from microgrid.config import ALL_SITES, SimConfig
    from microgrid.map_view import build_deck
    from microgrid.metrics_evaluator import energy_balance_residuals, kpi_table, network_summary
    from microgrid.plots import plot_grid_comparison, plot_kpi_bars, plot_timeseries
    from microgrid.simulation import run_all

    sites = ALL_SITES
    if args.sites:
        sites = [s for s in sites if any(q.lower() in s.name.lower() for q in args.sites)]
    cfg = SimConfig(days=args.days, dt_min=args.dt, seed=args.seed, start=f"{args.start} 00:00",
                    data_source=args.data, ev_fleet=args.fleet, forecast=args.forecast,
                    mpc_ev_forecast=not args.no_ev_forecast, network=args.network,
                    network_control=not args.no_net_control, pv_multiplier=args.pv_mult)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Symulacja: {len(sites)} lokalizacji × {len(args.strategies)} scenariusze, {cfg.days} dni od {args.start}, "
          f"Δt = {cfg.dt_min} min, seed = {cfg.seed}, dane = {cfg.data_source}, flota = {cfg.ev_fleet}"
          + (f", sieć 110/15 kV{' + koordynator' if cfg.network_control else ''}" if cfg.network else "")
          + (f", mnożnik PV = {cfg.pv_multiplier:g}" if cfg.pv_multiplier != 1.0 else ""))
    nets = {}
    if cfg.network:
        from microgrid.cosim import run_all_network
        results, nets = run_all_network(sites, cfg, strategies=tuple(args.strategies.upper()))
    else:
        results = run_all(sites, cfg, strategies=tuple(args.strategies.upper()))

    table = kpi_table(results)
    with pd.option_context("display.width", 250, "display.max_columns", 30, "display.float_format", "{:.2f}".format):
        print("\n=== KPI ===")
        print(table)
    table.to_csv(out / "kpi.csv")
    if nets:
        net_table = pd.DataFrame({s: network_summary(nr, cfg.dt_h) for s, nr in nets.items()}).T
        net_table.index.name = "Scenariusz"
        with pd.option_context("display.width", 250, "display.max_columns", 10,
                               "display.float_format", "{:.2f}".format):
            print("\n=== Sieć 110/15 kV ===")
            print(net_table)
        for s in nets:
            net_table.loc[[s]].to_csv(out / f"siec_{s}.csv")

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
    if "Koszt całk. [PLN]" in table:
        from microgrid.plots import plot_cost_breakdown
        for s in sites:
            fig = plot_cost_breakdown(table, s.name)
            fig.savefig(out / f"koszty_{slug(s.name)}.{args.format}", dpi=args.dpi, bbox_inches="tight")
            if args.no_show:
                plt.close(fig)
    if args.report:
        from microgrid.report import build_report
        (out / "raport.html").write_text(build_report(results, table), encoding="utf-8")
        print(f"Raport: {out}/raport.html")

    # mapa: chwila maksymalnego obciążenia (scenariusz A, suma po lokalizacjach)
    map_strats = [st for st in ("A", args.strategies.upper()[-1]) if (sites[0].name, st) in results]
    for strategy in dict.fromkeys(map_strats):
        rs = [results[(s.name, strategy)] for s in sites]
        total = sum(r.ts["p_grid"].abs() for r in rs)
        k = int(total.idxmax())
        build_deck(rs, k, nr=nets.get(strategy)).to_html(str(out / f"map_{strategy}.html"), open_browser=False, notebook_display=False)
        if strategy == map_strats[0]:
            k_peak = k
    if args.anim:
        from microgrid.animation import animated_map_html, flow_diagram_html, sankey_html, station_html
        for strategy in dict.fromkeys(map_strats):
            rs = [results[(s.name, strategy)] for s in sites]
            html = animated_map_html(rs, start_k=k_peak, nr=nets.get(strategy))
            (out / f"anim_map_{strategy}.html").write_text(html, encoding="utf-8")
            for r in rs:
                (out / f"anim_{slug(r.site.name)}_{strategy}.html").write_text(flow_diagram_html(r), encoding="utf-8")
                (out / f"sankey_{slug(r.site.name)}_{strategy}.html").write_text(sankey_html(r), encoding="utf-8")
        strats = list(dict.fromkeys(map_strats))
        for s in sites:
            rr = [results[(s.name, st)] for st in strats]
            (out / f"parking_{slug(s.name)}.html").write_text(station_html(rr), encoding="utf-8")
        print(f"Animacje: {out}/anim_*.html")
    if cfg.data_source == "real":
        from microgrid.plots import plot_real_inputs
        for s in sites:
            fig = plot_real_inputs(results[(s.name, "A")])
            fig.savefig(out / f"inputs_{slug(s.name)}.{args.format}", dpi=args.dpi, bbox_inches="tight")
            if args.no_show:
                plt.close(fig)
    t_peak = results[(sites[0].name, map_strats[0])].ts["time"].iloc[k_peak]
    print(f"Mapy przepływów: " + ", ".join(f"{out}/map_{st}.html" for st in dict.fromkeys(map_strats))
          + f" (szczyt {t_peak:%d.%m %H:%M})")
    print(f"Wyniki zapisane w: {out.resolve()}")

    if not args.no_show:
        plt.show()


if __name__ == "__main__":
    main()
