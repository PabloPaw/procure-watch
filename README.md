<div align="center">

# 🔍 ProcureWatch

### Offshore Network & Fraud Analytics

**Symulacja dochodzenia compliance na danych ICIJ Offshore Leaks — od surowych plików CSV, przez graf powiązań kapitałowych, po interaktywny pulpit analityka śledczego.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Pandas](https://img.shields.io/badge/pandas-3.0-150458?style=flat-square&logo=pandas&logoColor=white)](https://pandas.pydata.org/)
[![NetworkX](https://img.shields.io/badge/NetworkX-3.6-FF6F00?style=flat-square)](https://networkx.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.60-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Plotly](https://img.shields.io/badge/Plotly-6.5-3F4F75?style=flat-square&logo=plotly&logoColor=white)](https://plotly.com/python/)

</div>

---

## Spis treści

- [Cel projektu](#cel-projektu)
- [Źródło danych](#źródło-danych)
- [Metodologia śledcza](#metodologia-śledcza)
- [Przykładowe ustalenia](#przykładowe-ustalenia)
- [Stos technologiczny](#stos-technologiczny)
- [Struktura repozytorium](#struktura-repozytorium)
- [Jak uruchomić projekt lokalnie](#jak-uruchomić-projekt-lokalnie)
- [Format wyników](#format-wyników)
- [Ograniczenia i kierunki rozwoju](#ograniczenia-i-kierunki-rozwoju)
- [Zastrzeżenie prawne](#zastrzeżenie-prawne)

---

## Cel projektu

Zespoły compliance, AML i due diligence codziennie mierzą się z tym samym problemem: **pojedyncza transakcja wygląda niewinnie, dopiero sieć powiązań zdradza schemat**. Klasyczna analiza tabelaryczna tego nie pokaże — potrzebna jest perspektywa grafowa.

**ProcureWatch** odtwarza pełny cykl takiego dochodzenia na publicznym zbiorze **ICIJ Offshore Leaks**:

| Etap | Pytanie śledcze | Realizacja |
|---|---|---|
| 1. Pozyskanie | Jakimi danymi dysponujemy? | Wczytanie węzłów i relacji ICIJ (dwa warianty formatu) |
| 2. Oczyszczenie | Które rekordy są bezużyteczne dowodowo? | Reguły jakości zależne od typu podmiotu, deduplikacja, normalizacja |
| 3. Rekonstrukcja sieci | Kto z kim jest powiązany? | Graf skierowany podmiot ↔ adres / dyrektor / pośrednik |
| 4. Detekcja | Kto zachowuje się nietypowo? | Centralność stopnia + próg statystyczny w obrębie kategorii |
| 5. Raportowanie | Jak przekazać ustalenia? | Eksport CSV + dashboard Streamlit z filtrami |

Efektem końcowym jest **lista priorytetyzowanych tropów** (ang. *leads*) — węzłów, którym analityk powinien przyjrzeć się w pierwszej kolejności, wraz z liczbowym uzasadnieniem każdego oznaczenia.

---

## Źródło danych

Projekt korzysta z bazy [**ICIJ Offshore Leaks Database**](https://offshoreleaks.icij.org/pages/database) — skonsolidowanego zbioru z dochodzeń Panama Papers, Paradise Papers, Bahamas Leaks, Pandora Papers i Offshore Leaks.

Dane modelowane są jako graf o czterech typach węzłów:

| Typ węzła | Znaczenie śledcze |
|---|---|
| `entity` | Spółka offshore zarejestrowana w jurysdykcji o obniżonej transparentności |
| `officer` | Osoba lub podmiot pełniący funkcję (dyrektor, udziałowiec, beneficjent) |
| `intermediary` | Pośrednik — kancelaria lub agent zakładający spółki na zlecenie |
| `address` | Adres rejestracyjny podmiotu |

Analizowane są **trzy typy relacji** o największej wartości dowodowej:

```python
INVESTIGATIVE_REL_TYPES = {
    "registered_address",   # Entity      → Address   (gdzie spółka jest zarejestrowana)
    "officer_of",           # Officer     → Entity    (kto nią zarządza)
    "intermediary_of",      # Intermediary→ Entity    (kto ją założył)
}
```

> **Uwaga:** surowe pliki ICIJ (~600 MB) **nie są wersjonowane** w tym repozytorium — trzymane lokalnie w `data/raw/` i blokowane przez `.gitignore`. Instrukcja pobrania znajduje się w sekcji [Jak uruchomić projekt lokalnie](#jak-uruchomić-projekt-lokalnie).

---

## Metodologia śledcza

### 1. Kontrola jakości danych zależna od typu podmiotu

Uniwersalne `dropna()` niszczy dane śledcze — dla adresu brak pola `name` jest normą, dla dyrektora oznacza rekord bezwartościowy. Dlatego każdy typ węzła ma własny zestaw kolumn krytycznych:

```python
key_columns_by_type = {
    "entity":       ["name", "jurisdiction", "address", "countries"],
    "officer":      ["name", "countries"],
    "intermediary": ["name", "address", "countries"],
    "address":      ["address", "countries"],
}
```

Rekord zostaje odrzucony wyłącznie wtedy, gdy **wszystkie** kolumny krytyczne dla jego typu są puste. Następnie stosowana jest normalizacja tekstu i deduplikacja po `node_id`.

### 2. Rekonstrukcja sieci powiązań

Graf budowany jest w **NetworkX** jako `DiGraph` — kierunek krawędzi niesie informację o roli podmiotu w relacji. Przed dodaniem krawędzi odrzucane są te wskazujące na węzły usunięte w kroku czyszczenia (integralność referencyjna grafu):

```python
graph = nx.DiGraph()
graph.add_nodes_from(
    (row.node_id, {"node_type": row.node_type, "label": _node_label(row)})
    for row in nodes.itertuples(index=False)
)
graph.add_edges_from(
    (row.node_id_start, row.node_id_end, {"rel_type": row.rel_type})
    for row in edges.itertuples(index=False)
)
```

### 3. Centralność stopnia jako wskaźnik ryzyka

Kluczowa metryka to **liczba unikalnych spółek offshore powiązanych z jednym węzłem** — wariant *degree centrality* zawężony do krawędzi o określonej semantyce. Interpretacja zależy od typu węzła:

| Wysoka centralność węzła | Hipoteza śledcza |
|---|---|
| **Adres** | Skrzynka pocztowa / wirtualne biuro — setki spółek pod jednym fizycznym adresem |
| **Pośrednik** | „Fabryka spółek" — masowa, hurtowa rejestracja podmiotów |
| **Dyrektor** | Słup / nominee director — osoba figurująca we władzach dziesiątek niepowiązanych firm |

Kierunkowość grafu jest tu istotna: dla adresu liczone są **poprzedniki** (`Entity → Address`), dla dyrektora i pośrednika — **następniki** (`Officer → Entity`).

### 4. Próg anomalii liczony osobno dla każdej kategorii

Rozkłady liczby powiązań różnią się drastycznie między typami węzłów, więc jeden globalny próg generowałby fałszywe alarmy. Próg wyznaczany jest **per kategoria** metodą odchyleń standardowych:

$$\text{próg}_{\text{typ}} = \mu_{\text{typ}} + 2\sigma_{\text{typ}}$$

```python
ANOMALY_SIGMA = 2.0      # próg: średnia + 2 × odchylenie standardowe
MIN_ENTITY_LINKS = 5     # filtr szumu — poniżej 5 powiązań węzeł jest pomijany
```

Filtr `MIN_ENTITY_LINKS` eliminuje z rozkładu długi ogon węzłów o pojedynczych, przypadkowych powiązaniach, które sztucznie zaniżałyby średnią. Każde oznaczenie otrzymuje **czytelne uzasadnienie** — wymóg audytowalności w pracy compliance:

```text
Powiazany z 36334 spolkami (prog: 1215.9, srednia: 47.8)
```

---

## Przykładowe ustalenia

Czołowe tropy wygenerowane przez pipeline (fragment `data/processed_suspects.csv`):

| Podmiot | Typ | Jurysdykcja | Powiązanych spółek | Próg |
|---|---|---:|---:|---:|
| Portcullis TrustNet Chambers, P.O. Box 3444, Road Town, Tortola | Adres | Brytyjskie Wyspy Dziewicze | **36 334** | 1 215,9 |
| Morning Star Holdings Limited | Pośrednik | Saint Kitts i Nevis | **35 358** | 1 374,7 |
| Mossack Fonseca & Co. (Bahamas) Limited | Pośrednik | Bahamy | **14 901** | 1 374,7 |
| 3rd Floor, Yamraj Building, Market Square, Road Town, Tortola | Adres | Brytyjskie Wyspy Dziewicze | **11 962** | 1 215,9 |

Skala mówi sama za siebie: jedna skrzynka pocztowa na Tortoli jest adresem rejestracyjnym **ponad 36 tysięcy spółek** — blisko **trzydziestokrotność** progu anomalii dla swojej kategorii i ponad **750-krotność** średniej. Metoda niezależnie wskazała również kancelarię **Mossack Fonseca**, czyli podmiot znany z afery Panama Papers, co stanowi naturalną walidację sygnału.

---

## Stos technologiczny

| Technologia | Zastosowanie w projekcie |
|---|---|
| **Python 3.11+** | Język pipeline'u; adnotacje typów, `pathlib`, `from __future__ import annotations` |
| **pandas** | Wczytywanie wielomilionowych CSV, czyszczenie, `groupby`, wzbogacanie i eksport |
| **NetworkX** | Model grafowy `DiGraph`, przechodzenie po sąsiedztwie, analiza centralności |
| **NumPy** | Statystyka progowa i wektoryzowane warunki (`np.where`) |
| **Streamlit** | Pulpit analityka: metryki, filtry, tabela tropów, przełącznik motywu |
| **Plotly Express** | Interaktywna wizualizacja rozkładu geograficznego podejrzanych podmiotów |
| **streamlit-aggrid** | Interaktywna tabela tropów z filtrowaniem i sortowaniem w nagłówkach kolumn |

Kod podzielony jest na **warstwę analityczną** (`src/`) i **warstwę prezentacji** (`app/`). Pipeline jest w pełni skryptowalny i nie wymaga uruchomionego interfejsu, a dashboard czyta wyłącznie gotowy artefakt CSV — dzięki czemu start aplikacji jest natychmiastowy niezależnie od rozmiaru danych wejściowych.

---

## Struktura repozytorium

```text
procure-watch-fda/
├── app/
│   └── dashboard.py            # Pulpit Streamlit: metryki, tabela AgGrid, wykres Plotly
├── src/
│   ├── investigation.py        # Pipeline śledczy: ETL → graf → detekcja → eksport
│   ├── clean_data.py           # Stub: wstępne czyszczenie surowych CSV
│   └── analyze.py              # Stub: generyczne funkcje grafowe
├── data/
│   ├── raw/                    # [gitignored] Surowe pliki ICIJ (~600 MB)
│   │   ├── nodes-*.csv         #   Węzły: entities, officers, intermediaries, addresses
│   │   └── relationships.csv   #   Relacje między węzłami
│   └── processed_suspects.csv  # Artefakt wyjściowy: lista tropów (w repo)
├── .streamlit/
│   └── config.toml             # Motyw ciemny interfejsu Streamlit
├── .env.example                # Szablon konfiguracji środowiska (kopiuj jako .env)
├── .gitignore                  # Blokuje .venv/, data/raw/, surowe CSV, sekrety
├── requirements.txt            # Zależności z przypiętymi wersjami
└── README.md
```

Najważniejsze funkcje w `src/investigation.py`:

| Funkcja | Odpowiedzialność |
|---|---|
| `_resolve_data_files()` | Autodetekcja układu plików ICIJ w `data/raw/` (`nodes.csv` + `edges.csv` lub `nodes-*.csv` + `relationships.csv`) |
| `load_nodes()` / `clean_nodes()` | Scalenie plików węzłów, przypisanie `node_type`, kontrola jakości |
| `load_edges()` | Ujednolicenie nazw kolumn, filtr typów relacji, deduplikacja |
| `build_investigation_graph()` | Budowa `DiGraph` z zachowaniem integralności referencyjnej |
| `count_entity_links()` | Zliczenie unikalnych spółek na węzeł z uwzględnieniem kierunku krawędzi |
| `detect_anomalies()` | Progowanie μ + 2σ w obrębie kategorii i generowanie uzasadnień |
| `enrich_suspects()` / `save_results()` | Dołączenie metadanych, sortowanie po istotności, eksport CSV |

---

## Jak uruchomić projekt lokalnie

### Wymagania wstępne

- **Python 3.11 lub nowszy**
- **~4 GB wolnej pamięci RAM** (pełny zbiór ICIJ to kilka milionów krawędzi)
- **~2 GB miejsca na dysku** na dane surowe

### Krok 1 — Sklonuj repozytorium

```bash
git clone https://github.com/<twoj-uzytkownik>/procure-watch-fda.git
cd procure-watch-fda
```

### Krok 2 — Utwórz i aktywuj środowisko wirtualne

```bash
python -m venv .venv
```

**Windows (PowerShell):**

```powershell
.\.venv\Scripts\Activate.ps1
```

**Linux / macOS:**

```bash
source .venv/bin/activate
```

### Krok 3 — Zainstaluj zależności

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Krok 4 — Pobierz dane ICIJ

Pobierz pełny zrzut bazy ze strony [offshoreleaks.icij.org/pages/database](https://offshoreleaks.icij.org/pages/database) i rozpakuj pliki CSV do katalogu **`data/raw/`**. Pipeline obsługuje oba spotykane układy plików:

```text
data/raw/
├── nodes-entities.csv          # wariant A: pliki rozdzielone
├── nodes-officers.csv
├── nodes-intermediaries.csv
├── nodes-addresses.csv
└── relationships.csv
```

```text
data/raw/
├── nodes.csv                   # wariant B: pliki skonsolidowane
└── edges.csv
```

> Katalog `data/raw/` jest ignorowany przez git — surowe dane pozostają wyłącznie na Twoim dysku lokalnym.

### Krok 4a — Konfiguracja środowiska (opcjonalnie)

Jeśli planujesz rozszerzyć projekt o zewnętrzną bazę danych lub niestandardowy port Streamlit, skopiuj szablon konfiguracji:

```bash
cp .env.example .env
```

Plik `.env` jest ignorowany przez git i nigdy nie powinien trafić do repozytorium.

### Krok 5 — Uruchom pipeline śledczy

```bash
python src/investigation.py
```

W konsoli pojawi się przebieg analizy, a wyniki trafią do `data/processed_suspects.csv`:

```text
Wczytywanie węzłów z: ['nodes-addresses.csv', 'nodes-entities.csv', ...]
Wczytywanie krawędzi z: relationships.csv
Węzły po oczyszczeniu: ...
Krawędzie śledcze: ...
Graf: ... węzłów, ... krawędzi
Wykryto ... anomalii (prog: srednia + 2.0 * odchylenie)
Zapisano wyniki: .../data/processed_suspects.csv
```

> **Skrót:** repozytorium zawiera gotowy plik `data/processed_suspects.csv`, więc aby wyłącznie obejrzeć dashboard, możesz pominąć kroki 4–5 i przejść od razu do kroku 6.

### Krok 6 — Uruchom pulpit analityka

```bash
streamlit run app/dashboard.py
```

Aplikacja otworzy się w przeglądarce pod adresem **http://localhost:8501**.

Na wybranym porcie:

```bash
streamlit run app/dashboard.py --server.port 8502
```

### Funkcje dashboardu

- **Metryki nagłówkowe** — liczba przeanalizowanych podmiotów, wykrytych anomalii i reprezentowanych krajów
- **Tabela tropów (AgGrid)** — filtrowanie i sortowanie w nagłówkach kolumn, paginacja (20 wierszy), uzasadnienie każdego oznaczenia
- **Ranking geograficzny** — interaktywny wykres Plotly: Top 10 krajów według liczby anomalii
- **Motyw ciemny** — skonfigurowany w `.streamlit/config.toml`

---

## Format wyników

Plik `data/processed_suspects.csv` zawiera następujące kolumny:

| Kolumna | Opis |
|---|---|
| `node_id` | Identyfikator węzła w bazie ICIJ |
| `suspect_type` | Kategoria: `address`, `officer` lub `intermediary` |
| `label` | Etykieta prezentacyjna (nazwa lub adres) |
| `name`, `address`, `countries`, `country_codes`, `jurisdiction` | Metadane dołączone z tabeli węzłów |
| `linked_entity_count` | **Metryka wiodąca** — liczba unikalnych powiązanych spółek |
| `rel_type` | Typ relacji, na której oparto zliczenie |
| `threshold` | Próg anomalii wyliczony dla danej kategorii |
| `is_anomaly` | Flaga przekroczenia progu |
| `anomaly_reason` | Uzasadnienie w formie czytelnej dla człowieka |

---

## Ograniczenia i kierunki rozwoju

**Świadome ograniczenia obecnej wersji:**

- Metoda opiera się na **centralności stopnia**; nie wykrywa schematów wymagających analizy ścieżek, takich jak struktury karuzelowe czy warstwowe łańcuchy własnościowe.
- Próg μ + 2σ zakłada rozkład zbliżony do normalnego, podczas gdy rozkłady w sieciach rzeczywistych są **silnie skośne** (*power law*) — próg bywa przez to konserwatywny.
- Brak **rozstrzygania tożsamości** (*entity resolution*): ten sam podmiot zapisany w dwóch wariantach nazwy liczony jest jako dwa węzły.

**Planowane rozszerzenia:**

- [ ] Betweenness i eigenvector centrality — wykrywanie ukrytych brokerów w sieci
- [ ] Wykrywanie społeczności (algorytm Louvaina) — identyfikacja klastrów powiązanych struktur
- [ ] Interaktywna wizualizacja ego-grafu wybranego podmiotu w dashboardzie
- [ ] Fuzzy matching nazw podmiotów (`rapidfuzz`) jako warstwa entity resolution
- [ ] Persystencja grafu w bazie grafowej i zapytania po ścieżkach
- [ ] Testy jednostkowe `pytest` dla logiki progowania i przechodzenia po grafie

---

## Zastrzeżenie prawne

> Projekt ma charakter **edukacyjno-portfoliowy** i stanowi symulację metodyki analitycznej.
>
> Zgodnie ze stanowiskiem ICIJ: **obecność osoby lub podmiotu w bazie Offshore Leaks nie stanowi dowodu popełnienia jakiegokolwiek przestępstwa ani naruszenia prawa**. Korzystanie ze spółek offshore jest w wielu przypadkach całkowicie legalne.
>
> Oznaczenia generowane przez ProcureWatch to **statystyczne sygnały wymagające weryfikacji**, a nie ustalenia faktyczne. Nie należy ich traktować jako podstawy do formułowania oskarżeń ani podejmowania decyzji biznesowych.

---

<div align="center">

**Zbudowane z myślą o inżynierii danych i analityce śledczej.**

Dane: [ICIJ Offshore Leaks Database](https://offshoreleaks.icij.org/) · Licencja kodu: MIT

</div>
