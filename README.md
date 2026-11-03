# Symulator mikrosieci EV + BESS + OZE (AGH SSD)

Hybrydowa symulacja dyskretna zgodna z `../mikrosiec_ev_bess.tex`:
DES (SimPy: przyjazdy, kolejka FIFO, odjazdy EV) + krok bilansowy Δt w węźle PCC,
3 scenariusze EMS (A baseline, B peak shaving, C smart charging EDF + V2G + arbitraż BESS),
KPI (SSR, T_over, ΔP_over_max, UDR, EFC, koszt) i walidacja bilansu energii.

**Wszystkie dane wejściowe są na razie syntetyczne** (`src/microgrid/synthetic.py`, `ev_generator.py`).
Lokalizacje w Krakowie (`config.KRAKOW_SITES`) są przykładowe.

## Uruchomienie
```bash
uv sync
uv run pytest                              # testy
uv run python run.py --days 2 --no-show    # KPI w terminalu + out/*.png, out/map_*.html, CSV
uv run streamlit run app.py                # panel interaktywny (wykresy, porównanie, mapa Krakowa)
```
Opcje CLI: `--days`, `--dt {1,5,15}`, `--seed`, `--sites nowa kazim`, `--out`.

## Struktura
| moduł | rola |
|---|---|
| `config.py` | parametry węzłów (`SiteConfig`), symulacji (`SimConfig`), rozkładów EV |
| `synthetic.py` | profile: odbiory, PV, wiatr (krzywa mocy z dokumentacji), cena |
| `ev_generator.py` | agent EV, niejednorodny proces Poissona, procesy SimPy stacji |
| `grid_environment.py` | model BESS, równanie bilansu PCC |
| `controller.py` | strategie EMS A/B/C (C = Algorytm 1 z dokumentacji + rozszerzenia) |
| `simulation.py` | pętla symulacji, `run()` / `run_all()` (wspólne liczby losowe dla scenariuszy) |
| `metrics_evaluator.py` | KPI, rezydua bilansu energii |
| `plots.py`, `map_view.py` | wykresy matplotlib, mapa przepływów pydeck |

## Dalsze kroki (dane rzeczywiste dla Krakowa)
Podmiana funkcji w `synthetic.py` / `generate_evs` na: PVGIS (PV), Open-Meteo / IMGW Balice (wiatr),
TGE RDN (ceny), EIPA/OSM (lokalizacje ładowarek), ACN-Data (kalibracja KDE + test KS sesji EV).
Interfejs zostaje ten sam: wektor wartości na siatce Δt.
