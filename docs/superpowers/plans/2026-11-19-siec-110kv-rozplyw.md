# Realna sieć 110/15 kV z rozpływem mocy – plan wdrożenia

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wspólna symulacja wszystkich węzłów w prawdziwej sieci 110/15 kV Krakowa (OSM) z rozpływem mocy AC
w każdym kroku, koordynatorem sieciowym, elektrociepłownią Łęg, analizą zdolności przyłączeniowej PV
i mapą prawdziwej sieci.

**Architecture:** Dane OSM (stacje i linie WN) → topologia (odcinki między stacjami) → model pandapower
budowany raz, w każdym kroku aktualizowane tylko moce. Dotychczasowa pętla `simulation.run` zostaje
rozłożona na obiekt `SiteSim` (propozycja → zatwierdzenie kroku); nowy `cosim.run_network` prowadzi
wszystkie `SiteSim` w jednym środowisku SimPy, między propozycją a zatwierdzeniem liczy rozpływ i koryguje
węzły.

**Tech Stack:** Python 3.14, uv, pandapower ≥ 3.5 (pandas 2.3.x, numpy < 2.5), SimPy, scipy/HiGHS (MPC),
Streamlit, pydeck/deck.gl.

**Spec:** `docs/superpowers/specs/2026-11-19-siec-110kv-rozplyw-design.md`

## Global Constraints

- Interfejs, komentarze, nazwy KPI po polsku; styl jak w istniejącym kodzie (docstringi, `from __future__ import annotations`).
- Praca offline: dane OSM w `data/cache/osm_power_krakow.json` (w repo); Overpass tylko gdy brak cache.
- `SimConfig.network` domyślnie `False` – istniejące testy, Monte Carlo i dobór PV/BESS bez zmian zachowania.
- Granice: napięcie nN 0,90–1,10 p.u. (ostrzeżenie > 1,05), obciążenie linii i transformatorów ≤ 100%.
- Założenia liczbowe (stałe w `config.py`, nazwane w panelu):
  `GPZ_BG_PEAK_KW = 20_000`, `FEEDER_BG_PEAK_KW = 2_000`, `BG_COS_PHI = 0.95`, `ROUTE_FACTOR = 1.3`,
  `CHP_P_MAX_KW = 380_000`, `CHP_P_MIN_KW = 60_000`, `CHP_T_START_C = 15.0`, `CHP_T_DESIGN_C = -20.0`,
  `ZTPO_KW = 7_500`, transformator GPZ 2 × 25 MVA 110/15 kV vk = 11%, transformator węzła
  Sn = P_max/0,95 15/0,4 kV vk = 6%.
- Typy pandapower: linia 110 kV napowietrzna `243-AL1/39-ST1A 110.0`, kabel 110 kV
  `N2XS(FL)2Y 1x240 RM/35 64/110 kV`, kabel 15 kV `NA2XS2Y 1x240 RM/25 12/20 kV`.
- Pierwsze ładowanie panelu z siecią ≤ ok. 2 min (9 węzłów × 4 strategie × 2 doby, Δt = 5 min).
- Każde zadanie kończy się `uv run pytest -q` na zielono i commitem z linią
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Luki w danych OSM** (stacja bez połączenia, linia urwana na granicy prostokąta): sieć musi się zbudować,
   każdy GPZ węzła połączony z PSE, połączenia zastępcze oznaczone – test w Task 3.
2. **Rozpływ się nie zbiega** (skrajny mnożnik PV, awaria wielu GPZ): krok ma zostać zapisany z flagą
   `pf_ok = False` i wartościami NaN, symulacja leci dalej – test w Task 5.
3. **Awaria części GPZ**: węzły pod wyłączonym GPZ pracują wyspowo, pozostałe normalnie, rozpływ bez
   wyłączonej stacji – test w Task 7.
4. **Sieć wyłączona (`network=False`)**: wyniki identyczne jak przed refaktoryzacją – test w Task 6.
5. **Brak sieci przy włączonej farmie / awarii całkowitej**: rozpływ pominięty w krokach pełnego blackoutu,
   bez wyjątków – test w Task 7.

---

## File Structure

| plik | odpowiedzialność |
|---|---|
| `src/microgrid/grid_osm.py` (nowy) | pobranie z Overpass, cache, parsowanie stacji i linii WN → `PowerData` |
| `src/microgrid/grid_topology.py` (nowy) | sklejanie odcinków OSM w gałęzie stacja–stacja, spójność, najbliższy GPZ |
| `src/microgrid/grid_profiles.py` (nowy) | tło GPZ/linii SN, elektrociepłownia, ZTPO (szeregi czasowe) |
| `src/microgrid/network.py` (nowy) | model pandapower: budowa, aktualizacja mocy, rozpływ, wyniki kroku |
| `src/microgrid/simulation.py` | `SiteSim` (propozycja/zatwierdzenie), `run` na `SiteSim`, mnożnik PV, awaria per GPZ |
| `src/microgrid/cosim.py` (nowy) | `run_network`, koordynator, `NetworkResults`, `run_all_network` |
| `src/microgrid/metrics_evaluator.py` | KPI sieciowe węzła i sieci |
| `src/microgrid/analysis.py` | `hosting_capacity` |
| `src/microgrid/config.py` | nowe pola `SimConfig`, `SiteConfig.gpz`, stałe sieci |
| `src/microgrid/map_view.py`, `animation.py` | warstwy prawdziwej sieci, usunięcie `GRID_SUPPLY_POINT` |
| `app.py`, `run.py` | przełączniki, analiza, opis założeń, `--network` |

---

### Task 1: Zależność pandapower

**Files:**
- Modify: `pyproject.toml`, `uv.lock`

- [ ] **Step 1:** `uv add "pandapower>=3.5"` – resolver obniży pandas do 2.3.x i numpy poniżej 2.5.
- [ ] **Step 2:** `uv run python -c "import pandapower, pandas, numpy; print(pandapower.__version__, pandas.__version__, numpy.__version__)"` – oczekiwane: `3.5.x 2.3.x 2.4.x`.
- [ ] **Step 3:** `uv run python -c "import pandapower as pp; n=pp.create_empty_network(); [pp.std_types.load_std_type(n, t, 'line') for t in ('243-AL1/39-ST1A 110.0','N2XS(FL)2Y 1x240 RM/35 64/110 kV','NA2XS2Y 1x240 RM/25 12/20 kV')]"` – bez błędu (jeśli którejś nazwy brak, wybrać najbliższy typ z `pp.std_types.available_std_types(n, 'line')` i poprawić stałe w Global Constraints oraz w spec).
- [ ] **Step 4:** `uv run pytest -q` – 51 passed. Każdą różnicę pandas 2/3 naprawić w kodzie (nie przypinać testów).
- [ ] **Step 5:** Commit `Zależność pandapower (pandas 2.3, numpy 2.4)`.

### Task 2: Dane sieci z OSM

**Files:**
- Create: `src/microgrid/grid_osm.py`, `data/cache/osm_power_krakow.json`
- Test: `tests/test_grid.py`

**Interfaces:**
- Produces:
  - `POWER_BBOX = (49.95, 19.70, 50.17, 20.32)`, `POWER_CACHE = "osm_power_krakow.json"`
  - `@dataclass(frozen=True) class Substation: osm_id: str; name: str; lat: float; lon: float; voltages_kv: tuple[int, ...]; kind: str; operator: str; ref: str`
    – `name` = tekst w „…” z tagu `name` (np. `"GPZ AGH"`, `"SE Skawina"`), inaczej cały `name`;
    `kind ∈ {"pse", "gpz", "hv_only"}`: `pse` gdy napięcia zawierają 220 lub 400 kV, `gpz` gdy zawierają 15 kV,
    inaczej `hv_only`.
  - `@dataclass(frozen=True) class HVLine: osm_id: str; voltage_kv: int; cable: bool; circuits: int; coords: tuple[tuple[float, float], ...]; name: str`
    – `voltage_kv` = największe z {110, 220, 400} obecne w tagu `voltage`; `circuits` z tagu (domyślnie 1).
  - `@dataclass(frozen=True) class PowerData: substations: tuple[Substation, ...]; lines: tuple[HVLine, ...]`
  - `parse_power(js: dict) -> PowerData` (format pliku cache: `{"substations": <elements>, "lines": <elements>}` – surowe elementy Overpass)
  - `fetch_power() -> dict` (dwa zapytania jak w rozpoznaniu: stacje `out center tags`, linie `out tags geom`; serwery i retry jak w `osm.fetch_chargers`)
  - `load_power_data(fetch_if_missing: bool = False) -> PowerData | None` (cache w `data_dir()`)

- [ ] **Step 1: Testy**
  - `test_parse_power_fixture`: słownik z 3 stacjami (400/220/110 kV, 110/15 kV z nazwą `Stacja elektroenergetyczna 110/15 kV „GPZ Test”`, 110 kV) i 1 linią (`voltage="110000"`, `circuits="2"`, geometria 3 punkty) → `kind == ("pse","gpz","hv_only")`, `name == "GPZ Test"`, `lines[0].circuits == 2`, `len(coords) == 3`.
  - `test_cache_has_krakow_grid`: `load_power_data()` z repo → ≥ 40 stacji; dokładnie 3 o `kind == "pse"` (`SE Skawina`, `SE Wanda`, `SE Lubocza`); jest `GPZ AGH`, `GPZ Łęg`, `GPZ ZTPO`; suma długości linii 110 kV > 400 km.
- [ ] **Step 2:** `uv run pytest tests/test_grid.py -q` → FAIL (brak modułu).
- [ ] **Step 3:** Zaimplementować moduł; zbudować cache z plików rozpoznania
  (`{"substations": subs.json["elements"], "lines": lines.json["elements"]}` z katalogu tymczasowego sesji)
  albo przez `fetch_power()`. Dopisać licencję ODbL w docstringu jak w `osm.py`.
- [ ] **Step 4:** testy PASS.
- [ ] **Step 5:** Commit `Dane sieci WN z OpenStreetMap (stacje i linie 110/220/400 kV)`.

### Task 3: Topologia sieci 110 kV

**Files:**
- Create: `src/microgrid/grid_topology.py`
- Modify: `src/microgrid/config.py` (`SiteConfig.gpz: str | None = None`; dla „Bieżanów-Prokocim” `gpz="GPZ Bieżanów"`)
- Test: `tests/test_grid.py`

**Interfaces:**
- Consumes: `PowerData`, `Substation`, `HVLine` (Task 2)
- Produces:
  - `@dataclass(frozen=True) class Branch: a: str; b: str; length_km: float; cable: bool; circuits: int; coords: tuple[tuple[float, float], ...]; synthetic: bool`
    (`a`, `b` – nazwy stacji; `synthetic=True` dla połączeń zastępczych)
  - `hv_branches(data: PowerData, snap_km: float = 0.4) -> list[Branch]` – tylko linie 110 kV
  - `site_gpz(site: SiteConfig, data: PowerData) -> Substation` – `site.gpz` po nazwie, inaczej najbliższa stacja `kind == "gpz"`
  - `km(a: tuple[float, float], b: tuple[float, float]) -> float` (rzut równoodległościowy przy 50,05° N)

Algorytm `hv_branches`: graf, w którym wierzchołkami są punkty końcowe odcinków (zaokrąglone do 1e-5°)
i stacje; końce w promieniu `snap_km` od stacji (środek z Overpass) utożsamiane ze stacją; odcinki o
wspólnym końcu sklejane (BFS po punktach niebędących stacjami) w ścieżkę stacja → stacja; długość = suma
długości geometrii; ścieżki kończące się poza stacją (granica prostokąta) odrzucane; duplikaty a–b z
różnymi `osm_id` zostają (osobne tory). Następnie spójność: dla każdej składowej bez stacji `pse` dodać
`Branch(synthetic=True)` od jej stacji najbliższej do najbliższej stacji ze składowej z `pse`
(długość = odległość × `ROUTE_FACTOR`, `cable=False`, `circuits=1`).

- [ ] **Step 1: Testy**
  - `test_branches_fixture`: dwie stacje oddalone o ok. 5 km połączone dwoma odcinkami ze wspólnym końcem → 1 `Branch`, długość ≈ suma odcinków (±1%).
  - `test_all_site_gpz_connected_to_pse`: dla `ALL_SITES` każdy `site_gpz` jest w składowej (po `hv_branches`) zawierającej stację `pse`.
  - `test_site_gpz_mapping`: `{s.name: site_gpz(s, data).name for s in ALL_SITES}` ==
    `{"AGH / Czarna Wieś": "GPZ AGH", "Nowa Huta – os. Centrum": "GPZ Czyżyny", "Kazimierz": "GPZ Dajwór", "Bonarka / Podgórze": "GPZ Bonarka", "Ruczaj / Kampus UJ": "GPZ Kampus", "Dworzec Główny": "GPZ Centrum Komunikacyjne", "Czyżyny": "GPZ Wieczysta", "Bieżanów-Prokocim": "GPZ Bieżanów", "Prądnik Biały": "GPZ Górka"}`
  - `test_synthetic_branches_marked`: liczba gałęzi `synthetic` ≤ 10 (luki OSM są wyjątkiem, nie regułą).
- [ ] **Step 2:** FAIL. **Step 3:** implementacja. **Step 4:** PASS.
- [ ] **Step 5:** Commit `Topologia sieci 110 kV z OSM, przypisanie węzłów do GPZ`.

### Task 4: Profile tła i źródeł lokalnych

**Files:**
- Create: `src/microgrid/grid_profiles.py`
- Modify: `src/microgrid/config.py` (stałe z Global Constraints)
- Test: `tests/test_grid.py`

**Interfaces:**
- Produces:
  - `background_shape(cfg: SimConfig) -> np.ndarray` – długość `cfg.n_steps`, max = 1; `real`: `fetch_kse` → `demand / demand.max()` na siatce symulacji; `synthetic`: kształt `synthetic.base_load` bez szumu (`0.35 + 0.30·bump(8 h, 1,5) + 0.65·bump(19 h, 2)`), znormalizowany.
  - `chp_profile(cfg: SimConfig) -> np.ndarray` [kW] – `P = P_min + (P_max − P_min)·clip((T_START − T_dzień)/(T_START − T_DESIGN), 0, 1)`; `T_dzień` = średnia dobowa temperatura z `fetch_weather` dla „GPZ Łęg” (tryb `real`), 20 °C (`synthetic`).
  - `ztpo_profile(cfg: SimConfig) -> np.ndarray` = stałe `ZTPO_KW`.

- [ ] **Step 1: Testy** – `background_shape` max == 1 i min > 0 (oba tryby; `real` na dacie z cache `2026-01-12`);
  `chp_profile` syntetyczny == `CHP_P_MIN_KW` wszędzie; `chp_profile` real styczeń (`2026-01-12`) średnio > lipcowy (`2026-06-15`) i ≤ `CHP_P_MAX_KW`.
- [ ] **Steps 2–4:** FAIL → implementacja → PASS.
- [ ] **Step 5:** Commit `Profile tła sieci, elektrociepłownia Łęg i ZTPO`.

### Task 5: Model pandapower

**Files:**
- Create: `src/microgrid/network.py`
- Test: `tests/test_network.py`

**Interfaces:**
- Consumes: `PowerData`, `hv_branches`, `site_gpz`, `km` (Task 2–3); profile (Task 4)
- Produces:
  - `@dataclass class StepResult: pf_ok: bool; v_node: dict[str, float]; feeder_loading: dict[str, float]; trafo_loading: dict[str, float]; hv_loading: np.ndarray; gpz_loading: dict[str, float]; losses_kw: float; pse_import_kw: float`
    (klucze dict = nazwy węzłów lub GPZ; `hv_loading` w kolejności `GridModel.branches`; przy `pf_ok=False` wartości NaN)
  - `class GridModel:`
    - `__init__(self, sites: list[SiteConfig], data: PowerData, farm: bool)` – buduje sieć wg spec (szyny 110 kV
      wszystkich stacji; `ext_grid` vm=1,0 na stacjach `pse`; dla `gpz`: szyna 15 kV + trafo 2×25 MVA
      + obciążenie tła; dla każdego węzła: linia 15 kV GPZ→środek (½ długości), środek→węzeł (½), obciążenie
      tła linii w środku, trafo węzła, szyna nN; `sgen` elektrociepłowni na „GPZ Łęg” 110 kV, `sgen` ZTPO
      na „GPZ ZTPO” 110 kV; `sgen` farmy na 15 kV najbliższego GPZ do `WIND_FARM_SITE`, gdy `farm`).
    - `branches: list[Branch]`, `site_gpz: dict[str, str]`, `gpz_names: list[str]`
    - `set_step(self, p_node_kw: dict[str, float], bg: float, chp_kw: float, ztpo_kw: float, farm_kw: float, gpz_out: frozenset[str]) -> None`
      (`p_node_kw` > 0 pobór; tło = `GPZ_BG_PEAK_KW·bg` i `FEEDER_BG_PEAK_KW·bg`, Q z `BG_COS_PHI`;
      `gpz_out` → transformatory tych GPZ `in_service=False`)
    - `solve(self) -> StepResult` – `pp.runpp(net, init="results")`, przy wyjątku `LoadflowNotConverged`
      → `pf_ok=False`; `pse_import_kw` = suma `res_ext_grid.p_mw`·1000.

- [ ] **Step 1: Testy**
  - `test_base_case_converges`: `GridModel(ALL_SITES, data, farm=False)`, `set_step` z `p_node_kw` = 50 kW każdy, `bg=0.8`, CHP min, ZTPO → `pf_ok`, wszystkie `v_node` w [0,95; 1,05].
  - `test_node_load_raises_loading`: ten sam model, węzeł AGH 0 kW vs 200 kW → `trafo_loading["AGH / Czarna Wieś"]` rośnie, `feeder_loading` rośnie.
  - `test_export_raises_voltage`: AGH −200 kW vs +200 kW → `v_node` przy eksporcie wyższe.
  - `test_chp_reduces_pse_import`: CHP 60 MW vs 380 MW → `pse_import_kw` mniejsze o > 250 MW.
  - `test_gpz_out_isolates_node`: `gpz_out={"GPZ AGH"}` → `pf_ok` i `v_node["AGH / Czarna Wieś"]` jest NaN, pozostałe węzły liczbowe.
  - `test_non_convergence_flagged`: `p_node_kw` AGH = −1e7 → `pf_ok is False`, bez wyjątku.
  - `test_solve_speed`: 50 kolejnych `set_step`+`solve` < 5 s.
- [ ] **Steps 2–4:** FAIL → implementacja → PASS.
- [ ] **Step 5:** Commit `Model sieci 110/15 kV w pandapower`.

### Task 6: `SiteSim` – refaktoryzacja pętli symulacji

**Files:**
- Modify: `src/microgrid/simulation.py`
- Test: `tests/test_simulation.py`

**Interfaces:**
- Produces:
  - `@dataclass class Proposal: k: int; p_ev: dict[int, float]; p_bess: float; load: float; pv: float; wind: float; ch_max: dict[int, float]; dis_max: dict[int, float]; b_ch: float; b_dis: float; shed: float; curt: float; farm_k: float; grid_ok: bool`
    (stan po decyzji EMS, przycięciu do fizyki i ewentualnej dyspozycji wyspowej – dokładnie to, co dziś jest liczone przed `ev.apply`)
  - `class SiteSim:` `__init__(self, env: simpy.Environment, site: SiteConfig, strategy: str, cfg: SimConfig, grid_ok: np.ndarray | None = None)`;
    `propose(self, k: int) -> Proposal`; `commit(self, prop: Proposal, extra: dict | None = None) -> float` (stosuje EV/BESS, domyka bilans jak dziś, dopisuje wiersz `ts` z kolumnami `extra`, zwraca `p_grid`);
    `start(self) -> None` (rejestruje proces przyjazdów w `env`); `results(self) -> Results`.
  - `run(site, strategy, cfg)` = jedna `SiteSim` + ticker `propose`→`commit`; wynik identyczny jak przed zmianą.
  - `SimConfig.pv_multiplier: float = 1.0` – mnoży `profiles["pv"]` w `make_inputs`.
  - `SimConfig.outage_gpz: tuple[str, ...] = ()` – puste = awaria wszystkich; `SiteSim` używa maski `cfg.grid_available()` tylko gdy `site_gpz(site, load_power_data()).name in cfg.outage_gpz` lub krotka pusta (Task 3); farma w wariancie `gpz` zasila wyspy tylko przy pustej krotce.

- [ ] **Step 1: Test regresji przed zmianą** – `test_run_unchanged_after_refactor`: dla `run(KRAKOW_SITES[1], st, SimConfig(days=2, dt_min=15, seed=3, outage_start_h=17.0, outage_dur_h=4.0, wind_farm="gpz"))`, `st ∈ ABCD`, zapisać w teście dokładne wartości `round(ts["p_grid"].sum(), 6)` i `round(ts["soc_bess"].sum(), 6)` policzone na obecnym kodzie (przed refaktoryzacją); test PASS na starym kodzie.
- [ ] **Step 2:** Test `test_pv_multiplier`: `pv_multiplier=2.0` → suma `pv` ×2 (±1e-9 względnie), reszta wejść bez zmian. Test `test_outage_only_selected_gpz`: `outage_gpz=("GPZ AGH",)` → AGH ma kroki `grid_ok=False`, Kazimierz nie. FAIL.
- [ ] **Step 3:** Refaktoryzacja + nowe pola.
- [ ] **Step 4:** `uv run pytest -q` – wszystko PASS, w tym test regresji.
- [ ] **Step 5:** Commit `SiteSim: pętla symulacji jako propozycja i zatwierdzenie kroku; mnożnik PV; awaria wybranych GPZ`.

### Task 7: Wspólna symulacja sieci i koordynator

**Files:**
- Create: `src/microgrid/cosim.py`
- Modify: `src/microgrid/config.py` (`SimConfig.network: bool = False`, `network_control: bool = True`)
- Test: `tests/test_cosim.py`

**Interfaces:**
- Consumes: `SiteSim`, `Proposal` (Task 6); `GridModel`, `StepResult` (Task 5); profile (Task 4); `assign_shares` (farma)
- Produces:
  - `@dataclass class NetworkResults: strategy: str; times: pd.Series; branches: list[Branch]; hv_loading: np.ndarray (n_steps × n_branches); gpz_loading: pd.DataFrame; losses_kw: pd.Series; pse_import_kw: pd.Series; chp_kw: pd.Series; pf_ok: pd.Series; internal_exchange_kw: pd.Series; site_gpz: dict[str, str]`
  - `run_network(sites: list[SiteConfig], strategy: str, cfg: SimConfig) -> tuple[dict[str, Results], NetworkResults]`
  - `run_all_network(sites, cfg, strategies=("A","B","C","D"), n_jobs=None) -> tuple[dict[tuple[str, str], Results], dict[str, NetworkResults]]` (równolegle po strategiach przez `parallel_map`)
  - `coordinate(props: dict[str, Proposal], res: StepResult, model: GridModel) -> dict[str, float]` – zwraca dla węzła wymaganą zmianę mocy PCC [kW] (ujemna = mniej poboru/eksportu w kierunku przepływu)
  - kolumny `Results.ts` przy `network=True`: `v_node`, `feeder_loading`, `trafo_loading`, `p_curt_net`, `net_violation` (bool)

Krok k: `propose` dla wszystkich → `p_node` (bilans PCC z propozycji) → `set_step`/`solve` (pomijane, gdy
wszystkie węzły mają `grid_ok=False`) → jeśli `network_control`: `coordinate` → korekta propozycji
(eksport: BESS ładowanie do `b_ch`, EV do `ch_max`, potem ograniczenie PV/wiatru proporcjonalnie →
`p_curt_net`; pobór: BESS rozładowanie do `b_dis`, potem dławienie EV) → ponowny `solve` (maks. 3 iteracje)
→ `commit` z `extra`. Wymagana zmiana: obciążenie L > 100% → `|P_PCC|·(1 − 100/L)·1,02`; napięcie
V > 1,10 przy eksporcie → `|P_PCC|·(V − 1,10)/(V − 1,0)·1,02`; V < 0,90 przy poborze analogicznie.
Wymiana wewnętrzna w kroku = `min(Σ eksportów, Σ poborów)` węzłów.

- [ ] **Step 1: Testy**
  - `test_network_matches_independent_when_no_violations`: `sites = ALL_SITES`, `SimConfig(days=1, dt_min=15, network=True)`, strategia „C” → dla każdego węzła `p_grid` z `run_network` == `run(site, "C", cfg_bez_sieci)` (np.allclose, atol 1e-6), o ile `net_violation` nigdy nie wystąpiło (asercja: nie wystąpiło).
  - `test_stress_pv_control_removes_violations`: AGH, `pv_multiplier=8`, czerwiec syntetyczny, „A”: bez kontroli (`network_control=False`) liczba `net_violation` > 0; z kontrolą = 0 lub mniejsza o ≥ 90%, a `p_curt_net` > 0.
  - `test_energy_balance_with_network`: rezydua `energy_balance_residuals` < 1e-6 dla wszystkich węzłów w teście stresowym (bilans uwzględnia `p_curt_net`).
  - `test_full_blackout_skips_pf`: awaria wszystkich, 4 h → w tych krokach `pf_ok` False/NaN, brak wyjątku, wyniki wysp jak bez sieci.
  - `test_partial_outage`: `outage_gpz=("GPZ AGH",)` → AGH wyspa (`p_grid`=0 w awarii), inne węzły normalnie z `pf_ok` True.
  - `test_internal_exchange_nonnegative`: `internal_exchange_kw ≥ 0` i ≤ min(Σ eksport, Σ pobór).
- [ ] **Steps 2–4:** FAIL → implementacja → PASS.
- [ ] **Step 5:** pomiar czasu: `run_all_network(ALL_SITES, SimConfig(days=2, dt_min=5, network=True))` – zapisać w commicie; jeśli > 2 min, ograniczyć iteracje koordynatora do kroków z naruszeniem i użyć `init="results"`.
- [ ] **Step 6:** Commit `Wspólna symulacja sieci z rozpływem mocy i koordynatorem sieciowym`.

### Task 8: KPI sieciowe i zdolność przyłączeniowa PV

**Files:**
- Modify: `src/microgrid/metrics_evaluator.py`, `src/microgrid/analysis.py`
- Test: `tests/test_cosim.py`

**Interfaces:**
- Produces:
  - w `kpis(r)` (gdy kolumny sieci są w `ts`, inaczej NaN): `"U min [p.u.]"`, `"U max [p.u.]"`, `"Obc. linii SN max [%]"`, `"Obc. transf. max [%]"`, `"Ograniczenie PV przez sieć [kWh]"`, `"Naruszenia sieci [kroki]"`
  - `network_summary(nr: NetworkResults, dt_h: float) -> dict[str, float]`: `"Obc. linii 110 kV max [%]"`, `"Straty sieci [MWh]"`, `"Wymiana wewnętrzna [kWh]"`, `"Pobór z PSE max [MW]"`, `"Produkcja EC Łęg [MWh]"`
  - `hosting_capacity(site: SiteConfig, cfg: SimConfig, strategy: str = "A", pv_max_kwp: float = 5000.0, tol_kwp: float = 25.0) -> dict` → `{"kWp": float, "ograniczenie": str}` (bisekcja po `site.pv_kwp`, `run_network([site], …)` z `network_control=False`, `days=1`, `dt_min=15`; „ograniczenie” = pierwsze naruszone: `"transformator"`, `"linia SN"` albo `"napięcie"`).
- [ ] **Step 1: Testy** – KPI sieciowe obecne i skończone dla `run_network`; NaN dla `run`; `hosting_capacity` dla AGH (syntetyczne, czerwiec) zwraca kWp > obecne 150 i ograniczenie z listy; monotoniczność: przy `strategy="A"` wynik ≤ wyniku dla `"B"` (BESS zjada część nadwyżki).
- [ ] **Steps 2–4:** FAIL → implementacja → PASS.
- [ ] **Step 5:** Commit `KPI sieciowe i analiza zdolności przyłączeniowej PV`.

### Task 9: Mapa prawdziwej sieci

**Files:**
- Modify: `src/microgrid/map_view.py`, `src/microgrid/animation.py`, `src/microgrid/config.py` (usunąć `GRID_SUPPLY_POINT` i jego użycia)
- Test: `tests/test_cosim.py`, istniejące testy map

**Interfaces:**
- Consumes: `load_power_data`, `hv_branches`, `site_gpz` (Task 2–3); `NetworkResults` (Task 7)
- Produces:
  - `grid_layers(data: PowerData, nr: NetworkResults | None, k: int) -> list[pdk.Layer]` – `PathLayer` linii 110/220/400 kV (kolory: 110 `[124,58,237]`, 220 `[240,140,0]`, 400 `[201,42,42]`; linie 110 kV z `nr` kolorowane wg obciążenia jak `_load_color`), `ScatterplotLayer` stacji (`pse` większe) z tooltipem nazwa/napięcia/operator, linie 15 kV węzeł→GPZ kolorowane wg `feeder_loading` w kroku k (bez `nr` – szare); gałęzie `synthetic` półprzezroczyste z dopiskiem „połączenie zastępcze (luka w OSM)”; obwódka węzła wg `v_node` (zielona 0,95–1,05, pomarańczowa do 0,90/1,10, czerwona poza)
  - `build_deck(results, k, show_chargers=True, nr: NetworkResults | None = None)`; przepływ PCC ↔ GPZ węzła zamiast umownego GPZ; awaria oznacza wyłączone GPZ
  - `animated_map_html(results, start_k=0, autoplay=True, height=620, nr: NetworkResults | None = None)` – payload `grid` (ścieżki, kolory, obciążenia 110 kV na klatkę zaokrąglone do 1%); przepływ PCC ↔ własny GPZ
- [ ] **Step 1: Testy** – JSON `build_deck` zawiera `"SE Skawina"` i `"GPZ AGH"`; HTML animacji zawiera `"grid":` i nazwę `"GPZ Bonarka"`; brak `GRID_SUPPLY_POINT` w kodzie (`grep`).
- [ ] **Steps 2–4:** FAIL → implementacja → PASS.
- [ ] **Step 5:** Commit `Mapa prawdziwej sieci WN Krakowa (statyczna i animowana)`.

### Task 10: Panel, CLI, dokumentacja

**Files:**
- Modify: `app.py`, `run.py`, `CHANGELOG.md` (0.7.0), `README.md`

- [ ] **Step 1:** Panel: sekcja „Sieć” – przełącznik „Sieć 110/15 kV (rozpływ mocy)” (domyślnie wł.), „Koordynator sieciowy” (wł.), suwak „Mnożnik PV” (1–10, krok 0,5); w sekcji awarii multiselect „GPZ objęte awarią” (nazwy GPZ wybranych węzłów; puste = wszystkie). `simulate` wywołuje `run_all_network`, gdy `cfg.network`. Zakładka Przebiegi: metryki sieciowe węzła; zakładka Porównanie: tabela `network_summary` na strategię; zakładka 🔬: „Zdolność przyłączeniowa PV” (przycisk, tabela węzeł → kWp, ograniczenie). Pod mapą `st.caption` z założeniami (tło 20 MW/GPZ, 2 MW/linia, typy linii, EC Łęg maks. 380 MW, dane © OSM).
- [ ] **Step 2:** `run.py --network` (oraz `--pv-mult`); zapis `siec_<strategia>.csv` z `network_summary`.
- [ ] **Step 3:** AppTest (jak w poprzednich zmianach): start, włączenie sieci, mnożnik PV 5, awaria `GPZ AGH`, przycisk analizy → brak wyjątków; zmierzony czas pierwszego ładowania zapisany w CHANGELOG.
- [ ] **Step 4:** CHANGELOG 0.7.0 (co, założenia, wyniki: obciążenie 110 kV, straty, zdolność przyłączeniowa per węzeł, porównanie zima/lato), README (nowa zależność, `--network`).
- [ ] **Step 5:** `uv run pytest -q` → wszystko PASS. Commit `Panel i CLI: sieć 110/15 kV, mnożnik PV, zdolność przyłączeniowa; CHANGELOG 0.7.0`.
