# Changelog

Wszystkie zmiany na gałęzi `feature/real-data-animations` względem `master` (`092cb05 Initial commit`).

## [0.3.0] – 2026-11-18 · optymalizacja, taryfa, analizy

### Dodane
- **Strategia D – MPC (programowanie liniowe)** (`mpc.py`). Problem LP (HiGHS przez `scipy.optimize.linprog`)
  rozwiązywany w horyzoncie przesuwnym 24 h: przez pierwsze 6 h krok 15 min, dalej 60 min. Ponowne planowanie
  co 15 min oraz przy każdym nowym pojeździe.
  - Funkcja celu: pełna cena poboru z taryfy, przychód z eksportu po cenie spot, zużycie BESS,
    wartość energii pozostawionej w magazynie, kary za przeciążenie PCC i za niedoładowanie EV,
    mała kara za gwałtowne zmiany mocy w PCC (wygładza profil poboru).
  - Model energii pojazdów uwzględnia niepełny ostatni kwadrans przed odjazdem.
  - Korekta czasu rzeczywistego (najpierw BESS, potem dławienie EV), gdy chwilowa moc odbiega od średniej z prognozy.
  - Dwa tryby prognozy: `perfect` (górne ograniczenie jakości) i `persistence` (odbiory, PV i wiatr z poprzedniej doby).
- **Taryfa** (`tariff.py`): orientacyjny model grupy C2x u OSD Tauron Dystrybucja. Składniki:
  - energia (spot + marża sprzedawcy, eksport po cenie spot),
  - dystrybucja zmienna, jakościowa, OZE i kogeneracyjna,
  - opłata mocowa (dni robocze 7–22),
  - składnik stały,
  - kara za przekroczenie mocy umownej: 10× składnik stały × suma 10 największych nadwyżek mocy 15-min,
  - zużycie BESS (CAPEX / (2 · cykle · DoD)).
- **Nowe KPI**: struktura kosztu (6 składników), `Koszt całk. [PLN]`, `Utrata SoH BESS [%]`.
- **Profile odbiorców** dla danych rzeczywistych: osiedle, biurowiec/uczelnia, handel i usługi.
  - Osobne profile dla dni roboczych i wolnych; w handlu niedziele niehandlowe.
  - Korekta temperaturowa: ogrzewanie poniżej 15 °C, klimatyzacja powyżej 22 °C.
  - Węzły: AGH = biurowiec/uczelnia, Nowa Huta = osiedle, Kazimierz = handel.
- **Ładowarki z OpenStreetMap** (`osm.py`): 195 stacji w Krakowie (moc, operator, liczba punktów) w cache
  i jako warstwa na mapie statycznej i animowanej (DC ≥ 40 kW wyróżnione kolorem).
- **Analizy** (`analysis.py`, zakładka 🔬):
  - Monte Carlo: N powtórzeń z kolejnymi ziarnami, średnia, odchylenie, 95% CI (t-Studenta), wykres rozrzutu;
  - dobór PV × BESS: mapa cieplna kosztu łącznego = eksploatacja + annuitet inwestycji
    (PV 3500 PLN/kWp / 25 lat, BESS 1500 PLN/kWh / 15 lat, stopa 6%).
- **Raport HTML** (`report.py`): jeden plik z osadzonymi wykresami SVG i tabelami KPI.
  W panelu jest przycisk w pasku bocznym, w CLI flaga `--report`.
- Wykres struktury kosztu dla każdej strategii (zakładka 📊, CLI `koszty_*.png`).
- CLI: `--strategies`, `--forecast`, `--report`.
- Panel: wybór prognozy MPC i typu odbiorcy, KPI kosztu całkowitego i kary.
- Równoległe obliczenia (`parallel_map`, pula procesów) w `run_all`, Monte Carlo i doborze.
  Gdy nie da się uruchomić procesów potomnych, liczenie przechodzi na tryb sekwencyjny.
- Testy: 26 (było 9), m.in. taryfa (okna opłaty mocowej, kara z mocy 15-min), MPC w obu trybach prognozy,
  D tańsze od C tańszego od A, parser OSM, analizy, raport.

### Zmienione
- `run_all` domyślnie liczy 4 strategie (A–D).
- Zależność: `scipy>=1.13`.

## [0.2.0] – 2026-11-18 · dane rzeczywiste i animacje

### Dodane
- **Dane rzeczywiste** (`real_data.py`), publiczne API bez kluczy:
  - Open-Meteo Historical: nasłonecznienie na płaszczyznę 35°/S (GTI), temperatura, wiatr na 10 m i 100 m;
  - PSE `rce-pln`: Rynkowa Cena Energii RCE w rozdzielczości 15 min (także ceny ujemne);
  - PSE `his-wlk-cal`: zapotrzebowanie KSE oraz krajowa generacja PV i wiatrowa;
  - modele: PV z temperaturą ogniw (NOCT, γ = −0.35%/K, straty systemu, falownik); wiatr z profilem
    potęgowym dopasowanym co godzinę do pomiarów 10/100 m → piasta 30 m → krzywa mocy z dokumentacji;
  - cache miesięczny w `data/cache/` (w repo: czerwiec i styczeń 2026), praca offline;
  - poprawne strefy czasowe (UTC ↔ Europe/Warsaw) i znaczniki czasu: średnie godzinowe Open-Meteo,
    koniec okresu w PSE.
- **Realna flota EV**: 17 popularnych w Polsce modeli BEV (pojemność netto, moc AC/DC) z wagami udziału,
  ładowarki AC 22 kW / DC 50 kW. Sesje DC są krótsze i bez V2G; model pojazdu trafia do tabeli sesji.
- **Animacje** (`animation.py`) działające w przeglądarce (▶/pauza, suwak, tempo):
  - schemat węzła PCC (SVG): płynące przepływy mocy, turbina kręcąca się zgodnie z wiatrem, słońce zgodne z PV,
    poziom SoC BESS, zajętość ładowarek, wykres z kursorem;
  - mapa Krakowa (deck.gl): cząstki energii między GPZ, PCC i komponentami, kolor PCC zależny od obciążenia.
- Panel: wybór źródła danych i daty, przełącznik floty, zakładki 🎬 Animacja i 🌦️ Dane wejściowe.
- CLI: `--data`, `--start`, `--fleet`, `--anim`.

## Wyniki na danych rzeczywistych (7 dni, flota realna, suma 3 węzłów)

| scenariusz | koszt całk. – czerwiec | w tym kara za moc | koszt całk. – styczeń | w tym kara za moc | T_over [min/d] (cz / st) | UDR [%] (cz / st) |
|---|---:|---:|---:|---:|---:|---:|
| A | 119 188 PLN | 65 669 | 342 953 PLN | 247 816 | 48 / 171 | 22.2 / 22.2 |
| B | 54 088 PLN | 0 | 93 704 PLN | 0 | 0 / 0 | 22.2 / 25.1 |
| C | 51 657 PLN | 0 | 92 328 PLN | 0 | 0 / 0 | 22.3 / 27.3 |
| D | **49 702 PLN** | 0 | **90 222 PLN** | 0 | 0 / 0 | 22.7 / 30.6 |

Wnioski:
- Koszt w scenariuszu A to w ponad połowie (czerwiec) i w ~70% (styczeń) kary za przekroczenie mocy umownej.
  Samo sterowanie szczytem (B) obniża koszt 2,2–3,7×.
- D jest najtańsze (latem −4% względem C i −8% względem B, zimą −2% względem C) i zużywa najwięcej V2G (1154 kWh/tydzień latem).
  Magazyn ładuje się w południe, gdy RCE spada do ~0, a oddaje energię w wieczornym szczycie.
- Turbina wiatrowa w mieście pracuje ze współczynnikiem wykorzystania ok. 5%. W Krakowie słabo wieje.
- Zimą PV pokrywa 4–7% zapotrzebowania (latem 10–18%).
- Ok. 22% aut odjeżdża niedoładowanych już w scenariuszu A: duże baterie przy 11 kW nie zdążą się naładować.
  Zimą, przy ograniczeniu mocy, UDR rośnie w B–D, najbardziej w D. MPC nie zna przyszłych przyjazdów,
  odkłada ładowanie na tańsze godziny, a potem musi dzielić moc z nowymi pojazdami.
- W przeglądzie doboru (2 doby, czerwiec, strategia C) najtańszy wyszedł wariant z największym badanym PV (250 kWp) i bez BESS:
  przy obecnych cenach magazyn nie zwraca się z samego arbitrażu, a jego wartość leży w unikaniu kar za moc.

## Znane ograniczenia / dalsze kroki
- Stawki taryfy są orientacyjne. Trzeba je zweryfikować z taryfą Tauron Dystrybucja na dany rok i umową sprzedaży.
- MPC nie przewiduje przyjazdów EV. Kolejny krok to stochastyczne MPC z oczekiwanym napływem pojazdów
  (rezerwa mocy) albo kara za UDR zależna od czasu.
- Kalibracja przyjazdów i postojów EV na danych rzeczywistych (ACN-Data / operatorzy, KDE + test KS).
- Moce transformatorów nadal są umowne. Do rozważenia typoszereg 160/250/400/630 kVA i cos φ.
- Ceny RCE są publikowane ex post. W trybie `persistence` MPC traktuje je jak znaną z wyprzedzeniem cenę RDN.
