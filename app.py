"""Panel Streamlit: symulacja mikrosieci EV + BESS + OZE w Krakowie (dane syntetyczne lub rzeczywiste).

Uruchomienie:
    uv run streamlit run app.py
"""

from __future__ import annotations

import dataclasses
import datetime as dt_
import io

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from microgrid.analysis import (InvestmentCosts, best_contract, contract_sweep, hosting_capacity_table, monte_carlo,
                                sizing_grid, summarize)
from microgrid import config as C
from microgrid.animation import animated_map_html, flow_diagram_html, sankey_html, station_html
from microgrid.config import ALL_SITES, SimConfig
from microgrid.controller import CONTROLLERS
from microgrid.cosim import run_all_network
from microgrid.grid_osm import load_power_data
from microgrid.grid_topology import site_gpz
from microgrid.map_view import build_deck
from microgrid.metrics_evaluator import energy_balance_residuals, kpi_table, kpis, network_summary
from microgrid.plots import (plot_contract_sweep, plot_cost_breakdown, plot_grid_comparison, plot_kpi_bars,
                             plot_monte_carlo, plot_real_inputs, plot_sizing_heatmap, plot_timeseries)
from microgrid.report import build_report
from microgrid.simulation import run_all
from microgrid.tariff import DEFAULT_TARIFF

def show(fig) -> None:
    """Wyświetla wykres jako SVG (grafika wektorowa – ostra przy każdej szerokości i skali ekranu).

    Uwaga: obrazy rastrowe (PNG) Streamlit zmniejsza po stronie serwera do 1460 px,
    co przy szerokim układzie daje rozmycie – SVG nie jest skalowane.
    """
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    st.image(buf.getvalue(), width="stretch")


@st.cache_data(show_spinner="Symulacja w toku… (z siecią 110/15 kV pierwsze liczenie ok. 1 min)")
def simulate(sites_t: tuple, cfg: SimConfig):
    """Wyniki węzłów {(węzeł, strategia): Results} i – przy symulacji sieci – {strategia: NetworkResults}."""
    if cfg.network:
        return run_all_network(list(sites_t), cfg)
    return run_all(list(sites_t), cfg), {}


@st.cache_data(show_spinner=False)
def gpz_of_sites() -> dict[str, str]:
    """GPZ zasilający każdy węzeł (dane sieci WN z OSM); pusty słownik, gdy brak danych."""
    data = load_power_data()
    return {s.name: site_gpz(s, data).name for s in ALL_SITES} if data is not None else {}


NET_ASSUMPTIONS = (
    f"Sieć 110/15 kV (pandapower): topologia stacji i linii WN z OpenStreetMap (dane © współtwórcy OSM, ODbL), "
    f"brakujące połączenia zastępcze ×{C.ROUTE_FACTOR:g} długości w linii prostej. Tło: "
    f"{C.GPZ_BG_PEAK_KW / 1000:g} MW szczytu na GPZ, {C.FEEDER_BG_PEAK_KW / 1000:g} MW na linię 15 kV węzła, "
    f"cos φ = {C.BG_COS_PHI:g}. Linie 110 kV: napowietrzne 243-AL1/39-ST1A, kable N2XS(FL)2Y 1×240; "
    f"linie 15 kV: NA2XS2Y 1×240. GPZ 2 × 25 MVA 110/15 kV (vk 11%), transformator węzła 15/0,4 kV "
    f"Sn = P_max/0,95 (vk 6%). EC Łęg {C.CHP_P_MIN_KW / 1000:g}–{C.CHP_P_MAX_KW / 1000:g} MW (zależnie od "
    f"temperatury: moc rośnie od {C.CHP_T_START_C:g} °C do {C.CHP_T_DESIGN_C:g} °C średniej dobowej "
    f"temperatury), ZTPO {C.ZTPO_KW / 1000:g} MW, napięcie w stacjach PSE {C.EXT_GRID_VM_PU:g} p.u. "
    f"Granice: U 0,90–1,10 p.u., obciążenie ≤ 100%.")


def main() -> None:
    """Cały panel. Wywoływany tylko w głównym procesie Streamlit (nie w procesach puli obliczeń)."""
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
        ev_fc = st.toggle("MPC: prognoza przyjazdów EV", value=True,
                          help="Plan rezerwuje moc i energię dla aut, które statystycznie dopiero przyjadą.")

        st.header("Sieć")
        network = st.toggle("Sieć 110/15 kV (rozpływ mocy)", value=True, key="network",
                            help="Wspólna symulacja węzłów w rzeczywistej sieci WN Krakowa (OSM) z rozpływem mocy "
                                 "w każdym kroku: napięcia nN, obciążenia linii 15 kV, transformatorów i linii 110 kV. Z siecią pierwsze liczenie trwa ok. 1 min (kolejne – z pamięci podręcznej).")
        net_ctrl = st.toggle("Koordynator sieciowy", value=True, key="net_ctrl", disabled=not network,
                             help="Węzeł naruszający ograniczenia sieci (U poza 0,90–1,10 p.u., obciążenie > 100%) "
                                  "dostaje żądanie zmiany mocy PCC: BESS → EV → ograniczenie PV/wiatru.")
        pv_mult = st.slider("Mnożnik PV", 1.0, 10.0, 1.0, step=0.5, key="pv_mult",
                            help="Mnoży produkcję PV wszystkich węzłów – scenariusze nasycenia sieci fotowoltaiką.")

        st.header("Awaria sieci (GPZ)")
        outage = st.toggle("⚠️ Symuluj awarię / blackout", value=False,
                           help="Brak zasilania z GPZ – węzły pracują wyspowo (P_grid = 0). Deficyt: BESS → "
                                "ograniczenie ładowania EV → V2G → odciążenie odbiorów; nadwyżka: BESS → EV → "
                                "ograniczenie OZE.")
        out_start = st.slider("Początek awarii [h od startu]", 0.0, days * 24.0 - 0.5, min(18.0, days * 24.0 - 1.0),
                              step=0.5, disabled=not outage)
        out_dur = st.slider("Czas trwania [h]", 0.5, 24.0, 4.0, step=0.5, disabled=not outage)
        out_ann = st.toggle("Awaria zapowiedziana (MPC zna ją z wyprzedzeniem)", value=False, disabled=not outage,
                            help="Np. planowane wyłączenie OSD. MPC (D) zawczasu ładuje BESS; pozostałe strategie "
                                 "nie korzystają z prognoz.")
        site_gpzs = gpz_of_sites()
        # węzły wybrane w sekcji „Lokalizacja” (widget niżej – wartość z poprzedniego przebiegu skryptu)
        sel_sites = st.session_state.get("sites_chosen") or [s.name for s in ALL_SITES]
        gpz_opts = sorted({site_gpzs[n] for n in sel_sites if n in site_gpzs})
        out_gpz = st.multiselect("GPZ objęte awarią", gpz_opts, key="outage_gpz", disabled=not outage,
                                 help="Puste = awaria wszystkich GPZ (blackout). Wybrane stacje: węzły pod nimi "
                                      "pracują wyspowo, pozostałe normalnie; przy sieci rozpływ bez tych stacji.")

        st.header("Farma wiatrowa poza miastem")
        farm = st.radio("Wariant", ["none", "ppa", "gpz"], key="farm",
                        format_func=lambda x: {"none": "brak", "ppa": "przez sieć OSD (umowa PPA)",
                                               "gpz": "przyłączona do GPZ (+ wyspa przy awarii)"}[x],
                        help="Wspólna farma dzielona między węzły proporcjonalnie do mocy umownej. "
                             "PPA: energia płynie przez sieć publiczną – obniża rachunek, ale nie odciąża "
                             "transformatorów i nie pomaga przy awarii. GPZ: rozliczenie jak PPA, a w czasie "
                             "awarii GPZ pracuje wyspowo z farmą i zasila węzły do wysokości ich udziałów.")
        farm_mw = st.slider("Moc farmy [MW]", 0.5, 10.0, 3.0, step=0.5, disabled=farm == "none")
        farm_hub = st.select_slider("Wysokość piasty [m]", options=[80, 100, 120, 140], value=120,
                                    disabled=farm == "none")

        st.header("Lokalizacja")
        all_names = [s.name for s in ALL_SITES]
        chosen = st.multiselect("Węzły w symulacji", all_names, default=all_names, key="sites_chosen",
                                help="9 węzłów w Krakowie; nowe – w miejscach skupisk ładowarek z OpenStreetMap.")
        names = chosen or all_names[:1]
        site_name = st.selectbox("Węzeł do edycji / analizy", names)
        base = next(s for s in ALL_SITES if s.name == site_name)
        n_ch = st.slider("Liczba ładowarek", 2, 40, base.n_chargers)
        evd = st.slider("Pojazdy EV / dobę", 5, 250, int(base.ev_per_day))
        pmax = st.slider("P_max przyłącza [kW]", 50, 800, int(base.p_max_kw), step=2,
                         help="Typoszereg transformatorów: 160/250/400/630 kVA ≈ 152/238/380/600 kW przy cos φ = 0.95")
        pcon = st.slider("Moc umowna [kW]", 30, int(pmax), int(min(base.p_contract, pmax)), step=2,
                         help="Podstawa opłaty stałej i kar; EMS pilnuje min(moc umowna, P_max).")
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
                        p_contract_kw=None if pcon >= pmax else float(pcon),
                        wind_rated_kw=float(wind), bess_kwh=float(bess_kwh), bess_p_kw=float(bess_kw),
                        v2g_share=v2g / 100, load_type=load_type)
    sites = [edited if s.name == site_name else s for s in ALL_SITES if s.name in names]
    cfg = SimConfig(days=days, dt_min=int(dt), seed=int(seed), data_source=source,
                    ev_fleet="real" if fleet else "synthetic", forecast=forecast, mpc_ev_forecast=ev_fc,
                    outage_start_h=float(out_start) if outage else None, outage_dur_h=float(out_dur),
                    outage_announced=bool(out_ann),
                    outage_gpz=tuple(g for g in out_gpz if g in gpz_opts) if outage else (),
                    pv_multiplier=float(pv_mult), network=bool(network), network_control=bool(net_ctrl),
                    wind_farm=farm, wind_farm_mw=float(farm_mw), wind_farm_hub_m=float(farm_hub),
                    start=f"{start:%Y-%m-%d} 00:00" if source == "real" else SimConfig.start)
    caption.caption(
        "Hybrydowa symulacja DES (SimPy) + krok bilansowy Δt. Dane wejściowe: " +
        ("**rzeczywiste** – pogoda Open-Meteo (ERA5/IFS), ceny RCE i zapotrzebowanie KSE z API PSE."
         if source == "real" else "**syntetyczne**.") +
        (" Węzły w rzeczywistej sieci 110/15 kV Krakowa z rozpływem mocy (pandapower)"
         f"{' i koordynatorem sieciowym' if net_ctrl else ''}." if network else "") +
        (f" Mnożnik PV × {pv_mult:g}." if pv_mult != 1.0 else ""))




    try:
        results, nets = simulate(tuple(sites), cfg)
    except ConnectionError as e:
        st.error(f"Nie udało się pobrać danych rzeczywistych ({e}). Przełącz na dane syntetyczne "
                 "albo wybierz datę, dla której dane są już w `data/cache`.")
        st.stop()
    except RuntimeError as e:
        st.error(f"Brak danych sieci OSM w `data/cache` ({e}) – wyłącz sieć 110/15 kV albo przywróć plik danych.")
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
        view = st.radio("Widok", ["schemat", "parking", "porownanie", "sankey"], horizontal=True,
                        format_func=lambda x: {"schemat": "⚡ Schemat węzła", "parking": "🅿️ Parking stacji",
                                               "porownanie": "🆚 Porównanie strategii", "sankey": "🔀 Sankey"}[x])
        if view == "porownanie":
            c1, c2 = st.columns(2)
            sa = c1.selectbox("Lewa", list(CONTROLLERS), index=0, format_func=lambda k: CONTROLLERS[k].label)
            sb = c2.selectbox("Prawa", list(CONTROLLERS), index=3, format_func=lambda k: CONTROLLERS[k].label)
        else:
            a_strategy = st.radio("Scenariusz", list(CONTROLLERS), format_func=lambda k: CONTROLLERS[k].label,
                                  horizontal=True, key="anim_strategy", index=3)
        ra = results[(site_name, a_strategy)] if view != "porownanie" else None
        if view == "schemat":
            st.caption("Przepływy mocy w węźle PCC – grubość i prędkość ∝ mocy, kierunek kropek = kierunek przepływu.")
            st.iframe(flow_diagram_html(ra), height=640)
        elif view == "parking":
            st.caption("Auta wjeżdżają na stanowiska i odjeżdżają; kolor: 🟧 ładowanie · 🟩 V2G · 🟦 naładowane · "
                       "⬜ czeka. Pasek = SoC, kreska = cel. Najedź na auto, aby zobaczyć model.")
            st.iframe(station_html([ra]), height=620)
        elif view == "porownanie":
            st.caption("Ten sam strumień pojazdów i pogody, dwa algorytmy sterowania – wspólny odtwarzacz.")
            st.iframe(station_html([results[(site_name, sa)], results[(site_name, sb)]]), height=640)
        else:
            st.caption("Skąd pochodzi energia i dokąd płynie – chwilowo lub narastająco.")
            st.iframe(sankey_html(ra), height=540)
        st.caption("Odtwarzanie działa w przeglądarce (▶, suwak, tempo).")

    # ---------------- Przebiegi ----------------
    with tab_ts:
        strategy = st.radio("Scenariusz", list(CONTROLLERS), format_func=lambda k: CONTROLLERS[k].label, horizontal=True)
        r = results[(site_name, strategy)]
        k_now, k_base = kpis(r), kpis(results[(site_name, "A")])
        cols = st.columns(7)
        for col, (m, better_low) in zip(cols, [("T_over [min/doba]", True), ("dP_over_max [kW]", True),
                                               ("SSR_import [%]", False), ("UDR [%]", True),
                                               ("UDR wykonalne [%]", True), ("Koszt całk. [PLN]", True),
                                               ("Utrata SoH BESS [%]", True)]):
            delta = k_now[m] - k_base[m]
            col.metric(m, f"{k_now[m]:.2f}", f"{delta:+.2f} vs A" if strategy != "A" else None,
                       delta_color="inverse" if better_low else "normal")
        if cfg.outage_start_h is not None:
            t0 = pd.Timestamp(cfg.start) + pd.Timedelta(hours=cfg.outage_start_h)
            st.warning(f"⚠️ Awaria GPZ {t0:%d.%m %H:%M} – "
                       f"{t0 + pd.Timedelta(hours=cfg.outage_dur_h):%d.%m %H:%M}: praca wyspowa"
                       f"{' (zapowiedziana)' if cfg.outage_announced else ' (nagła)'}.")
            cols = st.columns(4)
            for col, m in zip(cols, ["Awaria GPZ [h]", "ENS [kWh]", "ENS [%]", "Ograniczenie OZE [kWh]"]):
                delta = k_now[m] - k_base[m]
                col.metric(m, f"{k_now[m]:.1f}", f"{delta:+.1f} vs A" if strategy != "A" and m != "Awaria GPZ [h]"
                           else None, delta_color="inverse",
                           help="ENS – energia niedostarczona odbiorcom (odciążenie) w czasie awarii."
                           if m.startswith("ENS") else None)
        if cfg.wind_farm != "none":
            cols = st.columns(3)
            cols[0].metric("Farma: energia [kWh]", f"{k_now['Farma: energia [kWh]']:.0f}",
                           help="Udział węzła w produkcji farmy (proporcjonalny do mocy umownej).")
            cols[1].metric("Farma: pokrycie poboru [%]", f"{k_now['Farma: pokrycie poboru [%]']:.1f}",
                           help="Część energii pobranej z sieci, zbilansowana w tym samym kroku produkcją farmy.")
            cols[2].metric("Farma PPA [PLN]", f"{k_now['Farma PPA [PLN]']:.0f}")
        if cfg.network:
            cols = st.columns(6)
            for col, (m, fmt) in zip(cols, [("U min [p.u.]", "{:.3f}"), ("U max [p.u.]", "{:.3f}"),
                                            ("Obc. linii SN max [%]", "{:.1f}"), ("Obc. transf. max [%]", "{:.1f}"),
                                            ("Ograniczenie PV przez sieć [kWh]", "{:.1f}"),
                                            ("Naruszenia sieci [kroki]", "{:.0f}")]):
                col.metric(m, fmt.format(k_now[m]),
                           help="Szyna nN węzła, linia 15 kV GPZ → węzeł, transformator 15/0,4 kV; ograniczenie "
                                "PV i naruszenia – koordynator sieciowy (granice 0,90–1,10 p.u., ≤ 100%).")
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
        st.dataframe(table.dropna(axis=1, how="all").style.format("{:.2f}"), width="stretch")
        if nets:
            st.subheader("Sieć 110/15 kV – podsumowanie strategii")
            st.dataframe(pd.DataFrame({CONTROLLERS[s].label: network_summary(nr, cfg.dt_h) for s, nr in nets.items()})
                         .T.style.format("{:.2f}"), width="stretch")
            st.caption("Obciążenie linii 110 kV, straty w sieci 110/15 kV, wymiana wewnętrzna (energia oddana przez "
                       "jedne węzły i pobrana przez inne w tym samym kroku), szczytowy pobór z sieci PSE i produkcja "
                       "EC Łęg w horyzoncie. Kroki bez rozpływu (blackout) pominięte.")

    # ---------------- Mapa ----------------
    with tab_map:
        c1, c2 = st.columns([1, 3])
        with c1:
            map_strategy = st.radio("Scenariusz na mapie", list(CONTROLLERS),
                                    format_func=lambda k: CONTROLLERS[k].label, key="map_strategy")
            animated = st.toggle("Animacja (cząstki energii)", value=True)
        rs = [results[(s.name, map_strategy)] for s in sites]
        nr = nets.get(map_strategy)
        times = rs[0].ts["time"]
        k_peak = int(sum(x.ts["p_grid"].abs() for x in rs).idxmax())
        if not rs[0].ts["grid_ok"].all():  # awaria → start od jej początku
            k_peak = int((~rs[0].ts["grid_ok"]).idxmax())
        if animated:
            with c2:
                st.markdown("Cząstki płyną od źródła do odbiorcy; ich prędkość i liczba ∝ moc. "
                            "Kolor PCC: 🟢 niskie → 🔴 przeciążenie (|P_grid| / P_max). Start: chwila szczytu.")
            st.iframe(animated_map_html(rs, start_k=k_peak, autoplay=False, height=880, nr=nr), height=900)
            k = k_peak
            st.caption(f"Tabela: stan w chwili szczytu {times.iloc[k]:%d.%m %H:%M}")
        else:
            with c2:
                k = st.slider("Chwila symulacji", 0, len(times) - 1, k_peak,
                              help="Domyślnie: chwila największego łącznego poboru z sieci")
                st.markdown(f"**{times.iloc[k]:%d.%m.%Y %H:%M}** – grubość łuku ∝ moc, kolor węzła PCC: "
                            "🟢 niskie → 🔴 przeciążenie (|P_grid| / P_max)")
            st.pydeck_chart(build_deck(rs, k, nr=nr), height=850)
            st.caption(f"Tabela: stan w chwili {times.iloc[k]:%d.%m %H:%M}")
        snap = pd.DataFrame([{
            "Lokalizacja": x.site.name, "P_grid [kW]": x.ts["p_grid"].iloc[k], "P_max [kW]": x.site.p_max_kw,
            "Odbiory [kW]": x.ts["load"].iloc[k], "EV [kW]": x.ts["p_ev"].iloc[k], "PV [kW]": x.ts["pv"].iloc[k],
            "Wiatr [kW]": x.ts["wind"].iloc[k], "BESS [kW]": x.ts["p_bess"].iloc[k],
            "SoC BESS [%]": 100 * x.ts["soc_bess"].iloc[k],
        } for x in rs]).set_index("Lokalizacja")
        st.dataframe(snap.style.format("{:.1f}"), width="stretch")
        if nr is not None:
            st.caption(NET_ASSUMPTIONS)

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
        mc_metrics = ["T_over [min/doba]", "UDR wykonalne [%]", "Koszt całk. [PLN]", "SSR_import [%]"]
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
        st.subheader("Dobór mocy umownej")
        st.caption("Niższa moc umowna = niższa opłata stała, ale EMS musi mocniej ograniczać pobór (rośnie UDR), "
                   "a bez sterowania (A) – kary za przekroczenia.")
        c1, c2 = st.columns([2, 1])
        ct_strats = c1.multiselect("Strategie ", list(CONTROLLERS), default=["A", "C", "D"],
                                   format_func=lambda k: CONTROLLERS[k].label)
        udr_pen = c2.number_input("Kara za 1 p.p. UDR wykonalnego [PLN]", 0, 5000, 0, step=100,
                                  help="Wycena niezadowolenia klientów – przesuwa optimum w stronę wyższej mocy.")
        if st.button("▶ Przelicz moc umowną") and ct_strats:
            vals = sorted({int(round(edited.p_max_kw * f)) for f in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)})
            with st.spinner(f"{len(vals) * len(ct_strats)} symulacji…"):
                st.session_state["contract"] = contract_sweep(edited, cfg, tuple(ct_strats), vals)
        if "contract" in st.session_state:
            ct = st.session_state["contract"]
            show(plot_contract_sweep(ct))
            best = best_contract(ct, udr_pen)[["P_umowna [kW]", "Koszt całk. [PLN]", "UDR wykonalne [%]",
                                               "Opł. stała [PLN]", "Kara moc umowna [PLN]"]]
            st.dataframe(best.style.format("{:.1f}"), width="stretch")

        st.divider()
        st.subheader("Zdolność przyłączeniowa PV")
        st.caption("Największa moc PV węzła, przy której sieć 110/15 kV nie jest przeciążona (napięcie nN ≤ 1,10 p.u., "
                   "linia 15 kV i transformator ≤ 100%). Bisekcja po mocy PV, 1 doba od daty startu, Δt = 15 min, "
                   "bez koordynatora, awarii i mnożnika PV; liczą się tylko naruszenia, których nie ma przy PV = 0. "
                   "Ok. 10–20 symulacji na węzeł.")
        c1, c2 = st.columns(2)
        hc_strat = c1.selectbox("Strategia ", list(CONTROLLERS), index=0, key="hc_strategy",
                                format_func=lambda k: CONTROLLERS[k].label)
        hc_max = c2.select_slider("Szukaj do [kWp]", options=[1000, 2000, 5000, 10000], value=5000, key="hc_max")
        hc_key = (tuple((s.name, s.pv_kwp) for s in sites), cfg.start, cfg.data_source, hc_strat, hc_max)
        if st.button("▶ Wyznacz zdolność przyłączeniową", key="hc_run"):
            with st.spinner(f"Zdolność przyłączeniowa PV {len(sites)} węzłów…"):
                st.session_state["hosting"] = (hc_key, hosting_capacity_table(
                    sites, dataclasses.replace(cfg, pv_multiplier=1.0, outage_gpz=()), hc_strat, float(hc_max)))
        if "hosting" in st.session_state and st.session_state["hosting"][0] != hc_key:
            st.info("Ustawienia zmieniły się – przelicz ponownie.")
        elif "hosting" in st.session_state:
            st.dataframe(st.session_state["hosting"][1].style.format({"PV obecnie [kWp]": "{:.0f}",
                                                                   "Zdolność PV [kWp]": "{:.0f}"}),
                         width="stretch")

        st.divider()
        st.subheader("Parametry taryfy (orientacyjne)")
        st.dataframe(pd.Series(DEFAULT_TARIFF.__dict__, name="wartość").to_frame()
                     .assign(jednostka=["PLN/MWh"] * 5 + ["PLN/kW/mies.", "×", "PLN/kWh", "cykli", "-", "PLN/MWh"]),
                     width="stretch")



# Streamlit wykonuje skrypt jako moduł __main__. Procesy potomne puli (forkserver/spawn) importują
# go ponownie jako __mp_main__ – bez tej osłony każdy worker uruchamiałby cały panel i własną symulację.
if __name__ == "__main__":
    main()
