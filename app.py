"""Panel Streamlit: symulacja mikrosieci EV + BESS + OZE w Krakowie (dane syntetyczne lub rzeczywiste).

Uruchomienie:
    uv run streamlit run app.py
"""

from __future__ import annotations

import datetime as dt_
import io

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from microgrid.analysis import InvestmentCosts, monte_carlo, sizing_grid, summarize
from microgrid.animation import animated_map_html, flow_diagram_html
from microgrid.config import KRAKOW_SITES, SimConfig
from microgrid.controller import CONTROLLERS
from microgrid.map_view import build_deck
from microgrid.metrics_evaluator import energy_balance_residuals, kpi_table, kpis
from microgrid.plots import (plot_cost_breakdown, plot_grid_comparison, plot_kpi_bars, plot_monte_carlo,
                             plot_real_inputs, plot_sizing_heatmap, plot_timeseries)
from microgrid.report import build_report
from microgrid.simulation import run_all
from microgrid.tariff import DEFAULT_TARIFF

st.set_page_config(page_title="Mikrosieć Kraków – symulacja SSD", layout="wide")
st.title("⚡ Mikrosieć miejska: EV + BESS + OZE – Kraków")
caption = st.empty()

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("Symulacja")
    days = st.slider("Horyzont [doby]", 1, 7, 2)
    dt = st.select_slider("Krok Δt [min]", options=[1, 5, 15], value=5)
    seed = st.number_input("Seed", value=42, step=1)

    st.header("Dane wejściowe")
    source = st.radio("Profile PV / wiatr / odbiory / ceny", ["real", "synthetic"], key="source",
                      format_func=lambda x: {"real": "🌍 Rzeczywiste (Open-Meteo + PSE)",
                                             "synthetic": "🎲 Syntetyczne"}[x])
    start = st.date_input("Data startu", dt_.date(2026, 6, 15),
                          min_value=dt_.date(2024, 6, 14), max_value=dt_.date.today() - dt_.timedelta(days=7),
                          disabled=source != "real",
                          help="Dla danych rzeczywistych: RCE (PSE) dostępne od 14.06.2024, Open-Meteo ~5 dni wstecz.")
    fleet = st.toggle("Realna flota EV (modele + ładowarki AC 22 kW / DC 50 kW)", value=True)
    forecast = st.radio("Prognoza dla MPC (D)", ["perfect", "persistence"], horizontal=True,
                        format_func=lambda x: {"perfect": "doskonała", "persistence": "z wczoraj"}[x],
                        help="Doskonała = górne ograniczenie jakości; „z wczoraj” = odbiory/PV/wiatr "
                             "z poprzedniej doby (ceny RCE znane z wyprzedzeniem).")

    st.header("Lokalizacja")
    names = [s.name for s in KRAKOW_SITES]
    site_name = st.selectbox("Węzeł do edycji / analizy", names)
    base = next(s for s in KRAKOW_SITES if s.name == site_name)
    n_ch = st.slider("Liczba ładowarek", 2, 30, base.n_chargers)
    evd = st.slider("Pojazdy EV / dobę", 5, 200, int(base.ev_per_day))
    pmax = st.slider("P_max / moc umowna [kW]", 50, 600, int(base.p_max_kw), step=10)
    load_types = ["kse", "residential", "office", "commercial"]
    load_type = st.selectbox("Typ odbiorcy (dane rzeczywiste)", load_types, index=load_types.index(base.load_type),
                             format_func=lambda x: {"kse": "kształt KSE", "residential": "osiedle",
                                                    "office": "biurowiec / uczelnia",
                                                    "commercial": "handel i usługi"}[x])
    pv = st.slider("Moc PV [kWp]", 0, 400, int(base.pv_kwp), step=10)
    wind = st.slider("Turbina wiatrowa [kW]", 0, 150, int(base.wind_rated_kw), step=5)
    bess_kwh = st.slider("BESS pojemność [kWh]", 0, 800, int(base.bess_kwh), step=10)
    bess_kw = st.slider("BESS moc [kW]", 0, 400, int(base.bess_p_kw), step=10)
    v2g = st.slider("Udział EV z V2G [%]", 0, 100, int(100 * base.v2g_share), step=5)

edited = base.with_(n_chargers=n_ch, ev_per_day=float(evd), p_max_kw=float(pmax), pv_kwp=float(pv),
                    wind_rated_kw=float(wind), bess_kwh=float(bess_kwh), bess_p_kw=float(bess_kw),
                    v2g_share=v2g / 100, load_type=load_type)
sites = [edited if s.name == site_name else s for s in KRAKOW_SITES]
cfg = SimConfig(days=days, dt_min=int(dt), seed=int(seed), data_source=source,
                ev_fleet="real" if fleet else "synthetic", forecast=forecast,
                start=f"{start:%Y-%m-%d} 00:00" if source == "real" else SimConfig.start)
caption.caption(
    "Hybrydowa symulacja DES (SimPy) + krok bilansowy Δt. Dane wejściowe: " +
    ("**rzeczywiste** – pogoda Open-Meteo (ERA5/IFS), ceny RCE i zapotrzebowanie KSE z API PSE."
     if source == "real" else "**syntetyczne**."))


def show(fig) -> None:
    """Wyświetla wykres jako SVG (grafika wektorowa – ostra przy każdej szerokości i skali ekranu).

    Uwaga: obrazy rastrowe (PNG) Streamlit zmniejsza po stronie serwera do 1460 px,
    co przy szerokim układzie daje rozmycie – SVG nie jest skalowane.
    """
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    st.image(buf.getvalue(), width="stretch")


@st.cache_data(show_spinner="Symulacja w toku…")
def simulate(sites_t: tuple, cfg: SimConfig):
    return run_all(list(sites_t), cfg)


try:
    results = simulate(tuple(sites), cfg)
except ConnectionError as e:
    st.error(f"Nie udało się pobrać danych rzeczywistych ({e}). Przełącz na dane syntetyczne "
             "albo wybierz datę, dla której dane są już w `data/cache`.")
    st.stop()
table = kpi_table(results)

with st.sidebar:
    st.header("Raport")
    if st.button("Przygotuj raport HTML"):
        with st.spinner("Generowanie raportu…"):
            st.session_state["report"] = build_report(results, table)
    if "report" in st.session_state:
        st.download_button("⬇️ Pobierz raport", st.session_state["report"], file_name="raport_mikrosiec.html",
                           mime="text/html")

tab_anim, tab_ts, tab_cmp, tab_map, tab_in, tab_ev, tab_an = st.tabs(
    ["🎬 Animacja", "📈 Przebiegi", "📊 Porównanie scenariuszy", "🗺️ Mapa przepływów", "🌦️ Dane wejściowe",
     "🚗 Sesje EV", "🔬 Analizy"])

# ---------------- Animacja ----------------
with tab_anim:
    a_strategy = st.radio("Scenariusz", list(CONTROLLERS), format_func=lambda k: CONTROLLERS[k].label,
                          horizontal=True, key="anim_strategy", index=3)
    st.caption("Przepływy mocy w węźle PCC – grubość i prędkość ∝ mocy, kierunek kropek = kierunek przepływu. "
               "Odtwarzanie działa w przeglądarce (▶, suwak, tempo).")
    st.iframe(flow_diagram_html(results[(site_name, a_strategy)]), height=640)

# ---------------- Przebiegi ----------------
with tab_ts:
    strategy = st.radio("Scenariusz", list(CONTROLLERS), format_func=lambda k: CONTROLLERS[k].label, horizontal=True)
    r = results[(site_name, strategy)]
    k_now, k_base = kpis(r), kpis(results[(site_name, "A")])
    cols = st.columns(7)
    for col, (m, better_low) in zip(cols, [("T_over [min/doba]", True), ("dP_over_max [kW]", True),
                                           ("SSR_import [%]", False), ("UDR [%]", True),
                                           ("Koszt całk. [PLN]", True), ("Kara moc umowna [PLN]", True),
                                           ("EFC [-]", True)]):
        delta = k_now[m] - k_base[m]
        col.metric(m, f"{k_now[m]:.2f}", f"{delta:+.2f} vs A" if strategy != "A" else None,
                   delta_color="inverse" if better_low else "normal")
    fig = plot_timeseries(r)
    show(fig)
    res = energy_balance_residuals(r)
    st.caption("Walidacja bilansu energii (rezydua [kWh]): " + ", ".join(f"{k} = {v:.1e}" for k, v in res.items()))

# ---------------- Porównanie ----------------
with tab_cmp:
    show(plot_cost_breakdown(table, site_name))
    st.caption("Koszt wg orientacyjnej taryfy C2x (Tauron): energia po cenie spot + marża, opłaty zmienne, "
               "mocowa (dni robocze 7–22), składnik stały, kara za przekroczenie mocy umownej, zużycie BESS.")
    fig = plot_grid_comparison(results, site_name)
    show(fig)
    fig = plot_kpi_bars(table)
    show(fig)
    st.dataframe(table.style.format("{:.2f}"), width="stretch")

# ---------------- Mapa ----------------
with tab_map:
    c1, c2 = st.columns([1, 3])
    with c1:
        map_strategy = st.radio("Scenariusz na mapie", list(CONTROLLERS),
                                format_func=lambda k: CONTROLLERS[k].label, key="map_strategy")
        animated = st.toggle("Animacja (cząstki energii)", value=True)
    rs = [results[(s.name, map_strategy)] for s in sites]
    times = rs[0].ts["time"]
    k_peak = int(sum(x.ts["p_grid"].abs() for x in rs).idxmax())
    if animated:
        with c2:
            st.markdown("Cząstki płyną od źródła do odbiorcy; ich prędkość i liczba ∝ moc. "
                        "Kolor PCC: 🟢 niskie → 🔴 przeciążenie (|P_grid| / P_max). Start: chwila szczytu.")
        st.iframe(animated_map_html(rs, start_k=k_peak, autoplay=False), height=640)
        k = k_peak
        st.caption(f"Tabela: stan w chwili szczytu {times.iloc[k]:%d.%m %H:%M}")
    else:
        with c2:
            k = st.slider("Chwila symulacji", 0, len(times) - 1, k_peak,
                          help="Domyślnie: chwila największego łącznego poboru z sieci")
            st.markdown(f"**{times.iloc[k]:%d.%m.%Y %H:%M}** – grubość łuku ∝ moc, kolor węzła PCC: "
                        "🟢 niskie → 🔴 przeciążenie (|P_grid| / P_max)")
        st.pydeck_chart(build_deck(rs, k), height=600)
        st.caption(f"Tabela: stan w chwili {times.iloc[k]:%d.%m %H:%M}")
    snap = pd.DataFrame([{
        "Lokalizacja": x.site.name, "P_grid [kW]": x.ts["p_grid"].iloc[k], "P_max [kW]": x.site.p_max_kw,
        "Odbiory [kW]": x.ts["load"].iloc[k], "EV [kW]": x.ts["p_ev"].iloc[k], "PV [kW]": x.ts["pv"].iloc[k],
        "Wiatr [kW]": x.ts["wind"].iloc[k], "BESS [kW]": x.ts["p_bess"].iloc[k],
        "SoC BESS [%]": 100 * x.ts["soc_bess"].iloc[k],
    } for x in rs]).set_index("Lokalizacja")
    st.dataframe(snap.style.format("{:.1f}"), width="stretch")

# ---------------- Dane wejściowe ----------------
with tab_in:
    r0 = results[(site_name, "A")]
    if r0.raw is None:
        st.info("Tryb syntetyczny – wybierz w panelu bocznym **dane rzeczywiste**, aby zobaczyć pogodę, "
                "ceny RCE i stan KSE dla wybranego okresu.")
    else:
        raw, ts0 = r0.raw, r0.ts
        e = lambda col: ts0[col].sum() * cfg.dt_h
        c = st.columns(5)
        c[0].metric("Energia PV", f"{e('pv'):.0f} kWh", f"{e('pv') / max(edited.pv_kwp, 1):.2f} kWh/kWp")
        c[1].metric("Energia wiatru", f"{e('wind'):.0f} kWh",
                    f"CF {100 * ts0['wind'].mean() / edited.wind_rated_kw:.1f}%" if edited.wind_rated_kw else None)
        c[2].metric("Śr. wiatr na piaście", f"{raw['v_hub'].mean():.1f} m/s", f"{edited.wind_hub_m:.0f} m n.p.t.",
                    delta_color="off")
        c[3].metric("Śr. cena RCE", f"{raw['rce'].mean():.0f} PLN/MWh",
                    f"min {raw['rce'].min():.0f} / max {raw['rce'].max():.0f}", delta_color="off")
        c[4].metric("Udział OZE w KSE", f"{100 * (raw['kse_pv'] + raw['kse_wind']).sum() / raw['kse_demand'].sum():.1f}%")
        show(plot_real_inputs(r0))
        st.caption("Źródła: Open-Meteo Historical Weather API (archive-api.open-meteo.com), "
                   "PSE API raportów (api.raporty.pse.pl: rce-pln, his-wlk-cal). Dane w cache: `data/cache/`.")

# ---------------- Sesje EV ----------------
with tab_ev:
    s_strategy = st.radio("Scenariusz", list(CONTROLLERS), format_func=lambda k: CONTROLLERS[k].label,
                          horizontal=True, key="ev_strategy")
    ses = results[(site_name, s_strategy)].sessions
    st.write(ses["status"].value_counts().rename("liczba pojazdów"))
    st.dataframe(ses, width="stretch")

# ---------------- Analizy ----------------
with tab_an:
    st.subheader("Monte Carlo – niepewność wyników")
    st.caption(f"Powtórzenia dla węzła **{site_name}** z kolejnymi ziarnami (inne przyjazdy EV; w trybie "
               "syntetycznym także inne profile). Przedział ufności 95% z rozkładu t-Studenta.")
    c1, c2 = st.columns([1, 3])
    n_mc = c1.slider("Liczba powtórzeń", 3, 40, 10)
    mc_strats = c2.multiselect("Strategie", list(CONTROLLERS), default=list(CONTROLLERS),
                               format_func=lambda k: CONTROLLERS[k].label)
    mc_metrics = ["T_over [min/doba]", "UDR [%]", "Koszt całk. [PLN]", "SSR_import [%]"]
    if st.button("▶ Uruchom Monte Carlo") and mc_strats:
        with st.spinner(f"{n_mc} × {len(mc_strats)} symulacji…"):
            st.session_state["mc"] = monte_carlo(edited, cfg, tuple(mc_strats), n_mc)
    if "mc" in st.session_state:
        mc = st.session_state["mc"]
        show(plot_monte_carlo(mc, mc_metrics))
        st.dataframe(summarize(mc, mc_metrics).style.format("{:.2f}"), width="stretch")

    st.divider()
    st.subheader("Dobór mocy PV i pojemności BESS")
    inv = InvestmentCosts()
    st.caption(f"Koszt łączny = koszt eksploatacji (taryfa) + annuitet inwestycji przeliczony na horyzont "
               f"(PV {inv.pv_pln_kwp:g} PLN/kWp / {inv.pv_life_y:g} lat, BESS {inv.bess_pln_kwh:g} PLN/kWh / "
               f"{inv.bess_life_y:g} lat, stopa {100 * inv.discount_rate:g}%). Moc BESS = 0.5 C.")
    c1, c2, c3 = st.columns(3)
    sz_strat = c1.selectbox("Strategia", list(CONTROLLERS), index=2, format_func=lambda k: CONTROLLERS[k].label)
    pv_max = c2.slider("PV do [kWp]", 50, 400, 250, step=50)
    bess_max = c3.slider("BESS do [kWh]", 100, 800, 400, step=100)
    sz_metric = st.radio("Mapa", ["Koszt łączny [PLN]", "Koszt całk. [PLN]", "T_over [min/doba]", "SSR_import [%]",
                                  "UDR [%]"], horizontal=True)
    if st.button("▶ Przelicz siatkę"):
        pv_vals = list(range(0, pv_max + 1, max(10, pv_max // 5)))
        bess_vals = list(range(0, bess_max + 1, max(50, bess_max // 4)))
        with st.spinner(f"{len(pv_vals) * len(bess_vals)} symulacji…"):
            st.session_state["sizing"] = sizing_grid(edited, cfg, sz_strat, pv_vals, bess_vals, inv=inv)
    if "sizing" in st.session_state:
        sz = st.session_state["sizing"]
        show(plot_sizing_heatmap(sz, sz_metric))
        best = sz.loc[sz["Koszt łączny [PLN]"].idxmin()]
        cost_txt = f"{best['Koszt łączny [PLN]']:,.0f}".replace(",", " ")
        st.success(f"Minimum kosztu łącznego: PV **{best['P_PV [kWp]']:g} kWp**, BESS **{best['E_BESS [kWh]']:g} kWh** "
                   f"→ {cost_txt} PLN w horyzoncie")

    st.divider()
    st.subheader("Parametry taryfy (orientacyjne)")
    st.dataframe(pd.Series(DEFAULT_TARIFF.__dict__, name="wartość").to_frame()
                 .assign(jednostka=["PLN/MWh"] * 5 + ["PLN/kW/mies.", "×", "PLN/kWh", "cykli", "-"]),
                 width="stretch")
