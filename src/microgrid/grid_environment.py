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
