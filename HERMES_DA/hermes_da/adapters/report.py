# -*- coding: utf-8 -*-
"""Adaptateur AGT-09 REPORTING → synthèse descriptive (console KLDataViz).

Génère un rapport synthétique (forme, complétude, statistiques clés) et le
journalise dans `hermes.reports`.
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
                "errors": ["dataset_version_id manquant : impossible de rapporter."]}

    df, err = _load_dataset(db, dv)
    if err:
        return {"status": "FAILED", "result_reference": {}, "warnings": [], "errors": [err]}

    rows, cols = int(len(df)), int(len(df.columns))
    missing = int(df.isna().sum().sum())
    missing_pct = round(missing / (rows * cols) * 100, 2) if rows and cols else 0.0
    numeric = df.select_dtypes(include=["number"]).columns.tolist()

    sections = {
        "forme": {"lignes": rows, "colonnes": cols},
        "complétude": {"cellules_vides": missing, "pct_vide": missing_pct},
        "colonnes": [str(c) for c in df.columns],
        "colonnes_numeriques": numeric,
        "exemple": df.head(3).astype(str).to_dict(orient="records"),
    }
    title = f"Rapport — {rows} lignes × {cols} colonnes"

    report_id = new_job_id("RPT")
    db.insert_json("reports", {
        "report_id": report_id, "job_id": env.job_id, "title": title,
        "report_format": "json", "sections": sections,
        "dataset_reference": {"dataset_id": env.dataset_id, "dataset_version_id": dv},
        "validation_id": None, "file_path": None,
    }, jsonb_fields=("sections", "dataset_reference"))

    artifacts.write_contract(env.job_id, "agent09_report",
                             {"report_id": report_id, "title": title})

    return {
        "status": "DONE",
        "result_reference": {"report_id": report_id, "title": title},
        "warnings": [],
        "errors": [],
    }
