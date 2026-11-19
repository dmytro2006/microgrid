"""Rzeczywista sieć wysokiego napięcia Krakowa z OpenStreetMap (Overpass API).

Pobierane są stacje (``power=substation``) oraz linie i kable 110/220/400 kV
(``power=line|cable``) z prostokąta obejmującego Kraków i okolice. Surowe elementy
Overpass trafiają do ``data/cache/osm_power_krakow.json`` – model działa offline;
Overpass jest odpytywany tylko wtedy, gdy cache nie istnieje.
Dane © współtwórcy OpenStreetMap, licencja ODbL.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .osm import ENDPOINTS
from .real_data import USER_AGENT, data_dir

POWER_BBOX = (49.95, 19.70, 50.17, 20.32)  # (S, W, N, E)
POWER_CACHE = "osm_power_krakow.json"
_HV_KV = (110, 220, 400)


@dataclass(frozen=True)
class Substation:
    osm_id: str
    name: str
    lat: float
    lon: float
    voltages_kv: tuple[int, ...]
    kind: str  # "pse" (220/400 kV), "gpz" (zawiera 15 kV), "hv_only"
    operator: str
    ref: str


@dataclass(frozen=True)
class HVLine:
    osm_id: str
    voltage_kv: int
    cable: bool
    circuits: int
    coords: tuple[tuple[float, float], ...]  # (lat, lon)
    name: str


@dataclass(frozen=True)
class PowerData:
    substations: tuple[Substation, ...]
    lines: tuple[HVLine, ...]


def _voltages_kv(tag: str | None) -> tuple[int, ...]:
    """Napięcia w kV z tagu ``voltage`` (np. '110000;15000'), rosnąco, bez zer i duplikatów."""
    out = set()
    for part in re.split(r"[;,]", tag or ""):
        try:
            v = float(part.strip()) / 1000
        except ValueError:
            continue
        if v > 0:
            out.add(int(round(v)))
    return tuple(sorted(out))


def _short_name(name: str) -> str:
    m = re.search(r"„([^”\"]+)[”\"]", name)
    return m.group(1).strip() if m else name


def _kind(volts: tuple[int, ...]) -> str:
    if 220 in volts or 400 in volts:
        return "pse"
    return "gpz" if 15 in volts else "hv_only"


def parse_power(js: dict) -> PowerData:
    subs = []
    for el in js.get("substations", []):
        tags = el.get("tags", {})
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        volts = _voltages_kv(tags.get("voltage"))
        subs.append(Substation(
            osm_id=f"{el['type']}/{el['id']}", name=_short_name(tags.get("name") or tags.get("ref") or "stacja"),
            lat=float(lat), lon=float(lon), voltages_kv=volts, kind=_kind(volts),
            operator=tags.get("operator", ""), ref=tags.get("ref", ""),
        ))
    lines = []
    for el in js.get("lines", []):
        tags = el.get("tags", {})
        hv = [v for v in _voltages_kv(tags.get("voltage")) if v in _HV_KV]
        geom = el.get("geometry") or []
        if not hv or len(geom) < 2:
            continue
        circ = tags.get("circuits", "")
        lines.append(HVLine(
            osm_id=f"{el['type']}/{el['id']}", voltage_kv=max(hv), cable=tags.get("power") == "cable",
            circuits=int(circ) if circ.isdigit() and int(circ) > 0 else 1,
            coords=tuple((float(p["lat"]), float(p["lon"])) for p in geom),
            name=tags.get("name") or tags.get("ref", ""),
        ))
    return PowerData(tuple(subs), tuple(lines))


def _overpass(query: str, retries_per_endpoint: int, timeout: float) -> list[dict]:
    body = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for url in ENDPOINTS:
        for i in range(retries_per_endpoint):
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode())["elements"]
            except Exception as ex:  # noqa: BLE001 – serwery Overpass bywają przeciążone
                last = ex
                time.sleep(5 * (i + 1))
    raise ConnectionError(f"Overpass niedostępny: {last}")


def fetch_power(retries_per_endpoint: int = 2, timeout: float = 120.0) -> dict:
    """Pobiera surowe elementy: ``{"substations": [...], "lines": [...]}`` (format cache)."""
    s, w, n, e = POWER_BBOX
    bb = f"({s},{w},{n},{e})"
    q_sub = f'[out:json][timeout:90];nwr["power"="substation"]["voltage"~"110000|220000|400000"]{bb};out center tags;'
    q_lin = (f'[out:json][timeout:90];way["power"~"^(line|cable)$"]["voltage"~"110000|220000|400000"]{bb};'
             "out tags geom;")
    return {"substations": _overpass(q_sub, retries_per_endpoint, timeout),
            "lines": _overpass(q_lin, retries_per_endpoint, timeout)}


def load_power_data(fetch_if_missing: bool = False) -> PowerData | None:
    path = data_dir() / POWER_CACHE
    if path.exists():
        return parse_power(json.loads(path.read_text(encoding="utf-8")))
    if not fetch_if_missing:
        return None
    try:
        raw = fetch_power()
    except ConnectionError:
        return None
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return parse_power(raw)
