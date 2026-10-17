# -*- coding: utf-8 -*-
"""Adaptateur AGT-05 STATISTICAL ANALYSIS → `modeling.bivariate_stats` (console KLDataViz).

Calcule les statistiques bivariées (corrélations, V de Cramér, moyennes par
groupe) et les journalise dans `hermes.analysis_jobs` + `hermes.analysis_results`.
"""
from __future__ import annotations

from typing import Any

from .. import artifacts
from ..contracts import new_job_id
from .quality import _load_dataset


def run(env, db, jobs) -> dict:
    dv = env.dataset_version_id or (env.parameters or {}).get("dataset_version_id") \
        or (env.input_reference or {}).get("dataset_version_id")
    if not dv:
        return {"status": "FAILED", "result_reference": {}, "warnings": [],
                "errors": ["dataset_version_id manquant : impossible d'analyser."]}

    df, err = _load_dataset(db, dv)
    if err:
        return {"status": "FAILED", "result_reference": {}, "warnings": [], "errors": [err]}

    try:
        from modeling import bivariate_stats
    except Exception as e:  # noqa: BLE001
        return {"status": "FAILED", "result_reference": {}, "warnings": [],
                "errors": [f"module modeling introuvable : {e}"]}

    stats = bivariate_stats(df)

    analysis_job_id = new_job_id("ANA")
    numeric = df.select_dtypes(include=["number"]).columns.tolist()
    db.insert_json("analysis_jobs", {
        "analysis_job_id": analysis_job_id, "job_id": env.job_id,
        "dataset_version_id": dv, "method": "bivariate_stats",
        "variables": numeric, "hypotheses": {}, "sample_size": int(len(df)),
    }, jsonb_fields=("variables", "hypotheses"))

    db.insert_json("analysis_results", {
        "analysis_result_id": new_job_id("RES"), "analysis_job_id": analysis_job_id,
        "statistics": stats,
        "interpretation": (f"{len(stats.get('correlations', []))} corrélation(s), "
                           f"{len(stats.get('cramer', []))} association(s) de Cramér, "
                           f"{len(stats.get('groupes', []))} comparaison(s) de moyennes."),
        "limitations": ["Analyse bivariée descriptive uniquement — pas d'inférence causale."],
    }, jsonb_fields=("statistics", "limitations"))

    artifacts.write_contract(env.job_id, "agent05_stats",
                             {"analysis_job_id": analysis_job_id, "stats": stats})

    return {
        "status": "DONE",
        "result_reference": {
            "analysis_job_id": analysis_job_id,
            "correlations": len(stats.get("correlations", [])),
            "cramer": len(stats.get("cramer", [])),
            "groupes": len(stats.get("groupes", [])),
        },
        "warnings": [],
        "errors": [],
    }
