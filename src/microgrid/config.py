"""Konfiguracja mikrosieci i symulacji.

Parametry węzłów są przykładowe (lokalizacje w Krakowie). Profile wejściowe mogą
pochodzić z generatorów syntetycznych (``data_source="synthetic"``) albo z danych
rzeczywistych Open-Meteo + PSE (``data_source="real"``, patrz ``real_data.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

RHO_AIR = 1.225  # gęstość powietrza [kg/m^3]
ROUTE_FACTOR = 1.3  # współczynnik wydłużenia trasy względem linii prostej (połączenia zastępcze)


@dataclass(frozen=True)
class EVParams:
    """Parametry rozkładów generatora sesji EV (syntetyczne, do kalibracji ACN-Data)."""

    battery_kwh: tuple[float, ...] = (40.0, 60.0, 75.0)
    p_ch_max_kw: tuple[float, ...] = (11.0, 22.0, 50.0)
    p_ch_weights: tuple[float, ...] = (0.4, 0.45, 0.15)
    soc_init: tuple[float, float] = (0.20, 0.60)        # U(a, b)
    soc_target: tuple[float, float] = (0.70, 0.90)      # U(a, b)
    park_median_h: float = 3.5                          # mediana postoju (lognormal)
    park_median_evening_h: float = 8.0                  # dla przyjazdów po 17:00
    park_sigma: float = 0.5
    park_min_h: float = 0.5
    park_max_h: float = 12.0
    v2g_soc_floor: float = 0.30                         # V2G nie rozładuje poniżej tego SoC
    # flota "real": parametry ładowarek (moc punktu AC / DC) i udział sesji DC
    ac_charger_kw: float = 22.0
    dc_charger_kw: float = 50.0
    dc_share: float = 0.15
    park_median_dc_h: float = 0.75                      # sesje DC są krótkie


@dataclass(frozen=True)
class EVModel:
    """Rzeczywisty model pojazdu (pojemność netto, maks. moc ładowania AC / DC)."""

    name: str
    battery_kwh: float
    ac_kw: float
    dc_kw: float
    weight: float  # orientacyjny udział w parku EV w Polsce


# Popularne modele BEV w Polsce (dane katalogowe producentów, wagi orientacyjne
# na podstawie struktury rejestracji PZPM/PSPA 2023–2025 – do kalibracji).
EV_MODELS: tuple[EVModel, ...] = (
    EVModel("Tesla Model Y LR", 75.0, 11.0, 250.0, 0.14),
    EVModel("Tesla Model 3 RWD", 57.5, 11.0, 170.0, 0.08),
    EVModel("Skoda Enyaq 85", 77.0, 11.0, 135.0, 0.08),
    EVModel("VW ID.3 Pro", 58.0, 11.0, 120.0, 0.06),
    EVModel("VW ID.4 Pro", 77.0, 11.0, 135.0, 0.06),
    EVModel("BMW iX1", 64.7, 11.0, 130.0, 0.06),
    EVModel("Volvo EX30", 64.0, 11.0, 153.0, 0.06),
    EVModel("Kia EV6 LR", 77.4, 11.0, 233.0, 0.05),
    EVModel("Hyundai Kona 65", 64.8, 11.0, 102.0, 0.05),
    EVModel("MG4 Standard", 51.0, 6.6, 117.0, 0.05),
    EVModel("BYD Atto 3", 60.5, 11.0, 88.0, 0.05),
    EVModel("Renault Zoe R135", 52.0, 22.0, 50.0, 0.04),
    EVModel("Fiat 500e", 37.3, 11.0, 85.0, 0.04),
    EVModel("Nissan Leaf 40", 39.0, 6.6, 50.0, 0.04),
    EVModel("Dacia Spring", 26.8, 7.0, 30.0, 0.04),
    EVModel("Renault Megane E-Tech", 60.0, 22.0, 130.0, 0.04),
    EVModel("Porsche Taycan", 83.7, 11.0, 270.0, 0.02),
)


@dataclass(frozen=True)
class SiteConfig:
    """Pojedyncza mikrosieć z węzłem PCC."""

    name: str
    lat: float
    lon: float
    # stacja EV
    n_chargers: int = 10
    queue_cap: int = 5
    ev_per_day: float = 60.0
    v2g_share: float = 0.3
    # transformator / PCC
    p_max_kw: float = 250.0              # zdolność przyłącza / transformatora (limit techniczny)
    p_contract_kw: float | None = None   # moc umowna (taryfa); None → równa p_max_kw
    # odbiory bazowe
    base_load_peak_kw: float = 160.0
    load_type: str = "kse"  # tryb "real": "kse" | "residential" | "office" | "commercial"
    # udział we wspólnej farmie wiatrowej poza miastem (0 → proporcjonalnie do mocy umownej wśród ALL_SITES)
    farm_share: float = 0.0
    # GPZ 110/15 kV zasilający węzeł (nazwa stacji z OSM); None → najbliższy GPZ
    gpz: str | None = None
    # OZE
    pv_kwp: float = 120.0
    inverter_eff: float = 0.96
    wind_rated_kw: float = 30.0
    wind_v_cut_in: float = 3.0
    wind_v_rated: float = 11.0
    wind_v_cut_out: float = 25.0
    wind_cp: float = 0.40
    wind_mean_speed: float = 5.5   # tylko tryb syntetyczny
    wind_hub_m: float = 30.0       # wysokość piasty (tryb danych rzeczywistych)
    # BESS
    bess_kwh: float = 200.0
    bess_p_kw: float = 100.0
    bess_roundtrip_eff: float = 0.90
    bess_soc_min: float = 0.10
    bess_soc_max: float = 0.95
    bess_soc_init: float = 0.50

    @property
    def p_contract(self) -> float:
        return self.p_max_kw if self.p_contract_kw is None else self.p_contract_kw

    @property
    def p_limit_kw(self) -> float:
        """Limit, którego pilnuje EMS: mniejsza z mocy umownej i zdolności przyłącza."""
        return min(self.p_max_kw, self.p_contract)

    @property
    def wind_rotor_area_m2(self) -> float:
        """Powierzchnia wirnika dobrana tak, by krzywa mocy była ciągła w v_rated."""
        return self.wind_rated_kw * 1000.0 / (0.5 * self.wind_cp * RHO_AIR * self.wind_v_rated**3)

    def with_(self, **kw) -> "SiteConfig":
        return replace(self, **kw)


@dataclass(frozen=True)
class SimConfig:
    days: int = 2
    dt_min: int = 5
    seed: int = 42
    start: str = "2026-06-15 00:00"  # data startowa (czas lokalny; w trybie "real" wybiera dane)
    data_source: str = "synthetic"   # "synthetic" | "real" (Open-Meteo + PSE)
    ev_fleet: str = "synthetic"      # "synthetic" | "real" (modele EV_MODELS + ładowarki AC/DC)
    forecast: str = "perfect"        # prognoza dla MPC: "perfect" | "persistence" (wczorajszy profil)
    mpc_ev_forecast: bool = True     # MPC uwzględnia oczekiwane przyjazdy EV (rezerwa mocy / energii)
    ev: EVParams = field(default_factory=EVParams)
    # awaria GPZ (blackout): od outage_start_h [h od startu] przez outage_dur_h [h] brak zasilania z sieci,
    # węzły pracują wyspowo (P_grid = 0). outage_announced → MPC zna okno awarii z wyprzedzeniem.
    outage_start_h: float | None = None
    outage_dur_h: float = 4.0
    outage_announced: bool = False
    # wspólna farma wiatrowa poza miastem: "none" | "ppa" (przez sieć OSD, rozliczenie umową PPA)
    # | "gpz" (przyłączona do GPZ – rozliczenie jak PPA, a w czasie awarii GPZ pracuje wyspowo z farmą)
    wind_farm: str = "none"
    wind_farm_mw: float = 3.0
    wind_farm_hub_m: float = 120.0

    @property
    def horizon_min(self) -> int:
        return self.days * 24 * 60

    @property
    def n_steps(self) -> int:
        return self.horizon_min // self.dt_min

    @property
    def dt_h(self) -> float:
        return self.dt_min / 60.0

    def grid_available(self) -> np.ndarray:
        """Maska kroków z dostępnym zasilaniem z GPZ (False = awaria / blackout)."""
        ok = np.ones(self.n_steps, dtype=bool)
        if self.outage_start_h is not None and self.outage_dur_h > 0:
            t = np.arange(self.n_steps) * self.dt_min / 60.0
            ok[(t >= self.outage_start_h) & (t < self.outage_start_h + self.outage_dur_h)] = False
        return ok


# Przykładowe węzły w Krakowie (parametry syntetyczne).
KRAKOW_SITES: list[SiteConfig] = [
    SiteConfig(
        name="AGH / Czarna Wieś",
        lat=50.0665, lon=19.9185,
        n_chargers=12, ev_per_day=50, p_max_kw=250, base_load_peak_kw=170,
        pv_kwp=150, wind_rated_kw=20, bess_kwh=250, bess_p_kw=120, v2g_share=0.3, load_type="office",
    ),
    SiteConfig(
        name="Nowa Huta – os. Centrum",
        lat=50.0720, lon=20.0375,
        n_chargers=10, ev_per_day=38, p_max_kw=200, base_load_peak_kw=150,
        pv_kwp=80, wind_rated_kw=40, bess_kwh=180, bess_p_kw=90, v2g_share=0.4, load_type="residential",
    ),
    SiteConfig(
        name="Kazimierz",
        lat=50.0510, lon=19.9450,
        n_chargers=14, ev_per_day=60, p_max_kw=300, base_load_peak_kw=200,
        pv_kwp=100, wind_rated_kw=0, bess_kwh=300, bess_p_kw=150, v2g_share=0.2, load_type="commercial",
    ),
]

# Dodatkowe węzły w miejscach, gdzie OpenStreetMap pokazuje skupiska ładowarek
# (data/cache/osm_chargers_krakow.csv, stan 11.2026; liczba stanowisk ≈ suma ``capacity``
# stacji OSM w promieniu 500 m, dla Bonarki ograniczona do modelowanego hubu).
# Moc przyłącza wg typoszeregu transformatorów SN/nN (160/250/400/630 kVA, cos φ = 0.95).
KRAKOW_SITES_EXTRA: list[SiteConfig] = [
    SiteConfig(
        name="Bonarka / Podgórze", lat=50.0255, lon=19.9555,  # OSM: 32 stacje / 125 pkt, Tesla SC 250 kW
        n_chargers=30, queue_cap=8, ev_per_day=140, p_max_kw=600, base_load_peak_kw=420, load_type="commercial",
        pv_kwp=300, wind_rated_kw=0, bess_kwh=500, bess_p_kw=250, v2g_share=0.2,
    ),
    SiteConfig(
        name="Ruczaj / Kampus UJ", lat=50.0125, lon=19.8870,  # OSM: 11 stacji / 22 pkt (Elocity, Leroy Merlin)
        n_chargers=22, ev_per_day=80, p_max_kw=380, base_load_peak_kw=240, load_type="office",
        pv_kwp=200, wind_rated_kw=0, bess_kwh=300, bess_p_kw=150, v2g_share=0.3,
    ),
    SiteConfig(
        name="Dworzec Główny", lat=50.0690, lon=19.9465,  # OSM: 5 stacji / 13 pkt
        n_chargers=13, ev_per_day=70, p_max_kw=238, base_load_peak_kw=150, load_type="commercial",
        pv_kwp=60, wind_rated_kw=0, bess_kwh=150, bess_p_kw=75, v2g_share=0.1,
    ),
    SiteConfig(
        name="Czyżyny", lat=50.0645, lon=20.0000,  # OSM: GreenWay 50 kW + GO+EAuto 22 kW
        n_chargers=8, ev_per_day=35, p_max_kw=152, base_load_peak_kw=100, load_type="residential",
        pv_kwp=60, wind_rated_kw=10, bess_kwh=100, bess_p_kw=50, v2g_share=0.4,
    ),
    SiteConfig(
        name="Bieżanów-Prokocim", lat=49.9885, lon=20.0450,  # OSM: 5 stacji / 14 pkt
        n_chargers=14, ev_per_day=45, p_max_kw=238, base_load_peak_kw=160, load_type="residential",
        pv_kwp=90, wind_rated_kw=0, bess_kwh=150, bess_p_kw=75, v2g_share=0.4, gpz="GPZ Bieżanów",
    ),
    SiteConfig(
        name="Prądnik Biały", lat=50.0945, lon=19.9510,  # OSM: GO+EAuto 8 pkt + Lidl
        n_chargers=10, ev_per_day=40, p_max_kw=152, base_load_peak_kw=110, load_type="residential",
        pv_kwp=50, wind_rated_kw=0, bess_kwh=120, bess_p_kw=60, v2g_share=0.4,
    ),
]
ALL_SITES: list[SiteConfig] = KRAKOW_SITES + KRAKOW_SITES_EXTRA

# Umowny punkt zasilania z sieci nadrzędnej (GPZ) – do wizualizacji na mapie.
GRID_SUPPLY_POINT = {"name": "GPZ – sieć nadrzędna (umowny)", "lat": 50.0600, "lon": 19.9800}
# wspólna farma wiatrowa poza miastem (lokalizacja umowna, otwarty teren na płn.-zach. od Krakowa)
WIND_FARM_SITE = {"name": "Farma wiatrowa (poza miastem)", "lat": 50.1150, "lon": 19.8750}
