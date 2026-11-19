"""Mapa Krakowa z przepływami mocy (pydeck / deck.gl, kafelki OpenFreeMap – bez klucza API)."""

from __future__ import annotations

import pydeck as pdk

from .config import WIND_FARM_SITE
from .grid_osm import PowerData, load_power_data
from .grid_topology import nearest_district_gpz, site_gpz
from .simulation import Results

MAP_STYLE = "https://tiles.openfreemap.org/styles/positron"  # OpenFreeMap: OSM, bez klucza

# przesunięcia komponentów względem węzła PCC [stopnie]
OFFSETS = {
    "PV": (0.0030, -0.0045),
    "Wiatr": (0.0030, 0.0045),
    "BESS": (-0.0030, -0.0045),
    "Stacja EV": (-0.0030, 0.0045),
}
NODE_COLORS = {
    "PV": [242, 183, 5], "Wiatr": [59, 178, 208], "BESS": [47, 158, 68],
    "Stacja EV": [232, 89, 12], "GPZ": [28, 76, 120], "Farma": [12, 166, 120],
}
VOLTAGE_COLORS = {110: [124, 58, 237], 220: [240, 140, 0], 400: [201, 42, 42]}  # linie WN wg napięcia
GREY = [134, 142, 150]
SYNTHETIC_NOTE = "połączenie zastępcze (luka w OSM)"
FLOW_COLORS = {  # (kolor źródła, kolor celu) – gradient pokazuje kierunek
    "gen": ([255, 220, 80], [47, 158, 68]),
    "load": ([255, 170, 90], [201, 42, 42]),
    "import": ([120, 160, 220], [28, 76, 120]),
    "export": ([140, 220, 140], [20, 110, 60]),
}


def _load_color(ratio: float) -> list[int]:
    """Zielony → żółty → czerwony wg obciążenia |P_grid| / P_max."""
    r = max(0.0, min(ratio, 1.2)) / 1.2
    if r < 0.6:
        f = r / 0.6
        return [int(47 + f * (250 - 47)), int(158 + f * (176 - 158)), int(68 - f * 60), 230]
    f = (r - 0.6) / 0.4
    return [int(250 - f * (250 - 201)), int(176 - f * (176 - 42)), int(8 + f * 34), 230]


def _flow(src, dst, p_kw, p_max, kind, label):
    c_src, c_dst = FLOW_COLORS[kind]
    return {
        "source": [src[1], src[0]], "target": [dst[1], dst[0]],
        "width": 1.5 + 16.0 * min(abs(p_kw), 1.5 * p_max) / p_max,
        "c_src": c_src, "c_dst": c_dst,
        "name": label, "info": f"{abs(p_kw):.1f} kW",
    }


def voltage_outline(v: float) -> list[int]:
    """Obwódka węzła wg napięcia nN: zielona 0,95–1,05 p.u., pomarańczowa do 0,90/1,10, czerwona poza;
    biała, gdy brak rozpływu (NaN)."""
    if v != v:
        return [255, 255, 255]
    if 0.95 <= v <= 1.05:
        return [47, 158, 68]
    if 0.90 <= v <= 1.10:
        return [240, 140, 0]
    return [201, 42, 42]


def _gpz_xy(data: PowerData | None, name: str) -> tuple[float, float] | None:
    if data is None:
        return None
    for s in data.substations:
        if s.name == name:
            return (s.lat, s.lon)
    return None


def outage_gpzs(results: list[Results], k: int, data: PowerData | None) -> set[str]:
    """Nazwy GPZ objętych awarią w kroku k: GPZ węzłów bez zasilania + ``cfg.outage_gpz`` w oknie awarii."""
    out: set[str] = set()
    if data is None:
        return out
    for r in results:
        if "grid_ok" in r.ts and not bool(r.ts["grid_ok"].iloc[k]):
            out.add(site_gpz(r.site, data).name)
    cfg = results[0].cfg
    if cfg.outage_gpz and not bool(cfg.grid_available()[k]):
        out |= set(cfg.outage_gpz)
    return out


def map_layers_data(results: list[Results], k: int, data: PowerData | None = None,
                    ) -> tuple[list[dict], list[dict], list[dict]]:
    """Zwraca (węzły, przepływy, etykiety) dla kroku czasowego k; przepływ PCC ↔ GPZ węzła do jego własnego GPZ."""
    data = data if data is not None else load_power_data()
    out_gpz = outage_gpzs(results, k, data)
    nodes: list[dict] = []
    flows: list[dict] = []
    labels: list[dict] = []
    for g in sorted({site_gpz(r.site, data).name for r in results}) if data is not None else []:
        lat, lon = _gpz_xy(data, g)
        labels.append({"position": [lon, lat], "text": g + (" – AWARIA" if g in out_gpz else "")})

    for r in results:
        row = r.ts.iloc[k]
        s = r.site
        pcc = (s.lat, s.lon)
        gname = site_gpz(s, data).name if data is not None else None
        gpz = _gpz_xy(data, gname) if gname else None
        ok = bool(row["grid_ok"]) if "grid_ok" in row else True
        ratio = abs(row["p_grid"]) / s.p_max_kw
        v = float(row["v_node"]) if "v_node" in row else float("nan")
        v_info = f"<br/>Napięcie nN: {v:.3f} p.u." if v == v else ""
        nodes.append({
            "position": [pcc[1], pcc[0]], "radius": 170, "color": _load_color(ratio),
            "line": voltage_outline(v),
            "name": f"{s.name} – PCC",
            "info": (f"P_grid = {row['p_grid']:.1f} kW ({100 * ratio:.0f}% P_max)<br/>"
                     f"Odbiory: {row['load']:.1f} kW · EV: {row['p_ev']:.1f} kW ({int(row['n_active'])} poj., "
                     f"kolejka {int(row['n_queue'])})<br/>PV: {row['pv']:.1f} kW · Wiatr: {row['wind']:.1f} kW · "
                     f"BESS: {row['p_bess']:.1f} kW (SoC {100 * row['soc_bess']:.0f}%)<br/>"
                     f"Cena: {row['price']:.0f} PLN/MWh{v_info}"),
        })
        labels.append({"position": [pcc[1], pcc[0] + 0.0055], "text": s.name})

        comp_p = {"PV": row["pv"], "Wiatr": row["wind"], "BESS": row["p_bess"], "Stacja EV": row["p_ev"]}
        for comp, (dlat, dlon) in OFFSETS.items():
            if comp == "Wiatr" and s.wind_rated_kw <= 0:
                continue
            pos = (pcc[0] + dlat, pcc[1] + dlon)
            p = comp_p[comp]
            extra = f" · SoC {100 * row['soc_bess']:.0f}%" if comp == "BESS" else ""
            nodes.append({"position": [pos[1], pos[0]], "radius": 80, "color": NODE_COLORS[comp] + [220],
                          "name": f"{s.name} – {comp}", "info": f"{p:.1f} kW{extra}"})
            if abs(p) < 0.5:
                continue
            if comp in ("PV", "Wiatr") or (comp in ("BESS", "Stacja EV") and p < 0):
                flows.append(_flow(pos, pcc, p, s.p_max_kw, "gen", f"{comp} → PCC"))
            else:
                flows.append(_flow(pcc, pos, p, s.p_max_kw, "load", f"PCC → {comp}"))

        if gpz is None:
            continue
        if not ok:
            shed = f" · odciążenie {row['p_shed']:.1f} kW" if row.get("p_shed", 0) > 0.05 else ""
            flows.append({**_flow(gpz, pcc, 0.0, s.p_max_kw, "import", f"{gname} ✕ {s.name}"),
                          "width": 2.0, "c_src": [201, 42, 42], "c_dst": [201, 42, 42],
                          "info": "brak zasilania – praca wyspowa" + shed})
        elif row["p_grid"] >= 0:
            flows.append(_flow(gpz, pcc, row["p_grid"], s.p_max_kw, "import", f"{gname} → {s.name}"))
        else:
            flows.append(_flow(pcc, gpz, row["p_grid"], s.p_max_kw, "export", f"{s.name} → {gname}"))
    farm = farm_layer_data(results, k, data)
    if farm:
        node, flow, label = farm
        nodes.append(node)
        flows.append(flow)
        labels.append(label)
    return nodes, flows, labels


def farm_variant(results: list[Results]) -> str:
    return results[0].cfg.wind_farm if results else "none"


def farm_gpz(data: PowerData | None) -> tuple[str, tuple[float, float]] | None:
    """(nazwa, (lat, lon)) GPZ, do którego przyłączona jest farma – najbliższy GPZ dzielnicowy."""
    if data is None:
        return None
    s = nearest_district_gpz(WIND_FARM_SITE["lat"], WIND_FARM_SITE["lon"], data)
    return s.name, (s.lat, s.lon)


def farm_layer_data(results: list[Results], k: int, data: PowerData | None = None):
    """Węzeł farmy wiatrowej i jej połączenie z GPZ (fizyczne – "gpz", rozliczeniowe – "ppa")."""
    variant = farm_variant(results)
    if variant == "none":
        return None
    fpos = (WIND_FARM_SITE["lat"], WIND_FARM_SITE["lon"])
    data = data if data is not None else load_power_data()
    fg = farm_gpz(data)
    gname, gpz = fg if fg else ("GPZ", fpos)
    total = sum(float(r.ts["p_farm"].iloc[k]) for r in results)
    ok = bool(results[0].ts["grid_ok"].iloc[k])
    rated = 1000.0 * results[0].cfg.wind_farm_mw
    node = {"position": [fpos[1], fpos[0]], "radius": 260, "color": NODE_COLORS["Farma"] + [230],
            "name": WIND_FARM_SITE["name"],
            "info": f"produkcja {total:.0f} kW z {rated:.0f} kW · wariant: "
                    + ("przyłączona do GPZ" if variant == "gpz" else "PPA przez sieć OSD")}
    label = {"position": [fpos[1], fpos[0] + 0.006], "text": "Farma wiatrowa"}
    if variant == "gpz":
        p = total if ok else sum(max(0.0, float(r.ts["p_grid"].iloc[k])) for r in results)
        flow = _flow(fpos, gpz, p, rated, "gen", f"Farma → {gname}" + ("" if ok else " (wyspa)"))
        flow["info"] = f"{p:.0f} kW" + ("" if ok else " – zasila węzły w czasie awarii")
    else:
        flow = {**_flow(fpos, gpz, total, rated, "gen", "Farma – rozliczenie PPA"),
                "width": 1.5, "c_src": [12, 166, 120], "c_dst": [150, 150, 150],
                "info": f"{total:.0f} kW · energia płynie przez sieć OSD, nie przez ten GPZ"}
    return node, flow, label


def fit_zoom(lats: list[float], lons: list[float]) -> float:
    """Przybliżenie mapy obejmujące wszystkie węzły (z marginesem)."""
    import math

    span = max(max(lats) - min(lats), (max(lons) - min(lons)) * 0.64, 0.02)
    return max(10.0, min(13.0, math.log2(360 / span) - 1.6))


def chargers_data() -> list[dict]:
    """Rzeczywiste stacje ładowania z OSM (cache) w formacie warstw mapy; [] gdy brak danych."""
    from .osm import load_chargers

    df = load_chargers()
    if df is None or df.empty:
        return []
    out = []
    for r in df.itertuples():
        kw = f"{r.max_kw:g} kW" if r.max_kw == r.max_kw and r.max_kw else "moc nieznana"
        cap = f" · {int(r.capacity)} pkt" if r.capacity == r.capacity and r.capacity else ""
        out.append({"position": [r.lon, r.lat], "name": f"⚡ {r.name}",
                    "info": f"{r.operator or 'operator nieznany'} · {kw}{cap} (OSM)",
                    "dc": bool(r.max_kw == r.max_kw and r.max_kw and r.max_kw >= 40)})
    return out


def _path(coords) -> list[list[float]]:
    """OSM (lat, lon) → deck.gl [lon, lat]."""
    return [[round(lon, 5), round(lat, 5)] for lat, lon in coords]


def grid_lines_data(data: PowerData) -> list[dict]:
    """Wszystkie linie 110/220/400 kV z OSM (ścieżki [lon, lat], kolor wg napięcia)."""
    out = []
    for ln in data.lines:
        kind = "kabel" if ln.cable else "napowietrzna"
        out.append({"path": _path(ln.coords), "color": VOLTAGE_COLORS.get(ln.voltage_kv, GREY) + [190],
                    "name": ln.name or f"linia {ln.voltage_kv} kV",
                    "info": f"{ln.voltage_kv} kV · {kind} · {ln.circuits} obw. (OSM)"})
    return out


def station_data(data: PowerData, out_gpz: set[str] = frozenset(), nr=None, k: int = 0) -> list[dict]:
    """Stacje WN z OSM: ``pse`` większe; GPZ w awarii szare; z ``nr`` – obciążenie transformatora GPZ."""
    out = []
    for s in data.substations:
        down = s.name in out_gpz
        info = f"{' / '.join(str(v) for v in s.voltages_kv)} kV · {s.operator or 'operator nieznany'}"
        if down:
            info = "AWARIA – brak zasilania<br/>" + info
        elif nr is not None and s.name in nr.gpz_loading.columns:
            load = nr.gpz_loading[s.name].iloc[k]
            if load == load:
                info += f"<br/>Transformatory 110/15 kV: {load:.0f}%"
        color = GREY if down else (VOLTAGE_COLORS[400] if s.kind == "pse" else NODE_COLORS["GPZ"])
        out.append({"position": [s.lon, s.lat], "radius": 520 if s.kind == "pse" else 260,
                    "color": color + [235], "name": s.name + (" – AWARIA" if down else ""), "info": info})
    return out


def grid_layers(data: PowerData, nr=None, k: int = 0, results: list[Results] | None = None) -> list[pdk.Layer]:
    """Warstwy prawdziwej sieci WN Krakowa: linie OSM (wg napięcia), gałęzie modelu 110 kV (wg obciążenia
    w kroku k, zastępcze półprzezroczyste), stacje, linie 15 kV węzeł → GPZ (wg obciążenia w kroku k)."""
    layers = [pdk.Layer("PathLayer", grid_lines_data(data), get_path="path", get_color="color",
                        get_width=2, width_units="pixels", pickable=True)]
    if nr is not None:
        branches = []
        for i, b in enumerate(nr.branches):
            load = float(nr.hv_loading[k, i])
            color = _load_color(load / 100.0) if load == load else GREY + [200]
            if b.synthetic:
                color = color[:3] + [110]
            info = f"{b.length_km:.1f} km · " + (f"obciążenie {load:.0f}%" if load == load else "brak rozpływu")
            if b.synthetic:
                info += f" · {SYNTHETIC_NOTE}"
            branches.append({"path": _path(b.coords), "color": color, "name": f"{b.a} – {b.b}", "info": info})
        layers.append(pdk.Layer("PathLayer", branches, get_path="path", get_color="color",
                                get_width=4, width_units="pixels", pickable=True))
    out_gpz = outage_gpzs(results, k, data) if results else set()
    if results:
        feeders = []
        for r in results:
            s = r.site
            g = site_gpz(s, data)
            load = float(r.ts["feeder_loading"].iloc[k]) if "feeder_loading" in r.ts else float("nan")
            feeders.append({
                "path": [[s.lon, s.lat], [g.lon, g.lat]],
                "color": (_load_color(load / 100.0) if load == load else GREY + [160]),
                "name": f"{g.name} – {s.name} (linia 15 kV)",
                "info": f"obciążenie {load:.0f}%" if load == load else "brak rozpływu"})
        layers.append(pdk.Layer("PathLayer", feeders, get_path="path", get_color="color",
                                get_width=3, width_units="pixels", pickable=True))
    layers.append(pdk.Layer("ScatterplotLayer", station_data(data, out_gpz, nr, k), get_position="position",
                            get_radius="radius", radius_min_pixels=4, get_fill_color="color", stroked=True,
                            get_line_color=[255, 255, 255], line_width_min_pixels=1, pickable=True))
    return layers


def build_deck(results: list[Results], k: int, show_chargers: bool = True, nr=None) -> pdk.Deck:
    data = load_power_data()
    nodes, flows, labels = map_layers_data(results, k, data)
    for n in nodes:
        n.setdefault("line", [255, 255, 255])
    lats = [r.site.lat for r in results]
    lons = [r.site.lon for r in results]
    if farm_variant(results) != "none":
        lats.append(WIND_FARM_SITE["lat"])
        lons.append(WIND_FARM_SITE["lon"])
    view = pdk.ViewState(latitude=(max(lats) + min(lats)) / 2, longitude=(max(lons) + min(lons)) / 2,
                         zoom=fit_zoom(lats, lons), pitch=40, bearing=0)
    layers = [
        pdk.Layer("ArcLayer", flows, get_source_position="source", get_target_position="target",
                  get_source_color="c_src", get_target_color="c_dst", get_width="width",
                  get_height=0.25, pickable=True, auto_highlight=True),
        pdk.Layer("ScatterplotLayer", nodes, get_position="position", get_radius="radius",
                  get_fill_color="color", stroked=True, get_line_color="line",
                  line_width_min_pixels=2, pickable=True),
        pdk.Layer("TextLayer", labels, get_position="position", get_text="text", get_size=14,
                  get_color=[30, 30, 30], get_alignment_baseline="'bottom'",
                  character_set="auto", font_weight="bold"),
    ]
    if data is not None:
        layers = grid_layers(data, nr, k, results) + layers
    chargers = chargers_data() if show_chargers else []
    if chargers:
        layers.insert(0, pdk.Layer(
            "ScatterplotLayer", chargers, get_position="position", get_radius=35, radius_min_pixels=2.5,
            get_fill_color="dc ? [174, 62, 201, 210] : [132, 94, 247, 150]", pickable=True))
    return pdk.Deck(layers=layers, initial_view_state=view, map_style=MAP_STYLE, map_provider="carto",  # = renderer MapLibre; styl z MAP_STYLE
                    tooltip={"html": "<b>{name}</b><br/>{info}",
                             "style": {"fontSize": "12px"}})
