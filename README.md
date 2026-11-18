# Symulator mikrosieci EV + BESS + OZE (AGH SSD)

Hybrydowa symulacja dyskretna zgodna z `../mikrosiec_ev_bess.tex`:
DES (SimPy: przyjazdy, kolejka FIFO, odjazdy EV) + krok bilansowy Δt w węźle PCC,
3 scenariusze EMS (A baseline, B peak shaving, C smart charging EDF + V2G + arbitraż BESS),
KPI (SSR, T_over, ΔP_over_max, UDR, EFC, koszt) i walidacja bilansu energii.

Dane wejściowe mogą być **syntetyczne** (`synthetic.py`) albo **rzeczywiste** (`real_data.py`):

| wielkość | źródło rzeczywiste | model przeliczeniowy |
|---|---|---|
| PV | Open-Meteo Historical (GTI na płaszczyznę 35°/S, temperatura) | NOCT + współczynnik temperaturowy, straty systemu, falownik |
| wiatr | Open-Meteo (prędkość 10 m i 100 m) | profil potęgowy dopasowany co godzinę → piasta, krzywa mocy |
| odbiory | PSE `his-wlk-cal` – zapotrzebowanie KSE (15 min) | kształt KSE przeskalowany do szczytu węzła |
| cena | PSE `rce-pln` – Rynkowa Cena Energii RCE (15 min, może być ujemna) | bez zmian |
| flota EV | `config.EV_MODELS` – 17 popularnych modeli BEV w PL (pojemność, AC/DC) | ładowarki AC 22 kW / DC 50 kW |

API są publiczne (bez kluczy). Odpowiedzi trafiają miesiącami do `data/cache/` (w repo są czerwiec i styczeń 2026),
więc po pierwszym pobraniu wszystko działa offline. Lokalizacje w Krakowie (`config.KRAKOW_SITES`) są przykładowe.

## Uruchomienie
```bash
uv sync
uv run pytest                              # testy
uv run python run.py --days 2 --no-show    # KPI w terminalu + out/*.png, out/map_*.html, CSV
uv run python run.py --data real --fleet real --start 2026-01-12 --days 7 --anim --no-show
uv run streamlit run app.py                # panel interaktywny (animacje, wykresy, porównanie, mapa Krakowa)
```
Opcje CLI: `--days`, `--dt {1,5,15}`, `--seed`, `--sites nowa kazim`, `--out`,
`--data {synthetic,real}`, `--start RRRR-MM-DD`, `--fleet {synthetic,real}`, `--anim` (animacje HTML).

## Animacje
- **Schemat węzła PCC** (zakładka 🎬): przepływy mocy jako płynące kropki (prędkość/grubość ∝ moc),
  turbina obraca się proporcjonalnie do mocy wiatru, słońce ∝ PV, poziom SoC BESS, zajętość ładowarek,
  wykres z kursorem czasu.
- **Mapa Krakowa** (zakładka 🗺️): cząstki energii płyną między GPZ, PCC i komponentami; kolor PCC = obciążenie.

Obie działają w przeglądarce (play / pauza / suwak / tempo), bez przeliczania po stronie Streamlit.

## Struktura
| moduł | rola |
|---|---|
| `config.py` | parametry węzłów (`SiteConfig`), symulacji (`SimConfig`), rozkładów EV |
| `synthetic.py` | profile: odbiory, PV, wiatr (krzywa mocy z dokumentacji), cena |
| `real_data.py` | pobieranie + cache Open-Meteo / PSE, modele PV i wiatru na danych rzeczywistych |
| `animation.py` | animowany schemat węzła (SVG) i animowana mapa (deck.gl) |
| `ev_generator.py` | agent EV, niejednorodny proces Poissona, procesy SimPy stacji |
| `grid_environment.py` | model BESS, równanie bilansu PCC |
| `controller.py` | strategie EMS A/B/C (C = Algorytm 1 z dokumentacji + rozszerzenia) |
| `simulation.py` | pętla symulacji, `run()` / `run_all()` (wspólne liczby losowe dla scenariuszy) |
| `metrics_evaluator.py` | KPI, rezydua bilansu energii |
| `plots.py`, `map_view.py` | wykresy matplotlib, mapa przepływów pydeck |

## Dalsze kroki
- EIPA / OpenStreetMap: rzeczywiste lokalizacje i moce ładowarek w Krakowie,
- ACN-Data / dane operatorów: kalibracja rozkładów przyjazdów i postojów (KDE + test KS),
- taryfa dystrybucyjna Tauron (C11/C21/B21: opłata zmienna, mocowa, kara za przekroczenie mocy umownej),
- profile standardowe OSD zamiast kształtu KSE (inne dla osiedla, biurowca, galerii).
