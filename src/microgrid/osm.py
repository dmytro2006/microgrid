"""Rzeczywiste stacje ładowania EV w Krakowie z OpenStreetMap (Overpass API).

Pobierane są obiekty ``amenity=charging_station`` z prostokąta obejmującego Kraków.
Wynik trafia do ``data/cache/osm_chargers_krakow.csv`` – mapa korzysta z cache i działa
offline; przy braku cache i niedostępności Overpass warstwa jest po prostu pomijana.
Dane © współtwórcy OpenStreetMap, licencja ODbL.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

import pandas as pd

from .real_data import USER_AGENT, data_dir

KRAKOW_BBOX = (49.97, 19.79, 50.13, 20.22)  # (S, W, N, E)
ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
CACHE_NAME = "osm_chargers_krakow.csv"


def _max_power_kw(tags: dict) -> float | None:
    """Największa moc z tagów ``socket:*:output`` / ``charging_station:output`` (np. '22 kW', '50kW')."""
    vals = []
    for k, v in tags.items():
        if k.endswith(":output") or k == "maxpower":
            for num, unit in re.findall(r"([\d.]+)\s*(kw|w|mw)?", str(v).lower()):
                try:
                    x = float(num)
                except ValueError:
                    continue
                x = x / 1000 if unit == "w" else x * 1000 if unit == "mw" else x
                if 0 < x <= 1000:
                    vals.append(x)
    return max(vals) if vals else None


def parse_overpass(js: dict) -> pd.DataFrame:
    rows = []
    for el in js.get("elements", []):
        tags = el.get("tags", {})
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        cap = tags.get("capacity")
        rows.append({
            "osm_id": f"{el['type']}/{el['id']}", "lat": float(lat), "lon": float(lon),
            "name": tags.get("name") or tags.get("operator") or "stacja ładowania",
            "operator": tags.get("operator", ""),
            "capacity": int(cap) if str(cap).isdigit() else None,
            "max_kw": _max_power_kw(tags),
        })
    return pd.DataFrame(rows, columns=["osm_id", "lat", "lon", "name", "operator", "capacity", "max_kw"])


def fetch_chargers(retries_per_endpoint: int = 2, timeout: float = 90.0) -> pd.DataFrame:
    s, w, n, e = KRAKOW_BBOX
    query = f'[out:json][timeout:60];nwr["amenity"="charging_station"]({s},{w},{n},{e});out center tags;'
    body = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for url in ENDPOINTS:
        for i in range(retries_per_endpoint):
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return parse_overpass(json.loads(r.read().decode()))
            except Exception as ex:  # noqa: BLE001 – serwery Overpass bywają przeciążone
                last = ex
                time.sleep(5 * (i + 1))
    raise ConnectionError(f"Overpass niedostępny: {last}")


def load_chargers(fetch_if_missing: bool = False) -> pd.DataFrame | None:
    path = data_dir() / CACHE_NAME
    if path.exists():
        return pd.read_csv(path)
    if not fetch_if_missing:
        return None
    try:
        df = fetch_chargers()
    except ConnectionError:
        return None
    df.to_csv(path, index=False)
    return df
