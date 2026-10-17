# -*- coding: utf-8 -*-
"""Adaptateur AGT-08 VISUALIZATION → specs de graphiques adaptés (console KLDataViz).

Choisit un type de graphique adapté à chaque variable (histogramme pour le
numérique continu, barres pour le catégoriel) et journalise les specs dans
`hermes.visualizations`.
"""
from __future__ import annotations

from typing import Any

from .. import artifacts
from ..contracts import new_job_id
from .quality import _load_dataset


def _chart_for(series) -> str:
    import pandas as pd
    import numpy as np
    if pd.api.types.is_numeric_dtype(series):
        if series.dropna().nunique() <= 15:
            return "bar"
        return "histogram"
    return "bar"


def run(env, db, jobs) -> dict:
    dv = env.dataset_version_id or (env.parameters or {}).get("dataset_version_id") \
        or (env.input_reference or {}).get("dataset_version_id")
    if not dv:
        return {"status": "FAILED", "result_reference": {}, "warnings": [],
                "errors": ["dataset_version_id manquant : impossible de visualiser."]}

    df, err = _load_dataset(db, dv)
    if err:
        return {"status": "FAILED", "result_reference": {}, "warnings": [], "errors": [err]}

    inserted = 0
    for c in df.columns[:20]:  # plafond pour éviter l'explosion
        ctype = _chart_for(df[c])
        spec = {"variable": str(c), "type": ctype,
                "title": f"Distribution de {c}" if ctype == "histogram" else f"{c} (effectifs)"}
        db.insert_json("visualizations", {
            "visualization_id": new_job_id("VIZ"), "job_id": env.job_id,
            "chart_type": ctype, "source_reference": {"dataset_version_id": dv, "variable": str(c)},
            "spec": spec, "file_path": None,
        }, jsonb_fields=("source_reference", "spec"))
        inserted += 1

    artifacts.write_contract(env.job_id, "agent08_visualization",
                             {"visualizations": inserted})

    return {
        "status": "DONE",
        "result_reference": {"visualizations": inserted},
        "warnings": [],
        "errors": [],
    }
