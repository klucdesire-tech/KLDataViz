# -*- coding: utf-8 -*-
"""Adaptateur AGT-06 ANOMALY DETECTION → détection IQR (console KLDataViz).

Détecte les valeurs aberrantes (méthode IQR 1.5) sur les colonnes numériques et
journalise chaque anomalie dans `hermes.anomalies`.
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
                "errors": ["dataset_version_id manquant : impossible de détecter les anomalies."]}

    df, err = _load_dataset(db, dv)
    if err:
        return {"status": "FAILED", "result_reference": {}, "warnings": [], "errors": [err]}

    import numpy as np

    base_id = new_job_id("ANO")
    anomalies: list[dict[str, Any]] = []

    for c in df.select_dtypes(include=[np.number]).columns:
        s = df[c].dropna()
        if len(s) < 4:
            continue
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        out = s[(s < lo) | (s > hi)]
        if len(out):
            anomalies.append({
                "anomaly_id": new_job_id("ANO"), "job_id": env.job_id,
                "dataset_version_id": dv, "anomaly_type": "outlier_iqr",
                "variable": str(c), "scope": "univarié",
                "evidence": {"count": int(len(out)), "min_outlier": float(out.min()),
                             "max_outlier": float(out.max()), "lo": float(lo), "hi": float(hi)},
                "method": "IQR 1.5", "severity": "warning",
                "confidence": 0.8, "possible_explanations": ["erreur de saisie", "valeur réelle extrême"],
                "verification_required": True,
            })

    inserted = 0
    for a in anomalies:
        db.insert_json("anomalies", {
            "anomaly_id": a["anomaly_id"], "job_id": a["job_id"],
            "dataset_version_id": a["dataset_version_id"], "anomaly_type": a["anomaly_type"],
            "variable": a["variable"], "scope": a["scope"], "evidence": a["evidence"],
            "method": a["method"], "severity": a["severity"], "confidence": a["confidence"],
            "possible_explanations": a["possible_explanations"],
            "verification_required": a["verification_required"],
        }, jsonb_fields=("evidence", "possible_explanations"))
        inserted += 1

    artifacts.write_contract(env.job_id, "agent06_anomalies",
                             {"anomalies": len(anomalies)})

    return {
        "status": "DONE",
        "result_reference": {"anomalies": inserted},
        "warnings": [],
        "errors": [],
    }
