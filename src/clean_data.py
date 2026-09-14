"""
Czyszczenie i normalizacja surowych danych offshore.

Moduł pomocniczy do wstępnego przygotowania plików CSV przed
głównym pipeline'em śledczym (investigation.py).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DATA_DIR = DATA_DIR / "raw"


def load_raw(filename: str) -> pd.DataFrame:
    """
    Wczytaj surowy plik CSV z katalogu data/raw/.

    Surowe dane ICIJ wymagają lokalnej kopii — pliki te nie są
    wersjonowane w repozytorium ze względu na rozmiar (~600 MB).
    """
    source_path = RAW_DATA_DIR / filename
    return pd.read_csv(source_path)


def clean(raw_records: pd.DataFrame) -> pd.DataFrame:
    """
    Wykonaj podstawowe oczyszczenie rekordów przed analizą grafową.

    Usunięcie duplikatów i pustych wierszy eliminuje szum, który
    mógłby zniekształcić metryki centralności w sieci powiązań.
    """
    return raw_records.drop_duplicates().dropna(how="all")


def main() -> None:
    print(f"Katalog surowych danych: {RAW_DATA_DIR}")
    print("Umieść pliki CSV w data/raw/ i rozszerz tę funkcję o pipeline czyszczenia.")


if __name__ == "__main__":
    main()
