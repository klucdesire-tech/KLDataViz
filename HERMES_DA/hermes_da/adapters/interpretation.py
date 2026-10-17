# -*- coding: utf-8 -*-
"""Adaptateur AGT-07 INTERPRETATION → interprétation objective (LLM ou règles).

Relit les résultats validés produits par les agents précédents (statistiques,
anomalies, indicateurs) pour la version de travail courante, puis rédige une
interprétation en 4 sections distinctes : FAITS (mesurés), INTERPRÉTATIONS,
HYPOTHÈSES (à vérifier), LIMITES. La corrélation n'est jamais présentée comme
une causalité. Sans LLM, un texte par règles est produit à la place.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from .. import artifacts, config
from .quality import _load_dataset

SYSTEM = """Tu es Hermes, analyste de données senior (santé publique, BI).
On te donne des RÉSULTATS MESURÉS (statistiques, anomalies, indicateurs calculés sur les données).
Rédige une interprétation objective en français, en 4 sections distinctes et titrées :
- FAITS : uniquement des chiffres mesurés, sans jugement.
- INTERPRÉTATIONS : ce que ces chiffres signifient.
- HYPOTHÈSES : causes possibles, toujours formulées comme « possible » / « probable ».
- LIMITES : ce que les données ne permettent PAS de conclure.
Règles absolues :
- Ne jamais transformer une corrélation en causalité.
- Ne jamais affirmer une hypothèse comme un fait.
- Dire explicitement quand les données ne permettent pas de conclure.
- Réponse courte (moins de 250 mots), pour un responsable non technique.
Réponds en texte brut (pas de JSON)."""


def _llm_interpret(context: str) -> str | None:
    """Appelle le LLM (config de la console) pour interpréter, ou None si indisponible."""
    cfg_path = os.path.join(config.CONSOLE_DIR, "llm_config.json")
    if not os.path.isfile(cfg_path):
        return None
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        key = cfg.get("api_key", "")
        if not key or "VOTRE_CLE" in str(key):
            return None
        body = json.dumps({
            "model": cfg.get("model", "deepseek-v4-pro"),
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": context}],
            "max_tokens": 8000, "temperature": 0,
        }).encode("utf-8")
        req = urllib.request.Request(cfg["base_url"], data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key}"})
        r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
        return (r.get("choices") or [{}])[0].get("message", {}).get("content")
    except Exception:
        return None


def _rule_interpretation(context: dict) -> str:
    """Interprétation par règles quand le LLM est indisponible."""
    facts = [f"{context['lignes']} lignes analysées."]
    if context["correlations"]:
        facts.append(f"{context['correlations']} corrélation(s) détectée(s).")
    if context["anomalies"]:
        facts.append(f"{context['anomalies']} anomalie(s) détectée(s).")
    if context["indicateurs"]:
        facts.append(f"{context['indicateurs']} indicateur(s) calculé(s).")
    return (
        "FAITS : " + " ".join(facts) + "\n"
        "INTERPRÉTATIONS : synthèse descriptive des données ; les associations observées "
        "sont des corrélations, pas des causalités.\n"
        "HYPOTHÈSES : aucune avancée par le moteur de règles (LLM indisponible).\n"
        "LIMITES : analyse descriptive uniquement, sans inférence causale ni test d'hypothèse."
    )


def run(env, db, jobs) -> dict:
    dv = env.dataset_version_id or (env.parameters or {}).get("dataset_version_id") \
        or (env.input_reference or {}).get("dataset_version_id")
    if not dv:
        return {"status": "FAILED", "result_reference": {}, "warnings": [],
                "errors": ["dataset_version_id manquant : impossible d'interpréter."]}

    # ---- relire les résultats des agents précédents (par version de travail)
    df, _ = _load_dataset(db, dv)
    stats_rows = db.query(
        "SELECT r.statistics FROM hermes.analysis_results r "
        "JOIN hermes.analysis_jobs j ON r.analysis_job_id = j.analysis_job_id "
        "WHERE j.dataset_version_id = %s", (dv,))
    anomalies_rows = db.query(
        "SELECT anomaly_type, variable, evidence, severity FROM hermes.anomalies "
        "WHERE dataset_version_id = %s", (dv,))
    indicators_rows = db.query(
        "SELECT i.name, i.formula, COUNT(*) AS n, SUM(r.value) AS total "
        "FROM hermes.indicator_results r "
        "JOIN hermes.indicators i ON r.indicator_id = i.indicator_id "
        "WHERE r.dataset_version_id = %s GROUP BY i.name, i.formula", (dv,))

    context = {
        "lignes": len(df) if df is not None else 0,
        "correlations": 0,
        "anomalies": len(anomalies_rows),
        "indicateurs": len(indicators_rows),
        "stats": stats_rows[:1] if stats_rows else [],
        "anomalies_detail": anomalies_rows[:15],
        "indicateurs_detail": indicators_rows[:15],
    }
    if stats_rows:
        s = stats_rows[0].get("statistics") or {}
        context["correlations"] = len(s.get("correlations", [])) if isinstance(s, dict) else 0

    # ---- construire le prompt et interpréter (une seule passe LLM)
    prompt = json.dumps(context, ensure_ascii=False, default=str)[:20000]
    llm_result = _llm_interpret(prompt)
    ia = llm_result is not None
    interpretation = llm_result or _rule_interpretation(context)

    # ---- écrire l'interprétation sur les analysis_results concernés
    updated = 0
    if stats_rows:
        updated = db.execute(
            "UPDATE hermes.analysis_results SET interpretation = %s "
            "WHERE analysis_job_id IN (SELECT analysis_job_id FROM hermes.analysis_jobs "
            "WHERE dataset_version_id = %s)", (interpretation, dv))

    artifacts.write_contract(env.job_id, "agent07_interpretation",
                             {"interpretation": interpretation, "ia": ia})

    return {
        "status": "DONE",
        "result_reference": {"interpretation": interpretation, "ia": ia,
                             "updated": updated},
        "warnings": [] if ia else ["LLM indisponible — interprétation par règles."],
        "errors": [],
    }
