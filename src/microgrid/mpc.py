"""D. Sterowanie predykcyjne (MPC) – programowanie liniowe w horyzoncie przesuwnym.

Co ``replan_min`` minut (oraz przy każdej zmianie zbioru podłączonych pojazdów) rozwiązywany
jest problem LP (HiGHS, ``scipy.optimize.linprog``) na horyzoncie ``horizon_h`` z krokiem
``lp_step_min``:

    min  Σ_j Δ·[ c_imp,j·P_imp,j − c_exp,j·P_exp,j ]            (taryfa: spot + opłaty zmienne + mocowa)
         + Σ_j Δ·w·(P_ch,j + P_dis,j)                            (zużycie BESS)
         − v·ΔE_BESS(H)                                           (wartość energii pozostawionej w BESS)
         + M_ov·Σ_j Δ·ov_j + M_s·Σ_i s_i                          (kary: przeciążenie PCC, niedoładowanie EV)
         + r·Σ_j |P_grid,j − P_grid,j−1|                           (wygładzenie profilu poboru)
    p.o. P_imp,j − P_exp,j = P_load,j − P_PV,j − P_wind,j + Σ_i (c_ij − d_ij) + P_ch,j − P_dis,j
         P_imp,j − ov_j ≤ 0.98·P_max,   P_exp,j − ov_j ≤ 0.98·P_max
         E_ij = E_i,j−1 + frac_ij·(c_ij − d_ij)·Δ ∈ [E_i,floor, E_i,target],   E_i,dep + s_i ≥ E_i,target
         E_B,j = E_B,j−1 + (η·P_ch,j − P_dis,j/η)·Δ ∈ [E_min, E_max]
         0 ≤ c_ij, d_ij ≤ P_i,max (d tylko V2G),  0 ≤ P_ch, P_dis ≤ P_BESS
    (frac_ij – część kroku j, w której pojazd i jest jeszcze podłączony)

Prognozy: ``perfect`` (znane wartości – górne ograniczenie jakości) lub ``persistence``
(odbiory / PV / wiatr z poprzedniej doby; ceny RCE/RDN znane z wyprzedzeniem).
Przyszłe przyjazdy EV nie są znane – plan obejmuje wyłącznie pojazdy podłączone.
Na koniec każdego kroku działa korekta czasu rzeczywistego (BESS → dławienie EV), gdy
chwilowa moc odbiega od uśrednionej prognozy.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from .controller import Controller, Decision, SiteState

M_OVERLOAD = 50.0    # PLN/kWh przeciążenia
M_SHORTFALL = 5.0    # PLN/kWh niedoładowania pojazdu
EPS_EARLY = 1e-5     # preferencja wcześniejszego ładowania przy równych kosztach
RAMP_COST = 0.02     # PLN za 1 kW zmiany mocy w PCC między krokami (wygładzenie profilu)


class MPCController(Controller):
    name = "D"
    label = "D. MPC (optymalizacja LP)"
    safety = 0.98
    horizon_h = 24.0
    lp_step_min = 15       # krok drobny (pierwsze fine_h godzin)
    fine_h = 6.0
    coarse_step_min = 60   # krok zgrubny w dalszej części horyzontu
    replan_min = 15

    def __init__(self) -> None:
        self.plan: dict | None = None
        self.n_solves = 0

    # ------------------------------------------------------------ siatka i prognoza
    def _grid(self, s: SiteState) -> np.ndarray:
        """Granice kroków LP [min od teraz]: drobny krok przez ``fine_h``, dalej ``coarse_step_min``."""
        dt = s.dt_min
        fine = max(self.lp_step_min, dt) // dt * dt
        coarse = max(self.coarse_step_min, fine) // fine * fine
        n_left = (len(s.forecast["net"]) - s.k) * dt          # do końca symulacji
        horizon = min(self.horizon_h * 60, n_left)
        b = list(np.arange(0, min(self.fine_h * 60, horizon), fine))
        t = b[-1] + fine if b else 0
        while t < horizon:
            b.append(t)
            t += coarse
        b.append(horizon)
        return np.array(b, dtype=float)

    def _forecast(self, s: SiteState, bounds: np.ndarray) -> dict[str, np.ndarray]:
        idx = (s.k + bounds / s.dt_min).astype(int)
        out = {}
        for key in ("net", "p_imp", "p_exp"):
            cs = np.concatenate([[0.0], np.cumsum(s.forecast[key])])
            out[key] = (cs[idx[1:]] - cs[idx[:-1]]) / np.diff(idx)
        return out

    # ------------------------------------------------------------ LP
    def _solve(self, s: SiteState) -> dict:
        B = self._grid(s)
        Lmin = np.diff(B)
        Lh = Lmin / 60.0
        fc = self._forecast(s, B)
        n = len(Lmin)
        site, bess = s.site, s.bess
        p_lim = self.safety * site.p_max_kw
        t0 = s.t_min

        cols: list[float] = []      # współczynniki celu
        lb: list[float] = []
        ub: list[float] = []

        def var(cost: float, lo: float, hi: float) -> int:
            cols.append(cost)
            lb.append(lo)
            ub.append(hi)
            return len(cols) - 1

        imp = [var(Lh[j] * fc["p_imp"][j] / 1000.0, 0, None) for j in range(n)]
        exp = [var(-Lh[j] * fc["p_exp"][j] / 1000.0, 0, None) for j in range(n)]
        ov = [var(M_OVERLOAD * Lh[j], 0, None) for j in range(n)]

        has_bess = bess.cap > 0 and bess.p_rated > 0
        eta = bess.eff
        v_term = float(np.mean(fc["p_exp"])) / 1000.0  # wartość kWh w BESS na końcu horyzontu
        wear = s.tariff.bess_wear_pln_kwh if s.tariff is not None else 0.0
        ch = [var(Lh[j] * (wear - v_term * eta), 0, bess.p_rated) for j in range(n)] if has_bess else []
        dis = [var(Lh[j] * (wear + v_term / eta), 0, bess.p_rated) for j in range(n)] if has_bess else []

        ev_vars: dict[int, tuple[list[int], list[int]]] = {}
        rows_ub: list[tuple[dict[int, float], float]] = []
        rows_eq: list[tuple[dict[int, float], float]] = []
        floor = s.v2g_soc_floor
        for ev in s.active:
            # frac_j – część kroku j, w której pojazd jest jeszcze podłączony
            frac = np.clip((ev.t_dep - t0 - B[:-1]) / Lmin, 0.0, 1.0)
            m = int(np.count_nonzero(frac > 0))
            if m == 0:
                continue
            e0 = ev.soc * ev.cap_kwh
            e_tgt = max(e0, ev.soc_target * ev.cap_kwh)
            e_floor = min(e0, floor * ev.cap_kwh) if ev.v2g else e0
            c = [var(EPS_EARLY * j, 0, ev.p_max_kw) for j in range(m)]
            d = [var(EPS_EARLY, 0, ev.p_max_kw) for j in range(m)] if ev.v2g else []
            e = [var(0.0, e_floor, e_tgt) for _ in range(m)]          # energia po kroku j
            ev_vars[ev.id] = (c, d)
            for j in range(m):
                coef = {e[j]: 1.0, c[j]: -Lh[j] * frac[j]}
                if d:
                    coef[d[j]] = Lh[j] * frac[j]
                if j:
                    coef[e[j - 1]] = -1.0
                rows_eq.append((coef, e0 if j == 0 else 0.0))
            sv = var(M_SHORTFALL, 0, None)
            rows_ub.append(({e[m - 1]: -1.0, sv: -1.0}, -e_tgt))               # E_dep + s ≥ E_target

        # bilans PCC i limity mocy (moc EV w kroku j działa tylko przez frac_j kroku)
        ev_frac = {}
        for ev in s.active:
            if ev.id in ev_vars:
                ev_frac[ev.id] = np.clip((ev.t_dep - t0 - B[:-1]) / Lmin, 0.0, 1.0)
        for j in range(n):
            coef = {imp[j]: 1.0, exp[j]: -1.0}
            for i, (c, d) in ev_vars.items():
                if j < len(c):
                    coef[c[j]] = -ev_frac[i][j]
                if j < len(d):
                    coef[d[j]] = ev_frac[i][j]
            if has_bess:
                coef[ch[j]] = -1.0
                coef[dis[j]] = 1.0
            rows_eq.append((coef, float(fc["net"][j])))
            rows_ub.append(({imp[j]: 1.0, ov[j]: -1.0}, p_lim))
            rows_ub.append(({exp[j]: 1.0, ov[j]: -1.0}, p_lim))

        # wygładzenie: u_j ≥ |P_grid,j − P_grid,j−1| (P_grid,−1 = moc zmierzona w poprzednim kroku)
        for j in range(n):
            u = var(RAMP_COST, 0, None)
            coef = {imp[j]: 1.0, exp[j]: -1.0}
            if j:
                coef[imp[j - 1]] = -1.0
                coef[exp[j - 1]] = 1.0
            rhs = 0.0 if j else s.p_grid_prev
            rows_ub.append(({**coef, u: -1.0}, rhs))
            rows_ub.append(({**{k: -v for k, v in coef.items()}, u: -1.0}, -rhs))

        if has_bess:
            eb = [var(0.0, bess.e_min, bess.e_max) for _ in range(n)]
            for j in range(n):
                coef = {eb[j]: 1.0, ch[j]: -eta * Lh[j], dis[j]: Lh[j] / eta}
                if j:
                    coef[eb[j - 1]] = -1.0
                rows_eq.append((coef, bess.energy if j == 0 else 0.0))

        nv = len(cols)

        def mat(rows):
            r, cidx, v = [], [], []
            for i, (coef, _) in enumerate(rows):
                for k, val in coef.items():
                    r.append(i)
                    cidx.append(k)
                    v.append(val)
            return coo_matrix((v, (r, cidx)), shape=(len(rows), nv)).tocsr(), np.array([b for _, b in rows])

        A_ub, b_ub = mat(rows_ub)
        A_eq, b_eq = mat(rows_eq)
        res = linprog(np.array(cols), A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                      bounds=list(zip(lb, ub)), method="highs")
        self.n_solves += 1
        if res.status != 0:
            return {"t0": t0, "B": B, "ev": {}, "bess": None, "ids": frozenset(ev.id for ev in s.active)}
        x = res.x
        plan_ev = {i: np.array([x[a] for a in c]) - (np.array([x[a] for a in d]) if d else 0.0)
                   for i, (c, d) in ev_vars.items()}
        plan_bess = (np.array([x[a] for a in ch]) - np.array([x[a] for a in dis])) if has_bess else None
        return {"t0": t0, "B": B, "ev": plan_ev, "bess": plan_bess, "ids": frozenset(ev.id for ev in s.active)}

    # ------------------------------------------------------------ decyzja
    def decide(self, s: SiteState) -> Decision:
        ids = frozenset(ev.id for ev in s.active)
        # odjazd nie unieważnia planu – przeplanowanie przy nowym pojeździe lub co replan_min
        if (self.plan is None or not ids <= self.plan["ids"]
                or s.t_min - self.plan["t0"] >= self.replan_min):
            self.plan = self._solve(s)
        P = self.plan
        j = int(np.searchsorted(P["B"], s.t_min - P["t0"], side="right")) - 1

        p_ev: dict[int, float] = {}
        for ev in s.active:
            seq = P["ev"].get(ev.id)
            if seq is None:
                p_ev[ev.id] = ev.max_charge_kw(s.dt_h)
            else:
                p_ev[ev.id] = float(seq[j]) if j < len(seq) else 0.0
        p_bess = float(P["bess"][j]) if P["bess"] is not None and j < len(P["bess"]) else 0.0

        # --- korekta czasu rzeczywistego (rzeczywista moc chwilowa ≠ średnia z prognozy) ---
        p_lim = self.safety * s.site.p_max_kw
        p_ev_lim = {e.id: (e.max_charge_kw(s.dt_h) if p_ev[e.id] >= 0
                           else -e.max_discharge_kw(s.t_min, s.dt_h, s.v2g_soc_floor)) for e in s.active}
        p_ev = {k: (min(v, p_ev_lim[k]) if v >= 0 else max(v, p_ev_lim[k])) for k, v in p_ev.items()}
        p_bess = (min(p_bess, s.bess.max_charge_kw(s.dt_h)) if p_bess >= 0
                  else -min(-p_bess, s.bess.max_discharge_kw(s.dt_h)))
        excess = s.p_net + sum(p_ev.values()) + p_bess - p_lim
        if excess > 0:
            room = p_bess + s.bess.max_discharge_kw(s.dt_h)
            cut = min(excess, max(0.0, room))
            p_bess -= cut
            excess -= cut
        if excess > 0:
            ch_sum = sum(v for v in p_ev.values() if v > 0)
            if ch_sum > 0:
                f = max(0.0, 1.0 - excess / ch_sum)
                p_ev = {k: (v * f if v > 0 else v) for k, v in p_ev.items()}
        return p_ev, p_bess
