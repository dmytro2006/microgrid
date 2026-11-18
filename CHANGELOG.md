# Changelog

Wszystkie zmiany na gałęzi `feature/real-data-animations` względem `master` (`092cb05 Initial commit`).

## [0.4.0] – 2026-11-18 · prognoza przyjazdów, moc umowna, starzenie BESS, nowe animacje, 9 węzłów

### Dodane
- **MPC z prognozą przyjazdów EV** (`SimConfig.mpc_ev_forecast`, domyślnie włączona). Oczekiwane przyjazdy
  w każdym przyszłym kroku LP (intensywność procesu Poissona) tworzą zagregowany „wirtualny pojazd”:
  - potrzebna energia n·E̅, moc n·P̄ (≤ liczba stanowisk), postój zależny od godziny przyjazdu;
  - plan zostawia margines mocy i energii BESS na auta, które dopiero przyjadą.
  - Efekt (styczeń, 7 dni): UDR w Nowej Hucie 38,7% → 28,4%, w Kazimierzu 27,5% → 24,3%,
    przy koszcie wyższym o ok. 0,3%; latem bez istotnej zmiany.
- **Moc umowna oddzielona od mocy przyłącza** (`SiteConfig.p_contract_kw`). EMS pilnuje min(moc umowna, P_max);
  opłata stała i kary liczone są od mocy umownej.
  - Nowa analiza `contract_sweep` / `best_contract` z wykresem kosztu, opłat i UDR.
  - Opcjonalna wycena niedoładowania klientów (PLN za p.p. UDR) przesuwa optimum.
- **Model starzenia BESS** (`aging.py`) zastępuje liniowy koszt zużycia w KPI:
  - cykliczne: rainflow (ASTM E1049) + krzywa Wöhlera N(DoD) = N_ref·(DoD/DoD_ref)^−1.6;
  - kalendarzowe: 2%/rok przy 25 °C i SoC 50%, mnożnik SoC (0,5 + SoC) i Arrhenius (Ea = 50 kJ/mol),
    temperatura ogniwa zależna od temperatury otoczenia (dane rzeczywiste) lub 20 °C (syntetyczne);
  - KPI: `Starzenie cykl. [%]`, `Starzenie kal. [%]`, `Utrata SoH BESS [%]`, `Zużycie BESS [PLN]`.
- **KPI `UDR wykonalne [%]`**: niedoładowanie liczone tylko dla aut, które fizycznie dało się naładować do celu
  (P_max × czas postoju ≥ potrzebna energia). Oddziela wpływ sterowania od fizyki
  (w A zawsze 0%, w B–D średnio 0,5–8,6% zależnie od sezonu, w pojedynczych węzłach do 18%).
- **Nowe animacje** (zakładka 🎬, przełącznik widoku):
  - 🅿️ parking stacji: auta wjeżdżają na stanowiska i odjeżdżają, kolor = stan (ładowanie / V2G / naładowane /
    czeka), pasek SoC z kreską celu, moc na karoserii, kolejka przy wjeździe, model auta po najechaniu,
    pasek P_grid względem mocy umownej, koszt i minuty przeciążenia narastająco;
  - 🆚 porównanie dwóch strategii (np. A i D) obok siebie ze wspólnym odtwarzaczem;
  - 🔀 animowany wykres Sankeya: PV / wiatr / sieć / BESS / V2G → odbiory / EV / BESS / eksport,
    tryb chwilowy [kW] i narastający [kWh].
  - CLI `--anim` zapisuje też `parking_*.html` i `sankey_*.html`.
- **6 nowych węzłów** (`KRAKOW_SITES_EXTRA`, `ALL_SITES` = 9) w miejscach skupisk ładowarek z OpenStreetMap:
  Bonarka/Podgórze (OSM: 125 punktów w promieniu 500 m, Tesla Supercharger), Ruczaj/Kampus UJ (22),
  Dworzec Główny (13), Czyżyny, Bieżanów-Prokocim (14), Prądnik Biały (10).
  - Liczba stanowisk wg OSM; moc przyłącza wg typoszeregu transformatorów 160/250/400/630 kVA (cos φ = 0,95).
  - Mapy dopasowują przybliżenie do rozrzutu węzłów; w panelu jest wybór węzłów do symulacji.
- `Results.ev_trace`: SoC i moc każdego podłączonego auta w każdym kroku.
- Testy: 39 (było 26).

### Zmienione
- Dane pogodowe pobierane dla siatki 0,1° (rozdzielczość modeli ~9–11 km). Pobliskie węzły dzielą plik cache;
  cache przebudowany (`weather_50.0_19.9_2026-06.csv` itd.).
- CLI domyślnie liczy wszystkie 9 węzłów (`--sites` filtruje); nowa flaga `--no-ev-forecast`.
- Raport HTML zawiera nowe KPI (UDR wykonalne, utrata SoH).

### Wyniki – 9 węzłów, 7 dni, dane rzeczywiste, flota realna (suma węzłów)

| scenariusz | koszt cz. | kara cz. | koszt st. | kara st. | UDR wyk. cz./st. [%] | V2G cz. [kWh] | zużycie BESS cz./st. [PLN] |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 541 445 | 350 377 | 1 280 922 | 950 373 | 0,0 / 0,0 | 0 | 5 298 / 3 242 |
| B | 194 520 | 0 | 324 021 | 0 | 0,5 / 5,9 | 0 | 8 366 / 6 137 |
| C | 184 435 | 0 | 320 614 | 0 | 1,0 / 8,3 | 1 750 | 8 621 / 6 906 |
| D | **177 553** | 0 | **314 132** | 0 | 2,1 / 8,6 | 4 130 | 8 269 / **4 403** |

(cz. = tydzień od 15.06.2026, st. = tydzień od 12.01.2026; koszty w PLN)

- Bez sterowania kary za moc umowną to 65% (lato) i 74% (zima) kosztu.
- D jest najtańsze w obu sezonach (−3,7% / −2,0% względem C). Zimą zużywa magazyn najoszczędniej:
  płytsze cykle dają o 36% niższy koszt starzenia niż C.
- Starzenie magazynu: 0,03–0,06% SoH na tydzień, czyli ok. 1,5–3%/rok.
  Część kalendarzowa jest porównywalna z cykliczną, więc bezczynny magazyn (A) też się starzeje.
- Zimą najtrudniejsze są małe osiedlowe przyłącza (Bieżanów-Prokocim 18%, Czyżyny 13%, Prądnik Biały 13%
  UDR wykonalnego w D). Ogrzewanie podnosi odbiory bazowe, a transformator 160/250 kVA zostawia mało miejsca na EV.
- Dobór mocy umownej (Nowa Huta, P_max 200 kW):
  - latem C osiąga minimum kosztu przy 140 kW, ale kosztem 26% niedoładowanych aut;
    D – przy 180 kW (7% UDR) za praktycznie ten sam koszt;
  - zimą optimum to 160–200 kW;
  - bez wyceny niezadowolenia klientów optymalizacja kosztu „sprzedaje” jakość obsługi.
    Dlatego w panelu jest kara za p.p. UDR.

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
- MPC planuje dla wartości oczekiwanej przyjazdów. Kolejny krok to scenariusze (stochastyczne MPC) i kara za UDR
  zależna od czasu.
- Kalibracja przyjazdów i postojów EV na danych rzeczywistych (ACN-Data / operatorzy, KDE + test KS).
- Pierwsze 3 węzły mają moce umowne z wersji 0.1 (250/200/300 kW); nowe węzły – typoszereg transformatorów.
- Model starzenia kalendarzowego jest liniowy (dla horyzontu tygodni). W dłuższej perspektywie właściwe jest prawo √t.
- Ceny RCE są publikowane ex post. W trybie `persistence` MPC traktuje je jak znaną z wyprzedzeniem cenę RDN.
