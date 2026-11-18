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

from microgrid.animation import animated_map_html, flow_diagram_html
from microgrid.config import KRAKOW_SITES, SimConfig
from microgrid.controller import CONTROLLERS
from microgrid.map_view import build_deck
from microgrid.metrics_evaluator import energy_balance_residuals, kpi_table, kpis
from microgrid.plots import plot_grid_comparison, plot_kpi_bars, plot_real_inputs, plot_timeseries
from microgrid.simulation import run_all

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

    st.header("Lokalizacja")
    names = [s.name for s in KRAKOW_SITES]
    site_name = st.selectbox("Węzeł do edycji / analizy", names)
    base = next(s for s in KRAKOW_SITES if s.name == site_name)
    n_ch = st.slider("Liczba ładowarek", 2, 30, base.n_chargers)
    evd = st.slider("Pojazdy EV / dobę", 5, 200, int(base.ev_per_day))
    pmax = st.slider("P_max transformatora [kW]", 50, 600, int(base.p_max_kw), step=10)
    pv = st.slider("Moc PV [kWp]", 0, 400, int(base.pv_kwp), step=10)
    wind = st.slider("Turbina wiatrowa [kW]", 0, 150, int(base.wind_rated_kw), step=5)
    bess_kwh = st.slider("BESS pojemność [kWh]", 0, 800, int(base.bess_kwh), step=10)
    bess_kw = st.slider("BESS moc [kW]", 0, 400, int(base.bess_p_kw), step=10)
    v2g = st.slider("Udział EV z V2G [%]", 0, 100, int(100 * base.v2g_share), step=5)

edited = base.with_(n_chargers=n_ch, ev_per_day=float(evd), p_max_kw=float(pmax), pv_kwp=float(pv),
                    wind_rated_kw=float(wind), bess_kwh=float(bess_kwh), bess_p_kw=float(bess_kw),
                    v2g_share=v2g / 100)
sites = [edited if s.name == site_name else s for s in KRAKOW_SITES]
cfg = SimConfig(days=days, dt_min=int(dt), seed=int(seed), data_source=source,
                ev_fleet="real" if fleet else "synthetic",
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

tab_anim, tab_ts, tab_cmp, tab_map, tab_in, tab_ev = st.tabs(
    ["🎬 Animacja", "📈 Przebiegi", "📊 Porównanie scenariuszy", "🗺️ Mapa przepływów", "🌦️ Dane wejściowe", "🚗 Sesje EV"])

# ---------------- Animacja ----------------
with tab_anim:
    a_strategy = st.radio("Scenariusz", list(CONTROLLERS), format_func=lambda k: CONTROLLERS[k].label,
                          horizontal=True, key="anim_strategy", index=2)
    st.caption("Przepływy mocy w węźle PCC – grubość i prędkość ∝ mocy, kierunek kropek = kierunek przepływu. "
               "Odtwarzanie działa w przeglądarce (▶, suwak, tempo).")
    st.iframe(flow_diagram_html(results[(site_name, a_strategy)]), height=640)

# ---------------- Przebiegi ----------------
with tab_ts:
    strategy = st.radio("Scenariusz", list(CONTROLLERS), format_func=lambda k: CONTROLLERS[k].label, horizontal=True)
    r = results[(site_name, strategy)]
    k_now, k_base = kpis(r), kpis(results[(site_name, "A")])
    cols = st.columns(6)
    for col, (m, better_low) in zip(cols, [("T_over [min/doba]", True), ("dP_over_max [kW]", True),
                                           ("SSR_import [%]", False), ("UDR [%]", True),
                                           ("Koszt [PLN]", True), ("EFC [-]", True)]):
        delta = k_now[m] - k_base[m]
        col.metric(m, f"{k_now[m]:.2f}", f"{delta:+.2f} vs A" if strategy != "A" else None,
                   delta_color="inverse" if better_low else "normal")
    fig = plot_timeseries(r)
    show(fig)
    res = energy_balance_residuals(r)
    st.caption("Walidacja bilansu energii (rezydua [kWh]): " + ", ".join(f"{k} = {v:.1e}" for k, v in res.items()))

# ---------------- Porównanie ----------------
with tab_cmp:
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
