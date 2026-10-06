# -*- coding: utf-8 -*-
"""
Registre de métadonnées de l'agent DATA INGESTION.

Backend principal : PostgreSQL (schéma `ingestion` dans la base `kldataviz`) —
conforme à la responsabilité « enregistrer les métadonnées dans PostgreSQL ».
Repli automatique : SQLite local (`storage/registry.sqlite`) si le serveur est
injoignable, avec un avertissement explicite dans la sortie de l'agent.

Tables : ingestion.datasets / ingestion.dataset_versions /
         ingestion.dataset_columns / ingestion.ingestion_runs
Vue   : ingestion.v_latest_dataset_versions
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = os.environ.get("KLINGEST_PG_DB", "kldataviz")
DEFAULT_HOST = os.environ.get("KLINGEST_PG_HOST", "127.0.0.1")
DEFAULT_PORT = os.environ.get("KLINGEST_PG_PORT", "5432")
BASE_DIR = Path(__file__).resolve().parent.parent
SQLITE_PATH = BASE_DIR / "storage" / "registry.sqlite"


def default_dsn(dbname: str = DEFAULT_DB) -> str:
    if os.environ.get("KLINGEST_PG_DSN"):
        return os.environ["KLINGEST_PG_DSN"]
    parts = [f"host={DEFAULT_HOST}", f"port={DEFAULT_PORT}", f"dbname={dbname}"]
    if os.environ.get("KLINGEST_PG_USER"):
        parts.append(f"user={os.environ['KLINGEST_PG_USER']}")
    if os.environ.get("KLINGEST_PG_PASSWORD"):
        parts.append(f"password={os.environ['KLINGEST_PG_PASSWORD']}")
    return " ".join(parts)


PG_DDL = """
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.datasets (
    dataset_id      text PRIMARY KEY,
    dataset_name    text NOT NULL,
    source_kind     text NOT NULL,
    origin          text NOT NULL,
    logical_key     text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    version_count   integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS {schema}.dataset_versions (
    dataset_version_id text PRIMARY KEY,
    dataset_id         text NOT NULL REFERENCES {schema}.datasets(dataset_id) ON DELETE CASCADE,
    version_no         integer NOT NULL,
    content_sha256     text NOT NULL,
    source_path        text,
    raw_path           text,
    work_path          text,
    source_format      text,
    encoding           text,
    separator          text,
    sheet              text,
    table_name         text,
    row_count          bigint NOT NULL,
    column_count       integer NOT NULL,
    status             text NOT NULL,
    ingested_by        text,
    ingested_at        timestamptz NOT NULL DEFAULT now(),
    source_metadata    jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    schema_def         jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    UNIQUE (dataset_id, version_no)
);

CREATE TABLE IF NOT EXISTS {schema}.dataset_columns (
    dataset_version_id text NOT NULL REFERENCES {schema}.dataset_versions(dataset_version_id) ON DELETE CASCADE,
    ordinal            integer NOT NULL,
    column_name        text NOT NULL,
    normalized_type    text,
    source_type        text,
    semantic_role      text,
    nullable           boolean,
    null_count         bigint,
    null_pct           numeric(7,3),
    distinct_count     bigint,
    distinct_pct       numeric(7,3),
    id_candidate       boolean,
    is_constant        boolean,
    min_value          text,
    max_value          text,
    samples            jsonb,
    PRIMARY KEY (dataset_version_id, ordinal)
);

CREATE TABLE IF NOT EXISTS {schema}.ingestion_runs (
    run_id             text PRIMARY KEY,
    dataset_id         text,
    dataset_version_id text,
    started_at         timestamptz,
    finished_at        timestamptz,
    duration_ms        integer,
    status             text,
    metadata_store     text,
    source_kind        text,
    warnings           jsonb,
    errors             jsonb,
    audit              jsonb
);

CREATE OR REPLACE VIEW {schema}.v_latest_dataset_versions AS
SELECT d.dataset_id, d.dataset_name, d.source_kind, d.origin, d.version_count,
       v.dataset_version_id, v.version_no, v.row_count, v.column_count,
       v.source_format, v.encoding, v.separator, v.sheet, v.table_name,
       v.status, v.ingested_at, v.work_path
FROM {schema}.datasets d
JOIN {schema}.dataset_versions v ON v.dataset_id = d.dataset_id
WHERE v.version_no = (SELECT max(v2.version_no) FROM {schema}.dataset_versions v2
                      WHERE v2.dataset_id = d.dataset_id);
"""

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT PRIMARY KEY, dataset_name TEXT NOT NULL, source_kind TEXT NOT NULL,
    origin TEXT NOT NULL, logical_key TEXT, created_at TEXT, updated_at TEXT,
    version_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS dataset_versions (
    dataset_version_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, version_no INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL, source_path TEXT, raw_path TEXT, work_path TEXT,
    source_format TEXT, encoding TEXT, separator TEXT, sheet TEXT, table_name TEXT,
    row_count INTEGER NOT NULL, column_count INTEGER NOT NULL, status TEXT NOT NULL,
    ingested_by TEXT, ingested_at TEXT, source_metadata TEXT, schema_def TEXT);
CREATE TABLE IF NOT EXISTS dataset_columns (
    dataset_version_id TEXT NOT NULL, ordinal INTEGER NOT NULL, column_name TEXT NOT NULL,
    normalized_type TEXT, source_type TEXT, semantic_role TEXT, nullable INTEGER,
    null_count INTEGER, null_pct REAL, distinct_count INTEGER, distinct_pct REAL,
    id_candidate INTEGER, is_constant INTEGER, min_value TEXT, max_value TEXT, samples TEXT,
    PRIMARY KEY (dataset_version_id, ordinal));
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id TEXT PRIMARY KEY, dataset_id TEXT, dataset_version_id TEXT, started_at TEXT,
    finished_at TEXT, duration_ms INTEGER, status TEXT, metadata_store TEXT,
    source_kind TEXT, warnings TEXT, errors TEXT, audit TEXT);
CREATE VIEW IF NOT EXISTS v_latest_dataset_versions AS
SELECT d.dataset_id, d.dataset_name, d.source_kind, d.origin, d.version_count,
       v.dataset_version_id, v.version_no, v.row_count, v.column_count, v.source_format,
       v.encoding, v.separator, v.sheet, v.table_name, v.status, v.ingested_at, v.work_path
FROM datasets d JOIN dataset_versions v ON v.dataset_id = d.dataset_id
WHERE v.version_no = (SELECT max(v2.version_no) FROM dataset_versions v2 WHERE v2.dataset_id = d.dataset_id);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Registry:
    """Registre des datasets / versions / colonnes / exécutions."""

    def __init__(self, dsn: str | None = None, sqlite_path: str | Path | None = None,
                 force_sqlite: bool = False, schema: str = "ingestion", verbose: bool = False):
        self.schema = schema
        self.sqlite_path = Path(sqlite_path or SQLITE_PATH)
        self.dsn = dsn or default_dsn()
        self.backend = "sqlite"
        self.connection_info: dict[str, Any] = {}
        self.warnings: list[str] = []
        if not force_sqlite:
            self.backend = "postgres"
        self._connect_error: str | None = None

    # ------------------------------------------------------------------ connexion
    def connect(self):
        if self.backend == "postgres":
            try:
                import psycopg

                return psycopg.connect(self.dsn, autocommit=True)
            except Exception as exc:
                self._connect_error = str(exc)
                self.warnings.append(
                    f"PostgreSQL injoignable ({self._connect_error}) → registre de repli SQLite : {self.sqlite_path}")
                self.backend = "sqlite"
        import sqlite3

        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self.sqlite_path))

    @property
    def placeholder(self) -> str:
        return "%s" if self.backend == "postgres" else "?"

    def _j(self, value: Any):
        if self.backend == "postgres":
            from psycopg.types.json import Jsonb

            return Jsonb(value if value is not None else {})
        return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)

    def ensure(self) -> dict:
        """Crée la base (si besoin), le schéma et les tables du registre."""
        if self.backend == "postgres":
            self._ensure_database()
        ddl = (PG_DDL if self.backend == "postgres" else SQLITE_DDL)
        conn = self.connect()
        try:
            if self.backend == "postgres":
                with conn.cursor() as cur:
                    cur.execute(ddl.format(schema=self.schema))
                    cur.execute("SELECT current_database(), current_user, version()")
                    db, user, version = cur.fetchone()
                self.connection_info = {"backend": "postgres", "dsn": _mask(self.dsn), "database": db,
                                        "user": user, "server_version": version.split(" on ")[0],
                                        "schema": self.schema}
            else:
                conn.executescript(ddl)
                conn.commit()
                self.connection_info = {"backend": "sqlite", "path": str(self.sqlite_path)}
        finally:
            conn.close()
        return self.connection_info

    def _ensure_database(self) -> None:
        import psycopg

        dbname = _dsn_get(self.dsn, "dbname") or DEFAULT_DB
        maintenance = _dsn_with(self.dsn, dbname="postgres")
        try:
            conn = psycopg.connect(maintenance, autocommit=True)
        except Exception as exc:
            raise RuntimeError(f"Connexion PostgreSQL impossible : {exc}")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
                if cur.fetchone() is None:
                    cur.execute(f'CREATE DATABASE "{dbname}"')
                    self.warnings.append(f"Base PostgreSQL « {dbname} » créée pour le registre de métadonnées.")
        finally:
            conn.close()

    # ------------------------------------------------------------------ écritures
    def save(self, result: dict) -> dict:
        """Enregistre dataset + version + colonnes + exécution ; renvoie l'état d'écriture."""
        pg = self.backend == "postgres"
        pre = f"{self.schema}." if pg else ""
        ph = self.placeholder

        def q(n: int) -> str:
            return ",".join([ph] * n)

        conn = self.connect()
        written: dict[str, Any] = {}
        try:
            cur = conn.cursor()
            if not pg:
                cur.execute("PRAGMA foreign_keys = ON")

            # Une tentative en échec (aucune version construite) ne doit pas créer de version vide :
            # elle est seulement journalisée dans ingestion_runs.
            register_version = bool(result.get("_register_version", True))
            if not register_version:
                run = result["_run"]
                rcols = ["run_id", "dataset_id", "dataset_version_id", "started_at", "finished_at", "duration_ms",
                         "status", "metadata_store", "source_kind", "warnings", "errors", "audit"]
                rvals = (run["run_id"], result.get("dataset_id"), result.get("dataset_version_id"),
                         run["started_at"], run["finished_at"], run["duration_ms"], result.get("status", "ERREUR"),
                         self.backend, (result.get("_dataset") or {}).get("source_kind"),
                         self._j(result.get("warnings") or []), self._j(result.get("errors") or []),
                         self._j(result.get("audit") or []))
                cur.execute(f"INSERT INTO {pre}ingestion_runs ({','.join(rcols)}) VALUES ({q(len(rcols))})", rvals)
                if not pg:
                    conn.commit()
                return {"metadata_store": self.backend, "dataset_rows": 0, "version_rows": 0,
                        "column_rows": 0, "run_rows": 1, "note": "tentative en échec : exécution journalisée uniquement"}

            # 1) dataset (identité logique stable)
            ds = result["_dataset"]
            ds_cols = "dataset_id, dataset_name, source_kind, origin, logical_key, created_at, updated_at, version_count"
            if pg:
                cur.execute(
                    f"INSERT INTO {pre}datasets ({ds_cols}) VALUES ({q(5)}, now(), now(), 0) "
                    f"ON CONFLICT (dataset_id) DO UPDATE SET dataset_name = EXCLUDED.dataset_name, updated_at = now()",
                    (ds["dataset_id"], ds["dataset_name"], ds["source_kind"], ds["origin"], ds["logical_key"]),
                )
            else:
                cur.execute(
                    f"INSERT INTO datasets ({ds_cols}) VALUES ({q(7)}, 0) "
                    f"ON CONFLICT(dataset_id) DO UPDATE SET dataset_name=excluded.dataset_name, updated_at=excluded.updated_at",
                    (ds["dataset_id"], ds["dataset_name"], ds["source_kind"], ds["origin"], ds["logical_key"],
                     _utcnow(), _utcnow()),
                )

            # 2) version de travail (immuable)
            version = result["_version"]
            src = result["source_metadata"]
            vcols = ["dataset_version_id", "dataset_id", "version_no", "content_sha256", "source_path", "raw_path",
                     "work_path", "source_format", "encoding", "separator", "sheet", "table_name", "row_count",
                     "column_count", "status", "ingested_by", "ingested_at", "source_metadata", "schema_def"]
            vvals = [version["dataset_version_id"], version["dataset_id"], version["version_no"],
                     version["content_sha256"], src.get("path") or src.get("origin"), version.get("raw_path"),
                     version.get("work_path"), src.get("format"), src.get("encoding"), src.get("separator"),
                     src.get("sheet"), src.get("table"), result["row_count"], result["column_count"],
                     result.get("status", "OK"), result.get("ingested_by"), version["created_at"],
                     self._j(src), self._j(result.get("schema") or {})]
            cur.execute(
                f"INSERT INTO {pre}dataset_versions ({','.join(vcols)}) VALUES ({q(len(vcols))}) "
                f"ON CONFLICT (dataset_version_id) DO UPDATE SET "
                + ", ".join(f"{c} = EXCLUDED.{c}" for c in vcols if c not in ("dataset_version_id", "dataset_id")),
                tuple(vvals),
            )

            # 3) colonnes / variables
            cur.execute(f"DELETE FROM {pre}dataset_columns WHERE dataset_version_id = {ph}",
                        (version["dataset_version_id"],))
            rows = []
            for c in result.get("variables", []):
                rows.append((version["dataset_version_id"], c.get("ordinal"), c.get("name"), c.get("normalized_type"),
                             c.get("source_type"), c.get("semantic_role"), c.get("nullable"), c.get("null_count"),
                             c.get("null_pct"), c.get("distinct_count"), c.get("distinct_pct"), c.get("id_candidate"),
                             c.get("constant"), _txt(c.get("min")), _txt(c.get("max")),
                             self._j(c.get("samples") or [])))
            if rows:
                cur.executemany(
                    f"INSERT INTO {pre}dataset_columns (dataset_version_id, ordinal, column_name, normalized_type, "
                    f"source_type, semantic_role, nullable, null_count, null_pct, distinct_count, distinct_pct, "
                    f"id_candidate, is_constant, min_value, max_value, samples) VALUES ({q(16)})",
                    rows,
                )

            # 4) compteur de versions du dataset
            if pg:
                cur.execute(
                    f"UPDATE {pre}datasets SET version_count = "
                    f"(SELECT count(*) FROM {pre}dataset_versions WHERE dataset_id = {ph}), updated_at = now() "
                    f"WHERE dataset_id = {ph}",
                    (version["dataset_id"], version["dataset_id"]),
                )
            else:
                cur.execute(
                    "UPDATE datasets SET version_count = "
                    "(SELECT count(*) FROM dataset_versions WHERE dataset_id = ?) WHERE dataset_id = ?",
                    (version["dataset_id"], version["dataset_id"]),
                )

            # 5) exécution / journal d'audit
            run = result["_run"]
            rcols = ["run_id", "dataset_id", "dataset_version_id", "started_at", "finished_at", "duration_ms",
                     "status", "metadata_store", "source_kind", "warnings", "errors", "audit"]
            rvals = (run["run_id"], version["dataset_id"], version["dataset_version_id"], run["started_at"],
                     run["finished_at"], run["duration_ms"], result.get("status", "OK"), self.backend,
                     ds["source_kind"], self._j(result.get("warnings") or []), self._j(result.get("errors") or []),
                     self._j(result.get("audit") or []))
            cur.execute(f"INSERT INTO {pre}ingestion_runs ({','.join(rcols)}) VALUES ({q(len(rcols))})", rvals)

            if not pg:
                conn.commit()
            written = {"metadata_store": self.backend, "dataset_rows": 1, "version_rows": 1,
                       "column_rows": len(rows), "run_rows": 1}
        finally:
            conn.close()
        return written

    # ------------------------------------------------------------------ lectures
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]
        finally:
            conn.close()

    def list_datasets(self) -> list[dict]:
        t = f"{self.schema}.datasets" if self.backend == "postgres" else "datasets"
        return self.query(f"SELECT * FROM {t} ORDER BY updated_at DESC")

    def list_versions(self, dataset_id: str) -> list[dict]:
        t = f"{self.schema}.dataset_versions" if self.backend == "postgres" else "dataset_versions"
        return self.query(f"SELECT * FROM {t} WHERE dataset_id = {self.placeholder} ORDER BY version_no", (dataset_id,))

    def latest_versions(self) -> list[dict]:
        t = f"{self.schema}.v_latest_dataset_versions" if self.backend == "postgres" else "v_latest_dataset_versions"
        return self.query(f"SELECT * FROM {t} ORDER BY ingested_at DESC")

    def columns_of(self, dataset_version_id: str) -> list[dict]:
        t = f"{self.schema}.dataset_columns" if self.backend == "postgres" else "dataset_columns"
        return self.query(f"SELECT * FROM {t} WHERE dataset_version_id = {self.placeholder} ORDER BY ordinal",
                          (dataset_version_id,))

    def summary(self) -> dict:
        t_ds = f"{self.schema}.datasets" if self.backend == "postgres" else "datasets"
        t_v = f"{self.schema}.dataset_versions" if self.backend == "postgres" else "dataset_versions"
        t_c = f"{self.schema}.dataset_columns" if self.backend == "postgres" else "dataset_columns"
        t_r = f"{self.schema}.ingestion_runs" if self.backend == "postgres" else "ingestion_runs"
        counts = {}
        for label, tbl in (("datasets", t_ds), ("versions", t_v), ("colonnes", t_c), ("executions", t_r)):
            try:
                counts[label] = self.query(f"SELECT count(*) AS n FROM {tbl}")[0]["n"]
            except Exception:
                counts[label] = None
        return {"backend": self.backend, "connection": self.connection_info, "counts": counts}


def _txt(value: Any) -> str | None:
    return None if value is None else str(value)[:200]


def _mask(dsn: str) -> str:
    import re

    return re.sub(r"(password=)\S+", r"\1***", dsn)


def _dsn_get(dsn: str, key: str) -> str | None:
    import re

    m = re.search(rf"{key}=([^\s]+)", dsn)
    return m.group(1) if m else None


def _dsn_with(dsn: str, **overrides) -> str:
    import re

    out = dsn
    for k, v in overrides.items():
        if re.search(rf"{k}=[^\s]+", out):
            out = re.sub(rf"{k}=[^\s]+", f"{k}={v}", out)
        else:
            out = f"{out} {k}={v}"
    return out
