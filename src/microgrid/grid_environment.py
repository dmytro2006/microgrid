"""Model węzła PCC i stacjonarnego magazynu energii BESS."""

from __future__ import annotations

import math

from .config import SiteConfig


class BESS:
    """Magazyn bateryjny. Konwencja: p > 0 ładowanie, p < 0 rozładowanie (moc po stronie sieci)."""

    def __init__(self, site: SiteConfig):
        self.cap = site.bess_kwh
        self.p_rated = site.bess_p_kw
        self.eff = math.sqrt(site.bess_roundtrip_eff)  # sprawność jednokierunkowa
        self.e_min = site.bess_soc_min * self.cap
        self.e_max = site.bess_soc_max * self.cap
        self.energy = site.bess_soc_init * self.cap
        self.e_init = self.energy
        self.losses_kwh = 0.0

    @property
    def soc(self) -> float:
        return self.energy / self.cap if self.cap > 0 else 0.0

    def max_charge_kw(self, dt_h: float) -> float:
        if self.cap <= 0:
            return 0.0
        return max(0.0, min(self.p_rated, (self.e_max - self.energy) / (self.eff * dt_h)))

    def max_discharge_kw(self, dt_h: float) -> float:
        if self.cap <= 0:
            return 0.0
        return max(0.0, min(self.p_rated, (self.energy - self.e_min) * self.eff / dt_h))

    def step(self, p_kw: float, dt_h: float) -> float:
        """Wykonuje krok; zwraca faktycznie zrealizowaną moc (po przycięciu do ograniczeń)."""
        if p_kw >= 0:
            p = min(p_kw, self.max_charge_kw(dt_h))
            stored = p * self.eff * dt_h
            self.losses_kwh += p * dt_h - stored
            self.energy += stored
        else:
            p = -min(-p_kw, self.max_discharge_kw(dt_h))
            drawn = -p / self.eff * dt_h
            self.losses_kwh += drawn - (-p * dt_h)
            self.energy -= drawn
        return p


def pcc_balance(load: float, ev_sum: float, p_bess: float, pv: float, wind: float) -> float:
    """Równanie bilansu PCC: P_grid = P_load + ΣP_EV ± P_BESS − P_PV − P_Wind."""
    return load + ev_sum + p_bess - pv - wind


def _shift(p: dict, room: dict, need: float) -> float:
    """Przesuwa moce ``p[i]`` o ułamek ``room[i]`` (ze znakiem) proporcjonalnie, łącznie o min(need, Σ|room|).

    Zwraca wartość bezwzględną łącznego przesunięcia [kW]."""
    total = sum(abs(r) for r in room.values())
    if total <= 1e-12 or need <= 0:
        return 0.0
    f = min(1.0, need / total)
    for i, r in room.items():
        p[i] += f * r
    return f * total


def island_dispatch(load: float, gen: float, p_ev: dict, ev_ch_max: dict, ev_dis_max: dict,
                    p_bess: float, bess_ch_max: float, bess_dis_max: float) -> tuple[dict, float, float, float]:
    """Praca wyspowa (awaria GPZ): koryguje polecenia EMS tak, by P_grid = 0.

    Deficyt: BESS (rozładowanie) → ograniczenie ładowania EV → V2G → odciążenie (load shedding).
    Nadwyżka: BESS (ładowanie) → ograniczenie V2G → doładowanie EV → ograniczenie OZE (curtailment).
    ``ev_dis_max`` ≥ 0 – dopuszczalna moc oddawana przez pojazd. Zwraca (p_ev, p_bess, shed, curtail) [kW].
    """
    p_ev = dict(p_ev)
    net = load - gen + sum(p_ev.values()) + p_bess
    shed = curt = 0.0
    if net > 0:
        d = min(net, max(0.0, p_bess + bess_dis_max))
        p_bess -= d
        net -= d
        net -= _shift(p_ev, {i: -v for i, v in p_ev.items() if v > 0}, net)               # mniej ładowania EV
        net -= _shift(p_ev, {i: -(v + ev_dis_max.get(i, 0.0)) for i, v in p_ev.items()
                             if v + ev_dis_max.get(i, 0.0) > 0}, net)                       # V2G (vehicle-to-home)
        shed = min(max(net, 0.0), load)
    elif net < 0:
        s = -net
        c = min(s, max(0.0, bess_ch_max - p_bess))
        p_bess += c
        s -= c
        s -= _shift(p_ev, {i: -v for i, v in p_ev.items() if v < 0}, s)                   # mniej V2G
        s -= _shift(p_ev, {i: ev_ch_max[i] - v for i, v in p_ev.items()
                         if ev_ch_max.get(i, 0.0) - v > 0}, s)                               # więcej ładowania EV
        curt = min(max(s, 0.0), gen)
    return p_ev, p_bess, shed, curt
