"""Raport HTML (jeden plik, wykresy SVG osadzone) – do pobrania z panelu lub zapisu z CLI."""

from __future__ import annotations

import datetime as dt
import html
import io

import matplotlib.pyplot as plt
import pandas as pd

from .controller import CONTROLLERS
from .plots import plot_cost_breakdown, plot_grid_comparison, plot_kpi_bars, plot_real_inputs, plot_timeseries
from .simulation import Results
from .tariff import DEFAULT_TARIFF

MAIN_KPIS = ["T_over [min/doba]", "dP_over_max [kW]", "SSR_import [%]", "UDR [%]", "EFC [-]",
             "Utrata SoH BESS [%]", "E_V2G [kWh]", "Koszt całk. [PLN]"]


def _svg(fig) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    s = buf.getvalue()
    return s[s.find("<svg"):]


def build_report(results: dict[tuple[str, str], Results], table: pd.DataFrame) -> str:
    any_r = next(iter(results.values()))
    cfg = any_r.cfg
    sites = list(dict.fromkeys(name for name, _ in results))
    strategies = list(dict.fromkeys(st for _, st in results))
    src = ("rzeczywiste: Open-Meteo (pogoda), PSE (RCE, KSE)" if cfg.data_source == "real" else "syntetyczne")
    t = DEFAULT_TARIFF

    parts = [f"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Raport mikrosieci</title>
<style>
body{{font:14px/1.5 system-ui,sans-serif;max-width:1180px;margin:0 auto;padding:16px;color:#1d2733;background:#fff}}
h1{{font-size:22px}} h2{{margin-top:32px;border-bottom:2px solid #dde3ea;padding-bottom:4px}}
table{{border-collapse:collapse;font-size:12px;margin:8px 0;width:100%}}
th,td{{border:1px solid #dde3ea;padding:3px 6px;text-align:right}} th{{background:#f4f6f9}}
td:first-child,th:first-child{{text-align:left}} svg{{max-width:100%;height:auto}}
.meta{{color:#6b7785}} .scroll{{overflow-x:auto}}
</style></head><body>
<h1>⚡ Mikrosieć EV + BESS + OZE – Kraków</h1>
<p class="meta">Wygenerowano {dt.datetime.now():%d.%m.%Y %H:%M} · start {cfg.start} · {cfg.days} dni ·
Δt = {cfg.dt_min} min · seed {cfg.seed} · dane {src} · flota EV {cfg.ev_fleet} · prognoza MPC {cfg.forecast}</p>
<h2>Scenariusze</h2><ul>""" + "".join(f"<li>{html.escape(CONTROLLERS[s].label)}</li>" for s in strategies) + "</ul>"]

    parts.append("<h2>Najważniejsze KPI</h2><div class='scroll'>" +
                 table[MAIN_KPIS].to_html(float_format=lambda x: f"{x:,.2f}".replace(",", " ")) + "</div>")
    parts.append(f"""<h2>Taryfa (orientacyjna)</h2><p>Import: spot + marża {t.seller_margin_pln_mwh:g} +
dystrybucja zm. {t.dist_variable_pln_mwh:g} + jakościowa {t.quality_pln_mwh:g} + OZE/kogen. {t.res_fees_pln_mwh:g} PLN/MWh;
opłata mocowa {t.capacity_fee_pln_mwh:g} PLN/MWh (dni robocze 7–22); składnik stały {t.fixed_pln_kw_month:g} PLN/kW/mies.;
kara za przekroczenie mocy umownej {t.overrun_multiplier:g}× składnik stały × suma 10 największych nadwyżek;
zużycie BESS {t.bess_wear_pln_kwh:.3f} PLN/kWh przepływu. Eksport rozliczany po cenie spot.</p>""")

    for site in sites:
        parts.append(f"<h2>{html.escape(site)}</h2>")
        parts.append(_svg(plot_cost_breakdown(table, site)))
        parts.append(_svg(plot_grid_comparison(results, site)))
        for st in strategies:
            parts.append(_svg(plot_timeseries(results[(site, st)])))
        r0 = results[(site, strategies[0])]
        if r0.raw is not None:
            parts.append(_svg(plot_real_inputs(r0)))

    parts.append("<h2>Porównanie lokalizacji</h2>" + _svg(plot_kpi_bars(table)))
    parts.append("<h2>Pełna tabela KPI</h2><div class='scroll'>" +
                 table.to_html(float_format=lambda x: f"{x:,.2f}".replace(",", " ")) + "</div>")
    parts.append("<p class='meta'>Źródła danych: Open-Meteo Historical Weather API, PSE API raportów "
                 "(rce-pln, his-wlk-cal), © współtwórcy OpenStreetMap (ODbL).</p></body></html>")
    return "\n".join(parts)
