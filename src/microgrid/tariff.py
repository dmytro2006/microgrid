"""Taryfa dla odbiorcy biznesowego przyłączonego do sieci nN (grupa typu C2x, OSD Tauron Dystrybucja).

Model kosztu w horyzoncie symulacji:
  - energia czynna:   import × (cena spot + marża sprzedawcy); eksport × cena spot (rozliczenie wg RCE),
  - dystrybucja zm.:  import × (składnik zmienny sieciowy + opłata jakościowa + OZE + kogeneracyjna),
  - opłata mocowa:    import w godzinach 7:00–21:59 w dni robocze × stawka [PLN/MWh]
                      (sposób naliczania dla odbiorców nN innych niż gospodarstwa domowe),
  - składnik stały:   moc umowna × stawka [PLN/kW/mies.] × (dni / 30),
  - przekroczenie mocy umownej (§ rozporządzenia taryfowego): 10 × składnik stały × suma
    10 największych nadwyżek 15-minutowej mocy średniej ponad moc umowną (naliczana za miesiąc),
  - farma wiatrowa (opcjonalnie): udział w produkcji × cena PPA; zbilansowana część poboru nie jest
                      kupowana po cenie spot, nadwyżka udziału sprzedawana po cenie spot,
  - degradacja BESS:  przepływ energii × koszt cyklu (CAPEX / (2 · liczba cykli · DoD)).

UWAGA: stawki domyślne są orientacyjne (rząd wielkości taryf 2025/2026) – do podmiany na
wartości z aktualnej taryfy OSD i umowy sprzedaży. Wszystkie są parametrami ``Tariff``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Tariff:
    seller_margin_pln_mwh: float = 60.0        # marża / koszty sprzedawcy ponad cenę spot
    dist_variable_pln_mwh: float = 190.0       # składnik zmienny stawki sieciowej (nN)
    quality_pln_mwh: float = 31.0              # stawka jakościowa
    res_fees_pln_mwh: float = 7.0              # opłata OZE + kogeneracyjna
    capacity_fee_pln_mwh: float = 120.0        # opłata mocowa (7–22, dni robocze)
    fixed_pln_kw_month: float = 12.0           # składnik stały stawki sieciowej
    overrun_multiplier: float = 10.0           # krotność stawki za przekroczenie mocy umownej
    bess_capex_pln_kwh: float = 1500.0         # koszt magazynu LFP (system)
    bess_cycle_life: float = 6000.0            # liczba cykli do 80% SoH
    bess_dod: float = 0.85
    ppa_price_pln_mwh: float = 380.0           # cena energii z farmy wiatrowej w umowie PPA (pay-as-produced)

    @property
    def import_adders_pln_mwh(self) -> float:
        return self.seller_margin_pln_mwh + self.dist_variable_pln_mwh + self.quality_pln_mwh + self.res_fees_pln_mwh

    @property
    def bess_wear_pln_kwh(self) -> float:
        """Koszt zużycia na 1 kWh przepływu energii (ładowanie LUB rozładowanie)."""
        return self.bess_capex_pln_kwh / (2.0 * self.bess_cycle_life * self.bess_dod)


DEFAULT_TARIFF = Tariff()


def capacity_fee_mask(times: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """True w godzinach objętych opłatą mocową (dni robocze 7:00–21:59)."""
    t = pd.DatetimeIndex(times)
    return ((t.day_of_week < 5) & (t.hour >= 7) & (t.hour < 22))


def import_price(price_spot: np.ndarray, times, tariff: Tariff = DEFAULT_TARIFF) -> np.ndarray:
    """Pełna cena krańcowa poboru z sieci [PLN/MWh] (spot + opłaty zmienne + mocowa w szczycie)."""
    return price_spot + tariff.import_adders_pln_mwh + tariff.capacity_fee_pln_mwh * capacity_fee_mask(times)


def cost_breakdown(ts: pd.DataFrame, dt_min: int, days: float, p_contract_kw: float, bess_kwh: float,
                   tariff: Tariff = DEFAULT_TARIFF) -> dict[str, float]:
    dt_h = dt_min / 60.0
    imp = ts["p_grid"].clip(lower=0)
    exp = (-ts["p_grid"]).clip(lower=0)
    mwh = lambda p: p * dt_h / 1000.0  # noqa: E731

    # farma wiatrowa (PPA pay-as-produced): udział w produkcji kupowany po cenie PPA; w części pokrywającej
    # pobór w tym samym kroku zastępuje zakup po cenie spot, nadwyżka udziału sprzedawana po cenie spot
    farm = ts["p_farm"] if "p_farm" in ts else pd.Series(0.0, index=ts.index)
    matched = np.minimum(imp, farm)
    energy = float((mwh(imp - matched) * (ts["price"] + tariff.seller_margin_pln_mwh)).sum()
                   - (mwh(exp) * ts["price"]).sum())
    # koszt PPA netto: zakup udziału po cenie PPA − odsprzedaż niewykorzystanej nadwyżki po cenie spot
    ppa = float(mwh(farm).sum() * tariff.ppa_price_pln_mwh - (mwh(farm - matched) * ts["price"]).sum())
    dist = float(mwh(imp).sum() * (tariff.dist_variable_pln_mwh + tariff.quality_pln_mwh + tariff.res_fees_pln_mwh))
    capacity = float((mwh(imp) * capacity_fee_mask(ts["time"])).sum() * tariff.capacity_fee_pln_mwh)
    fixed = p_contract_kw * tariff.fixed_pln_kw_month * days / 30.0

    # moc 15-minutowa średnia (okresy rozliczeniowe liczone od pełnego kwadransa)
    p15 = ts.set_index("time")["p_grid"].clip(lower=0).resample("15min").mean()
    over = (p15 - p_contract_kw).clip(lower=0).sort_values(ascending=False)
    penalty = tariff.overrun_multiplier * tariff.fixed_pln_kw_month * float(over.iloc[:10].sum())

    wear = float(ts["p_bess"].abs().sum() * dt_h * tariff.bess_wear_pln_kwh) if bess_kwh > 0 else 0.0
    total = energy + ppa + dist + capacity + fixed + penalty + wear
    return {
        "Energia [PLN]": energy, "Farma PPA [PLN]": ppa, "Dystrybucja [PLN]": dist, "Opł. mocowa [PLN]": capacity,
        "Opł. stała [PLN]": fixed, "Kara moc umowna [PLN]": penalty, "Zużycie BESS [PLN]": wear,
        "Koszt całk. [PLN]": total,
    }
