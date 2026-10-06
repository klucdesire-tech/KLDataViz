# -*- coding: utf-8 -*-
"""
AGENT 10 — VALIDATOR (implémenté).

Contrôle INDÉPENDANT des résultats d'un job : le Validator relit les artefacts, le disque
et PostgreSQL, puis recalcule lui-même les quantités vérifiables. Il cherche activement
les erreurs au lieu d'approuver. Décision :
APPROVED | APPROVED_WITH_WARNINGS | REQUIRES_CORRECTION | REJECTED.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .. import artifacts, contracts
from ..config import SCHEMA
from ..detectors_helper import sha256_file, read_tabular

VALIDATOR_ID = "AGT-10"
MASTER_ID = "AGT-00"


def run(env, db, jobs) -> dict:
    """Point d'entrée de l'agent de validation."""
    params = dict(env.parameters or {})
    target_job_id = params.get("target_job_id")
    target = jobs.get_raw(target_job_id) if target_job_id else None
    if target is None:
        return {"status": "FAILED", "result_reference": {},
                "warnings": [], "errors": [f"job à valider introuvable : {target_job_id}"]}

    contract_path = target.get("artifact_path") or params.get("contract_artifact")
    contract = artifacts.read(contract_path) if contract_path and Path(contract_path).exists() else None

    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, Any] = {"target_job_id": target_job_id, "task_type": target["task_type"],
                                "contract_artifact": contract_path}

    # ---------------------------------------------------------------- 1. enveloppe / contrat
    envelope = {
        "job_id": target["job_id"], "dataset_id": target.get("dataset_id"),
        "agent_id": target["agent_id"], "task_type": target["task_type"],
        "input_reference": target.get("input_reference") or {}, "parameters": target.get("parameters") or {},
        "status": target.get("status"), "result_reference": target.get("result_reference") or {},
        "warnings": target.get("warnings") or [], "errors": target.get("errors") or [],
        "created_at": str(target.get("created_at")), "completed_at": str(target.get("completed_at")),
    }
    problems = contracts.validate_envelope(envelope)
    checks.append({"check": "contrat_enveloppe", "ok": not problems, "detail": problems or "enveloppe complète"})
    if problems:
        errors.extend(f"contrat : {p}" for p in problems)

    if contract is None:
        warning = "aucun artefact de contrat disponible : validation limitée à l'enveloppe et à la base"
        warnings.append(warning)
        checks.append({"check": "artefact_contrat", "ok": False, "detail": warning})
    else:
        checks.append({"check": "artefact_contrat", "ok": True, "detail": contract_path})

    if target["task_type"] == "plan":
        reason = ("aucun job de données à valider : la chaîne s'est arrêtée avant toute production "
                  "de résultat (job de planification uniquement)")
        errors.append(reason)
        checks.append({"check": "cible_de_validation", "ok": False, "detail": reason})
        return _finish(db, jobs, env, target, "REJECTED", checks, errors, warnings, evidence, MASTER_ID)

    if target["status"] in ("FAILED", "SKIPPED"):
        errors.append(f"le job à valider est en statut {target['status']} : aucun résultat exploitable")
        checks.append({"check": "statut_job", "ok": False, "detail": target["status"]})
        return _finish(db, jobs, env, target, "REJECTED", checks, errors, warnings, evidence, target["agent_id"])

    # ---------------------------------------------------------------- 2. contrôles spécifiques
    if contract and contract.get("dataset_version_id"):
        _check_ingestion(db, contract, checks, errors, warnings, evidence, params)

    # ---------------------------------------------------------------- 3. décision
    decision = "APPROVED"
    if errors:
        decision = "REQUIRES_CORRECTION"
        critical = any(k in " ".join(errors) for k in ("source", "RAW", "inexistant", "incohérent"))
        if critical or len(errors) >= 3:
            decision = "REJECTED"
    elif warnings:
        decision = "APPROVED_WITH_WARNINGS"

    return _finish(db, jobs, env, target, decision, checks, errors, warnings, evidence, target["agent_id"])


# ---------------------------------------------------------------------- contrôles ingestion
def _check_ingestion(db, contract: dict, checks, errors, warnings, evidence, params) -> None:
    sm = contract.get("source_metadata") or {}
    integrity = sm.get("integrity") or {}
    storage = contract.get("storage") or {}
    schema = contract.get("schema") or {}
    version = contract.get("version") or {}
    dvid = contract.get("dataset_version_id")

    def check(name: str, ok: bool, detail: Any, *, severity: str = "ERROR") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "severity": severity})
        if not ok:
            (warnings if severity == "WARNING" else errors).append(f"{name} : {detail}")

    check("dataset_version_id", bool(dvid), dvid or "absent")
    check("row_count_positif", (contract.get("row_count") or 0) > 0,
          f"row_count={contract.get('row_count')}")
    check("column_count_positif", (contract.get("column_count") or 0) > 0,
          f"column_count={contract.get('column_count')}")
    check("colonnes_coherentes",
          int(contract.get("column_count") or 0) == len(contract.get("variables") or []) ==
          int(schema.get("column_count") or -1),
          f"contrat={contract.get('column_count')} / variables={len(contract.get('variables') or [])} "
          f"/ schema={schema.get('column_count')}")
    check("schema_present", bool(schema.get("columns")), f"{len(schema.get('columns') or [])} colonne(s) décrite(s)")
    check("ordinales_continues",
          [v.get("ordinal") for v in contract.get("variables") or []] ==
          list(range(1, len(contract.get("variables") or []) + 1)),
          "ordinales 1..n")
    check("source_intacte", integrity.get("source_unchanged") is True,
          f"source_unchanged={integrity.get('source_unchanged')} — {integrity.get('mode')}")
    check("copie_raw_identique", sm.get("copy_identical") is not False,
          f"copy_identical={sm.get('copy_identical')}")
    check("empreinte_source", bool(sm.get("sha256") or (version.get("content_sha256"))),
          "empreinte SHA-256 présente")
    check("erreurs_ingestion_vides", not (contract.get("errors") or []),
          f"{len(contract.get('errors') or [])} erreur(s) : {contract.get('errors')}")

    # ---- relecture indépendante de la version de travail (recomputation probe)
    work_path = storage.get("work_path")
    if work_path and Path(work_path).exists():
        try:
            rows, cols = read_tabular(work_path)
            check("relecture_version_travail",
                  rows == contract.get("row_count") and cols == contract.get("column_count"),
                  f"fichier relu : {rows}×{cols} vs contrat {contract.get('row_count')}×{contract.get('column_count')}")
            evidence["work_file"] = {"path": work_path, "rows": rows, "columns": cols,
                                     "sha256": sha256_file(work_path)}
            declared = storage.get("work_sha256")
            if declared:
                check("empreinte_version_travail", evidence["work_file"]["sha256"] == declared,
                      f"{evidence['work_file']['sha256'][:16]}… vs déclaré {declared[:16]}…")
        except Exception as exc:  # noqa: BLE001
            check("relecture_version_travail", False, f"relecture impossible : {exc}")
    else:
        check("relecture_version_travail", False, f"version de travail introuvable : {work_path}")

    # ---- vérification croisée dans PostgreSQL (schéma hermes)
    try:
        mirrored = db.query(f"SELECT count(*) AS n FROM {SCHEMA}.variables WHERE dataset_version_id = %s", (dvid,))
        n = mirrored[0]["n"] if mirrored else 0
        check("miroir_postgresql", n == int(contract.get("column_count") or 0),
              f"{n} variable(s) dans hermes.variables vs {contract.get('column_count')} attendues")
        evidence["postgres_mirror"] = {"variables": n}
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"vérification croisée PostgreSQL impossible : {exc}")
        checks.append({"check": "miroir_postgresql", "ok": False, "detail": str(exc), "severity": "WARNING"})

    # ---- contrôles de gravité moindre
    warn_count = len(contract.get("warnings") or [])
    checks.append({"check": "avertissements_ingestion", "ok": warn_count == 0,
                   "detail": f"{warn_count} avertissement(s) métier", "severity": "WARNING"})
    if warn_count:
        warnings.append(f"{warn_count} avertissement(s) métier à examiner")
    if (schema.get("duplicate_rows") or 0) > 0:
        checks.append({"check": "doublons_lignes", "ok": False,
                       "detail": f"{schema['duplicate_rows']} ligne(s) dupliquée(s)", "severity": "WARNING"})


# ---------------------------------------------------------------------- clôture
def _finish(db, jobs, env, target, decision: str, checks, errors, warnings, evidence,
            responsible: str) -> dict:
    validation_id = "VAL-" + uuid.uuid4().hex[:12].upper()
    required_actions = _required_actions(decision, errors)
    db.insert_json(
        "validation_results",
        {"validation_id": validation_id, "job_id": env.job_id, "validated_job_id": target["job_id"],
         "agent_id": VALIDATOR_ID, "decision": decision, "checks_performed": checks,
         "errors": errors, "warnings": warnings, "evidence": evidence,
         "required_actions": required_actions, "responsible_agent": responsible if required_actions else None},
        jsonb_fields=("checks_performed", "errors", "warnings", "evidence", "required_actions"),
    )
    return {
        "status": "DONE",
        "result_reference": {"validation_id": validation_id, "decision": decision,
                             "validated_job_id": target["job_id"],
                             "checks": len(checks), "checks_failed": sum(1 for c in checks if not c["ok"]),
                             "errors": errors, "warnings": warnings, "required_actions": required_actions,
                             "responsible_agent": responsible if required_actions else None},
        "warnings": warnings,
        "errors": errors,
    }


def _required_actions(decision: str, errors: list[str]) -> list[str]:
    if decision == "APPROVED":
        return []
    if decision == "APPROVED_WITH_WARNINGS":
        return ["examiner les avertissements avant toute conclusion"]
    actions = [f"corriger : {e}" for e in errors[:5]]
    actions.append("relancer la tâche puis redemander une validation")
    return actions
