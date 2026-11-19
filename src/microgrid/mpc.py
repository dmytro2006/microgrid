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
Awaria GPZ zapowiedziana (``SimConfig.outage_announced``): w krokach awarii P_imp = P_exp = 0
(proporcjonalnie do części kroku), a bilans domykają zmienne odciążenia (koszt VOLL) i ograniczenia OZE –
plan wcześniej ładuje BESS. Z farmą wiatrową przy GPZ (``wind_farm="gpz"``) w czasie awarii dozwolony jest
pobór do wysokości prognozowanego udziału węzła w produkcji farmy. Awaria nagła nie jest znana planowi; obsługuje ją dyspozycja wyspowa symulatora.
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
VOLL = 20.0          # PLN/kWh energii niedostarczonej odbiorcom (awaria GPZ, praca wyspowa)
CURT_COST = 1e-3     # PLN/kWh ograniczenia OZE (tylko w czasie awarii)


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
        for key in ("net", "load", "p_imp", "p_exp", "grid_ok", "farm"):
            if key not in s.forecast:
                continue
            cs = np.concatenate([[0.0], np.cumsum(np.asarray(s.forecast[key], dtype=float))])
            out[key] = (cs[idx[1:]] - cs[idx[:-1]]) / np.diff(idx)
        return out

    # ------------------------------------------------------------ oczekiwane przyjazdy
    def _virtual_agents(self, s: SiteState, B: np.ndarray) -> list[dict]:
        """Grupa oczekiwanych przyjazdów w każdym przyszłym kroku LP jako jeden zagregowany pojazd.

        n_j = Σ λ·Δt w kroku j; potrzebna energia n_j·E̅, moc n_j·P̄ (≤ wolne ładowarki),
        postój = średni czas postoju dla godziny przyjazdu. Kara za niedoładowanie jak dla
        prawdziwych pojazdów – plan zostawia więc margines mocy i energii BESS na przyszłe auta.
        """
        f = s.forecast
        lam, park, mu = f["ev_arrivals"], f["ev_park_h"], f["ev_means"]
        idx = (s.k + B / s.dt_min).astype(int)
        cs = np.concatenate([[0.0], np.cumsum(lam)])
        out = []
        for j in range(1, len(B) - 1):
            n_j = cs[idx[j + 1]] - cs[idx[j]]
            if n_j < 1e-3:
                continue
            t_a = s.t_min + B[j]
            out.append({
                "id": ("virt", j), "t_a": t_a, "t_d": t_a + 60.0 * float(park[min(idx[j], len(park) - 1)]),
                "e0": 0.0, "e_tgt": n_j * mu["e_need"], "e_floor": 0.0,
                "p": min(n_j, s.site.n_chargers) * mu["p"], "v2g": False, "pen": M_SHORTFALL,
            })
        return out

    # ------------------------------------------------------------ LP
    def _solve(self, s: SiteState) -> dict:
        B = self._grid(s)
        Lmin = np.diff(B)
        Lh = Lmin / 60.0
        fc = self._forecast(s, B)
        n = len(Lmin)
        site, bess = s.site, s.bess
        p_lim = self.safety * site.p_limit_kw
        t0 = s.t_min

        cols: list[float] = []      # współczynniki celu
        lb: list[float] = []
        ub: list[float] = []

        def var(cost: float, lo: float, hi: float) -> int:
            cols.append(cost)
            lb.append(lo)
            ub.append(hi)
            return len(cols) - 1

        # awaria zapowiedziana: o_j – część kroku j bez zasilania z GPZ (wymiana z siecią tylko poza awarią)
        out = 1.0 - fc["grid_ok"] if "grid_ok" in fc else np.zeros(n)
        # wariant "gpz": w czasie awarii pobór z GPZ do wysokości prognozowanego udziału farmy
        farm = fc["farm"] if "farm" in fc else np.zeros(n)
        cap_exp = [None if out[j] < 1e-9 else (1.0 - out[j]) * p_lim for j in range(n)]
        cap_imp = [None if out[j] < 1e-9 else (1.0 - out[j]) * p_lim + out[j] * max(0.0, farm[j]) for j in range(n)]
        imp = [var(Lh[j] * fc["p_imp"][j] / 1000.0, 0, cap_imp[j]) for j in range(n)]
        exp = [var(-Lh[j] * fc["p_exp"][j] / 1000.0, 0, cap_exp[j]) for j in range(n)]
        isl = {j: (var(VOLL * Lh[j], 0, out[j] * max(0.0, fc["load"][j])),                    # odciążenie
                   var(CURT_COST * Lh[j], 0, out[j] * max(0.0, fc["load"][j] - fc["net"][j])))  # ograniczenie OZE
               for j in range(n) if out[j] > 1e-9}
        ov = [var(M_OVERLOAD * Lh[j], 0, None) for j in range(n)]

        has_bess = bess.cap > 0 and bess.p_rated > 0
        eta = bess.eff
        v_term = float(np.mean(fc["p_exp"])) / 1000.0  # wartość kWh w BESS na końcu horyzontu
        wear = s.tariff.bess_wear_pln_kwh if s.tariff is not None else 0.0
        ch = [var(Lh[j] * (wear - v_term * eta), 0, bess.p_rated) for j in range(n)] if has_bess else []
        dis = [var(Lh[j] * (wear + v_term / eta), 0, bess.p_rated) for j in range(n)] if has_bess else []

        # --- agenci EV: pojazdy podłączone + „wirtualne” grupy oczekiwanych przyjazdów ---
        floor = s.v2g_soc_floor
        agents = []
        for ev in s.active:
            e0 = ev.soc * ev.cap_kwh
            agents.append({
                "id": ev.id, "t_a": t0, "t_d": ev.t_dep, "e0": e0,
                "e_tgt": max(e0, ev.soc_target * ev.cap_kwh),
                "e_floor": min(e0, floor * ev.cap_kwh) if ev.v2g else e0,
                "p": ev.p_max_kw, "v2g": ev.v2g, "pen": M_SHORTFALL,
            })
        if "ev_arrivals" in s.forecast:
            agents += self._virtual_agents(s, B)

        ev_vars: dict = {}
        ev_frac: dict = {}
        rows_ub: list[tuple[dict[int, float], float]] = []
        rows_eq: list[tuple[dict[int, float], float]] = []
        for ag in agents:
            # frac_j – część kroku j, w której pojazd jest podłączony
            frac = np.clip((np.minimum(ag["t_d"], t0 + B[1:]) - np.maximum(ag["t_a"], t0 + B[:-1])) / Lmin, 0.0, 1.0)
            idx = np.flatnonzero(frac > 0)
            if len(idx) == 0:
                continue
            js = list(range(idx[0], idx[-1] + 1))
            c = {j: var(EPS_EARLY * j, 0, ag["p"]) for j in js}
            d = {j: var(EPS_EARLY, 0, ag["p"]) for j in js} if ag["v2g"] else {}
            e = {j: var(0.0, ag["e_floor"], ag["e_tgt"]) for j in js}          # energia po kroku j
            ev_vars[ag["id"]] = (c, d)
            ev_frac[ag["id"]] = frac
            for j in js:
                coef = {e[j]: 1.0, c[j]: -Lh[j] * frac[j]}
                if d:
                    coef[d[j]] = Lh[j] * frac[j]
                if j != js[0]:
                    coef[e[j - 1]] = -1.0
                rows_eq.append((coef, ag["e0"] if j == js[0] else 0.0))
            sv = var(ag["pen"], 0, None)
            rows_ub.append(({e[js[-1]]: -1.0, sv: -1.0}, -ag["e_tgt"]))         # E_dep + s ≥ E_target

        # bilans PCC i limity mocy (moc EV w kroku j działa tylko przez frac_j kroku)
        for j in range(n):
            coef = {imp[j]: 1.0, exp[j]: -1.0}
            if j in isl:
                coef[isl[j][0]] = 1.0
                coef[isl[j][1]] = -1.0
            for i, (c, d) in ev_vars.items():
                if j in c:
                    coef[c[j]] = -ev_frac[i][j]
                if j in d:
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
        real_ids = {ev.id for ev in s.active}
        plan_ev = {i: np.array([x[c[j]] - (x[d[j]] if d else 0.0) for j in sorted(c)])
                   for i, (c, d) in ev_vars.items() if i in real_ids}
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
        p_lim = self.safety * s.site.p_limit_kw
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
