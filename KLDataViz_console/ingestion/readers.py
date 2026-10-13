# -*- coding: utf-8 -*-
"""
Lecteurs de l'agent DATA INGESTION.

CSV / Excel / JSON / Parquet / PostgreSQL (+ SQLite en bonus).

Les lecteurs NE MODIFIENT JAMAIS la source : ils reçoivent un chemin en lecture seule
et renvoient un DataFrame + les métadonnées de lecture réelles (encodage utilisé,
séparateur utilisé, ligne d'en-tête, feuille, feuilles ignorées, avertissements).
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path
from typing import Any

import pandas as pd

from .detectors import detect_encoding, detect_format, detect_separator, detect_excel_sheets, guess_header_row

MAX_ROWS_DEFAULT = None  # None = pas de limite


# --------------------------------------------------------------------------------------
# CSV / texte délimité
# --------------------------------------------------------------------------------------
def read_csv_file(path, encoding: str | None = None, separator: str | None = None,
                  limit: int | None = None, header: int | None = None,
                  detect_header: bool = True) -> tuple[pd.DataFrame, dict]:
    warnings: list[str] = []
    enc_info = detect_encoding(path)
    enc = encoding or enc_info["encoding"]
    attempts = [enc] + [e for e in ("utf-8-sig", "utf-8", "cp1252", "latin-1") if e != enc]

    raw = open(path, "r", encoding=attempts[0], errors="strict", newline="").read(200_000) \
        if _can_decode(path, attempts[0]) else open(path, "r", encoding="cp1252", newline="").read(200_000)
    sep_info = detect_separator(raw)
    sep = separator or sep_info["separator"]
    if separator and separator != sep_info["separator"]:
        warnings.append(f"Séparateur forcé manuellement « {separator} » (détection automatique : « {sep_info['separator']} »).")

    header_row = 0
    skipped = 0
    if header is None and detect_header:
        probe = pd.read_csv(path, sep=sep, header=None, nrows=12, encoding=enc,
                            engine="python", on_bad_lines="skip", dtype=str)
        header_row = guess_header_row(probe.values.tolist())
        if header_row:
            warnings.append(f"Préambule détecté : {header_row} ligne(s) ignorée(s) avant l'en-tête.")
    elif header is not None:
        header_row = header

    df = None
    last_error = None
    for candidate in attempts:
        try:
            df = pd.read_csv(path, sep=sep, header=header_row, encoding=candidate,
                             nrows=limit, engine="python", on_bad_lines="warn",
                             skip_blank_lines=True)
            if candidate != enc:
                warnings.append(f"Encodage « {enc} » invalide → repli sur « {candidate} ».")
            enc = candidate
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    if df is None:
        raise ValueError(f"Lecture CSV impossible ({last_error})")

    if df.columns.duplicated().any():
        dupes = sorted({str(c) for c in df.columns[df.columns.duplicated()]})
        warnings.append(f"En-têtes dupliqués renommés : {dupes}")
        df = df.loc[:, ~df.columns.duplicated(keep="first")]
    if any(str(c).startswith("Unnamed:") for c in df.columns):
        unnamed = [str(c) for c in df.columns if str(c).startswith("Unnamed:")]
        warnings.append(f"Colonne(s) sans nom dans la source : {unnamed}")
    df.columns = [str(c).strip() for c in df.columns]

    meta = {
        "reader": "csv",
        "encoding": enc,
        "encoding_confidence": enc_info["confidence"],
        "encoding_candidates": enc_info["candidates"],
        "separator": sep,
        "separator_label": sep_info["label"],
        "separator_confidence": sep_info["confidence"],
        "header_row": header_row,
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "warnings": warnings,
    }
    return df, meta


def _can_decode(path, encoding: str, limit: int = 400_000) -> bool:
    try:
        with open(path, "r", encoding=encoding) as fh:
            fh.read(limit)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------------------
def read_excel_file(path, sheet: str | int | None = None, header: int | None = None,
                    limit: int | None = None, detect_header: bool = True) -> tuple[pd.DataFrame, dict]:
    warnings: list[str] = []
    sheets_info = detect_excel_sheets(path)
    all_sheets = sheets_info["sheets"]
    if header is None and detect_header:
        probe = pd.read_excel(path, sheet_name=sheet if sheet is not None else 0, header=None,
                              nrows=12, dtype=object)
        header_row = guess_header_row(probe.fillna("").values.tolist())
    else:
        header_row = header or 0

    target = sheet if sheet is not None else (sheets_info["active"] or 0)
    df = pd.read_excel(path, sheet_name=target, header=header_row, nrows=limit)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(axis=1, how="all")

    if len(all_sheets) > 1:
        others = [s["name"] for s in all_sheets if s["name"] != _sheet_name(target, all_sheets)]
        warnings.append(f"Classeur multi-feuilles : feuille retenue « {_sheet_name(target, all_sheets)} » ; "
                        f"autres feuilles non importées : {others}. Relancer l'ingestion avec --sheet pour chacune.")
    if header_row:
        warnings.append(f"Ligne d'en-tête réelle : {header_row + 1} ({header_row} ligne(s) de préambule).")

    meta = {
        "reader": "excel",
        "sheet": _sheet_name(target, all_sheets),
        "sheets_available": all_sheets,
        "sheet_count": sheets_info["count"],
        "header_row": header_row,
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "warnings": warnings,
    }
    return df, meta


def _sheet_name(target, sheets: list[dict]) -> Any:
    if isinstance(target, int):
        for s in sheets:
            if s["index"] == target:
                return s["name"]
        return target
    return target


# --------------------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------------------
def read_json_file(path, encoding: str | None = None, limit: int | None = None) -> tuple[pd.DataFrame, dict]:
    import json

    warnings: list[str] = []
    enc = encoding or detect_encoding(path)["encoding"]
    with open(path, "r", encoding=enc) as fh:
        payload = json.load(fh)

    orient = None
    if isinstance(payload, list):
        records = payload
        orient = "records (liste d'objets)"
    elif isinstance(payload, dict):
        arrays = {k: v for k, v in payload.items() if isinstance(v, list) and v and isinstance(v[0], (dict, list))}
        if arrays:
            key = next((k for k in ("data", "records", "rows", "items", "results", "values") if k in arrays), next(iter(arrays)))
            records = arrays[key]
            orient = f"imbriqué (clé « {key} »)"
            warnings.append(f"JSON imbriqué : tableau « {key} » utilisé comme table principale ; "
                            f"autres clés racine conservées dans source_metadata.")
        else:
            records = [payload]
            orient = "objet unique (1 ligne)"
    else:
        records = [{"value": payload}]
        orient = "scalaire racine"

    df = pd.json_normalize(records, max_level=2)
    if limit:
        df = df.head(limit)
    nested = [c for c in df.columns if "." in c]
    if nested:
        warnings.append(f"Champs imbriqués aplatis : {nested[:8]}{'…' if len(nested) > 8 else ''}")
    df.columns = [str(c).strip() for c in df.columns]

    meta = {
        "reader": "json",
        "encoding": enc,
        "orient": orient,
        "root_type": type(payload).__name__,
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "warnings": warnings,
    }
    return df, meta


# --------------------------------------------------------------------------------------
# Parquet
# --------------------------------------------------------------------------------------
def read_parquet_file(path, limit: int | None = None) -> tuple[pd.DataFrame, dict]:
    warnings: list[str] = []
    try:
        import pyarrow.parquet as pq
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"pyarrow requis pour lire le Parquet ({exc})")

    pf = pq.ParquetFile(path)
    schema = pf.schema_arrow
    rg0 = pf.metadata.row_group(0) if pf.metadata.num_row_groups else None
    compression = sorted({
        f"{rg0.column(i).path_in_schema}:{rg0.column(i).compression}" for i in range(pf.metadata.num_columns)
    }) if rg0 is not None else []
    meta = {
        "reader": "parquet",
        "rows": int(pf.metadata.num_rows),
        "columns": int(pf.metadata.num_columns),
        "row_groups": int(pf.metadata.num_row_groups),
        "created_by": pf.metadata.created_by,
        "compression": compression,
        "arrow_schema": [{"name": f.name, "type": str(f.type)} for f in schema],
        "warnings": warnings,
    }
    table = pq.read_table(path) if not limit else pf.read_row_group(0).slice(0, limit)
    df = table.to_pandas()
    return df, meta


# --------------------------------------------------------------------------------------
# PostgreSQL
# --------------------------------------------------------------------------------------
def read_postgres_table(dsn: str, table: str, schema: str = "public", limit: int | None = None,
                        where: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Lit une table PostgreSQL (source externe) et en produit un instantané en mémoire."""
    import psycopg

    warnings: list[str] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable, ordinal_position,
                       character_maximum_length, numeric_precision, numeric_scale
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema, table),
            )
            cols = cur.fetchall()
            if not cols:
                raise ValueError(f"Table {schema}.{table} introuvable ou sans colonne visible.")
            cur.execute(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = %s::regclass AND i.indisprimary
                """,
                (f"{schema}.{table}",),
            )
            pk = [r[0] for r in cur.fetchall()]
            cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"' + (f" WHERE {where}" if where else ""))
            total = int(cur.fetchone()[0])
            sql = f'SELECT * FROM "{schema}"."{table}"' + (f" WHERE {where}" if where else "") + (f" LIMIT {int(limit)}" if limit else "")
            import warnings as _warnings

            with _warnings.catch_warnings():
                _warnings.filterwarnings("ignore", message=".*SQLAlchemy.*", category=UserWarning)
                df = pd.read_sql_query(sql, conn)
            cur.execute("SELECT current_database(), current_user, version()")
            db, user, version = cur.fetchone()

    df.columns = [str(c).strip() for c in df.columns]
    if limit and total > len(df):
        warnings.append(f"Lecture limitée à {len(df)} lignes sur {total} (option --limit).")
    meta = {
        "reader": "postgres",
        "dsn": _mask_dsn(dsn),
        "database": db,
        "user": user,
        "server_version": version.split(" on ")[0],
        "schema": schema,
        "table": table,
        "where": where,
        "primary_key": pk,
        "source_columns": [{"name": c[0], "type": c[1], "nullable": c[2] == "YES", "ordinal": c[3],
                            "length": c[4], "precision": c[5], "scale": c[6]} for c in cols],
        "row_count_source": total,
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "warnings": warnings,
    }
    return df, meta


def _mask_dsn(dsn: str) -> str:
    return re.sub(r"(password=)[^ ]+", r"\1***", dsn)


# --------------------------------------------------------------------------------------
# SQLite (source locale, lecture seule)
# --------------------------------------------------------------------------------------
def read_sqlite_table(path, table: str, limit: int | None = None) -> tuple[pd.DataFrame, dict]:
    import sqlite3

    uri = f"file:{Path(path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")
        tables = cur.fetchall()
        if table not in [t[0] for t in tables]:
            raise ValueError(f"Table « {table} » absente. Disponibles : {[t[0] for t in tables]}")
        cur.execute(f'PRAGMA table_info("{table}")')
        cols = cur.fetchall()
        sql = f'SELECT * FROM "{table}"' + (f" LIMIT {int(limit)}" if limit else "")
        df = pd.read_sql_query(sql, conn)
    finally:
        conn.close()
    df.columns = [str(c).strip() for c in df.columns]
    return df, {
        "reader": "sqlite",
        "path": str(path),
        "table": table,
        "tables_available": [t[0] for t in tables],
        "source_columns": [{"name": c[1], "type": c[2], "nullable": not c[3], "ordinal": c[0]} for c in cols],
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "warnings": [],
    }


# --------------------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------------------
READERS = {
    "csv": lambda path, **kw: read_csv_file(path, **kw),
    "tsv": lambda path, **kw: read_csv_file(path, separator="\t", **kw),
    "psv": lambda path, **kw: read_csv_file(path, separator="|", **kw),
    "xlsx": lambda path, **kw: read_excel_file(path, **kw),
    "xls": lambda path, **kw: read_excel_file(path, **kw),
    "xlsm": lambda path, **kw: read_excel_file(path, **kw),
    "json": lambda path, **kw: read_json_file(path, **kw),
    "parquet": lambda path, **kw: read_parquet_file(path, **kw),
}


def read_any(path, fmt: str | None = None, **kwargs) -> tuple[pd.DataFrame, dict]:
    """Lit un fichier et renvoie (données, métadonnées de lecture réelles)."""
    fmt = fmt or detect_format(path)["format"]
    # Normalisation des options selon le lecteur
    common = {"limit": kwargs.get("limit")}
    if fmt in ("csv", "tsv", "psv"):
        common.update({"encoding": kwargs.get("encoding"), "separator": kwargs.get("separator"),
                       "header": kwargs.get("header")})
    elif fmt in ("xlsx", "xls"):
        common.update({"sheet": kwargs.get("sheet"), "header": kwargs.get("header")})
    elif fmt == "json":
        common.update({"encoding": kwargs.get("encoding")})
    if fmt not in READERS:
        raise ValueError(f"Format « {fmt} » non pris en charge par les lecteurs d'ingestion.")
    return READERS[fmt](path, **{k: v for k, v in common.items() if v is not None})


def write_work_copy(df: pd.DataFrame, dest_dir, name: str, prefer: str = "parquet") -> tuple[Path, list[str], dict]:
    """Écrit la VERSION DE TRAVAIL (jamais la source) et renvoie le chemin + contrôle."""
    import json

    warnings: list[str] = []
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    info: dict[str, Any] = {}
    path = dest_dir / f"{name}.parquet"
    written = None
    if prefer == "parquet":
        try:
            df.to_parquet(path, index=False, compression="zstd")
            written = path
            info["work_format"] = "parquet"
        except Exception as exc:  # repli CSV
            warnings.append(f"Écriture Parquet impossible ({exc}) → repli CSV UTF-8.")
    if written is None:
        path = dest_dir / f"{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        written = path
        info["work_format"] = "csv (utf-8-sig)"

    # Un CSV de contrôle lisible est toujours conservé (échantillon si volumineux)
    ctrl = dest_dir / f"{name}_apercu.csv"
    df.head(500).to_csv(ctrl, index=False, encoding="utf-8-sig")

    from .detectors import sha256_file  # noqa: PLC0415

    digest = sha256_file(written)
    info.update({"work_path": str(written), "work_sha256": digest, "preview_path": str(ctrl)})
    return written, warnings, info
