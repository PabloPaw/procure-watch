"""
Analiza śledcza danych ICIJ Offshore Leaks.

Wczytuje węzły i relacje z katalogu data/raw/, buduje graf powiązań między
spółkami offshore (Entities) a adresami, dyrektorami (Officers) oraz
pośrednikami (Intermediaries), a następnie wykrywa anomalie wskazujące
na wspólne skrzynki pocztowe lub masowe tworzenie spółek-słupów.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

# Ścieżki względem katalogu projektu
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
OUTPUT_PATH = DATA_DIR / "processed_suspects.csv"

# Typy relacji istotne dla analizy powiązań podmiot–adres/osoba/pośrednik
INVESTIGATIVE_REL_TYPES = {
    "registered_address",
    "officer_of",
    "intermediary_of",
}

# Próg statystyczny: węzły powyżej średniej + N * odchylenie standardowe
# uznajemy za podejrzane (heurystyka typowa w analizie sieciowej).
ANOMALY_SIGMA = 2.0

# Minimalna liczba powiązanych spółek, aby w ogóle rozważać węzeł
# (eliminuje szum od pojedynczych, przypadkowych powiązań).
MIN_ENTITY_LINKS = 5


def _resolve_data_files() -> tuple[Path, list[Path] | None]:
    """
    Zwróć ścieżkę pliku krawędzi oraz listę plików węzłów.

    Obsługiwane są dwa formaty ICIJ:
    - skonsolidowane: nodes.csv + edges.csv
    - rozdzielone: nodes-*.csv + relationships.csv
    """
    edges_path = RAW_DATA_DIR / "edges.csv"
    if not edges_path.exists():
        edges_path = RAW_DATA_DIR / "relationships.csv"

    nodes_consolidated = RAW_DATA_DIR / "nodes.csv"
    if nodes_consolidated.exists():
        return edges_path, [nodes_consolidated]

    node_files = sorted(RAW_DATA_DIR.glob("nodes-*.csv"))
    if not node_files:
        raise FileNotFoundError(
            f"Brak plików węzłów w {RAW_DATA_DIR}. "
            "Oczekiwano nodes.csv lub nodes-*.csv."
        )
    return edges_path, node_files


def load_nodes(node_files: list[Path]) -> pd.DataFrame:
    """Wczytaj i połącz pliki węzłów w jedną tabelę z kolumną node_type."""
    frames: list[pd.DataFrame] = []

    for path in node_files:
        node_chunk = pd.read_csv(path, low_memory=False)

        if path.name == "nodes.csv":
            # Skonsolidowany plik powinien zawierać kolumnę typu węzła.
            if "node_type" not in node_chunk.columns:
                node_chunk["node_type"] = node_chunk["node_id"].apply(_infer_node_type)
        else:
            # Mapowanie nazw plików ICIJ na typy węzłów w grafie.
            node_type_map = {
                "nodes-entities": "entity",
                "nodes-officers": "officer",
                "nodes-intermediaries": "intermediary",
                "nodes-addresses": "address",
                "nodes-others": "other",
            }
            node_chunk["node_type"] = node_type_map.get(path.stem, "other")

        frames.append(node_chunk)

    combined_nodes = pd.concat(frames, ignore_index=True)
    combined_nodes["node_id"] = combined_nodes["node_id"].astype(str)
    return combined_nodes


def _infer_node_type(node_id: int | str) -> str:
    """Heurystyka ICIJ: prefiks ID wskazuje kategorię węzła."""
    prefix = str(node_id)[:2]
    mapping = {
        "10": "entity",
        "11": "intermediary",
        "12": "officer",
        "14": "address",
        "24": "address",
    }
    return mapping.get(prefix, "other")


def clean_nodes(nodes: pd.DataFrame) -> pd.DataFrame:
    """
    Usuń rekordy z brakami w kluczowych kolumnach identyfikacyjnych.

    W analizie śledczej nie możemy przypisać sensu węzłowi bez nazwy
    lub adresu — takie rekordy nie wnoszą wartości do grafu.
    """
    key_columns_by_type: dict[str, list[str]] = {
        "entity": ["name", "jurisdiction", "address", "countries"],
        "officer": ["name", "countries"],
        "intermediary": ["name", "address", "countries"],
        "address": ["address", "countries"],
        "other": ["name", "countries"],
    }

    cleaned_parts: list[pd.DataFrame] = []
    for node_type, group in nodes.groupby("node_type", sort=False):
        cols = [c for c in key_columns_by_type.get(node_type, ["name"]) if c in group.columns]
        if not cols:
            cleaned_parts.append(group.dropna(how="all"))
            continue

        # Usuń wiersze, w których WSZYSTKIE kluczowe pola są puste/NaN.
        mask = group[cols].notna().any(axis=1)
        cleaned_parts.append(group.loc[mask])

    cleaned_nodes = pd.concat(cleaned_parts, ignore_index=True)

    # Normalizacja tekstu: trim białych znaków w polach opisowych.
    text_cols = ["name", "address", "countries", "jurisdiction"]
    for col in text_cols:
        if col in cleaned_nodes.columns:
            cleaned_nodes[col] = cleaned_nodes[col].astype("string").str.strip()

    return cleaned_nodes.drop_duplicates(subset=["node_id"])


def load_edges(edges_path: Path) -> pd.DataFrame:
    """Wczytaj krawędzie i zostaw tylko relacje istotne dla śledztwa."""
    edge_records = pd.read_csv(edges_path, low_memory=False)

    # Ujednolicenie nazw kolumn (edges.csv vs relationships.csv).
    rename_map = {
        "source": "node_id_start",
        "target": "node_id_end",
        "rel_type": "rel_type",
    }
    edge_records = edge_records.rename(
        columns={k: v for k, v in rename_map.items() if k in edge_records.columns}
    )

    if "rel_type" not in edge_records.columns:
        raise ValueError(f"Plik {edges_path} nie zawiera kolumny rel_type.")

    investigative_edges = edge_records[
        edge_records["rel_type"].isin(INVESTIGATIVE_REL_TYPES)
    ].copy()
    investigative_edges["node_id_start"] = investigative_edges["node_id_start"].astype(str)
    investigative_edges["node_id_end"] = investigative_edges["node_id_end"].astype(str)
    return investigative_edges.drop_duplicates()


def build_investigation_graph(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
) -> nx.DiGraph:
    """
    Zbuduj graf skierowany powiązań podmiotów offshore.

    Krawędzie:
    - Entity → Address   (registered_address)
    - Officer → Entity   (officer_of)
    - Intermediary → Entity (intermediary_of)
    """
    valid_node_ids = set(nodes["node_id"])
    filtered_edges = edges[
        edges["node_id_start"].isin(valid_node_ids)
        & edges["node_id_end"].isin(valid_node_ids)
    ]

    investigation_graph = nx.DiGraph()
    investigation_graph.add_nodes_from(
        (row.node_id, {"node_type": row.node_type, "label": _node_label(row)})
        for row in nodes.itertuples(index=False)
    )
    investigation_graph.add_edges_from(
        (row.node_id_start, row.node_id_end, {"rel_type": row.rel_type})
        for row in filtered_edges.itertuples(index=False)
    )
    return investigation_graph


def _node_label(row: pd.Series | object) -> str:
    """Wybierz czytelną etykietę węzła do raportu."""
    if hasattr(row, "name") and pd.notna(getattr(row, "name", None)):
        return str(row.name)
    if hasattr(row, "address") and pd.notna(getattr(row, "address", None)):
        return str(row.address)
    return str(getattr(row, "node_id", ""))


def _entity_neighbors(graph: nx.DiGraph, node_id: str, rel_type: str) -> set[str]:
    """
    Zwróć zbiór ID spółek offshore powiązanych z danym węzłem.

    W zależności od typu relacji spółka może być źródłem lub celem krawędzi.
    """
    entities: set[str] = set()

    if rel_type == "registered_address":
        # Entity → Address: spółki wchodzące do tego adresu.
        entities = {
            pred
            for pred in graph.predecessors(node_id)
            if graph.nodes[pred].get("node_type") == "entity"
            and graph[pred][node_id].get("rel_type") == rel_type
        }
    elif rel_type in {"officer_of", "intermediary_of"}:
        # Officer/Intermediary → Entity: spółki wychodzące z węzła.
        entities = {
            succ
            for succ in graph.successors(node_id)
            if graph.nodes[succ].get("node_type") == "entity"
            and graph[node_id][succ].get("rel_type") == rel_type
        }

    return entities


def count_entity_links(graph: nx.DiGraph) -> pd.DataFrame:
    """
    Policz, ile różnych spółek offshore jest powiązanych z każdym
    adresem, dyrektorem lub pośrednikiem.

    Wysoka liczba unikalnych spółek przy jednym adresie/osobie to sygnał
    typowy dla skrzynki pocztowej lub fabryki spółek-słupów.
    """
    suspect_types = {
        "address": "registered_address",
        "officer": "officer_of",
        "intermediary": "intermediary_of",
    }

    records: list[dict] = []
    for node_id, attrs in graph.nodes(data=True):
        node_type = attrs.get("node_type")
        rel_type = suspect_types.get(node_type)
        if rel_type is None:
            continue

        linked_entities = _entity_neighbors(graph, node_id, rel_type)
        if len(linked_entities) < MIN_ENTITY_LINKS:
            continue

        records.append(
            {
                "node_id": node_id,
                "suspect_type": node_type,
                "label": attrs.get("label", node_id),
                "linked_entity_count": len(linked_entities),
                "rel_type": rel_type,
            }
        )

    return pd.DataFrame(records)


def detect_anomalies(link_counts: pd.DataFrame) -> pd.DataFrame:
    """
    Oznacz anomalie statystyczne w obrębie każdej kategorii podejrzanych.

    Osobno liczymy próg dla adresów, dyrektorów i pośredników, ponieważ
    rozkłady liczby powiązań różnią się między typami węzłów.
    """
    if link_counts.empty:
        return link_counts.assign(
            threshold=pd.Series(dtype=float),
            is_anomaly=pd.Series(dtype=bool),
            anomaly_reason=pd.Series(dtype="string"),
        )

    flagged_groups: list[pd.DataFrame] = []
    for suspect_type, suspect_group in link_counts.groupby("suspect_type", sort=False):
        entity_link_counts = suspect_group["linked_entity_count"]
        mean_links = entity_link_counts.mean()
        std_links = entity_link_counts.std(ddof=0)
        anomaly_threshold = mean_links + ANOMALY_SIGMA * std_links

        flagged_group = suspect_group.copy()
        flagged_group["threshold"] = anomaly_threshold
        flagged_group["is_anomaly"] = entity_link_counts > anomaly_threshold
        anomaly_reason = (
            "Powiazany z "
            + flagged_group["linked_entity_count"].astype(str)
            + f" spolkami (prog: {anomaly_threshold:.1f}, srednia: {mean_links:.1f})"
        )
        flagged_group["anomaly_reason"] = np.where(
            flagged_group["is_anomaly"], anomaly_reason, ""
        )
        flagged_groups.append(flagged_group)

    return pd.concat(flagged_groups, ignore_index=True)


def enrich_suspects(suspects: pd.DataFrame, nodes: pd.DataFrame) -> pd.DataFrame:
    """Dołącz metadane (kraj, adres) do raportu podejrzanych węzłów."""
    meta_cols = ["node_id", "name", "address", "countries", "country_codes", "jurisdiction"]
    meta = nodes[[c for c in meta_cols if c in nodes.columns]].copy()
    meta["node_id"] = meta["node_id"].astype(str)

    enriched = suspects.merge(meta, on="node_id", how="left")
    enriched = enriched.sort_values(
        ["is_anomaly", "linked_entity_count"],
        ascending=[False, False],
    )
    return enriched


def save_results(suspects_report: pd.DataFrame, path: Path = OUTPUT_PATH) -> None:
    """Zapisz listę podejrzanych węzłów do CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    export_cols = [
        "node_id",
        "suspect_type",
        "label",
        "name",
        "address",
        "countries",
        "country_codes",
        "jurisdiction",
        "linked_entity_count",
        "rel_type",
        "threshold",
        "is_anomaly",
        "anomaly_reason",
    ]
    available_cols = [c for c in export_cols if c in suspects_report.columns]
    suspects_report[available_cols].to_csv(path, index=False)


def run_investigation() -> pd.DataFrame:
    """Pełny pipeline analizy śledczej."""
    edges_path, node_files = _resolve_data_files()
    print(f"Wczytywanie węzłów z: {[p.name for p in node_files]}")
    print(f"Wczytywanie krawędzi z: {edges_path.name}")

    raw_nodes = load_nodes(node_files)
    cleaned_nodes = clean_nodes(raw_nodes)
    investigative_edges = load_edges(edges_path)

    print(f"Węzły po oczyszczeniu: {len(cleaned_nodes):,}")
    print(f"Krawędzie śledcze: {len(investigative_edges):,}")

    investigation_graph = build_investigation_graph(cleaned_nodes, investigative_edges)
    print(
        f"Graf: {investigation_graph.number_of_nodes():,} węzłów, "
        f"{investigation_graph.number_of_edges():,} krawędzi"
    )

    entity_link_counts = count_entity_links(investigation_graph)
    suspects_report = detect_anomalies(entity_link_counts)
    suspects_report = enrich_suspects(suspects_report, cleaned_nodes)

    flagged_anomalies = suspects_report[suspects_report["is_anomaly"]]
    print(
        f"Wykryto {len(flagged_anomalies):,} anomalii "
        f"(prog: srednia + {ANOMALY_SIGMA} * odchylenie)"
    )

    save_results(suspects_report)
    print(f"Zapisano wyniki: {OUTPUT_PATH}")
    return suspects_report


def main() -> None:
    run_investigation()


if __name__ == "__main__":
    main()
