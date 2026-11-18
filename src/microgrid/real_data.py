"""Dane rzeczywiste dla Krakowa: pogoda (Open-Meteo), ceny RCE i zapotrzebowanie KSE (PSE).

Źródła (publiczne, bez klucza API):
  - Open-Meteo Historical Weather API (reanaliza ERA5 / ECMWF IFS):
      https://archive-api.open-meteo.com/v1/archive
      → nasłonecznienie na płaszczyznę nachyloną (GTI), temperatura 2 m, prędkość wiatru 10 m i 100 m.
  - PSE API raportów (https://api.raporty.pse.pl):
      * ``rce-pln``     – Rynkowa Cena Energii elektrycznej RCE [PLN/MWh], rozdzielczość 15 min,
                          (od 06.2024 cena rozliczeniowa prosumentów w net-billingu),
      * ``his-wlk-cal`` – zapotrzebowanie KSE oraz generacja PV i wiatrowa w Polsce [MW], 15 min.

Odpowiedzi są zapisywane miesiącami w ``data/cache`` (CSV), więc po pierwszym pobraniu symulacja
działa offline. Szeregi przechowywane są w UTC i interpolowane na siatkę kroku Δt symulacji.

Modele przeliczeniowe:
  - PV:    P = P_STC · GTI/1000 · [1 + γ·(T_cell − 25)] · η_sys · η_inv,
           T_cell = T_amb + (NOCT − 20)/800 · GTI   (model NOCT),
  - wiatr: profil potęgowy dopasowany w każdej godzinie do pomiarów 10 m i 100 m
           (α = ln(v100/v10)/ln 10), v_hub = v10·(h/10)^α, następnie krzywa mocy z dokumentacji,
  - odbiory: ``load_type="kse"`` – kształt zapotrzebowania KSE przeskalowany do szczytu węzła,
             pozostałe typy – dobowe profile typowe (osiedle / biurowiec-uczelnia / handel i usługi),
             osobno dla dni roboczych i wolnych, korygowane temperaturą (ogrzewanie / klimatyzacja).
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from .config import SimConfig, SiteConfig

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"
PSE_URL = "https://api.raporty.pse.pl/api"
TZ = "Europe/Warsaw"
USER_AGENT = "microgrid-sim/0.2 (student project)"
ARCHIVE_DELAY_DAYS = 6    # opóźnienie publikacji reanalizy Open-Meteo / raportów PSE

# parametry modelu PV (typowe moduły mono-Si)
PV_TILT_DEG = 35.0
PV_AZIMUTH_DEG = 0.0      # 0 = południe (konwencja Open-Meteo)
PV_GAMMA = -0.0035        # współczynnik temperaturowy mocy [1/K]
PV_NOCT = 45.0            # [°C]
PV_SYSTEM_EFF = 0.90      # straty DC: zabrudzenie, okablowanie, niedopasowanie


def data_dir() -> Path:
    d = Path(os.environ.get("MICROGRID_DATA_DIR", Path(__file__).resolve().parents[2] / "data" / "cache"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_json(url: str, retries: int = 3, timeout: float = 30.0) -> dict:
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001 – sieć bywa zawodna, ponawiamy
            last = e
            time.sleep(1.5 * (i + 1))
    raise ConnectionError(f"Nie udało się pobrać {url}: {last}")


def _days(cfg: SimConfig) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Zakres dat (lokalnych) potrzebnych do pokrycia horyzontu z zapasem 1 doby po obu stronach."""
    start = pd.Timestamp(cfg.start).normalize()
    return start - pd.Timedelta(days=1), start + pd.Timedelta(days=cfg.days + 1)


def _months(d0: pd.Timestamp, d1: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Podział zakresu dat na pełne miesiące kalendarzowe (jednostka cache)."""
    out = []
    m = d0.to_period("M")
    while m <= d1.to_period("M"):
        out.append((m.start_time.normalize(), m.end_time.normalize()))
        m += 1
    return out


def _cached(prefix: str, d0: pd.Timestamp, d1: pd.Timestamp, fetch) -> pd.DataFrame:
    """Pobiera dane miesiącami i zapisuje w ``data/cache/{prefix}_{RRRR-MM}.csv``.

    Miesiąc, który jeszcze się nie skończył (lub jest świeższy niż opóźnienie archiwum),
    jest pobierany do bieżącej daty i nie trafia do cache.
    """
    available_to = pd.Timestamp.today().normalize() - pd.Timedelta(days=ARCHIVE_DELAY_DAYS)
    parts = []
    for m0, m1 in _months(d0, d1):
        path = data_dir() / f"{prefix}_{m0:%Y-%m}.csv"
        if path.exists():
            df = pd.read_csv(path, index_col=0)
            df.index = pd.to_datetime(df.index, utc=True)
        else:
            if m0 > available_to:
                raise ConnectionError(f"brak danych archiwalnych dla {m0:%Y-%m} (dostępne do {available_to:%Y-%m-%d})")
            df = fetch(m0, min(m1, available_to))
            if m1 <= available_to:
                df.to_csv(path)
        parts.append(df)
    out = pd.concat(parts)
    return out[~out.index.duplicated()].sort_index()


# ---------------------------------------------------------------- Open-Meteo
def fetch_weather(lat: float, lon: float, d0: pd.Timestamp, d1: pd.Timestamp) -> pd.DataFrame:
    """Godzinowe dane pogodowe [UTC]: gti [W/m²], temp [°C], v10, v100 [m/s]."""
    def fetch(d0: pd.Timestamp, d1: pd.Timestamp) -> pd.DataFrame:
        q = urllib.parse.urlencode({
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "start_date": f"{d0:%Y-%m-%d}", "end_date": f"{d1:%Y-%m-%d}",
            "hourly": "temperature_2m,wind_speed_10m,wind_speed_100m,global_tilted_irradiance",
            "tilt": PV_TILT_DEG, "azimuth": PV_AZIMUTH_DEG,
            "wind_speed_unit": "ms", "timezone": "GMT",
        })
        h = _get_json(f"{OPEN_METEO_URL}?{q}")["hourly"]
        idx = pd.to_datetime(h["time"], utc=True)
        df = pd.DataFrame({
            "gti": h["global_tilted_irradiance"], "temp": h["temperature_2m"],
            "v10": h["wind_speed_10m"], "v100": h["wind_speed_100m"],
        }, index=idx, dtype=float)
        # promieniowanie w Open-Meteo to średnia z godziny POPRZEDZAJĄCEJ znacznik czasu
        # → przesuwamy na środek przedziału; wielkości chwilowe (T, v) zostają bez zmian
        gti = df["gti"].copy()
        gti.index = gti.index - pd.Timedelta(minutes=30)
        df["gti"] = gti.reindex(df.index.union(gti.index)).interpolate("time").reindex(df.index)
        df.index.name = "time_utc"
        return df.dropna(how="all")

    return _cached(f"weather_{lat:.3f}_{lon:.3f}", d0, d1, fetch)


# ---------------------------------------------------------------- PSE
def _pse(entity: str, d0: pd.Timestamp, d1: pd.Timestamp, fields: list[str]) -> pd.DataFrame:
    flt = f"business_date ge '{d0:%Y-%m-%d}' and business_date le '{d1:%Y-%m-%d}'"
    url = f"{PSE_URL}/{entity}?" + urllib.parse.urlencode(
        {"$filter": flt, "$select": ",".join(["dtime_utc", *fields]), "$first": 20000})
    rows: list[dict] = []
    while url:
        js = _get_json(url)
        rows += js.get("value", [])
        url = js.get("nextLink")
    if not rows:
        raise ConnectionError(f"PSE {entity}: brak danych dla {d0:%Y-%m-%d}..{d1:%Y-%m-%d}")
    df = pd.DataFrame(rows)
    # dtime oznacza KONIEC 15-min okresu → środek przedziału = dtime − 7.5 min
    idx = pd.to_datetime(df["dtime_utc"]).dt.tz_localize("UTC") - pd.Timedelta(minutes=7.5)
    out = df[fields].astype(float).set_axis(idx)
    out = out[~out.index.duplicated()].sort_index()
    out.index.name = "time_utc"
    return out


def fetch_rce(d0: pd.Timestamp, d1: pd.Timestamp) -> pd.DataFrame:
    """Rynkowa Cena Energii RCE [PLN/MWh] (może być ujemna przy nadpodaży OZE)."""
    return _cached("pse_rce", d0, d1, lambda a, b: _pse("rce-pln", a, b, ["rce_pln"]))


def fetch_kse(d0: pd.Timestamp, d1: pd.Timestamp) -> pd.DataFrame:
    """Zapotrzebowanie KSE oraz generacja PV i wiatrowa w Polsce [MW]."""
    return _cached("pse_kse", d0, d1, lambda a, b: _pse("his-wlk-cal", a, b, ["demand", "pv", "wi"]))


# ---------------------------------------------------------------- przeliczenia
def sim_grid_utc(cfg: SimConfig) -> pd.DatetimeIndex:
    """Środki przedziałów [kΔt, (k+1)Δt) w UTC; ``cfg.start`` to czas lokalny (Europe/Warsaw)."""
    t0 = pd.Timestamp(cfg.start).tz_localize(TZ, ambiguous=False, nonexistent="shift_forward").tz_convert("UTC")
    return t0 + pd.to_timedelta(np.arange(cfg.n_steps) * cfg.dt_min + cfg.dt_min / 2, unit="min")


def resample(df: pd.DataFrame, grid: pd.DatetimeIndex) -> pd.DataFrame:
    """Interpolacja liniowa w czasie na siatkę symulacji."""
    full = df.reindex(df.index.union(grid)).interpolate("time", limit_direction="both")
    return full.reindex(grid)


def pv_from_weather(site: SiteConfig, gti: np.ndarray, temp: np.ndarray) -> np.ndarray:
    gti = np.clip(gti, 0.0, None)
    t_cell = temp + (PV_NOCT - 20.0) / 800.0 * gti
    p = site.pv_kwp * gti / 1000.0 * (1.0 + PV_GAMMA * (t_cell - 25.0)) * PV_SYSTEM_EFF * site.inverter_eff
    return np.clip(p, 0.0, site.pv_kwp * site.inverter_eff)


def hub_wind_speed(v10: np.ndarray, v100: np.ndarray, hub_m: float) -> np.ndarray:
    v10 = np.clip(v10, 0.05, None)
    v100 = np.clip(v100, 0.05, None)
    alpha = np.clip(np.log(v100 / v10) / np.log(10.0), 0.05, 0.6)  # teren miejski: zwykle 0.25–0.4
    return v10 * (hub_m / 10.0) ** alpha


# Profile typowe (godziny 0–23, wartość względna, max ≈ 1) – kształty zbliżone do profili
# standardowych OSD dla danej kategorii odbiorców; dni wolne = sobota/niedziela.
LOAD_SHAPES: dict[str, dict[str, list[float]]] = {
    "residential": {
        "work": [.45, .40, .38, .37, .38, .45, .60, .70, .62, .55, .52, .52, .55, .55, .55, .60, .70, .85, .95, 1.0, .98, .88, .72, .55],
        "free": [.50, .45, .40, .38, .37, .38, .42, .50, .62, .70, .72, .72, .75, .72, .68, .68, .72, .82, .92, .98, .95, .86, .72, .58],
    },
    "office": {
        "work": [.30, .30, .30, .30, .30, .32, .45, .70, .90, .98, 1.0, 1.0, .97, .98, .97, .92, .80, .60, .45, .38, .35, .33, .32, .30],
        "free": [.30, .30, .30, .30, .30, .30, .30, .31, .32, .33, .33, .33, .33, .33, .33, .32, .32, .31, .31, .30, .30, .30, .30, .30],
    },
    "commercial": {
        "work": [.35, .32, .30, .30, .30, .32, .40, .55, .75, .88, .95, .98, 1.0, 1.0, .98, .97, .97, .98, .97, .92, .85, .70, .50, .40],
        "free": [.35, .32, .30, .30, .30, .32, .38, .48, .62, .75, .82, .86, .88, .88, .86, .85, .85, .85, .84, .80, .74, .62, .48, .38],
    },
}
HEATING_COEF = 0.010   # wzrost zużycia na 1 K poniżej 15 °C (średnia dobowa)
COOLING_COEF = 0.015   # wzrost zużycia na 1 K powyżej 22 °C (temperatura chwilowa)


def typed_load(site: SiteConfig, grid_utc: pd.DatetimeIndex, temp: np.ndarray) -> np.ndarray:
    """Profil typowy × korekta temperaturowa; ``base_load_peak_kw`` = szczyt w warunkach neutralnych."""
    shapes = LOAD_SHAPES[site.load_type]
    loc = grid_utc.tz_convert(TZ)
    h = loc.hour + loc.minute / 60.0
    hh = np.arange(25)
    out = np.empty(len(loc))
    for kind in ("work", "free"):
        prof = np.array(shapes[kind] + shapes[kind][:1])
        mask = (loc.day_of_week < 5) if kind == "work" else (loc.day_of_week >= 5)
        out[mask] = np.interp(np.asarray(h)[mask], hh, prof)
    if site.load_type == "commercial":
        out[loc.day_of_week == 6] *= 0.75  # niedziele niehandlowe – czynna głównie gastronomia
    t_day = pd.Series(temp, index=loc).groupby(loc.date).transform("mean").to_numpy()
    factor = 1.0 + HEATING_COEF * np.clip(15.0 - t_day, 0, None) + COOLING_COEF * np.clip(temp - 22.0, 0, None)
    return site.base_load_peak_kw * out * factor


def real_inputs(site: SiteConfig, cfg: SimConfig) -> tuple[dict[str, np.ndarray], pd.DataFrame]:
    """Profile wejściowe z danych rzeczywistych + ramka z surowymi danymi (do wykresów)."""
    from .synthetic import wind_power_curve  # krzywa mocy z dokumentacji (wspólna dla obu trybów)

    d0, d1 = _days(cfg)
    grid = sim_grid_utc(cfg)
    w = resample(fetch_weather(site.lat, site.lon, d0, d1), grid)
    rce = resample(fetch_rce(d0, d1), grid)
    kse = resample(fetch_kse(d0, d1), grid)

    v_hub = hub_wind_speed(w["v10"].to_numpy(), w["v100"].to_numpy(), site.wind_hub_m)
    wind = wind_power_curve(site, v_hub) if site.wind_rated_kw > 0 else np.zeros(cfg.n_steps)
    demand = kse["demand"].to_numpy()
    if site.load_type == "kse":
        load = site.base_load_peak_kw * demand / demand.max()
    elif site.load_type in LOAD_SHAPES:
        load = typed_load(site, grid, w["temp"].to_numpy())
    else:
        raise ValueError(f"Nieznany typ odbiorcy: {site.load_type!r}")

    raw = pd.DataFrame({
        "gti": w["gti"].to_numpy(), "temp": w["temp"].to_numpy(), "v10": w["v10"].to_numpy(),
        "v100": w["v100"].to_numpy(), "v_hub": v_hub, "rce": rce["rce_pln"].to_numpy(),
        "kse_demand": demand, "kse_pv": kse["pv"].to_numpy(), "kse_wind": kse["wi"].to_numpy(),
    })
    profiles = {
        "load": load,
        "pv": pv_from_weather(site, w["gti"].to_numpy(), w["temp"].to_numpy()),
        "wind": wind,
        "price": rce["rce_pln"].to_numpy(),
    }
    return profiles, raw
