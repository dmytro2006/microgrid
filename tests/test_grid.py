"""Testy danych sieci WN z OpenStreetMap."""

from __future__ import annotations

import math

from microgrid.grid_osm import load_power_data, parse_power


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
