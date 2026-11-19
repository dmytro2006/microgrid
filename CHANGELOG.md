# Changelog

Wszystkie zmiany na gałęzi `feature/real-data-animations` względem `master` (`092cb05 Initial commit`).

## [0.7.0] – 2026-11-19 · rzeczywista sieć 110/15 kV Krakowa z rozpływem mocy

### Dodane
- **Sieć WN z OpenStreetMap** (`grid_osm.py`, cache `data/cache/osm_power_krakow.json` w repo – praca offline):
  stacje 110/220/400 kV i linie/kable WN w prostokącie 49,95–50,17 N, 19,70–20,32 E. Dane © współtwórcy OSM (ODbL).
- **Topologia** (`grid_topology.py`): odcinki OSM sklejone w linie między stacjami; stacje bez połączenia
  w danych dostają połączenie zastępcze (odległość × 1,3), oznaczone na mapie. Każdy węzeł ma swój GPZ
  (najbliższy GPZ dzielnicowy albo `SiteConfig.gpz`).
- **Model pandapower** (`network.py`): szyny 110 kV, stacje PSE (Skawina, Wanda, Lubocza) jako sieć krajowa,
  GPZ 2 × 25 MVA 110/15 kV, linia kablowa 15 kV do każdego węzła i transformator 15/0,4 kV węzła.
  Źródła na 110 kV: **EC Łęg** (60–380 MW zależnie od średniej temperatury dobowej) i **ZTPO** (7,5 MW).
- **Wspólna symulacja węzłów** (`cosim.py`, `SimConfig.network`, `run_all_network`). Jeden przebieg na strategię
  obejmuje wszystkie węzły. W każdym kroku: decyzje EMS → rozpływ mocy → **koordynator sieciowy**
  (`network_control`) → zapis. Koordynator koryguje węzeł z naruszeniem (U poza 0,90–1,10 p.u., linia 15 kV
  lub transformator > 100%): przy eksporcie BESS → EV → ograniczenie PV/wiatru, przy poborze BESS → dławienie EV.
  Naruszenia na 110 kV są tylko raportowane.
- **Mnożnik PV** (`SimConfig.pv_multiplier`) i **awaria wybranych GPZ** (`SimConfig.outage_gpz`): węzły pod
  wyłączonym GPZ pracują wyspowo, pozostałe normalnie, rozpływ bez wyłączonej stacji.
- KPI węzła: `U min/max [p.u.]`, `Obc. linii SN max [%]`, `Obc. transf. max [%]`,
  `Ograniczenie PV przez sieć [kWh]`, `Naruszenia sieci [kroki]`. KPI sieci (`network_summary`):
  `Obc. linii 110 kV max [%]`, `Straty sieci [MWh]`, `Wymiana wewnętrzna [kWh]`, `Pobór z PSE max [MW]`,
  `Produkcja EC Łęg [MWh]`.
- **Zdolność przyłączeniowa PV** (`analysis.hosting_capacity`, `hosting_capacity_table`): bisekcja po mocy PV
  węzła (1 doba, Δt = 15 min, bez koordynatora); liczą się tylko naruszenia, których nie ma przy PV = 0.
- **Mapy**: linie 110/220/400 kV i stacje z OSM, linie 15 kV węzeł–GPZ, kolor linii wg obciążenia,
  kolor węzła wg napięcia; zamiast umownego punktu zasilania – przepływ do własnego GPZ.
- **Panel**: sekcja „Sieć” (przełącznik „Sieć 110/15 kV (rozpływ mocy)” – domyślnie włączony, „Koordynator
  sieciowy”, „Mnożnik PV” 1–10), multiselect „GPZ objęte awarią”, metryki sieci węzła w 📈, tabela sieci
  na strategię w 📊, „Zdolność przyłączeniowa PV” w 🔬, założenia modelu pod mapą.
- **CLI**: `--network`, `--no-net-control`, `--pv-mult`; KPI sieci w `out/siec_<strategia>.csv`.
- Zależność `pandapower>=3.5` (pandas 2.3.x, numpy < 2.5).

### Założenia (stałe w `config.py`, wypisane w panelu)
- Tło: 20 MW szczytu na GPZ dzielnicowy, 2 MW na linię 15 kV węzła, cos φ = 0,95; kształt KSE (dane
  rzeczywiste) lub profil syntetyczny.
- Linie 110 kV: `243-AL1/39-ST1A 110.0` (napowietrzne), `N2XS(FL)2Y 1x240 RM/35 64/110 kV` (kable);
  linia 15 kV: `NA2XS2Y 1x240 RM/25 12/20 kV`, długość = odległość × 1,3.
- Transformator węzła Sn = P_max / 0,95, vk = 6%; napięcie w stacjach PSE 1,05 p.u.
- EC Łęg maks. 380 MW (ok. 80% z 480 MWe), min. 60 MW latem; ZTPO 7,5 MW stale.

### Wyniki (9 węzłów, 2 doby, dane rzeczywiste, flota rzeczywista, Δt = 5 min, seed 42)

| | lato (od 15.06) | zima (od 12.01) |
|---|---:|---:|
| obciążenie linii 110 kV maks. (A / D) | 113,3% / 113,1% | 96,7% / 96,7% |
| straty w sieci 110/15 kV | 299,6 MWh | 242,8 MWh |
| pobór z PSE maks. | 675 MW | 486 MW |
| produkcja EC Łęg | 3 050 MWh | 12 360 MWh |
| napięcie nN węzłów | 0,975–1,029 p.u. | 0,976–1,029 p.u. |
| obciążenie transformatora węzła maks. (D) | 91,8–94,5% | 92,6–95,4% |
| obciążenie linii 15 kV węzła maks. | 20–24% | 20–24% |
| naruszenia sieci przy obecnym PV | 0 | 0 |

- Mikrosieci są niewidoczne na 110 kV (różnice między strategiami < 0,4 p.p. obciążenia, < 0,1 MWh strat).
  Przeciążenie jednej linii 110 kV latem (ok. 113%) wynika z tła i małej produkcji EC Łęg – zimą EC pokrywa
  większą część zapotrzebowania i maksimum spada poniżej 100%.
- **Zdolność przyłączeniowa PV** (strategia A, dzień startu; we wszystkich węzłach pierwszym ograniczeniem jest
  transformator węzła, nie linia 15 kV ani napięcie):

| węzeł (GPZ) | PV obecnie [kWp] | lato [kWp] | zima [kWp] |
|---|---:|---:|---:|
| AGH / Czarna Wieś (GPZ AGH) | 150 | 684 | 1 582 |
| Nowa Huta – os. Centrum (GPZ Czyżyny) | 80 | 430 | 1 055 |
| Kazimierz (GPZ Dajwór) | 100 | 820 | 1 855 |
| Bonarka / Podgórze (GPZ Bonarka) | 300 | 1 680 | 3 652 |
| Ruczaj / Kampus UJ (GPZ Kampus) | 200 | 977 | 2 129 |
| Dworzec Główny (GPZ Centrum Komunikacyjne) | 60 | 664 | 1 523 |
| Czyżyny (GPZ Wieczysta) | 60 | 313 | 762 |
| Bieżanów-Prokocim (GPZ Bieżanów) | 90 | 566 | 1 250 |
| Prądnik Biały (GPZ Górka) | 50 | 313 | 781 |

  Obecne PV to 9–22% zdolności latem; zimą (niskie nasłonecznienie) zmieści się ok. 2,2–2,5× więcej.
- **PV × 5, lato**: bez koordynatora strategia A narusza ograniczenia w 112 krokach (transformator węzła do 140%
  w Prądniku Białym, 126% w AGH); koordynator usuwa wszystkie naruszenia, ograniczając PV tylko o 32 kWh
  (AGH) – resztę przejmuje ładowanie EV. MPC (D) z BESS mieści się w sieci bez korekt (maks. 97,7%).
  Wymiana wewnętrzna między węzłami rośnie z ~0 do 1,3 MWh (A) / 2,0 MWh (D).
- Pierwsze ładowanie panelu z siecią (9 węzłów × 4 strategie × 2 doby, Δt = 5 min, 16 rdzeni): **60 s**
  (bez sieci ok. 10 s); zdolność przyłączeniowa 9 węzłów: ok. 40 s.

## [0.6.0] – 2026-11-19 · wspólna farma wiatrowa poza miastem

### Dodane
- **Farma wiatrowa poza miastem** (`windfarm.py`, `SimConfig.wind_farm`, `wind_farm_mw`, `wind_farm_hub_m`).
  - Turbiny wielomegawatowe (start 3 m/s, moc znamionowa od 12 m/s), piasta domyślnie 120 m. Wiatr z Open-Meteo
    dla otwartego terenu na płn.-zach. od Krakowa; w trybie syntetycznym godzinowy proces AR(1), średnio 6 m/s.
  - Produkcja dzielona między symulowane węzły proporcjonalnie do mocy umownej.
  - Wariant **`ppa`**: farma przyłączona gdzie indziej w sieci OSD. Fizyczne przepływy w PCC bez zmian,
    zmienia się tylko rachunek: udział po cenie PPA (`Tariff.ppa_price_pln_mwh` = 380 PLN/MWh, pay-as-produced).
    W tym samym kroku zastępuje zakup po cenie spot, a nadwyżka jest odsprzedawana po cenie spot.
    Nie pomaga przy awarii GPZ.
  - Wariant **`gpz`**: farma przy tym samym GPZ. Rozliczenie jak PPA, a w czasie awarii GPZ pracuje wyspowo
    z farmą i każdy węzeł pobiera do wysokości swojego udziału. MPC przy awarii zapowiedzianej zna
    prognozowany udział.
- KPI: `Farma: energia [kWh]`, `Farma: pokrycie poboru [%]`; składnik kosztu `Farma PPA [PLN]` (netto).
- Panel: sekcja „Farma wiatrowa poza miastem” (wariant, moc, wysokość piasty), farma na obu mapach,
  udział farmy na wykresie przebiegów.

### Wyniki (9 węzłów, 2 doby, dane rzeczywiste, farma 3 MW / 120 m, awaria 17:00–22:00)

| | CF farmy | koszt D: bez / PPA / GPZ [PLN] | ENS D: bez / PPA / GPZ [kWh] | pokrycie poboru |
|---|---:|---:|---:|---:|
| styczeń (od 12.01) | 14% | 116 473 / 107 519 / 108 025 | 6 889 / 6 889 / 6 688 | 20% |
| czerwiec (od 15.06) | 29% | 43 089 / 44 055 / 47 216 | 4 184 / 4 184 / 1 394 | 61–64% |

- Zimą farma obniża koszt o ok. 8%. Latem PPA jest nieco droższe od taniej energii w południe.
- Farma przez sieć OSD nie zmienia ENS ani przeciążeń transformatorów. Przyłączona do GPZ latem obniża ENS 3×.
  W wariancie GPZ koszt jest wyższy, bo w czasie awarii obsłużono więcej odbiorców (opłaty dystrybucyjne).

## [0.5.0] – 2026-11-19 · awaria GPZ (blackout), mapy bez klucza API

### Dodane
- **Symulacja awarii GPZ / blackoutu** (`SimConfig.outage_start_h`, `outage_dur_h`, `outage_announced`).
  W oknie awarii wszystkie węzły pracują wyspowo (P_grid = 0); `island_dispatch` koryguje polecenia EMS:
  - deficyt: BESS → ograniczenie ładowania EV → V2G → odciążenie odbiorów;
  - nadwyżka: BESS → ograniczenie V2G → doładowanie EV → ograniczenie OZE.
  - Awaria zapowiedziana: MPC ma w LP zmienne odciążenia (VoLL 20 PLN/kWh) i ograniczenia OZE,
    więc zawczasu ładuje BESS. Przykład (AGH, 17:00, 5 h): ENS 440 → 387 kWh.
- KPI: `Awaria GPZ [h]`, `ENS [kWh]`, `ENS [%]`, `Ograniczenie OZE [kWh]`; kolumny `load_dem`, `p_shed`,
  `p_curt`, `grid_ok` w szeregu czasowym (`load` = moc faktycznie dostarczona).
- Panel: sekcja „Awaria sieci (GPZ)”, pas awarii i odciążenie na wykresach, GPZ „AWARIA” na mapach.

### Zmienione
- Mapy bez klucza API: animacja na kafelkach OpenStreetMap, mapa pydeck na stylu OpenFreeMap (zamiast Carto).
- Wyższe mapy w panelu (animowana 900 px, pydeck 850 px).

## [0.4.1] – 2026-11-18 · poprawka: panel ładował się kilkanaście minut

### Naprawione
- **Panel uruchamiał się rekurencyjnie w procesach roboczych.** Streamlit wykonuje `app.py` jako moduł `__main__`,
  a procesy puli (forkserver/spawn) importują `__main__` ponownie. Każdy worker uruchamiał więc cały panel
  z własną symulacją wszystkich węzłów i własną pulą. Objaw: setki ostrzeżeń
  `missing ScriptRunContext` i ładowanie rzędu 15 min.
  - Panel jest teraz w funkcji `main()` wywoływanej tylko przy `__name__ == "__main__"`.
  - `parallel_map` nie tworzy puli wewnątrz procesu roboczego.
  - Pierwsze ładowanie panelu (9 węzłów × 4 strategie, 2 doby): ok. 25–30 s, kolejne z pamięci podręcznej.

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
