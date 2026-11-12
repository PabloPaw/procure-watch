"""
Pulpit Analityka Śledczego — ProcureWatch.

Aplikacja Streamlit do przeglądania wyników analizy danych ICIJ Offshore Leaks.
Metryki liczą cały plik. Tabela i wykresy używają widoku z filtrów panelu bocznego.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from st_aggrid import (
    AgGrid,
    DataReturnMode,
    GridOptionsBuilder,
    GridUpdateMode,
    StAggridTheme,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_ROOT / "data" / "processed_suspects.csv"

REQUIRED_COLUMNS = (
    "node_id",
    "suspect_type",
    "label",
    "countries",
    "linked_entity_count",
    "threshold",
    "is_anomaly",
    "anomaly_reason",
)

TYPE_LABELS = {
    "officer": "osoba",
    "intermediary": "pośrednik",
    "address": "adres",
}
TYPE_CODES = {label: code for code, label in TYPE_LABELS.items()}
TYPE_ORDER = ("officer", "intermediary", "address")
TYPE_FILTER_OPTIONS = ("Wszystkie", "osoba", "pośrednik", "adres")
LINK_PRESETS = (5, 50, 100, 1000)
MIN_LINKS_DEFAULT = 5
UNKNOWN_COUNTRY = "nieznany"

CHARCOAL = "#14171C"
SURFACE = "#232830"
INK = "#E6E4DE"
ACCENT = "#6E8EAE"
LINE = "#3A424E"
ROW_ALT = "#1C2128"
ROW_HOVER = "#2C3642"

_ANOMALY_TRUE = frozenset({"true", "1", "yes", "t", "y"})
_ANOMALY_FALSE = frozenset({"false", "0", "no", "f", "n"})


class SuspectDataError(Exception):
    """Dane tropów nie dają się bezpiecznie odczytać do pulpitu."""


def format_number(value: int) -> str:
    """Formatuj liczbę całkowitą ze spacją jako separatorem tysięcy."""
    return f"{int(value):,}".replace(",", " ")


def format_threshold(value: float) -> str:
    """Formatuj próg z jedną cyfrą po kropce i spacją między tysiącami."""
    return f"{float(value):,.1f}".replace(",", " ")


def format_share(numerator: int, denominator: int) -> str:
    """Udział sygnałów w liście. Pusty mianownik nie jest dzielony."""
    if denominator <= 0:
        return "—"
    rendered = f"{(numerator / denominator) * 100:.2f}".replace(".", ",")
    return f"{rendered}%"


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return False
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(missing) if isinstance(missing, (bool, np.bool_)) else False


def _map_anomaly_token(value: object) -> bool | None:
    """Zmapuj jeden token na prawdę albo fałsz. Nieznany token zostaje pusty."""
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if _is_missing(value):
        return None
    if isinstance(value, (int, np.integer)):
        if int(value) == 1:
            return True
        if int(value) == 0:
            return False
        return None
    if isinstance(value, (float, np.floating)):
        if float(value) == 1.0:
            return True
        if float(value) == 0.0:
            return False
        return None
    token = str(value).strip().lower()
    if token in _ANOMALY_TRUE:
        return True
    if token in _ANOMALY_FALSE:
        return False
    return None


def parse_is_anomaly(series: pd.Series) -> pd.Series:
    """
    Odczytaj flagę z jawnej mapy prawda/fałsz.

    Samo astype(bool) zamieniłoby niepusty napis „False” na True.
    """
    mapped = series.map(_map_anomaly_token)
    unknown = mapped.isna()
    if bool(unknown.any()):
        samples = [str(item) for item in series.loc[unknown].drop_duplicates().head(3)]
        joined = ", ".join(samples)
        raise SuspectDataError(
            "Kolumna is_anomaly zawiera wartości spoza mapy prawda/fałsz "
            f"(na przykład: {joined})."
        )
    return mapped.astype(bool)


def _country_parts(value: object) -> list[str]:
    if _is_missing(value):
        return []
    parts: list[str] = []
    for part in str(value).split(";"):
        token = part.strip()
        if token and token.casefold() != UNKNOWN_COUNTRY:
            parts.append(token)
    return parts


def primary_country(value: object) -> str | None:
    """Pierwszy niepusty kraj poza zastępczym „Nieznany”."""
    parts = _country_parts(value)
    if not parts:
        return None
    return parts[0]


def count_known_countries(countries: pd.Series) -> int:
    """Liczba unikalnych krajów w pliku, bez pustych i bez „Nieznany”."""
    seen: set[str] = set()
    for value in countries:
        for token in _country_parts(value):
            seen.add(token.casefold())
    return len(seen)


def prepare_suspects(frame: pd.DataFrame) -> pd.DataFrame:
    """Uporządkuj typy kolumn bez zmiany reguły, która te tropy wyznaczyła."""
    prepared = frame.copy()
    prepared["linked_entity_count"] = pd.to_numeric(
        prepared["linked_entity_count"], errors="coerce"
    )
    prepared["threshold"] = pd.to_numeric(prepared["threshold"], errors="coerce")
    prepared["is_anomaly"] = parse_is_anomaly(prepared["is_anomaly"])
    prepared["label"] = prepared["label"].fillna("").astype(str)
    prepared["suspect_type"] = prepared["suspect_type"].fillna("").astype(str)
    prepared["countries"] = prepared["countries"].fillna("").astype(str)
    prepared["anomaly_reason"] = prepared["anomaly_reason"].fillna("").astype(str)
    prepared["type_label"] = (
        prepared["suspect_type"].map(TYPE_LABELS).fillna(prepared["suspect_type"])
    )
    return prepared


@st.cache_data(show_spinner=False)
def read_suspects_csv(csv_path: str, file_mtime_ns: int, file_size: int) -> pd.DataFrame:
    """
    Wczytaj CSV tropów.

    Klucz cache to ścieżka, czas modyfikacji i rozmiar pliku, więc nowy eksport
    odświeża pulpit bez restartu procesu. Funkcja nie woła widgetów Streamlit.
    """
    del file_mtime_ns, file_size
    return pd.read_csv(csv_path)


def load_dashboard_frame() -> pd.DataFrame:
    """Wczytaj tropy albo zatrzymaj pulpit czytelnym komunikatem poza cache."""
    if not DATA_PATH.is_file():
        st.error(
            "Brak pliku danych data/processed_suspects.csv. "
            "Uruchom pipeline albo umieść gotowy eksport w katalogu data."
        )
        st.stop()

    file_stat = DATA_PATH.stat()
    try:
        raw = read_suspects_csv(
            str(DATA_PATH.resolve()),
            file_stat.st_mtime_ns,
            file_stat.st_size,
        )
    except (OSError, pd.errors.ParserError, UnicodeError, ValueError):
        st.error("Nie udało się odczytać data/processed_suspects.csv.")
        st.stop()

    missing = [column for column in REQUIRED_COLUMNS if column not in raw.columns]
    if missing:
        st.error("W pliku danych brakuje kolumn: " + ", ".join(missing) + ".")
        st.stop()

    try:
        return prepare_suspects(raw)
    except SuspectDataError as error:
        st.error(str(error))
        st.stop()


def _set_min_links(value: int) -> None:
    st.session_state["min_links"] = int(value)


def show_label(label: str) -> None:
    """Pokaż etykietę z CSV jako tekst, bez składania z niej HTML."""
    escaped = (
        label.replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
        .replace("[", "\\[")
        .replace("$", "\\$")
    )
    st.caption(escaped)


def render_sidebar_filters() -> tuple[str, bool, int]:
    """Filtry kanoniczne. Filtry nagłówka tabeli zostają lokalne dla AgGrid."""
    st.sidebar.header("Filtry")
    st.sidebar.caption(
        "Filtry w nagłówkach tabeli zawężają tylko tabelę. "
        "Wykresy używają filtrów z tego panelu."
    )
    type_choice = st.sidebar.selectbox(
        "Typ",
        options=list(TYPE_FILTER_OPTIONS),
        key="type_filter",
    )
    signals_only = st.sidebar.checkbox(
        "Tylko sygnały powyżej progu",
        value=True,
        key="signals_only",
    )
    st.sidebar.caption("Minimalna liczba powiązań")
    for row_presets in (LINK_PRESETS[:2], LINK_PRESETS[2:]):
        preset_columns = st.sidebar.columns(2)
        for column, preset in zip(preset_columns, row_presets, strict=True):
            column.button(
                str(preset),
                key=f"min-links-{preset}",
                on_click=_set_min_links,
                args=(preset,),
                width="stretch",
            )
    min_links = st.sidebar.number_input(
        "Minimalna liczba powiązań",
        min_value=0,
        step=1,
        key="min_links",
        label_visibility="collapsed",
    )
    return str(type_choice), bool(signals_only), int(min_links)


def apply_filters(
    frame: pd.DataFrame,
    *,
    type_label: str,
    signals_only: bool,
    min_links: int,
) -> pd.DataFrame:
    """Zawęź widok tabeli i wykresów. Nie liczy metryk całego pliku."""
    view = frame
    code = TYPE_CODES.get(type_label)
    if code is not None:
        view = view.loc[view["suspect_type"].eq(code)]
    if signals_only:
        view = view.loc[view["is_anomaly"]]
    view = view.loc[view["linked_entity_count"].fillna(-1).ge(int(min_links))]
    return view


def largest_lead(frame: pd.DataFrame) -> tuple[str, int] | None:
    """Etykieta i liczba powiązań największego tropu. Pusta ramka nie używa max()."""
    valid = frame.loc[frame["linked_entity_count"].notna()]
    if valid.empty:
        return None
    top = valid.sort_values(
        ["linked_entity_count", "node_id"],
        ascending=[False, True],
    ).iloc[0]
    label = str(top["label"]).strip() or "—"
    return label, int(top["linked_entity_count"])


def render_summary(full_frame: pd.DataFrame, view_count: int) -> None:
    """Metryki całego pliku oraz osobna linia z liczbą wierszy w widoku."""
    lead_count = len(full_frame)
    signal_count = int(full_frame["is_anomaly"].sum()) if lead_count else 0
    country_count = count_known_countries(full_frame["countries"]) if lead_count else 0
    lead = largest_lead(full_frame)

    st.caption("Sygnał statystyczny, nie ustalenie nieprawidłowości.")

    first_row = st.columns(3, gap="medium")
    first_row[0].metric(
        "Tropy z co najmniej 5 powiązaniami",
        format_number(lead_count),
        border=True,
        help="Lista w pliku jest już ograniczona do węzłów z co najmniej 5 spółkami.",
    )
    first_row[1].metric(
        "Sygnały powyżej progu",
        format_number(signal_count),
        border=True,
        help="Węzły, których liczba powiązań przekracza próg własnej kategorii.",
    )
    first_row[2].metric(
        "Udział w tej liście",
        format_share(signal_count, lead_count),
        border=True,
        help="Udział sygnałów w liście tropów z co najmniej 5 powiązaniami.",
    )

    second_row = st.columns(2, gap="medium")
    second_row[0].metric(
        "Kraje",
        format_number(country_count),
        border=True,
        help="Unikalne kraje w całym pliku, bez pustych wartości i bez „Nieznany”.",
    )
    with second_row[1]:
        with st.container(border=True):
            if lead is None:
                st.metric("Największy trop", "—", border=False)
            else:
                label, links = lead
                st.metric(
                    "Największy trop",
                    format_number(links),
                    border=False,
                    help="Liczba powiązanych spółek największego tropu w całym pliku.",
                )
                show_label(label)

    st.caption(f"w widoku: {format_number(view_count)}")


def _format_node_ids(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    as_text = numeric.round(0).astype("Int64").astype("string")
    original = series.astype("string").str.strip()
    return as_text.where(numeric.notna(), original).fillna("")


def build_display_table(view: pd.DataFrame) -> pd.DataFrame:
    """Kolumny pulpitu. Etykieta pochodzi z label, nie z często pustego name."""
    ordered = view.sort_values(
        ["linked_entity_count", "node_id"],
        ascending=[False, True],
        na_position="last",
    )
    labels = ordered["label"].str.strip()
    thresholds = ordered["threshold"].round(1)
    links = ordered["linked_entity_count"]
    whole_links = links.notna().all() and bool((links.dropna() == links.dropna().round(0)).all())
    if whole_links:
        links = links.astype("int64")
    return pd.DataFrame(
        {
            "Trop": labels.where(labels.ne(""), "—"),
            "Identyfikator": _format_node_ids(ordered["node_id"]),
            "Typ": ordered["type_label"],
            "Próg": thresholds,
            "Liczba powiązań": links,
            "Uzasadnienie": ordered["anomaly_reason"],
        }
    )


def build_grid_options(display_table: pd.DataFrame) -> dict:
    """AgGrid Community: filter=True, bez filtra zestawu Enterprise."""
    builder = GridOptionsBuilder.from_dataframe(display_table)
    builder.configure_default_column(
        filter=True,
        sortable=True,
        resizable=True,
        wrapText=False,
        autoHeight=False,
        wrapHeaderText=True,
        autoHeaderHeight=True,
    )
    builder.configure_pagination(
        enabled=True,
        paginationAutoPageSize=False,
        paginationPageSize=20,
    )
    builder.configure_column("Trop", flex=2, minWidth=240)
    builder.configure_column("Identyfikator", minWidth=140, width=150)
    builder.configure_column("Typ", minWidth=120, width=130)
    builder.configure_column("Próg", minWidth=110, width=120)
    builder.configure_column("Liczba powiązań", minWidth=160, width=170, sort="desc")
    builder.configure_column("Uzasadnienie", flex=1, minWidth=240)
    options = builder.build()
    # fitGridWidth odrzuca flex i ścina nagłówki. Szerokość biorą minWidth i flex.
    options.pop("autoSizeStrategy", None)
    return options


def grid_theme() -> StAggridTheme:
    """Motyw siatki przez API AgGrid, bez CSS wstrzykiwanego do iframe."""
    return (
        StAggridTheme(base="quartz")
        .withParams(
            backgroundColor=SURFACE,
            foregroundColor=INK,
            accentColor=ACCENT,
            borderColor=LINE,
            browserColorScheme="dark",
            chromeBackgroundColor="#2A313A",
            headerBackgroundColor="#2A313A",
            headerTextColor=INK,
            cellTextColor=INK,
            oddRowBackgroundColor=ROW_ALT,
            rowHoverColor=ROW_HOVER,
            fontFamily="sans-serif",
            fontSize=13,
            borderRadius=6,
            wrapperBorderRadius=8,
            spacing=7,
        )
    )


def render_table(view: pd.DataFrame) -> None:
    """Tabela tropów. Pusty widok nie buduje siatki."""
    st.subheader("Tabela tropów")
    st.caption(
        "Sortowanie, filtry nagłówków i paginacja (20 wierszy) dotyczą tylko tabeli. "
        f"Źródło: {DATA_PATH.name}"
    )
    if view.empty:
        st.info("Brak tropów dla wybranych filtrów.")
        return

    display_table = build_display_table(view)
    AgGrid(
        display_table,
        gridOptions=build_grid_options(display_table),
        update_mode=GridUpdateMode.NO_UPDATE,
        update_on=[],
        data_return_mode=DataReturnMode.AS_INPUT,
        height=560,
        theme=grid_theme(),
        allow_unsafe_jscode=False,
        enable_enterprise_modules=False,
        server_sync_strategy="server_wins",
        show_toolbar=False,
        show_search=False,
        key="leads-grid",
    )


def style_bar_chart(figure: go.Figure, *, height: int) -> None:
    """Przezroczyste tło, etykiety wewnątrz słupka, spacja jako separator tysięcy."""
    figure.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=INK, family="sans-serif", size=13),
        separators=". ",
        margin=dict(l=8, r=16, t=8, b=8),
        bargap=0.28,
        hoverlabel=dict(bgcolor=SURFACE, font_color=INK, bordercolor=LINE),
    )
    figure.update_traces(
        marker_color=ACCENT,
        marker_line_width=0,
        textposition="inside",
        insidetextanchor="start",
        constraintext="none",
        textfont=dict(color=INK, size=12),
        cliponaxis=False,
    )
    figure.update_xaxes(
        title="",
        showgrid=False,
        zeroline=False,
        tickformat=",d",
        tickfont=dict(color=INK),
    )
    figure.update_yaxes(title="", automargin=True, tickfont=dict(color=INK))


def render_country_chart(view: pd.DataFrame) -> None:
    """Jeden sygnał, jeden kraj: pierwszy niepusty, bez „Nieznany”."""
    st.subheader("Sygnały według kraju")
    st.caption(
        "Każdy sygnał liczy się raz, z pierwszym niepustym krajem. "
        "Pusty kraj i „Nieznany” są pominięte. To nie jest suma powiązań."
    )
    if view.empty:
        st.info("Brak tropów dla wybranych filtrów.")
        return

    signals = view.loc[view["is_anomaly"]]
    if signals.empty:
        st.info("Brak sygnałów w tym widoku.")
        return

    countries = signals["countries"].map(primary_country).dropna()
    if countries.empty:
        st.info("Brak sygnałów z przypisanym krajem w tym widoku.")
        return

    counts = (
        countries.value_counts()
        .head(10)
        .rename_axis("Kraj")
        .reset_index(name="Sygnały")
        .sort_values("Sygnały", ascending=True)
    )
    counts["Etykieta"] = counts["Sygnały"].map(lambda count: format_number(int(count)))
    figure = px.bar(
        counts,
        x="Sygnały",
        y="Kraj",
        orientation="h",
        text="Etykieta",
    )
    style_bar_chart(figure, height=420)
    st.plotly_chart(figure, width="stretch", theme=None)


def render_type_chart(view: pd.DataFrame) -> None:
    """Liczba sygnałów oraz próg kategorii, żeby wysokość słupka nie udawała skali ryzyka."""
    st.subheader("Sygnały według typu")
    st.caption(
        "Długość słupka to liczba sygnałów w kategorii, nie liczba spółek. "
        "Etykieta podaje próg tej kategorii."
    )
    if view.empty:
        st.info("Brak tropów dla wybranych filtrów.")
        return

    signals = view.loc[view["is_anomaly"]]
    if signals.empty:
        st.info("Brak sygnałów w tym widoku.")
        return

    rows: list[dict[str, object]] = []
    for code in TYPE_ORDER:
        subset = signals.loc[signals["suspect_type"].eq(code)]
        if subset.empty:
            continue
        thresholds = subset["threshold"].dropna()
        threshold_text = (
            format_threshold(float(thresholds.median())) if not thresholds.empty else "—"
        )
        count = int(len(subset))
        rows.append(
            {
                "Typ": TYPE_LABELS.get(code, code),
                "Sygnały": count,
                "Etykieta": f"{format_number(count)}  ·  próg {threshold_text}",
            }
        )
    if not rows:
        st.info("Brak sygnałów w tym widoku.")
        return

    summary = pd.DataFrame(rows).sort_values("Sygnały", ascending=True)
    figure = px.bar(
        summary,
        x="Sygnały",
        y="Typ",
        orientation="h",
        text="Etykieta",
    )
    style_bar_chart(figure, height=280)
    st.plotly_chart(figure, width="stretch", theme=None)


def main() -> None:
    """Uruchom pulpit analityka śledczego ProcureWatch."""
    st.set_page_config(
        page_title="ProcureWatch — Pulpit Analityka",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if "min_links" not in st.session_state:
        st.session_state["min_links"] = MIN_LINKS_DEFAULT

    st.title("ProcureWatch")
    st.subheader("Pulpit Analityka Śledczego")
    st.write(
        "Tropy z danych ICIJ Offshore Leaks: adresy, pośrednicy i osoby "
        "z wysoką liczbą powiązań ze spółkami offshore."
    )

    full_frame = load_dashboard_frame()
    type_choice, signals_only, min_links = render_sidebar_filters()
    view = apply_filters(
        full_frame,
        type_label=type_choice,
        signals_only=signals_only,
        min_links=min_links,
    )

    render_summary(full_frame, len(view))
    st.divider()
    render_table(view)
    st.divider()
    render_country_chart(view)
    render_type_chart(view)


if __name__ == "__main__":
    main()
