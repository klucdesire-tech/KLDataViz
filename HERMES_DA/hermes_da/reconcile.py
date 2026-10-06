# -*- coding: utf-8 -*-
"""
Réconciliation du miroir `hermes` avec le registre de l'agent 01 (`ingestion`).

Les versions ingérées via l'orchestrateur sont mirées automatiquement. Celles créées
avant l'existence de HERMES_DA — ou ingérées directement en ligne de commande par l'agent 01 —
sont rattrapées par `backfill()` (lecture seule sur `ingestion`, écriture dans `hermes`).
"""
from __future__ import annotations

from typing import Any

from .adapters.ingestion import _source_id
from .config import SCHEMA

MISSING_VERSIONS_SQL = f"""
SELECT v.dataset_version_id, v.dataset_id, v.version_no, v.content_sha256, v.source_format,
       v.encoding, v.separator, v.sheet, v.table_name, v.row_count, v.column_count,
       v.work_path, v.raw_path, v.status, v.ingested_at, v.ingested_by,
       d.dataset_name, d.source_kind, d.origin
FROM ingestion.dataset_versions v
JOIN ingestion.datasets d ON d.dataset_id = v.dataset_id
WHERE NOT EXISTS (SELECT 1 FROM {SCHEMA}.dataset_versions h
                  WHERE h.dataset_version_id = v.dataset_version_id)
ORDER BY v.ingested_at
"""

COLUMNS_SQL = f"""
SELECT ordinal, column_name, normalized_type, source_type, semantic_role, nullable, null_count,
       null_pct, distinct_count, distinct_pct, id_candidate, is_constant, min_value, max_value, samples
FROM ingestion.dataset_columns WHERE dataset_version_id = %s ORDER BY ordinal
"""


def pending(db) -> list[dict[str, Any]]:
    """Versions présentes dans `ingestion` mais absentes du miroir `hermes`."""
    return db.query(MISSING_VERSIONS_SQL)


def backfill(db, *, limit: int | None = None) -> dict[str, Any]:
    """Mire les versions manquantes ; renvoie le détail de ce qui a été rattrapé."""
    rows = pending(db)
    if limit:
        rows = rows[:limit]
    done: list[str] = []
    for r in rows:
        db.insert_json(
            "data_sources",
            {"source_id": _source_id(str(r["origin"])), "kind": r["source_kind"],
             "location": str(r["origin"]), "last_seen": r["ingested_at"],
             "metadata": {"format": r["source_format"], "encoding": r["encoding"],
                          "separator": r["separator"], "sheet": r["sheet"], "table": r["table_name"],
                          "backfill": True}},
            jsonb_fields=("metadata",),
            on_conflict="ON CONFLICT (source_id) DO NOTHING",
        )
        db.insert_json(
            "datasets",
            {"dataset_id": r["dataset_id"], "dataset_name": r["dataset_name"],
             "source_kind": r["source_kind"], "origin": str(r["origin"]),
             "row_count": r["row_count"], "column_count": r["column_count"]},
            on_conflict="ON CONFLICT (dataset_id) DO NOTHING",
        )
        db.insert_json(
            "dataset_versions",
            {"dataset_version_id": r["dataset_version_id"], "dataset_id": r["dataset_id"],
             "version_no": r["version_no"], "content_sha256": r["content_sha256"],
             "source_format": r["source_format"], "encoding": r["encoding"], "separator": r["separator"],
             "sheet": r["sheet"], "table_name": r["table_name"], "row_count": r["row_count"],
             "column_count": r["column_count"], "work_path": r["work_path"], "raw_path": r["raw_path"],
             "status": r["status"], "ingested_at": r["ingested_at"], "ingested_by": r["ingested_by"],
             "metadata": {"backfill": True}},
            jsonb_fields=("metadata",),
            on_conflict="ON CONFLICT (dataset_version_id) DO NOTHING",
        )
        columns = db.query(COLUMNS_SQL, (r["dataset_version_id"],))
        for c in columns:
            db.insert_json(
                "variables",
                {"dataset_version_id": r["dataset_version_id"], "ordinal": c["ordinal"],
                 "variable": c["column_name"], "normalized_type": c["normalized_type"],
                 "source_type": c["source_type"], "semantic_role": c["semantic_role"],
                 "nullable": c["nullable"], "null_pct": c["null_pct"],
                 "distinct_count": c["distinct_count"], "id_candidate": c["id_candidate"],
                 "is_constant": c["is_constant"], "min_value": c["min_value"], "max_value": c["max_value"],
                 "samples": c["samples"]},
                jsonb_fields=("samples",),
                on_conflict="""ON CONFLICT (dataset_version_id, ordinal) DO UPDATE SET
                    variable = EXCLUDED.variable, normalized_type = EXCLUDED.normalized_type,
                    semantic_role = EXCLUDED.semantic_role, null_pct = EXCLUDED.null_pct,
                    distinct_count = EXCLUDED.distinct_count, id_candidate = EXCLUDED.id_candidate""",
            )
        db.execute(
            f"""UPDATE {SCHEMA}.datasets SET version_count =
                (SELECT count(*) FROM {SCHEMA}.dataset_versions WHERE dataset_id = %s),
                updated_at = now() WHERE dataset_id = %s""",
            (r["dataset_id"], r["dataset_id"]))
        done.append(r["dataset_version_id"])
    return {"versions_modèle": len(rows), "versions_mirées": len(done), "dataset_version_ids": done,
            "restantes": len(pending(db))}
