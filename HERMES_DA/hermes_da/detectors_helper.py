# -*- coding: utf-8 -*-
"""Petits utilitaires de lecture/empreinte partagés par les agents (sans dépendre de l'agent 01)."""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: str | Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def read_tabular(path: str | Path) -> tuple[int, int]:
    """Relit indépendamment une version de travail (parquet ou csv) : (lignes, colonnes)."""
    path = Path(path)
    if path.suffix.lower() == ".parquet":
        import pyarrow.parquet as pq

        md = pq.ParquetFile(path).metadata
        return int(md.num_rows), int(md.num_columns)
    import csv

    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        return sum(1 for _ in reader), len(header)
