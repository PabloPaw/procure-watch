"""
Analiza powiązań i wykrywanie anomalii w sieci podmiotów offshore.

Moduł pomocniczy — zawiera generyczne funkcje grafowe używane
jako punkt wyjścia do rozszerzeń pipeline'u śledczego.
Główna logika biznesowa znajduje się w investigation.py.
"""

from __future__ import annotations

import networkx as nx
import pandas as pd


def build_graph(
    edge_records: pd.DataFrame,
    source_col: str = "source",
    target_col: str = "target",
) -> nx.Graph:
    """
    Zbuduj nieskierowany graf powiązań z tabeli krawędzi.

    W kontekście śledczym graf reprezentuje sieć powiązań kapitałowych
    i operacyjnych — im gęstszy węzeł, tym większe ryzyko roli
    w schemacie typu shell-company factory.
    """
    return nx.from_pandas_edgelist(
        edge_records, source=source_col, target=target_col
    )


def detect_degree_anomalies(
    relationship_graph: nx.Graph,
    sigma_threshold: float = 2.0,
) -> list[str]:
    """
    Wykryj węzły o stopniu powyżej progu statystycznego (średnia + N * σ).

    Wysoki stopień węzła oznacza nietypową liczbę powiązań — typowy sygnał
    w analizie AML/compliance dla pośredników zakładających setki spółek
    lub adresów współdzielonych przez wiele podmiotów.
    """
    node_degrees = dict(relationship_graph.degree())
    if not node_degrees:
        return []

    degree_values = list(node_degrees.values())
    mean_degree = sum(degree_values) / len(degree_values)
    variance = sum((deg - mean_degree) ** 2 for deg in degree_values) / len(
        degree_values
    )
    std_deviation = variance**0.5
    anomaly_cutoff = mean_degree + sigma_threshold * std_deviation

    return [
        node_id
        for node_id, degree in node_degrees.items()
        if degree > anomaly_cutoff
    ]


def main() -> None:
    print("ProcureWatch — analiza powiązań")
    print("Podłącz oczyszczone dane i rozszerz pipeline wykrywania anomalii.")


if __name__ == "__main__":
    main()
