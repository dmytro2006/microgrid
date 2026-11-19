# Realna sieć 110 kV / 15 kV z rozpływem mocy – projekt

Data: 2026-11-19 · gałąź: `feature/siec-sn-rozplyw`

## Cel i powód

Prowadzący ocenił symulację jako niewystarczająco złożoną. Chcemy pogłębić model w stronę fizyki sieci
i odpowiedzieć na pytanie użytkownika: **czy nadwyżka (i pobór) mikrosieci zmieści się w sieci?**

Dziś każdy węzeł liczony jest osobno, a „GPZ” to jeden umowny punkt na mapie. Po zmianie:

- wszystkie wybrane węzły są symulowane **razem, krok po kroku**, w jednej sieci;
- sieć to **prawdziwe GPZ 110/15 kV i linie 110 kV Krakowa z OpenStreetMap**; połączenie z krajem przez
  stacje PSE;
- w każdym kroku liczony jest **rozpływ mocy AC (pandapower)**: napięcia, obciążenie linii i transformatorów,
  straty;
- **koordynator sieciowy** reaguje, gdy nadwyżka lub pobór węzła się „nie mieści”.

## Cele

1. **Dane sieci z OSM** (cache w repo, praca offline):
   - stacje `power=substation` z napięciem 110/220/400 kV w prostokącie 49.95–50.17 N, 19.70–20.32 E
     (rozpoznanie: 44 stacje, w tym ok. 35 GPZ 110/15 kV Tauron, SE Skawina 400/220/110, SE Wanda 220/110,
     SE Lubocza 220/110/15);
   - linie i kable `power=line|cable` 110/220/400 kV (rozpoznanie: ok. 520 km odcinków 110 kV, 205 km 220 kV,
     146 km 400 kV).
2. **Model sieci (pandapower)**:
   - szyny 110 kV wszystkich stacji WN; linie 110 kV odtworzone z geometrii OSM (długość z geometrii);
   - stacje PSE (Skawina, Wanda, Lubocza) = `ext_grid` (sieć krajowa) na szynie 110 kV, u = 1,0 p.u.;
   - w każdym GPZ 110/15 kV: transformator 110/15 kV i szyna 15 kV z **obciążeniem tła** (reszta dzielnicy);
   - każdy węzeł mikrosieci: **linia kablowa 15 kV do najbliższego GPZ**, po drodze szyna z obciążeniem tła
     innych stacji na tej linii, na końcu transformator 15/0,4 kV o mocy przyłącza węzła i szyna nN (PCC);
   - linie 220/400 kV tylko na mapie (nie liczone elektrycznie).
3. **Wspólna symulacja**: jeden przebieg na strategię obejmuje wszystkie węzły; w kroku k:
   decyzje EMS każdego węzła (bez zmian w strategiach A–D) → rozpływ mocy → koordynator → ewentualnie
   ponowny rozpływ → zapis.
4. **Koordynator sieciowy** (węzeł, którego dotyczy naruszenie):
   - naruszenia: obciążenie linii 15 kV węzła > 100%, transformatora węzła > 100%,
     napięcie na szynie nN poza 0,90–1,10 p.u.;
   - przy nadwyżce: najpierw ładowanie BESS, potem ładowanie EV, na końcu ograniczenie PV/wiatru;
   - przy poborze: najpierw rozładowanie BESS, potem dławienie EV;
   - naruszenia na 110 kV tylko raportowane (to domena OSD, nie mikrosieci).
5. **KPI sieciowe** (na węzeł i dla całej sieci): min/maks napięcie nN [p.u.], maks obciążenie linii 15 kV
   i transformatora węzła [%], maks obciążenie linii 110 kV [%], straty w sieci [kWh], energia PV
   ograniczona przez sieć [kWh], liczba kroków z naruszeniem, **wymiana wewnętrzna** między węzłami
   (Σ min(eksport, import) w kroku – energia, która mogła trafić z jednego węzła do innych).
6. **Zdolność przyłączeniowa PV** (zakładka 🔬): dla każdego węzła szukana (bisekcja) największa moc PV,
   przy której w słoneczną dobę nie ma naruszeń – bezpośrednia odpowiedź „czy nadwyżka się zmieści”.
   Do tego suwak **mnożnik PV** w panelu (scenariusz z dużą ilością PV).
7. **Awaria**: istniejąca „awaria GPZ” obejmuje całą sieć (blackout, praca wyspowa jak dziś). Nowe:
   **wybór GPZ objętych awarią** – węzły pod wyłączonym GPZ pracują wyspowo, reszta normalnie, a rozpływ
   liczony jest bez wyłączonej stacji.
8. **Mapa** (statyczna i animowana):
   - linie 110/220/400 kV z OSM (kolor wg napięcia), stacje GPZ i PSE (prawdziwe nazwy), linie 15 kV węzeł–GPZ;
   - w symulacji: kolor linii wg obciążenia, kolor węzła wg napięcia;
   - usunięty umowny punkt `GRID_SUPPLY_POINT`; przepływ PCC → własny GPZ; farma wiatrowa → najbliższy GPZ.
9. **Źródła lokalne na 110 kV** (dodane po akceptacji kierunku):
   - **Elektrociepłownia Kraków (Łęg, PGE)** – generator na szynie 110 kV stacji „GPZ Łęg” (OSM 50.0517 N,
     20.0060 E), praca według zapotrzebowania na ciepło: moc elektryczna rośnie liniowo od minimum przy
     średniej dobowej temperaturze ≥ 15 °C do maksimum przy −20 °C (temperatura z Open-Meteo; w trybie
     syntetycznym stała 20 °C → minimum);
   - **ZTPO (ekospalarnia, Nowa Huta)** – stała moc na szynie 110 kV stacji „GPZ ZTPO”;
   - obie z cos φ = 1, w rozpływie jako `gen`/`sgen`; w czasie pełnego blackoutu pomijane jak reszta sieci
     (scenariusz wyspy miasta wokół elektrociepłowni z SCO – osobny, kolejny etap).
10. Zależność `pandapower>=3.5` (pandas obniżony do 2.3.x, numpy < 2.5); wszystkie testy przechodzą.

## Poza zakresem

- Dynamika (częstotliwość, bezwładność, zabezpieczenia) – model pozostaje quasi-statyczny (krok minutowy).
- Prawdziwa topologia sieci 15 kV OSD (niedostępna) – linia węzeł–GPZ i tło na niej to założenie.
- Rynek wymiany między węzłami (ceny P2P, spółdzielnia energetyczna) – tylko KPI wymiany wewnętrznej.
- Wspólny MPC dla całej sieci (wariant C) – możliwy kolejny krok.
- Scenariusz „awaria sieci krajowej”: wyspa miasta zasilana przez elektrociepłownię i samoczynne
  częstotliwościowe odciążanie (SCO) – kolejny etap po tym projekcie.
- Inne źródła na 110 kV (np. Skawina, przemysł) – niemodelowane.

## Anty-cele (porażka mimo działania)

- Panel liczy się kilka minut: cel ≤ ok. 2 min przy pierwszym ładowaniu (9 węzłów × 4 strategie × 2 doby).
- Zepsute dotychczasowe funkcje (awaria, farma, animacje, raport, analizy, CLI).
- Fałszywa precyzja: założenia (tło, parametry linii, połączenia zastępcze) muszą być nazwane w panelu
  i w dokumentacji.

## Ograniczenia

- Python 3.14, uv; dane w `data/cache/` (praca offline); Overpass bywa przeciążony (retry, kilka serwerów).
- Interfejs i komentarze po polsku, styl kodu jak w projekcie.
- Wspólna symulacja nie może być zrównoleglona po węzłach – równolegle tylko strategie.

## Rozważone podejścia

**Topologia** (wybrał użytkownik: 1):
1. *Realna sieć*: węzły pod swoimi najbliższymi GPZ, prawdziwe linie 110 kV z OSM, stacje PSE jako sieć
   krajowa. Realistyczne; wymaga założenia o obciążeniu tła; wpływ węzłów na 110 kV będzie mały.
2. *Wszystkie węzły pod jednym GPZ, pierścień 15 kV*: wymiana między węzłami bardzo widoczna, ale
   geograficznie nieprawdziwa.

**Obliczenia rozpływu** (wybrał użytkownik: A):
- A. *pandapower* z obniżeniem pandas 3.1 → 2.3 i numpy 2.5 → 2.4: sprawdzona biblioteka i typy
  standardowe; ryzyko różnic pandas 2/3 wyłapują testy.
- B. *Własny Newton-Raphson*: bez zmian bibliotek, szybszy, ale wymaga własnej walidacji.
  Plan awaryjny, gdyby A okazało się niewykonalne.

**Sterowanie w sieci** (decyzja wykonawcy): lokalne EMS bez zmian + koordynator korekcyjny po rozpływie.
Alternatywy: sygnał cenowy koordynatora przed decyzjami (iteracje, wolniej) albo wspólny MPC (wariant C,
poza zakresem).

## Decyzje użytkownika

- Wspólna symulacja całej sieci z przepływami między węzłami, rozpływ mocy (wariant B z rozmowy).
- Realna sieć: prawdziwe GPZ i sieć wysokiego napięcia z danych, pokazane na mapie w aplikacji.
- pandapower z obniżeniem wersji pandas/numpy.
- Praca na osobnej gałęzi.

## Decyzje wykonawcy (do sprawdzenia przez użytkownika)

| decyzja | wartość | powód |
|---|---|---|
| obciążenie tła GPZ | 20 MW szczyt, kształt KSE (dane rzecz.) / profil syntetyczny, cos φ = 0,95 | ok. 35 GPZ × 20 MW ≈ 700 MW, rząd wielkości szczytu Krakowa |
| tło na linii 15 kV węzła | 2 MW szczyt (te same kształty), skupione w połowie długości | typowa linia SN zasila kilkanaście stacji |
| linie 110 kV | napowietrzne `243-AL1/39-ST1A 110.0`, kablowe `N2XS(FL)2Y 1x240 RM/35 64/110 kV`; tag `circuits` → linie równoległe | typy standardowe pandapower |
| linia 15 kV węzła | `NA2XS2Y 1x240 RM/25 12/20 kV`, długość = odległość × 1,3 | typowy kabel miejski, współczynnik trasy |
| transformator GPZ | 2 × 25 MVA 110/15 kV, uk = 11%, przekładnia 1,0 | typowa stacja miejska |
| transformator węzła | Sn = P_max / 0,95, 15/0,4 kV, uk = 6% | moc przyłącza z konfiguracji węzła |
| granice napięcia | 0,90–1,10 p.u. na nN (norma), ostrzeżenie > 1,05 | EN 50160; 1,10 p.u. = 253 V |
| GPZ węzła | najbliższa stacja 110/15 kV (z możliwością nadpisania w konfiguracji) | brak danych OSD |
| stacje bez połączenia w OSM | linia zastępcza do najbliższej połączonej stacji, oznaczona na mapie | luki w danych OSM |
| farma wiatrowa | przyłączona do najbliższego GPZ (Pasternik) na 15 kV | spójnie z wariantem „gpz” |
| rozpływ w czasie pełnego blackoutu | pomijany (brak źródła), wyspy jak dziś | brak sieci krajowej |
| farma w rozpływie | w obu wariantach wstrzykuje moc na szynie 15 kV GPZ Pasternik; warianty różnią się jak dziś (rozliczenie, wyspa) | fizycznie farma zawsze gdzieś oddaje moc |
| włączanie modelu sieci | `SimConfig.network` (domyślnie `False` w kodzie – dotychczasowe testy, Monte Carlo i dobór bez zmian); w panelu przełącznik „Sieć 110/15 kV (rozpływ mocy)” domyślnie włączony; CLI `--network` | czas obliczeń analiz wielokrotnych |
| wynik | dotychczasowe `Results` na (węzeł, strategia) bez zmian interfejsu + nowe `NetworkResults` na strategię (napięcia szyn, obciążenia linii i transformatorów, straty – szeregi czasowe) | reszta panelu działa bez przeróbek |
| MPC (D) | planuje lokalnie jak dziś, nie zna ograniczeń sieci; korekty robi koordynator | wspólny MPC poza zakresem |
| wybór GPZ w awarii | multiselect GPZ węzłów; domyślnie „wszystkie” = dotychczasowe zachowanie | zgodność wstecz |
| moc elektrociepłowni | maks. 380 MW (ok. 80% z 480 MWe zainstalowanej mocy wg danych z 2020 r.), min. 60 MW latem | trwa modernizacja (bloki gazowe ~100 MWe, docelowo ~150 MWe ok. 2034), więc dostępna moc jest niepewna – parametr w konfiguracji |
| moc ZTPO | 7,5 MW stale | ok. 65 GWh/rok energii elektrycznej ÷ 8760 h |
| tło w trybie syntetycznym | kształt `synthetic.base_load` bez szumu | spójność z resztą trybu |

## Pozostawione wykonawcy

Algorytm sklejania odcinków OSM w linie między stacjami (tolerancja dopasowania końców), budowa
i aktualizacja sieci pandapower w kolejnych krokach (zmiana tylko mocy, bez przebudowy), sposób oszacowania
korekty koordynatora, format cache, wygląd warstw mapy, nazwy KPI w duchu istniejących.

## Oczekiwany wynik (do zweryfikowania)

Przy obecnych mocach PV nadwyżka zmieści się w sieci: linie 110 kV prawie nie zauważą węzłów, a napięcia
na 15 kV zmienią się o ułamki procenta. Pierwszym ograniczeniem będzie transformator węzła, potem linia 15 kV.
Analiza zdolności przyłączeniowej pokaże, przy jakiej mocy PV to się zmienia.

## Źródła

- Moc zainstalowana EC Kraków 480 MWe / 1644 MWt: „Nowa Energia” 5–6/2020,
  https://yadda.icm.edu.pl/yadda/element/bwmeta1.element.baztech-9ad54270-dc2a-446c-8d07-314deeacd2c2/c/NE_5-6_2020_8-11.pdf
- Modernizacja EC Kraków (silniki gazowe ok. 100 MWe, docelowo ok. 150 MWe):
  https://www.parkiet.com/energetyka/art43447501-pge-zaczyna-od-gazu-w-krakowie-a-skonczy-na-hubie-ciepla-dzieki-elektryfikacji
- ZTPO ok. 65 GWh energii elektrycznej rocznie: https://www.cire.pl/pliki/2/2019/spalarnie_odpadow_komunalnych_w_polsce.pdf
- Stacje i linie WN: © współtwórcy OpenStreetMap (ODbL), pobrane przez Overpass API 2026-11-19.
