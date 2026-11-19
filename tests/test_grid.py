"""Testy danych sieci WN z OpenStreetMap."""

from __future__ import annotations

import math

from microgrid.config import ALL_SITES
from microgrid.grid_osm import HVLine, PowerData, Substation, load_power_data, parse_power
from microgrid.grid_topology import hv_branches, km, site_gpz


def _sub(i, name, voltage, lat=50.0, lon=20.0):
    return {"type": "way", "id": i, "center": {"lat": lat, "lon": lon},
            "tags": {"name": name, "voltage": voltage, "operator": "Tauron", "ref": f"R{i}", "power": "substation"}}


def test_parse_power_fixture():
    js = {
        "substations": [
            _sub(1, "Stacja elektroenergetyczna 400/220/110 kV „SE Test”", "400000;220000;110000"),
            _sub(2, "Stacja elektroenergetyczna 110/15 kV „GPZ Test”", "110000;15000"),
            _sub(3, "Rozdzielnia", "110000"),
        ],
        "lines": [{"type": "way", "id": 10, "tags": {"voltage": "110000;0", "circuits": "2", "power": "line"},
                   "geometry": [{"lat": 50.0, "lon": 20.0}, {"lat": 50.01, "lon": 20.0}, {"lat": 50.02, "lon": 20.01}]}],
    }
    d = parse_power(js)
    assert tuple(s.kind for s in d.substations) == ("pse", "gpz", "hv_only")
    assert d.substations[1].name == "GPZ Test"
    assert d.substations[0].voltages_kv == (110, 220, 400)
    assert d.substations[2].name == "Rozdzielnia"
    ln = d.lines[0]
    assert ln.circuits == 2 and len(ln.coords) == 3 and ln.voltage_kv == 110 and not ln.cable


def _len_km(coords):
    return sum(math.hypot((b[0] - a[0]) * 111.2, (b[1] - a[1]) * 111.2 * math.cos(math.radians(a[0])))
               for a, b in zip(coords, coords[1:]))


def test_cache_has_krakow_grid():
    d = load_power_data()
    assert d is not None and len(d.substations) >= 40
    assert sorted(s.name for s in d.substations if s.kind == "pse") == ["SE Lubocza", "SE Skawina", "SE Wanda"]
    names = {s.name for s in d.substations}
    assert {"GPZ AGH", "GPZ Łęg", "GPZ ZTPO"} <= names
    assert sum(_len_km(l.coords) for l in d.lines if l.voltage_kv == 110) > 400


def _fx_sub(name, lat, lon, kind="gpz"):
    return Substation(osm_id=name, name=name, lat=lat, lon=lon, voltages_kv=(110, 15), kind=kind, operator="", ref="")


def test_branches_fixture():
    a, b = _fx_sub("A", 50.0, 20.0), _fx_sub("B", 50.0, 20.07)
    mid = (50.01, 20.035)
    l1 = HVLine("way/1", 110, False, 2, ((50.0, 20.0), mid), "")
    l2 = HVLine("way/2", 110, False, 2, (mid, (50.0, 20.07)), "")
    data = PowerData((a, b), (l1, l2))
    br = hv_branches(data)
    assert len(br) == 1
    exp = km(l1.coords[0], l1.coords[1]) + km(l2.coords[0], l2.coords[1])
    assert abs(br[0].length_km - exp) / exp < 0.01
    assert {br[0].a, br[0].b} == {"A", "B"} and not br[0].synthetic


def _components(data):
    parent = {s.name: s.name for s in data.substations}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for b in hv_branches(data):
        parent[find(b.a)] = find(b.b)
    return find


def test_all_site_gpz_connected_to_pse():
    data = load_power_data()
    find = _components(data)
    pse = {find(s.name) for s in data.substations if s.kind == "pse"}
    for site in ALL_SITES:
        assert find(site_gpz(site, data).name) in pse, site.name


def test_site_gpz_mapping():
    data = load_power_data()
    assert {s.name: site_gpz(s, data).name for s in ALL_SITES} == {
        "AGH / Czarna Wieś": "GPZ AGH", "Nowa Huta – os. Centrum": "GPZ Czyżyny", "Kazimierz": "GPZ Dajwór",
        "Bonarka / Podgórze": "GPZ Bonarka", "Ruczaj / Kampus UJ": "GPZ Kampus",
        "Dworzec Główny": "GPZ Centrum Komunikacyjne", "Czyżyny": "GPZ Wieczysta",
        "Bieżanów-Prokocim": "GPZ Bieżanów", "Prądnik Biały": "GPZ Górka"}


def test_synthetic_branches_marked():
    br = hv_branches(load_power_data())
    assert sum(b.synthetic for b in br) <= 10
