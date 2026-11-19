"""Topologia sieci 110 kV: gałęzie stacja–stacja zbudowane z odcinków linii OpenStreetMap.

Odcinki OSM (``way``) rzadko łączą dwie stacje bezpośrednio – zwykle jedna linia składa się z wielu
odcinków o wspólnych końcach. Odcinki są więc sklejane w ścieżki stacja → stacja, a luki w danych
uzupełniane gałęziami zastępczymi (``synthetic=True``).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .config import ROUTE_FACTOR, SiteConfig
from .grid_osm import PowerData, Substation

_LAT0 = 50.05  # szerokość odniesienia rzutu równoodległościowego


@dataclass(frozen=True)
class Branch:
    a: str                                # nazwa stacji początkowej
    b: str                                # nazwa stacji końcowej
    length_km: float
    cable: bool
    circuits: int
    coords: tuple[tuple[float, float], ...]  # (lat, lon)
    synthetic: bool                       # True → połączenie zastępcze (luka w danych OSM)


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Odległość [km] między punktami (lat, lon) – rzut równoodległościowy przy 50,05° N."""
    dy = (b[0] - a[0]) * 111.195
    dx = (b[1] - a[1]) * 111.195 * math.cos(math.radians(_LAT0))
    return math.hypot(dx, dy)


def _path_km(coords) -> float:
    return sum(km(p, q) for p, q in zip(coords, coords[1:]))


def _key(p: tuple[float, float]) -> tuple[float, float]:
    return (round(p[0], 5), round(p[1], 5))


def hv_branches(data: PowerData, snap_km: float = 0.4) -> list[Branch]:
    """Gałęzie 110 kV między stacjami (linie 220/400 kV pomijane), z łącznością do stacji ``pse``."""
    subs = list(data.substations)

    def station_at(p) -> str | None:
        best, best_d = None, snap_km
        for s in subs:
            d = km(p, (s.lat, s.lon))
            if d <= best_d:
                best, best_d = s.name, d
        return best

    # graf: wierzchołek = nazwa stacji (str) lub punkt (tuple); krawędź = odcinek linii
    edges = []  # (u, v, line, coords w kierunku u→v)
    adj: dict = defaultdict(list)
    cache: dict = {}
    for ln in data.lines:
        if ln.voltage_kv != 110:
            continue
        ends = []
        for p in (ln.coords[0], ln.coords[-1]):
            k = _key(p)
            if k not in cache:
                cache[k] = station_at(k) or k
            ends.append(cache[k])
        u, v = ends
        if u == v:
            continue
        i = len(edges)
        edges.append((u, v, ln, ln.coords))
        adj[u].append((i, v, False))
        adj[v].append((i, u, True))
    for lst in adj.values():
        lst.sort(key=lambda t: _path_km(edges[t[0]][3]))

    used: set[int] = set()
    branches: list[Branch] = []

    def walk(start: str, first):
        """Szuka (DFS) ścieżki ze stacji ``start`` przez punkty niebędące stacjami do innej stacji."""
        stack = [(start, [first], {start})]
        while stack:
            node, path, seen = stack.pop()
            i, nxt, _ = path[-1]
            if isinstance(nxt, str):
                if nxt != start:
                    return path
                continue
            if nxt in seen:
                continue
            for t in reversed(adj[nxt]):
                if t[0] not in used and t[0] not in {p[0] for p in path}:
                    stack.append((nxt, path + [t], seen | {nxt}))
        return None

    for s in subs:
        for first in list(adj.get(s.name, [])):
            if first[0] in used:
                continue
            path = walk(s.name, first)
            if path is None:
                continue
            coords: list = []
            lines = []
            for i, _, rev in path:
                c = list(reversed(edges[i][3])) if rev else list(edges[i][3])
                coords.extend(c if not coords else c[1:])
                lines.append(edges[i][2])
                used.add(i)
            branches.append(Branch(
                a=s.name, b=path[-1][1], length_km=_path_km(coords),
                cable=all(l.cable for l in lines), circuits=max(1, min(l.circuits for l in lines)),
                coords=tuple(coords), synthetic=False))

    return branches + _synthetic(subs, branches)


def _synthetic(subs: list[Substation], branches: list[Branch]) -> list[Branch]:
    """Łączy składowe bez stacji ``pse`` z najbliższą stacją ze składowej z ``pse`` (połączenia zastępcze)."""
    parent = {s.name: s.name for s in subs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for b in branches:
        parent[find(b.a)] = find(b.b)
    by_name = {s.name: s for s in subs}
    out: list[Branch] = []
    while True:
        roots = {find(s.name) for s in subs}
        pse_roots = {find(s.name) for s in subs if s.kind == "pse"}
        if not pse_roots:
            return out
        orphans = sorted(roots - pse_roots)
        if not orphans:
            return out
        root = orphans[0]
        mine = [s for s in subs if find(s.name) == root]
        target = [s for s in subs if find(s.name) in pse_roots]
        d, s1, s2 = min(((km((x.lat, x.lon), (y.lat, y.lon)), x, y) for x in mine for y in target),
                        key=lambda t: t[0])
        out.append(Branch(a=s1.name, b=s2.name, length_km=d * ROUTE_FACTOR, cable=False, circuits=1,
                          coords=((s1.lat, s1.lon), (s2.lat, s2.lon)), synthetic=True))
        parent[find(s1.name)] = find(s2.name)


def site_gpz(site: SiteConfig, data: PowerData) -> Substation:
    """GPZ węzła: ``site.gpz`` po nazwie, inaczej najbliższa stacja ``kind == "gpz"`` o nazwie „GPZ …”
    (stacje trakcyjne, np. Płaszów, Podłęże, nie są GPZ dzielnicowymi)."""
    cands = [s for s in data.substations if s.kind == "gpz" and s.name.startswith("GPZ ")]
    if site.gpz is not None:
        for s in data.substations:
            if s.name == site.gpz:
                return s
        raise KeyError(f"brak stacji {site.gpz!r} w danych OSM")
    return min(cands, key=lambda s: km((site.lat, site.lon), (s.lat, s.lon)))
