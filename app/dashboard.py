"""
Pulpit Analityka Śledczego — ProcureWatch.

Aplikacja Streamlit do przeglądania wyników analizy danych ICIJ Offshore Leaks.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_ROOT / "data" / "processed_suspects.csv"

DISPLAY_COLUMNS = [
    "name",
    "suspect_type",
    "countries",
    "linked_entity_count",
    "rel_type",
    "is_anomaly",
    "anomaly_reason",
]

COLUMN_LABELS = {
    "name": "Nazwa podmiotu",
    "suspect_type": "Typ",
    "countries": "Kraj",
    "linked_entity_count": "Liczba powiązań",
    "rel_type": "Typ relacji",
    "is_anomaly": "Anomalia",
    "anomaly_reason": "Uzasadnienie",
}


@st.cache_data
def load_suspects() -> pd.DataFrame:
    """
    Wczytaj raport podejrzanych węzłów wygenerowany przez pipeline śledczy.

    Analityk pracuje na gotowym wyniku analizy — nie na surowych plikach ICIJ,
    aby uniknąć przypadkowego ujawnienia pełnej bazy w interfejsie.
    """
    if not DATA_PATH.exists():
        st.error(f"Brak pliku danych: `{DATA_PATH}`")
        st.stop()

    suspects_df = pd.read_csv(DATA_PATH)
    suspects_df["linked_entity_count"] = pd.to_numeric(
        suspects_df["linked_entity_count"], errors="coerce"
    )
    suspects_df["is_anomaly"] = suspects_df["is_anomaly"].astype(bool)
    return suspects_df


def extract_countries(country_field: pd.Series) -> pd.Series:
    """
    Rozbij wielokrotne jurysdykcje (np. 'PL;DE') na pojedyncze wpisy.

    W danych ICIJ jeden podmiot może być powiązany z wieloma krajami —
    rozdzielenie pozwala na analizę geograficzną koncentracji anomalii.
    """
    return (
        country_field.fillna("Nieznany")
        .astype(str)
        .str.split(";")
        .explode()
        .str.strip()
        .replace("", "Nieznany")
    )


def format_number(value: int) -> str:
    """Formatuj liczbę całkowitą z separatorem tysięcy (PL)."""
    return f"{value:,}".replace(",", " ")


def build_grid_options(display_table: pd.DataFrame) -> dict:
    """
    Skonfiguruj interaktywną tabelę AgGrid do pracy śledczej.

    Filtrowanie i sortowanie w nagłówkach kolumn umożliwia analitykowi
    szybkie zawężenie listy tropów bez ponownego uruchamiania pipeline'u.
    """
    builder = GridOptionsBuilder.from_dataframe(display_table)
    builder.configure_default_column(
        filterable=True,
        sortable=True,
        resizable=True,
        wrapText=True,
        autoHeight=False,
    )
    builder.configure_pagination(paginationPageSize=20, paginationAutoPageSize=False)
    builder.configure_column(
        "Liczba powiązań",
        type=["numericColumn", "numberColumnFilter"],
        width=160,
    )
    builder.configure_column("Nazwa podmiotu", width=280)
    builder.configure_column("Uzasadnienie", flex=1, minWidth=320)
    builder.configure_column(
        "Anomalia",
        filter="agSetColumnFilter",
        width=120,
    )
    return builder.build()


def build_anomaly_chart(suspects_df: pd.DataFrame) -> px.bar:
    """
    Zbuduj wykres geograficzny koncentracji anomalii (Top 10 krajów).

    Koncentracja w jednej jurysdykcji może wskazywać na lokalną fabrykę
    spółek-słupów lub wspólną skrzynkę pocztową rejestracyjną.
    """
    flagged_anomalies = suspects_df.loc[suspects_df["is_anomaly"]].copy()
    country_anomaly_counts = (
        extract_countries(flagged_anomalies["countries"])
        .value_counts()
        .head(10)
        .reset_index()
    )
    country_anomaly_counts.columns = ["Kraj", "Liczba anomalii"]
    country_anomaly_counts = country_anomaly_counts.sort_values(
        "Liczba anomalii", ascending=True
    )

    chart = px.bar(
        country_anomaly_counts,
        x="Liczba anomalii",
        y="Kraj",
        orientation="h",
        color="Liczba anomalii",
        color_continuous_scale="Blues",
        text="Liczba anomalii",
        labels={"Liczba anomalii": "Liczba anomalii", "Kraj": "Kraj"},
        title="Top 10 krajów — liczba anomalii",
    )
    chart.update_layout(
        height=450,
        margin=dict(l=10, r=10, t=50, b=10),
        coloraxis_showscale=False,
        xaxis_title="Liczba anomalii",
        yaxis_title="",
    )
    chart.update_traces(texttemplate="%{text:,}", textposition="outside")
    return chart


def render_summary_metrics(suspects_df: pd.DataFrame) -> None:
    """
    Wyświetl kluczowe wskaźniki dochodzenia w panelu metryk.

    Trzy liczby dają analitykowi szybki obraz skali: ile podmiotów
    przeszło filtr, ile wymaga priorytetowej weryfikacji i jaki zasięg
    geograficzny ma zbiór.
    """
    anomaly_count = int(suspects_df["is_anomaly"].sum())
    unique_country_count = extract_countries(suspects_df["countries"]).nunique()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Przeanalizowane podmioty", format_number(len(suspects_df)))
    with col2:
        st.metric("Wykryte anomalie", format_number(anomaly_count))
    with col3:
        st.metric("Kraje w zbiorze", format_number(unique_country_count))


def render_suspects_table(suspects_df: pd.DataFrame) -> None:
    """Wyświetl tabelę priorytetyzowanych tropów z paginacją."""
    st.subheader("Tabela podejrzanych podmiotów")
    st.caption(
        f"Sortowanie, filtrowanie w nagłówkach kolumn i paginacja (20 wierszy na stronę). "
        f"Źródło: `{DATA_PATH.name}`"
    )

    display_table = suspects_df[DISPLAY_COLUMNS].rename(columns=COLUMN_LABELS)
    AgGrid(
        display_table,
        gridOptions=build_grid_options(display_table),
        update_mode=GridUpdateMode.NO_UPDATE,
        height=480,
        theme="streamlit",
        fit_columns_on_grid_load=True,
        allow_unsafe_jscode=False,
    )


def main() -> None:
    """Uruchom pulpit analityka śledczego ProcureWatch."""
    st.set_page_config(
        page_title="ProcureWatch — Pulpit Analityka",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.title("ProcureWatch")
    st.subheader("Pulpit Analityka Śledczego")
    st.write(
        "Narzędzie do analizy powiązań podmiotów offshore na podstawie danych "
        "**ICIJ Offshore Leaks**. System identyfikuje adresy rejestracyjne, pośredników "
        "i dyrektorów o nietypowo wysokiej liczbie powiązań ze spółkami — wskaźnik "
        "potencjalnych skrzynek pocztowych lub masowego tworzenia spółek-słupów."
    )

    suspects_df = load_suspects()
    render_summary_metrics(suspects_df)

    st.divider()

    render_suspects_table(suspects_df)

    st.subheader("Analiza geograficzna anomalii")
    st.plotly_chart(build_anomaly_chart(suspects_df), use_container_width=True)


if __name__ == "__main__":
    main()
