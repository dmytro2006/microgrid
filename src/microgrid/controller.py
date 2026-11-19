"""Strategie koordynatora EMS (scenariusze A / B / C z dokumentacji)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import SiteConfig
from .ev_generator import EV
from .grid_environment import BESS


@dataclass
class SiteState:
    t_min: float
    dt_h: float
    load: float
    pv: float
    wind: float
    price: float
    price_signal: float  # cena wygładzona (średnia godzinowa) – podstawa decyzji EMS
    price_low: float   # próg „taniej” energii (percentyl 30 cen dobowych – RDN znany z góry)
    price_high: float  # próg „drogiej” energii (percentyl 80)
    active: list[EV]
    bess: BESS
    site: SiteConfig
    v2g_soc_floor: float
    # dane dla strategii predykcyjnych (D): indeks kroku, prognozy na całym horyzoncie, taryfa
    k: int = 0
    dt_min: int = 5
    forecast: dict = field(default_factory=dict)
    tariff: object = None
    p_grid_prev: float = 0.0

    @property
    def p_net(self) -> float:
        """Bilans bez floty EV i BESS: P_load − P_PV − P_Wind."""
        return self.load - self.pv - self.wind


Decision = tuple[dict[int, float], float]  # ({ev_id: P_EV}, P_BESS)


class Controller:
    name = "base"
    label = ""

    def decide(self, s: SiteState) -> Decision:  # pragma: no cover
        raise NotImplementedError


class BaselineController(Controller):
    """A. Dumb charging: pełna moc od wpięcia, BESS wyłączony."""

    name = "A"
    label = "A. Baseline (dumb charging)"

    def decide(self, s: SiteState) -> Decision:
        return {ev.id: ev.max_charge_kw(s.dt_h) for ev in s.active}, 0.0


class PeakShavingController(Controller):
    """B. Heurystyka progowa: BESS powyżej 0.85·P_max, dławienie proporcjonalne EV."""

    name = "B"
    label = "B. Peak shaving (heurystyka)"
    threshold = 0.85
    valley = 0.50  # doładowanie BESS z sieci w dolinie obciążenia

    def decide(self, s: SiteState) -> Decision:
        p_max = s.site.p_limit_kw
        p_ev = {ev.id: ev.max_charge_kw(s.dt_h) for ev in s.active}
        ev_sum = sum(p_ev.values())
        p_pred = s.p_net + ev_sum

        p_bess = 0.0
        if p_pred > self.threshold * p_max:
            p_bess = -min(s.bess.max_discharge_kw(s.dt_h), p_pred - self.threshold * p_max)
        elif p_pred < 0:  # nadwyżka OZE
            p_bess = min(s.bess.max_charge_kw(s.dt_h), -p_pred)
        elif p_pred < self.valley * p_max:
            p_bess = min(s.bess.max_charge_kw(s.dt_h), self.valley * p_max - p_pred)
        p_pred += p_bess

        if p_pred > p_max and ev_sum > 0:
            factor = max(0.0, 1.0 - (p_pred - p_max) / ev_sum)
            p_ev = {k: v * factor for k, v in p_ev.items()}
        return p_ev, p_bess


class SmartController(Controller):
    """C. Smart charging + V2G: Algorytm 1 (EDF) + arbitraż cenowy BESS + V2G."""

    name = "C"
    label = "C. Smart charging + V2G"
    safety = 0.98          # margines bezpieczeństwa względem P_max
    defer_slack_min = 120  # przy drogiej energii odłóż ładowanie, jeśli zapas czasu > 2 h
    v2g_slack_min = 180    # V2G tylko dla pojazdów z zapasem > 3 h

    def decide(self, s: SiteState) -> Decision:
        dt_h = s.dt_h
        p_lim = self.safety * s.site.p_limit_kw
        p_net = s.p_net
        cheap = s.price_signal <= s.price_low
        expensive = s.price_signal >= s.price_high

        # --- Krok 1-9 Alg. 1: BESS (peak shaving / akumulacja OZE / arbitraż – rozładowanie) ---
        margin = p_lim - p_net
        if margin < 0:
            p_bess = -min(s.bess.max_discharge_kw(dt_h), -margin)
        elif p_net < 0:
            p_bess = min(s.bess.max_charge_kw(dt_h), -p_net)
        elif expensive:
            p_bess = -min(s.bess.max_discharge_kw(dt_h), p_net)  # zasilanie lokalnych odbiorów
        else:
            p_bess = 0.0
        margin = p_lim - p_net - p_bess

        # --- Krok 10-13: sortowanie EDF i zachłanny przydział mocy ---
        p_ev: dict[int, float] = {}
        order = sorted(s.active, key=lambda ev: ev.laxity_deadline())
        for ev in order:
            slack = ev.laxity_deadline() - s.t_min
            p_dis = ev.max_discharge_kw(s.t_min, dt_h, s.v2g_soc_floor)
            if p_dis > 0 and slack > self.v2g_slack_min and (expensive or margin < 0):
                p = -p_dis  # V2G
            elif expensive and slack > self.defer_slack_min:
                p = 0.0  # przesunięcie ładowania na tańsze godziny
            else:
                p = min(ev.max_charge_kw(dt_h), max(0.0, margin))
            p_ev[ev.id] = p
            margin -= p

        # --- Arbitraż: tania energia -> doładuj BESS wolnym marginesem ---
        if cheap and p_bess >= 0 and margin > 0:
            extra = min(s.bess.max_charge_kw(dt_h) - p_bess, margin)
            if extra > 0:
                p_bess += extra
                margin -= extra
        return p_ev, p_bess


CONTROLLERS: dict[str, type[Controller]] = {
    "A": BaselineController,
    "B": PeakShavingController,
    "C": SmartController,
}


from .mpc import MPCController  # noqa: E402  (rejestracja strategii D; import po definicjach – brak cyklu)

CONTROLLERS["D"] = MPCController
