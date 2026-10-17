# -*- coding: utf-8 -*-
"""Adaptateur AGT-02 DATA QUALITY → `modeling.clean_dataframe` (console KLDataViz).

Charge la version de travail ingérée (Parquet), exécute le contrôle qualité
(doublons, colonnes vides, valeurs manquantes, types) et journalise chaque
constat dans `hermes.quality_checks`.
"""
from __future__ import annotations

from typing import Any

from .. import artifacts
from ..contracts import new_job_id


def _load_dataset(db, dataset_version_id: str):
    """Charge le DataFrame depuis le work_path d'une version ingérée."""
    row = db.one(
        "SELECT work_path, row_count, column_count FROM ingestion.dataset_versions "
        "WHERE dataset_version_id = %s", (dataset_version_id,))
    if not row or not row.get("work_path"):
        return None, "dataset_version introuvable ou sans work_path"
    import pandas as pd
    try:
        df = pd.read_parquet(row["work_path"])
    except Exception as e:  # noqa: BLE001
        return None, f"lecture Parquet impossible : {e}"
    return df, None


def run(env, db, jobs) -> dict:
    """Exécute le contrôle qualité et renvoie un bloc de résultat normalisé."""
    dv = env.dataset_version_id or (env.parameters or {}).get("dataset_version_id") \
        or (env.input_reference or {}).get("dataset_version_id")
    if not dv:
        return {"status": "FAILED", "result_reference": {}, "warnings": [],
                "errors": ["dataset_version_id manquant : impossible de contrôler la qualité."]}

    df, err = _load_dataset(db, dv)
    if err:
        return {"status": "FAILED", "result_reference": {}, "warnings": [], "errors": [err]}

    try:
        from modeling import clean_dataframe
    except Exception as e:  # noqa: BLE001
        return {"status": "FAILED", "result_reference": {}, "warnings": [],
                "errors": [f"module modeling introuvable : {e}"]}

    df_clean, rapport = clean_dataframe(df)

    # -------- construction des contrôles qualité à journaliser
    total = int(rapport.get("lignes_avant") or len(df))
    checks: list[dict[str, Any]] = []

    if rapport.get("doublons_supprimes"):
        checks.append({"check_type": "doublons", "severity": "warn", "category": "intégrité",
                       "variable": None, "rule": "lignes dupliquées",
                       "records_affected": int(rapport["doublons_supprimes"]), "records_total": total,
                       "score": None, "details": {"doublons_supprimes": int(rapport["doublons_supprimes"])}})
    for c in rapport.get("colonnes_vides", []):
        checks.append({"check_type": "colonne_vide", "severity": "bad", "category": "complétude",
                       "variable": c, "rule": "colonne entièrement vide",
                       "records_affected": total, "records_total": total, "score": None, "details": {}})
    for im in rapport.get("imputations", []):
        checks.append({"check_type": "valeur_manquante", "severity": "warn", "category": "complétude",
                       "variable": im.get("colonne"), "rule": f"imputation par {im.get('methode')}",
                       "records_affected": int(im.get("n", 0)), "records_total": total,
                       "score": None, "details": im})
    for col, info in rapport.get("types_corriges", {}).items():
        checks.append({"check_type": "type", "severity": "info", "category": "typage",
                       "variable": col, "rule": f"{info.get('avant')} → {info.get('apres')}",
                       "records_affected": None, "records_total": total, "score": None, "details": info})

    # -------- journalisation dans hermes.quality_checks
    base_id = new_job_id("QC")
    inserted = 0
    for i, c in enumerate(checks):
        db.insert_json("quality_checks", {
            "check_id": f"{base_id}-{i}",
            "job_id": env.job_id,
            "dataset_id": env.dataset_id,
            "dataset_version_id": dv,
            "check_type": c["check_type"],
            "severity": c["severity"],
            "category": c["category"],
            "variable": c["variable"],
            "rule": c["rule"],
            "records_affected": c["records_affected"],
            "records_total": c["records_total"],
            "score": c["score"],
            "details": c["details"],
        }, jsonb_fields=("details",))
        inserted += 1

    artifacts.write_contract(env.job_id, "agent02_quality",
                             {"checks": len(checks), "rapport": rapport})

    return {
        "status": "DONE",
        "result_reference": {
            "checks": len(checks),
            "doublons": int(rapport.get("doublons_supprimes") or 0),
            "colonnes_vides": len(rapport.get("colonnes_vides", [])),
            "imputations": len(rapport.get("imputations", [])),
            "types_corriges": len(rapport.get("types_corriges", {})),
            "lignes_avant": int(rapport.get("lignes_avant") or 0),
            "lignes_apres": int(rapport.get("lignes_apres") or 0),
        },
        "warnings": [],
        "errors": [],
    }
