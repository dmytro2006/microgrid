"""Konfiguracja mikrosieci i symulacji.

Wszystkie parametry są na razie syntetyczne (brak danych rzeczywistych).
Lokalizacje odpowiadają przykładowym punktom w Krakowie – docelowo zostaną
podmienione na rzeczywiste węzły / stacje ładowania.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

RHO_AIR = 1.225  # gęstość powietrza [kg/m^3]


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
    p_max_kw: float = 250.0
    # odbiory bazowe
    base_load_peak_kw: float = 160.0
    # OZE
    pv_kwp: float = 120.0
    inverter_eff: float = 0.96
    wind_rated_kw: float = 30.0
    wind_v_cut_in: float = 3.0
    wind_v_rated: float = 11.0
    wind_v_cut_out: float = 25.0
    wind_cp: float = 0.40
    wind_mean_speed: float = 5.5
    # BESS
    bess_kwh: float = 200.0
    bess_p_kw: float = 100.0
    bess_roundtrip_eff: float = 0.90
    bess_soc_min: float = 0.10
    bess_soc_max: float = 0.95
    bess_soc_init: float = 0.50

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
    start: str = "2026-06-15 00:00"  # data startowa (tylko do opisu osi czasu)
    ev: EVParams = field(default_factory=EVParams)

    @property
    def horizon_min(self) -> int:
        return self.days * 24 * 60

    @property
    def n_steps(self) -> int:
        return self.horizon_min // self.dt_min

    @property
    def dt_h(self) -> float:
        return self.dt_min / 60.0


# Przykładowe węzły w Krakowie (parametry syntetyczne).
KRAKOW_SITES: list[SiteConfig] = [
    SiteConfig(
        name="AGH / Czarna Wieś",
        lat=50.0665, lon=19.9185,
        n_chargers=12, ev_per_day=50, p_max_kw=250, base_load_peak_kw=170,
        pv_kwp=150, wind_rated_kw=20, bess_kwh=250, bess_p_kw=120, v2g_share=0.3,
    ),
    SiteConfig(
        name="Nowa Huta – os. Centrum",
        lat=50.0720, lon=20.0375,
        n_chargers=10, ev_per_day=38, p_max_kw=200, base_load_peak_kw=150,
        pv_kwp=80, wind_rated_kw=40, bess_kwh=180, bess_p_kw=90, v2g_share=0.4,
    ),
    SiteConfig(
        name="Kazimierz",
        lat=50.0510, lon=19.9450,
        n_chargers=14, ev_per_day=60, p_max_kw=300, base_load_peak_kw=200,
        pv_kwp=100, wind_rated_kw=0, bess_kwh=300, bess_p_kw=150, v2g_share=0.2,
    ),
]

# Umowny punkt zasilania z sieci nadrzędnej (GPZ) – do wizualizacji na mapie.
GRID_SUPPLY_POINT = {"name": "GPZ – sieć nadrzędna (umowny)", "lat": 50.0600, "lon": 19.9800}
