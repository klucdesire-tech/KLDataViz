# -*- coding: utf-8 -*-
"""Adaptateur AGT-04 INDICATOR ANALYTICS → calcul déterministe d'indicateurs.

À partir d'une version ingérée, identifie la colonne « indicateur » (le libellé
de ce qui est mesuré), la colonne « mesure » (numérique) et les dimensions.
Enregistre chaque indicateur (`hermes.indicators`) et calcule des agrégats réels
(total, par période, par dimension) dans `hermes.indicator_results`.

Règle absolue : aucune formule inventée — uniquement des agrégations calculées
sur les données (somme / moyenne), jamais de ratio numérateur/dénominateur deviné.
"""
from __future__ import annotations

import re
import hashlib
from typing import Any

from .. import artifacts
from ..contracts import new_job_id
from .quality import _load_dataset

LABEL_CANDIDATES = ("indicator", "indicateur", "kpi", "label", "libelle", "libellé", "metric", "measure")
PERIOD_CANDIDATES = ("quarter", "period", "periode", "période", "month", "annee", "année", "year", "date", "trimestre")


def _slug(s: str) -> str:
    x = str(s).lower()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("ç", "c"),
                 ("ô", "o"), ("î", "i"), ("û", "u")):
        x = x.replace(a, b)
    x = re.sub(r"[^a-z0-9]+", "_", x).strip("_")
    return x or "ind"


def _find_label_col(df) -> str | None:
    """Colonne qui porte le nom de l'indicateur (sinon None)."""
    cols = [str(c) for c in df.columns]
    for c in cols:
        if c.lower() in LABEL_CANDIDATES:
            return c
    # sinon : colonne catégorielle à cardinalité moyenne (10..300) = libellés probables
    import pandas as pd
    for c in df.select_dtypes(exclude=["number"]).columns:
        n = int(df[c].nunique(dropna=True))
        if 10 <= n <= 300:
            return str(c)
    return None


def run(env, db, jobs) -> dict:
    dv = env.dataset_version_id or (env.parameters or {}).get("dataset_version_id") \
        or (env.input_reference or {}).get("dataset_version_id")
    if not dv:
        return {"status": "FAILED", "result_reference": {}, "warnings": [],
                "errors": ["dataset_version_id manquant : impossible de calculer des indicateurs."]}

    df, err = _load_dataset(db, dv)
    if err:
        return {"status": "FAILED", "result_reference": {}, "warnings": [], "errors": [err]}

    import pandas as pd
    import numpy as np

    label_col = _find_label_col(df)
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    # la mesure = la (première) colonne numérique non-identifiante
    measure_col = None
    for c in numeric_cols:
        if not re.search(r"(^|_)(id|key|code|year|annee|quarter)$", str(c).lower()):
            measure_col = str(c)
            break
    if measure_col is None and numeric_cols:
        measure_col = numeric_cols[0]

    dim_cols = [str(c) for c in df.columns if str(c) not in (label_col, measure_col)
                and not re.search(r"(^|_)(id|key)$", str(c).lower())]
    period_col = next((c for c in dim_cols if c.lower() in PERIOD_CANDIDATES), None)

    # ------------------------------------------------------------------ enregistrement
    labels = sorted(df[label_col].dropna().unique()) if label_col else [None]
    indicators = 0
    results = 0

    for label in labels:
        sub = df if label is None else df[df[label_col] == label]
        if sub.empty:
            continue
        name = str(label) if label is not None else f"total_{measure_col}"
        code = f"{_slug(name)[:50]}_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}"
        indicator_id = f"IND-{code}"
        # un libellé = un indicateur ; la formule est l'agrégat honnête calculé
        formula = f"somme de {measure_col}"
        db.insert_json("indicators", {
            "indicator_id": indicator_id, "code": code, "name": name,
            "description": f"Indicateur « {name} » calculé depuis {dv}.",
            "numerator": measure_col, "denominator": None, "formula": formula,
            "population": None, "period_type": period_col, "exclusions": [],
            "dataset_id": env.dataset_id, "status": "ACTIF",
        }, jsonb_fields=("exclusions",), on_conflict="ON CONFLICT (indicator_id) DO NOTHING")
        indicators += 1

        # dimensions enregistrées
        for d in dim_cols[:6]:
            db.insert_json("indicator_dimensions", {
                "indicator_id": indicator_id, "dimension": str(d),
                "value": "toutes", "source_column": str(d),
            }, on_conflict="ON CONFLICT (indicator_id, dimension, value) DO NOTHING")

        # ------- résultats : total + par période + par dimension
        def _emit(period, dims, num, den, unit):
            nonlocal results
            db.insert_json("indicator_results", {
                "result_id": new_job_id("IR"), "indicator_id": indicator_id,
                "job_id": env.job_id, "dataset_version_id": dv, "period": period,
                "dimensions": dims, "numerator_value": num, "denominator_value": den,
                "value": num, "unit": unit,
            }, jsonb_fields=("dimensions",))
            results += 1

        total = float(sub[measure_col].sum()) if pd.notna(sub[measure_col].sum()) else 0.0
        _emit("total", {}, total, None, "valeur")

        if period_col and sub[period_col].notna().any():
            for p, g in sub.groupby(period_col, dropna=True):
                _emit(str(p), {}, float(g[measure_col].sum()), None, "valeur")

        for d in dim_cols[:4]:
            if d == period_col:
                continue
            for v, g in sub.groupby(d, dropna=True):
                _emit(None, {str(d): str(v)}, float(g[measure_col].sum()), None, "valeur")

    artifacts.write_contract(env.job_id, "agent04_indicators",
                             {"indicators": indicators, "results": results})

    return {
        "status": "DONE",
        "result_reference": {"indicators": indicators, "results": results,
                             "mesure": measure_col, "label": label_col},
        "warnings": [],
        "errors": [],
    }
