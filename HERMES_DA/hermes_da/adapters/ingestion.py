# -*- coding: utf-8 -*-
"""
Adaptateur AGT-01 DATA INGESTION → agent 01 existant (`KLDataViz_console/ingestion`).

Aucune modification du package existant : il est importé par chemin, exécuté, puis ses
métadonnées sont MIRÉES dans le schéma `hermes` (mêmes identifiants DS-… / DV-…).
"""
from __future__ import annotations

import hashlib
from typing import Any

from .. import artifacts, config
from ..config import SCHEMA


def _source_id(origin: str) -> str:
    return "SRC-" + hashlib.sha256(origin.encode("utf-8")).hexdigest()[:12].upper()


def run(env, db, jobs) -> dict:
    """Exécute une tâche d'ingestion et renvoie un bloc de résultat normalisé."""
    if not config.register_existing_ingestion_agent():
        return {"status": "FAILED", "result_reference": {},
                "warnings": [], "errors": [f"agent 01 introuvable : {config.INGESTION_PKG}"]}
    from ingestion import ingest  # agent 01 existant (non modifié)

    params = dict(env.parameters or {})
    ref = dict(env.input_reference or {})
    source = ref.get("path") or ref.get("source") or ref.get("dsn")
    if not source:
        return {"status": "FAILED", "result_reference": {}, "warnings": [],
                "errors": ["input_reference sans 'path'/'source'/'dsn' : impossible d'ingérer."]}

    contract = ingest(
        source,
        kind=params.get("kind", "auto"),
        sheet=params.get("sheet"),
        table=params.get("table"),
        schema_name=params.get("schema", "public"),
        where=params.get("where"),
        encoding=params.get("encoding"),
        separator=params.get("separator"),
        header=params.get("header"),
        limit=params.get("limit"),
        dataset_name=params.get("dataset_name"),
        force=bool(params.get("force", False)),
    )

    artifact = artifacts.write_contract(env.job_id, "agent01_ingestion", contract)
    mirrored = mirror(db, contract, env.job_id) if contract.get("dataset_id") else {}

    status = {"ERREUR": "FAILED"}.get(contract["status"], "DONE")
    return {
        "status": status,
        "result_reference": {
            "dataset_id": contract.get("dataset_id"),
            "dataset_version_id": contract.get("dataset_version_id"),
            "row_count": contract.get("row_count"),
            "column_count": contract.get("column_count"),
            "work_path": (contract.get("storage") or {}).get("work_path"),
            "raw_path": (contract.get("version") or {}).get("raw_path"),
            "ingestion_status": contract.get("status"),
            "source_unchanged": ((contract.get("source_metadata") or {}).get("integrity") or {}).get("source_unchanged"),
            "contract_artifact": artifact,
            "mirror": mirrored,
        },
        "warnings": list(contract.get("warnings") or []),
        "errors": list(contract.get("errors") or []),
        "_contract": contract,
    }


def mirror(db, contract: dict, job_id: str) -> dict:
    """Reproduit les métadonnées de l'agent 01 dans le schéma hermes (sans altérer `ingestion`)."""
    sm = contract.get("source_metadata") or {}
    version = contract.get("version") or {}
    schema = contract.get("schema") or {}
    origin = sm.get("origin") or sm.get("path") or "-"
    source_id = _source_id(str(origin))

    db.insert_json(
        "data_sources",
        {"source_id": source_id, "kind": sm.get("kind") or "fichier", "location": str(origin),
         "last_seen": version.get("created_at"), "metadata": {k: sm.get(k) for k in
         ("format", "encoding", "separator", "size_bytes", "sha256", "sheet", "table")}},
        jsonb_fields=("metadata",),
        on_conflict="ON CONFLICT (source_id) DO UPDATE SET last_seen = now(), metadata = EXCLUDED.metadata",
    )

    db.insert_json(
        "datasets",
        {"dataset_id": contract["dataset_id"],
         "dataset_name": (schema.get("table_name") or sm.get("filename") or "-"),
         "source_kind": sm.get("kind"), "origin": str(origin),
         "row_count": contract.get("row_count"), "column_count": contract.get("column_count"),
         "updated_at": version.get("created_at")},
        on_conflict="""ON CONFLICT (dataset_id) DO UPDATE SET
            dataset_name = EXCLUDED.dataset_name, row_count = EXCLUDED.row_count,
            column_count = EXCLUDED.column_count, updated_at = now()""",
    )

    db.insert_json(
        "dataset_versions",
        {"dataset_version_id": contract["dataset_version_id"], "dataset_id": contract["dataset_id"],
         "version_no": version.get("version_no"), "content_sha256": version.get("content_sha256"),
         "source_format": sm.get("format"), "encoding": sm.get("encoding"), "separator": sm.get("separator"),
         "sheet": sm.get("sheet"), "table_name": sm.get("table"), "row_count": contract.get("row_count"),
         "column_count": contract.get("column_count"), "work_path": version.get("work_path"),
         "raw_path": version.get("raw_path"), "status": contract.get("status"),
         "ingested_at": version.get("created_at"), "ingested_by": contract.get("ingested_by"),
         "metadata": {"source_metadata": sm, "warnings": contract.get("warnings"), "artifact": None}},
        jsonb_fields=("metadata",),
        on_conflict="""ON CONFLICT (dataset_version_id) DO UPDATE SET
            row_count = EXCLUDED.row_count, column_count = EXCLUDED.column_count,
            status = EXCLUDED.status, work_path = EXCLUDED.work_path, raw_path = EXCLUDED.raw_path,
            metadata = EXCLUDED.metadata""",
    )

    dvid = contract["dataset_version_id"]
    db.execute(f"DELETE FROM {SCHEMA}.variables WHERE dataset_version_id = %s", (dvid,))
    rows = 0
    for var in contract.get("variables") or []:
        db.insert_json(
            "variables",
            {"dataset_version_id": dvid, "ordinal": var.get("ordinal"), "variable": var.get("name"),
             "normalized_type": var.get("normalized_type"), "source_type": var.get("source_type"),
             "semantic_role": var.get("semantic_role"), "nullable": var.get("nullable"),
             "null_pct": var.get("null_pct"), "distinct_count": var.get("distinct_count"),
             "id_candidate": var.get("id_candidate"), "is_constant": var.get("constant"),
             "min_value": str(var.get("min")) if var.get("min") is not None else None,
             "max_value": str(var.get("max")) if var.get("max") is not None else None,
             "samples": var.get("samples") or []},
            jsonb_fields=("samples",),
        )
        rows += 1

    db.execute(
        f"""UPDATE {SCHEMA}.datasets SET version_count =
            (SELECT count(*) FROM {SCHEMA}.dataset_versions WHERE dataset_id = %s) WHERE dataset_id = %s""",
        (contract["dataset_id"], contract["dataset_id"]))

    return {"dataset_rows": 1, "version_rows": 1, "variable_rows": rows, "source_rows": 1,
            "source_id": source_id, "job_id": job_id}
