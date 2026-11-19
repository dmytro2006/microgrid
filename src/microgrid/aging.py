"""Półempiryczny model starzenia magazynu LFP (cykliczne + kalendarzowe).

Utrata pojemności (SoH) w horyzoncie symulacji:

  - **cykliczna** – cykle wyznaczane metodą *rainflow* (ASTM E1049, wersja 3-punktowa)
    z przebiegu SoC; każdy cykl o głębokości DoD zużywa 1/N(DoD) „życia”, gdzie
    N(DoD) = N_ref · (DoD / DoD_ref)^(−k)  (krzywa Wöhlera; k ≈ 1.6 dla LFP),
    a koniec życia = utrata ``EOL_LOSS`` (20%) pojemności;
  - **kalendarzowa** – liniowa w krótkim horyzoncie (w długim ~√t):
    dQ/dt = q_ref · f_SoC(SoC) · exp(Ea/R · (1/T_ref − 1/T_cell)),
    f_SoC = 0.5 + SoC (=1 przy SoC 50%), T_cell = 25 °C + 0.3·(T_amb − 25 °C)
    (kontener z częściowym zarządzaniem temperaturą).

Koszt starzenia = utrata SoH / EOL_LOSS · CAPEX · pojemność.
Strategia MPC używa w funkcji celu liniowego przybliżenia (koszt na kWh przepływu,
``Tariff.bess_wear_pln_kwh``); ten moduł służy do oceny ex post.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AgingParams:
    cycle_life_ref: float = 6000.0     # cykli do EOL przy DoD_ref
    dod_ref: float = 0.85
    wohler_k: float = 1.6
    eol_loss: float = 0.20             # koniec życia: 80% SoH
    calendar_loss_per_year: float = 0.02   # przy 25 °C i SoC 50%
    activation_energy_j_mol: float = 50_000.0
    t_ref_c: float = 25.0
    thermal_coupling: float = 0.3      # T_cell − 25 = k·(T_amb − 25)


R_GAS = 8.314


def turning_points(x: np.ndarray, tol: float = 1e-6) -> np.ndarray:
    """Punkty zwrotne przebiegu (z pominięciem płaskich odcinków)."""
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return x
    keep = [x[0]]
    for v in x[1:]:          # usuń odcinki stałe
        if abs(v - keep[-1]) > tol:
            keep.append(v)
    y = np.array(keep)
    if len(y) < 3:
        return y
    d = np.sign(np.diff(y))
    idx = np.flatnonzero(d[1:] != d[:-1]) + 1
    return np.concatenate([[y[0]], y[idx], [y[-1]]])


def rainflow(x: np.ndarray) -> list[tuple[float, float]]:
    """Zwraca listę (zakres, liczba) – liczba = 1.0 (pełny cykl) lub 0.5 (półcykl)."""
    stack: list[float] = []
    out: list[tuple[float, float]] = []
    for p in turning_points(x):
        stack.append(float(p))
        while len(stack) >= 3:
            rx = abs(stack[-1] - stack[-2])
            ry = abs(stack[-2] - stack[-3])
            if rx < ry:
                break
            if len(stack) == 3:
                out.append((ry, 0.5))
                stack.pop(0)
            else:
                out.append((ry, 1.0))
                del stack[-3:-1]
    for a, b in zip(stack[:-1], stack[1:]):
        out.append((abs(b - a), 0.5))
    return [(r, c) for r, c in out if r > 1e-9]


def cycle_loss(soc: np.ndarray, p: AgingParams = AgingParams()) -> float:
    """Utrata pojemności [ułamek] z cykli rainflow."""
    loss = 0.0
    for dod, cnt in rainflow(soc):
        n = p.cycle_life_ref * (dod / p.dod_ref) ** (-p.wohler_k)
        loss += cnt / n
    return loss * p.eol_loss


def calendar_loss(soc: np.ndarray, t_amb_c: np.ndarray, dt_h: float, p: AgingParams = AgingParams()) -> float:
    t_cell = p.t_ref_c + p.thermal_coupling * (np.asarray(t_amb_c, dtype=float) - p.t_ref_c) + 273.15
    t_ref = p.t_ref_c + 273.15
    arr = np.exp(p.activation_energy_j_mol / R_GAS * (1.0 / t_ref - 1.0 / t_cell))
    f_soc = 0.5 + np.asarray(soc, dtype=float)
    rate_h = p.calendar_loss_per_year / 8760.0
    return float(np.sum(rate_h * f_soc * arr) * dt_h)


def aging_kpis(soc: np.ndarray, t_amb_c: np.ndarray, dt_h: float, bess_kwh: float, capex_pln_kwh: float,
               p: AgingParams = AgingParams()) -> dict[str, float]:
    if bess_kwh <= 0:
        return {"Starzenie cykl. [%]": 0.0, "Starzenie kal. [%]": 0.0, "Utrata SoH BESS [%]": 0.0,
                "Zużycie BESS [PLN]": 0.0}
    cyc = cycle_loss(soc, p)
    cal = calendar_loss(soc, t_amb_c, dt_h, p)
    total = cyc + cal
    return {
        "Starzenie cykl. [%]": 100 * cyc,
        "Starzenie kal. [%]": 100 * cal,
        "Utrata SoH BESS [%]": 100 * total,
        "Zużycie BESS [PLN]": total / p.eol_loss * capex_pln_kwh * bess_kwh,
    }
