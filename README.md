# Symulator mikrosieci EV + BESS + OZE (AGH SSD)

Hybrydowa symulacja dyskretna zgodna z `../mikrosiec_ev_bess.tex`:
DES (SimPy: przyjazdy, kolejka FIFO, odjazdy EV) + krok bilansowy Δt w węźle PCC,
4 scenariusze EMS, KPI techniczne i kosztowe (taryfa, starzenie BESS), walidacja bilansu energii,
animacje (schemat, parking, porównanie strategii, Sankey, mapa) oraz analizy Monte Carlo,
doboru PV/BESS i mocy umownej. 9 węzłów w Krakowie.

| scenariusz | opis |
|---|---|
| A. Baseline | ładowanie pełną mocą od wpięcia, BESS wyłączony |
| B. Peak shaving | heurystyka progowa 0.85·P_max, dławienie proporcjonalne EV |
| C. Smart + V2G | Algorytm 1 (EDF) + arbitraż cenowy BESS + V2G |
| D. MPC (LP) | optymalizacja liniowa (HiGHS) w horyzoncie przesuwnym 24 h, pełna taryfa, prognoza doskonała lub „z wczoraj”, rezerwa na oczekiwane przyjazdy EV |

## Dane wejściowe
**Syntetyczne** (`synthetic.py`) albo **rzeczywiste** (`real_data.py`, `osm.py`):

| wielkość | źródło rzeczywiste | model przeliczeniowy |
|---|---|---|
| PV | Open-Meteo Historical (GTI na płaszczyznę 35°/S, temperatura) | NOCT + współczynnik temperaturowy, straty systemu, falownik |
| wiatr | Open-Meteo (prędkość 10 m i 100 m) | profil potęgowy dopasowany co godzinę → piasta 30 m, krzywa mocy |
| odbiory | PSE `his-wlk-cal` (zapotrzebowanie KSE) / profile typowe | osiedle, biurowiec/uczelnia, handel (dni robocze/wolne) + korekta temperaturowa |
| cena | PSE `rce-pln` – Rynkowa Cena Energii RCE (15 min, może być ujemna) | + taryfa (`tariff.py`) |
| flota EV | `config.EV_MODELS` – 17 popularnych modeli BEV w PL | ładowarki AC 22 kW / DC 50 kW |
| ładowarki | OpenStreetMap (Overpass) – 195 stacji w Krakowie | warstwa na mapach; lokalizacje i liczba stanowisk 6 nowych węzłów |

API są publiczne (bez kluczy). Odpowiedzi trafiają miesiącami do `data/cache/` (w repo: czerwiec i styczeń 2026
+ ładowarki OSM), więc wszystko działa offline. Trzy pierwsze węzły (`config.KRAKOW_SITES`) są przykładowe,
sześć kolejnych (`KRAKOW_SITES_EXTRA`) leży w miejscach skupisk ładowarek z OSM.

## Uruchomienie
```bash
uv sync
uv run pytest                              # testy (39)
uv run python run.py --days 2 --no-show    # KPI w terminalu + out/*.png, out/map_*.html, CSV
uv run python run.py --data real --fleet real --start 2026-01-12 --days 7 --anim --report --no-show
uv run streamlit run app.py                # panel interaktywny
```
Opcje CLI: `--days`, `--dt {1,5,15}`, `--seed`, `--sites nowa kazim`, `--out`, `--strategies ABCD`,
`--data {synthetic,real}`, `--start RRRR-MM-DD`, `--fleet {synthetic,real}`, `--forecast {perfect,persistence}`,
`--no-ev-forecast`, `--anim` (animacje HTML: schemat, parking, Sankey, mapa), `--report` (raport HTML).

## Panel (Streamlit)
🎬 animacje: schemat węzła / parking stacji / porównanie dwóch strategii / Sankey · 📈 przebiegi + KPI · 📊 porównanie scenariuszy i struktura kosztu ·
🗺️ animowana mapa Krakowa (cząstki energii, ładowarki OSM) · 🌦️ dane rzeczywiste (pogoda, RCE, KSE) ·
🚗 sesje EV (z modelem pojazdu) · 🔬 analizy (Monte Carlo z 95% CI, mapa cieplna doboru PV × BESS, dobór mocy umownej, taryfa) ·
pobieranie raportu HTML z panelu bocznego.

## Struktura
| moduł | rola |
|---|---|
| `config.py` | parametry węzłów (`SiteConfig`), symulacji (`SimConfig`), rozkładów i modeli EV |
| `synthetic.py` | profile syntetyczne: odbiory, PV, wiatr (krzywa mocy z dokumentacji), cena |
| `real_data.py` | pobieranie + cache Open-Meteo / PSE, modele PV, wiatru i profili odbiorców |
| `osm.py` | stacje ładowania z OpenStreetMap |
| `tariff.py` | taryfa C2x: energia, dystrybucja, opłata mocowa, składnik stały, kara za moc umowną |
| `aging.py` | starzenie BESS: rainflow + krzywa Wöhlera, starzenie kalendarzowe (SoC, Arrhenius) |
| `ev_generator.py` | agent EV, niejednorodny proces Poissona, procesy SimPy stacji |
| `grid_environment.py` | model BESS, równanie bilansu PCC |
| `controller.py` | strategie EMS A/B/C |
| `mpc.py` | strategia D – MPC / programowanie liniowe |
| `simulation.py` | pętla symulacji, `run()` / `run_all()` (równolegle, wspólne liczby losowe) |
| `metrics_evaluator.py` | KPI, struktura kosztu, rezydua bilansu energii |
| `analysis.py` | Monte Carlo, dobór PV × BESS z kosztem inwestycji, dobór mocy umownej |
| `plots.py`, `map_view.py`, `animation.py`, `report.py` | wykresy, mapa, animacje, raport HTML |

Historia zmian: [CHANGELOG.md](CHANGELOG.md).
