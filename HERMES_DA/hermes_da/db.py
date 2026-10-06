# -*- coding: utf-8 -*-
"""
Couche PostgreSQL du système HERMES DATA ANALYTICS.

Crée et maintient le schéma `hermes` (17 tables métier + agents/jobs/audit + vues),
en coexistence avec le schéma `ingestion` de l'agent 01 (jamais modifié).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from .config import SCHEMA, dsn as build_dsn

DDL = """
CREATE SCHEMA IF NOT EXISTS {S};

-- 1. catalogue des agents
CREATE TABLE IF NOT EXISTS {S}.agents (
    agent_id      text PRIMARY KEY,
    ordinal       integer NOT NULL,
    name          text NOT NULL,
    role          text NOT NULL,
    responsibilities jsonb NOT NULL DEFAULT '[]',
    tools         jsonb NOT NULL DEFAULT '[]',
    permissions   jsonb NOT NULL DEFAULT '{}',
    status        text NOT NULL,                -- IMPLEMENTED | DECLARED
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- 2. enveloppes de tâches (protocole de communication)
CREATE TABLE IF NOT EXISTS {S}.jobs (
    job_id             text PRIMARY KEY,
    parent_job_id      text REFERENCES {S}.jobs(job_id) ON DELETE SET NULL,
    agent_id           text NOT NULL,
    task_type          text NOT NULL,
    objective          text,
    dataset_id         text,
    dataset_version_id text,
    input_reference    jsonb NOT NULL DEFAULT '{}',
    parameters         jsonb NOT NULL DEFAULT '{}',
    status             text NOT NULL,
    result_reference   jsonb NOT NULL DEFAULT '{}',
    warnings           jsonb NOT NULL DEFAULT '[]',
    errors             jsonb NOT NULL DEFAULT '[]',
    attempts           integer NOT NULL DEFAULT 0,
    max_attempts       integer NOT NULL DEFAULT 2,
    created_at         timestamptz NOT NULL DEFAULT now(),
    started_at         timestamptz,
    completed_at       timestamptz,
    duration_ms        integer,
    artifact_path      text
);

-- 3. sources déclarées
CREATE TABLE IF NOT EXISTS {S}.data_sources (
    source_id    text PRIMARY KEY,
    kind         text NOT NULL,                 -- fichier | postgres | sqlite | url
    location     text NOT NULL,
    immutability text NOT NULL DEFAULT 'lecture seule (copie RAW + SHA-256)',
    first_seen   timestamptz NOT NULL DEFAULT now(),
    last_seen    timestamptz,
    metadata     jsonb NOT NULL DEFAULT '{}'
);

-- 4/5. datasets et versions (miroir enrichi du schéma ingestion)
CREATE TABLE IF NOT EXISTS {S}.datasets (
    dataset_id    text PRIMARY KEY,
    dataset_name  text NOT NULL,
    source_kind   text,
    origin        text,
    row_count     bigint,
    column_count  integer,
    version_count integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {S}.dataset_versions (
    dataset_version_id text PRIMARY KEY,
    dataset_id         text NOT NULL REFERENCES {S}.datasets(dataset_id) ON DELETE CASCADE,
    version_no         integer NOT NULL,
    content_sha256     text,
    source_format      text,
    encoding           text,
    separator          text,
    sheet              text,
    table_name         text,
    row_count          bigint,
    column_count       integer,
    work_path          text,
    raw_path           text,
    status             text,
    ingestion_run_id   text,
    ingested_at        timestamptz,
    ingested_by        text,
    metadata           jsonb NOT NULL DEFAULT '{}'
);

-- 6. dictionnaire de données (description documentée, jamais inventée)
CREATE TABLE IF NOT EXISTS {S}.data_dictionary (
    dataset_id     text NOT NULL,
    variable       text NOT NULL,
    description    text NOT NULL DEFAULT 'Description non documentée',
    unit           text,
    expected_type  text,
    mandatory      boolean,
    source         text,
    documented_by  text,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, variable)
);

-- 7. profil des variables par version
CREATE TABLE IF NOT EXISTS {S}.variables (
    dataset_version_id text NOT NULL REFERENCES {S}.dataset_versions(dataset_version_id) ON DELETE CASCADE,
    ordinal            integer NOT NULL,
    variable           text NOT NULL,
    normalized_type    text,
    source_type        text,
    semantic_role      text,
    nullable           boolean,
    null_pct           numeric(7,3),
    distinct_count     bigint,
    id_candidate       boolean,
    is_constant        boolean,
    min_value          text,
    max_value          text,
    samples            jsonb,
    PRIMARY KEY (dataset_version_id, ordinal)
);

-- 8. contrôles qualité
CREATE TABLE IF NOT EXISTS {S}.quality_checks (
    check_id           text PRIMARY KEY,
    job_id             text REFERENCES {S}.jobs(job_id) ON DELETE SET NULL,
    dataset_id         text,
    dataset_version_id text,
    check_type         text NOT NULL,           -- completude | unicite | validite | coherence | conformite | doublon | format | plage | temporel | inter_variables | business_rule
    severity           text NOT NULL,           -- INFO | WARNING | ERROR | CRITICAL
    category           text,                    -- ANOMALIE_STATISTIQUE | ERREUR_DONNEE_CONFIRMEE | ERREUR_POTENTIELLE
    variable           text,
    rule               text,
    records_affected   bigint,
    records_total      bigint,
    score              numeric(7,3),
    details            jsonb NOT NULL DEFAULT '{}',
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- 9. opérations de nettoyage / transformation
CREATE TABLE IF NOT EXISTS {S}.cleaning_operations (
    operation_id       text PRIMARY KEY,
    job_id             text REFERENCES {S}.jobs(job_id) ON DELETE SET NULL,
    dataset_id         text,
    dataset_version_id text,
    operation_type     text NOT NULL,
    description        text,
    before_state       jsonb,
    after_state        jsonb,
    parameters         jsonb,
    rows_affected      bigint,
    performed_by       text,
    performed_at       timestamptz NOT NULL DEFAULT now()
);

-- 10/11. indicateurs et dimensions
CREATE TABLE IF NOT EXISTS {S}.indicators (
    indicator_id   text PRIMARY KEY,
    code           text UNIQUE NOT NULL,
    name           text,
    description    text NOT NULL DEFAULT 'Description non documentée',
    numerator      text,
    denominator    text,
    formula        text,
    population     text,
    period_type    text,
    exclusions     jsonb NOT NULL DEFAULT '[]',
    dataset_id     text,
    status         text NOT NULL DEFAULT 'DECLARED',   -- DECLARED | VALIDATED | DEPRECATED
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {S}.indicator_dimensions (
    indicator_id text NOT NULL REFERENCES {S}.indicators(indicator_id) ON DELETE CASCADE,
    dimension    text NOT NULL,                  -- SEXE | AGE | SITE | DISTRICT | REGION | MOIS | TRIMESTRE | ANNEE
    value        text,
    source_column text,
    PRIMARY KEY (indicator_id, dimension, value)
);

CREATE TABLE IF NOT EXISTS {S}.indicator_results (
    result_id          text PRIMARY KEY,
    indicator_id       text REFERENCES {S}.indicators(indicator_id) ON DELETE CASCADE,
    job_id             text REFERENCES {S}.jobs(job_id) ON DELETE SET NULL,
    dataset_version_id text,
    period             text,
    dimensions         jsonb NOT NULL DEFAULT '{}',
    numerator_value    numeric(20,4),
    denominator_value  numeric(20,4),
    value              numeric(20,4),
    unit               text,
    computed_at        timestamptz NOT NULL DEFAULT now()
);

-- 12/13. analyses
CREATE TABLE IF NOT EXISTS {S}.analysis_jobs (
    analysis_job_id    text PRIMARY KEY,
    job_id             text REFERENCES {S}.jobs(job_id) ON DELETE SET NULL,
    dataset_version_id text,
    method             text NOT NULL,
    variables          jsonb NOT NULL DEFAULT '[]',
    hypotheses         jsonb NOT NULL DEFAULT '[]',
    sample_size        bigint,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {S}.analysis_results (
    analysis_result_id text PRIMARY KEY,
    analysis_job_id    text REFERENCES {S}.analysis_jobs(analysis_job_id) ON DELETE CASCADE,
    statistics         jsonb NOT NULL DEFAULT '{}',
    interpretation     text,
    limitations        jsonb NOT NULL DEFAULT '[]',
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- 14. anomalies
CREATE TABLE IF NOT EXISTS {S}.anomalies (
    anomaly_id           text PRIMARY KEY,
    job_id               text REFERENCES {S}.jobs(job_id) ON DELETE SET NULL,
    dataset_version_id   text,
    anomaly_type         text NOT NULL,          -- STATISTICAL | TEMPORAL | BUSINESS_RULE | DATA_QUALITY | CROSS_INDICATOR
    variable             text,
    scope                text,
    evidence             jsonb NOT NULL DEFAULT '{}',
    method               text,
    severity             text,
    confidence           numeric(5,3),
    possible_explanations jsonb NOT NULL DEFAULT '[]',
    verification_required boolean NOT NULL DEFAULT true,
    created_at           timestamptz NOT NULL DEFAULT now()
);

-- 15. visualisations
CREATE TABLE IF NOT EXISTS {S}.visualizations (
    visualization_id text PRIMARY KEY,
    job_id           text REFERENCES {S}.jobs(job_id) ON DELETE SET NULL,
    chart_type       text NOT NULL,
    source_reference jsonb NOT NULL DEFAULT '{}',
    spec             jsonb NOT NULL DEFAULT '{}',
    file_path        text,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- 16. rapports
CREATE TABLE IF NOT EXISTS {S}.reports (
    report_id        text PRIMARY KEY,
    job_id           text REFERENCES {S}.jobs(job_id) ON DELETE SET NULL,
    title            text,
    report_format    text NOT NULL,              -- HTML | PDF | DOCX | EXCEL | JSON
    sections         jsonb NOT NULL DEFAULT '[]',
    dataset_reference jsonb NOT NULL DEFAULT '{}',
    validation_id    text,
    file_path        text,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- 17. validations
CREATE TABLE IF NOT EXISTS {S}.validation_results (
    validation_id      text PRIMARY KEY,
    job_id             text REFERENCES {S}.jobs(job_id) ON DELETE SET NULL,
    validated_job_id   text,
    agent_id           text,
    decision           text NOT NULL,            -- APPROVED | APPROVED_WITH_WARNINGS | REQUIRES_CORRECTION | REJECTED
    checks_performed   jsonb NOT NULL DEFAULT '[]',
    errors             jsonb NOT NULL DEFAULT '[]',
    warnings           jsonb NOT NULL DEFAULT '[]',
    evidence           jsonb NOT NULL DEFAULT '{}',
    required_actions   jsonb NOT NULL DEFAULT '[]',
    responsible_agent  text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- journal d'audit
CREATE TABLE IF NOT EXISTS {S}.audit_logs (
    audit_id    text PRIMARY KEY,
    job_id      text,
    agent_id    text,
    event_type  text NOT NULL,
    message     text,
    payload     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON {S}.jobs (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_parent ON {S}.jobs (parent_job_id);
CREATE INDEX IF NOT EXISTS idx_audit_job ON {S}.audit_logs (job_id, created_at);

CREATE OR REPLACE VIEW {S}.v_job_pipeline AS
SELECT j.job_id, j.parent_job_id, j.agent_id, a.name AS agent_name, j.task_type, j.status,
       j.dataset_id, j.dataset_version_id, j.attempts, j.duration_ms,
       j.created_at, j.completed_at, j.artifact_path,
       jsonb_array_length(j.warnings) AS nb_warnings,
       jsonb_array_length(j.errors) AS nb_errors
FROM {S}.jobs j
LEFT JOIN {S}.agents a ON a.agent_id = j.agent_id
ORDER BY j.created_at DESC;

CREATE OR REPLACE VIEW {S}.v_dataset_lineage AS
SELECT d.dataset_id, d.dataset_name, d.source_kind, d.origin,
       v.dataset_version_id, v.version_no, v.row_count, v.column_count, v.source_format,
       v.work_path, v.raw_path, v.status, v.ingested_at,
       i.status AS ingestion_status, i.run_id AS ingestion_run_id, i.audit AS ingestion_audit
FROM {S}.datasets d
LEFT JOIN {S}.dataset_versions v ON v.dataset_id = d.dataset_id
LEFT JOIN LATERAL (
    SELECT iv.status, ir.run_id, ir.audit
    FROM ingestion.dataset_versions iv
    LEFT JOIN ingestion.ingestion_runs ir ON ir.dataset_version_id = iv.dataset_version_id
    WHERE iv.dataset_version_id = v.dataset_version_id
    ORDER BY ir.started_at DESC NULLS LAST
    LIMIT 1
) i ON true;

CREATE OR REPLACE VIEW {S}.v_agent_status AS
SELECT a.agent_id, a.ordinal, a.name, a.role, a.status, a.tools,
       count(j.job_id) AS nb_jobs,
       count(*) FILTER (WHERE j.status = 'DONE') AS jobs_reussis,
       count(*) FILTER (WHERE j.status = 'FAILED') AS jobs_echoues,
       max(j.created_at) AS dernier_job
FROM {S}.agents a
LEFT JOIN {S}.jobs j ON j.agent_id = a.agent_id
GROUP BY a.agent_id, a.ordinal, a.name, a.role, a.status, a.tools
ORDER BY a.ordinal;
"""


class Database:
    """Accès PostgreSQL minimal (aucun ORM) : DDL, requêtes, insertions JSON."""

    def __init__(self, dsn: str | None = None, autocommit: bool = True):
        self.dsn = dsn or build_dsn()
        self.autocommit = autocommit
        self._conn = None

    # ------------------------------------------------------------------ connexion
    @property
    def conn(self):
        import psycopg

        if self._conn is None or getattr(self._conn, "closed", False):
            self._conn = psycopg.connect(self.dsn, autocommit=self.autocommit)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ schéma
    def ensure_schema(self) -> dict:
        """Crée le schéma `hermes`, ses tables et ses vues (idempotent)."""
        ddl = DDL.replace("{S}", SCHEMA)
        with self.conn.cursor() as cur:
            cur.execute(ddl)
            cur.execute("SELECT current_database(), current_user, version()")
            db, user, version = cur.fetchone()
            cur.execute(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name""",
                (SCHEMA,))
            tables = [r[0] for r in cur.fetchall()]
            cur.execute(
                """SELECT table_name FROM information_schema.views
                   WHERE table_schema = %s ORDER BY table_name""",
                (SCHEMA,))
            views = [r[0] for r in cur.fetchall()]
        return {"backend": "postgres", "database": db, "user": user,
                "server_version": version.split(" on ")[0], "schema": SCHEMA,
                "tables": tables, "table_count": len(tables), "views": views}

    def ingestion_schema_info(self) -> dict:
        """État du schéma de l'agent 01 (lecture seule)."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("""SELECT table_name FROM information_schema.tables
                               WHERE table_schema = 'ingestion' AND table_type='BASE TABLE'
                               ORDER BY table_name""")
                tables = [r[0] for r in cur.fetchall()]
                counts = {}
                for t in tables:
                    cur.execute(f'SELECT count(*) FROM ingestion."{t}"')
                    counts[t] = cur.fetchone()[0]
            return {"present": True, "tables": tables, "counts": counts}
        except Exception as exc:  # schéma absent : l'agent 01 n'a pas encore tourné
            return {"present": False, "error": str(exc)}

    # ------------------------------------------------------------------ requêtes
    def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        with self.conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.rowcount

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params or ())
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    def one(self, sql: str, params: Sequence[Any] | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert_json(self, table: str, data: dict[str, Any], *, jsonb_fields: Iterable[str] = (),
                    on_conflict: str | None = None) -> int:
        """Insertion générique : les champs de `jsonb_fields` sont sérialisés en JSONB."""
        import psycopg.types.json

        columns = list(data.keys())
        values = [psycopg.types.json.Jsonb(data[c]) if c in jsonb_fields else data[c] for c in columns]
        sql = (f'INSERT INTO {SCHEMA}."{table}" ({", ".join(columns)}) '
               f'VALUES ({", ".join(["%s"] * len(columns))})')
        if on_conflict:
            sql += " " + on_conflict
        return self.execute(sql, values)

    # ------------------------------------------------------------------ inventaire
    def table_counts(self, tables: Sequence[str] | None = None) -> dict[str, int]:
        info = self.ensure_schema()
        tables = list(tables or info["tables"])
        out = {}
        for t in tables:
            out[t] = self.query(f'SELECT count(*) AS n FROM {SCHEMA}."{t}"')[0]["n"]
        return out

    def schema_report(self) -> dict:
        """Description du schéma pour l'API /api/da/schema."""
        rows = self.query(
            """SELECT c.table_name, c.column_name, c.data_type, c.is_nullable
               FROM information_schema.columns c
               JOIN information_schema.tables t
                 ON t.table_schema = c.table_schema AND t.table_name = c.table_name AND t.table_type='BASE TABLE'
               WHERE c.table_schema = %s ORDER BY c.table_name, c.ordinal_position""",
            (SCHEMA,))
        tables: dict[str, dict] = {}
        for r in rows:
            t = tables.setdefault(r["table_name"], {"table": r["table_name"], "columns": [], "rows": None})
            t["columns"].append({"name": r["column_name"], "type": r["data_type"], "nullable": r["is_nullable"] == "YES"})
        for name, info in tables.items():
            info["rows"] = self.query(f'SELECT count(*) AS n FROM {SCHEMA}."{name}"')[0]["n"]
        views = self.query("""SELECT table_name FROM information_schema.views WHERE table_schema = %s ORDER BY table_name""",
                           (SCHEMA,))
        return {"schema": SCHEMA, "table_count": len(tables), "tables": list(tables.values()),
                "views": [v["table_name"] for v in views]}


def jsonable(value: Any) -> Any:
    """Convertit récursivement datetimes/decimals pour l'API JSON."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    try:
        import decimal

        if isinstance(value, decimal.Decimal):
            return float(value)
    except Exception:
        pass
    return value


def as_json(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False, default=str)
