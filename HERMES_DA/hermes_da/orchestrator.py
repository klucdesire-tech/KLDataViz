# -*- coding: utf-8 -*-
"""
AGENT 00 — MASTER ORCHESTRATOR.

Transforme une demande utilisateur en workflow analytique contrôlé :
REQUEST → PLAN → EXECUTION → QUALITY CHECK → ANALYSIS → VALIDATION → REPORT → RESPONSE.

Il ne calcule rien lui-même : il planifie, dispatche, surveille, collecte, décide des reprises,
demande la validation, puis compose la réponse finale. Toutes les étapes vont dans PostgreSQL.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import agents_registry, artifacts, contracts, jobs as jobs_module
from .adapters import for_agent
from .config import SCHEMA
from .contracts import Envelope
from .db import Database, jsonable

MASTER_ID = "AGT-00"
VALIDATOR_ID = "AGT-10"

# Mots-clés (français et anglais) → étape du pipeline
KEYWORDS: dict[str, tuple[str, ...]] = {
    "quality_check": ("qualité", "qualite", "quality", "dqa", "contrôle", "controle", "complétude",
                      "doublon", "manquant", "valeur manquante"),
    "clean": ("nettoy", "clean", "standardis", "normalis", "dédoublonn", "dedoublonn", "transforme",
              "corrige", "typage", "convertir"),
    "indicator": ("indicateur", "kpi", "taux", "couverture", "numérateur", "numerateur", "dénominateur",
                  "denominateur", "cascade", "tx_new", "tx_curr", "pvl", "hiv", "vih"),
    "analyze": ("analyse", "statistique", "corrélation", "correlation", "distribution", "tendance",
                "régression", "regression", "comparaison", "moyenne", "test"),
    "detect_anomalies": ("anomalie", "anomaly", "aberrant", "outlier", "rupture", "atypique"),
    "interpret": ("interprèt", "interprete", "explique", "comprendre", "pourquoi", "hypothèse", "hypothese"),
    "visualize": ("graphique", "visualis", "diagramme", "chart", "courbe", "carte"),
    "report": ("rapport", "report", "synthèse", "synthese", "pdf", "excel", "présentation", "presentation"),
    "dashboard": ("dashboard", "tableau de bord", "kpi"),
}

DEFAULT_PIPELINE_APPENDIX = ("validate",)


# ---------------------------------------------------------------------- planification
def _norm(s: str) -> str:
    """Normalise casse et accents pour un matching de mots-clés robuste."""
    out = str(s).lower()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("ë", "e"), ("à", "a"), ("â", "a"),
                 ("ä", "a"), ("î", "i"), ("ï", "i"), ("ô", "o"), ("ö", "o"), ("ù", "u"),
                 ("û", "u"), ("ü", "u"), ("ç", "c"), ("œ", "oe"), ("æ", "ae")):
        out = out.replace(a, b)
    return out


def plan(objective: str, *, source: str | None = None, dataset_version_id: str | None = None,
         parameters: dict | None = None) -> list[dict[str, Any]]:
    """Plan d'exécution déterministe et justifié (aucune conclusion calculée ici)."""
    text = _norm(objective or "")
    steps: list[dict[str, Any]] = []

    def add(task_type: str, reason: str) -> None:
        agent = agents_registry.by_task_type(task_type)
        steps.append({
            "order": len(steps) + 1, "task_type": task_type,
            "agent_id": agent["agent_id"], "agent_name": agent["name"],
            "available": agent["status"] == "IMPLEMENTED", "agent_status": agent["status"],
            "reason": reason,
        })

    if source:
        add("ingest", "une source a été fournie : import et création d'une version de travail")
    elif not dataset_version_id:
        add("ingest", "aucune donnée disponible : il faut d'abord importer une source")

    for task_type, words in KEYWORDS.items():
        hits = [w for w in words if _norm(w) in text]
        if hits:
            add(task_type, f"demande contenant {hits[:3]}")

    if not any(s["task_type"] not in ("ingest", "validate") for s in steps):
        # demande générique : parcours analytique de base
        for task_type, reason in (("quality_check", "contrôle qualité préalable systématique"),
                                  ("analyze", "analyse descriptive de base")):
            add(task_type, reason)

    add("validate", "validation indépendante obligatoire avant toute restitution")
    return steps


# Dépendances entre types de tâches : une étape ne démarre qu'une fois ses prérequis terminés.
# « validate » n'est pas listée : elle est toujours exécutée en dernière vague (contrôle final).
DEPENDENCIES: dict[str, set[str]] = {
    "ingest": set(),
    "quality_check": {"ingest"},
    "clean": {"ingest"},
    "indicator": {"ingest"},
    "analyze": {"ingest"},
    "detect_anomalies": {"ingest"},
    "visualize": {"ingest"},
    "dashboard": {"ingest"},
    "interpret": {"indicator", "analyze", "detect_anomalies"},
    "report": {"interpret"},
}


def _execution_levels(steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Regroupe les étapes en vagues topologiques.

    Au sein d'une vague, les étapes sont indépendantes (elles peuvent s'exécuter en
    parallèle) ; les vagues s'enchaînent séquentiellement dans l'ordre des dépendances.
    """
    types_present = {s["task_type"] for s in steps}
    done: set[str] = set()
    levels: list[list[dict[str, Any]]] = []
    remaining = list(steps)
    while remaining:
        ready: list[dict[str, Any]] = []
        rest: list[dict[str, Any]] = []
        for s in remaining:
            if s["task_type"] == "validate":
                rest.append(s)  # toujours la dernière vague
                continue
            prereq = DEPENDENCIES.get(s["task_type"], set()) & types_present
            if prereq <= done:
                ready.append(s)
            else:
                rest.append(s)
        if not ready:
            ready, rest = rest, []  # ne restent que des étapes validate
        levels.append(ready)
        for s in ready:
            done.add(s["task_type"])
        remaining = rest
    return levels


def _execute_parallel(step: dict[str, Any], objective: str, source: str | None,
                      parameters: dict | None, master_job_id: str,
                      dataset_version_id: str | None) -> tuple[Envelope, dict[str, Any]]:
    """Exécute une étape (hors ingest/validate) dans un thread avec connexion dédiée."""
    db = Database()
    manager = jobs_module.JobManager(db)
    try:
        env = Envelope(
            agent_id=step["agent_id"], task_type=step["task_type"], objective=objective,
            parent_job_id=master_job_id, dataset_version_id=dataset_version_id,
            input_reference={},
            parameters={"dataset_version_id": dataset_version_id},
            max_attempts=2,
        )
        outcome = dispatch(db, manager, env)
        return env, outcome
    finally:
        db.close()


# ---------------------------------------------------------------------- exécution
def dispatch(db: Database, manager: jobs_module.JobManager, env: Envelope) -> dict[str, Any]:
    """Dispatche une étape vers l'agent compétent (ou échoue proprement si non implémenté)."""
    adapter = for_agent(env.agent_id)
    manager.create(env)
    manager.start(env)

    if adapter is None:
        agent = agents_registry.by_id(env.agent_id) or {}
        reason = (f"agent {env.agent_id} ({agent.get('name')}) non implémenté — statut "
                  f"{agent.get('status')} : aucun résultat ne peut être produit")
        manager.audit("dispatch_refuse", reason, job_id=env.job_id, agent_id=env.agent_id)
        manager.finish(env, {"status": "SKIPPED", "result_reference": {}, "warnings": [],
                             "errors": [reason]})
        return {"status": "SKIPPED", "result_reference": {}, "warnings": [], "errors": [reason],
                "attempts": env.attempts}

    last_error: str | None = None
    while True:
        try:
            result = adapter(env, db, manager)
            last_error = None
            break
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__} : {exc}"
            decision = manager.retry_policy(type(exc).__name__, attempt=env.attempts,
                                            max_attempts=env.max_attempts)
            manager.audit("retry_decision", f"{decision['policy']} — {decision['reason']}",
                          job_id=env.job_id, agent_id=env.agent_id, payload=decision)
            if decision["policy"] == "RETRY" and env.attempts < env.max_attempts:
                env.transition("QUEUED", errors=env.errors + [last_error])
                manager.start(env)
                continue
            result = {"status": "FAILED", "result_reference": {}, "warnings": [],
                      "errors": [last_error], "retry_policy": decision}
            break

    contract = result.pop("_contract", None)
    artifact_path = (result.get("result_reference") or {}).get("contract_artifact")
    manager.finish(env, result, artifact_path=artifact_path)
    result["job_id"] = env.job_id
    result["agent_id"] = env.agent_id
    result["task_type"] = env.task_type
    result["attempts"] = env.attempts
    if contract is not None:
        result["_contract"] = contract
    return result


def run(objective: str, *, source: str | None = None, parameters: dict | None = None,
        db: Database | None = None, verbose: bool = False) -> dict[str, Any]:
    """Exécute une demande complète et renvoie la réponse finale structurée."""
    close_db = False
    if db is None:
        db = Database()
        close_db = True
    manager = jobs_module.JobManager(db)
    try:
        schema_info = db.ensure_schema()
        agents_registry.sync(db)
        parameters = dict(parameters or {})

        # ---------------- job maître (planification)
        master = Envelope(agent_id=MASTER_ID, task_type="plan", objective=objective,
                          input_reference={"source": source} if source else {},
                          parameters={"requested_parameters": parameters})
        manager.create(master)
        manager.start(master)
        steps = plan(objective, source=source, parameters=parameters)
        manager.audit("plan_ready", f"{len(steps)} étape(s) planifiée(s)", job_id=master.job_id, agent_id=MASTER_ID,
                      payload={"steps": steps})
        manager.finish(master, {"status": "DONE",
                                "result_reference": {"plan": steps, "objective": objective},
                                "warnings": [], "errors": []})

        results: dict[str, Any] = {}
        agents_used: list[dict[str, Any]] = []
        datasets_used: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        step_logs: list[dict[str, Any]] = []
        state: dict[str, Any] = {"current_dataset_version": None,
                                 "primary_job_id": None, "last_data_job_id": None}

        def build_env(step: dict[str, Any]) -> Envelope:
            env = Envelope(
                agent_id=step["agent_id"], task_type=step["task_type"], objective=objective,
                parent_job_id=master.job_id, dataset_version_id=state["current_dataset_version"],
                input_reference=({"path": source} if source and step["task_type"] == "ingest" else {}),
                parameters=(parameters if step["task_type"] == "ingest"
                            else {"dataset_version_id": state["current_dataset_version"]}),
                max_attempts=2,
            )
            if step["task_type"] == "validate":
                # On valide le résultat produit ; à défaut, le dernier job de données exécuté
                # (même en échec) — jamais le job de planification, qui ne produit aucun résultat.
                target = state["primary_job_id"] or state["last_data_job_id"]
                if target is None:
                    target = master.job_id
                env.parameters = {"target_job_id": target}
                env.input_reference = {"target_job_id": target}
            return env

        def collect(step: dict[str, Any], env: Envelope, outcome: dict[str, Any]) -> None:
            summary = {"order": step["order"], "agent_id": env.agent_id, "agent_name": step["agent_name"],
                       "task_type": env.task_type, "status": outcome["status"], "job_id": env.job_id,
                       "attempts": outcome.get("attempts"), "available": step["available"],
                       "result_reference": outcome.get("result_reference") or {}}
            step_logs.append(summary)
            results[env.task_type] = outcome
            agents_used.append({"agent_id": env.agent_id, "name": step["agent_name"], "status": outcome["status"]})
            warnings.extend(f"[{env.agent_id}] {w}" for w in (outcome.get("warnings") or []))
            errors.extend(f"[{env.agent_id}] {e}" for e in (outcome.get("errors") or []))

            ref = outcome.get("result_reference") or {}
            if ref.get("dataset_version_id"):
                state["current_dataset_version"] = ref["dataset_version_id"]
                state["primary_job_id"] = env.job_id
                datasets_used.append({"dataset_id": ref.get("dataset_id"),
                                      "dataset_version_id": ref.get("dataset_version_id"),
                                      "row_count": ref.get("row_count"), "column_count": ref.get("column_count")})
            if step["task_type"] == "ingest" and outcome["status"] == "DONE":
                state["primary_job_id"] = env.job_id
            if env.task_type not in ("plan", "validate") and outcome["status"] in ("DONE", "FAILED"):
                state["last_data_job_id"] = env.job_id

        # ---------------- exécution par vagues (étapes indépendantes en parallèle)
        for level in _execution_levels(steps):
            if len(level) == 1:
                step = level[0]
                env = build_env(step)
                outcome = dispatch(db, manager, env)
                collect(step, env, outcome)
            else:
                with ThreadPoolExecutor(max_workers=len(level)) as pool:
                    futures = {step["task_type"]: pool.submit(
                        _execute_parallel, step, objective, source, parameters,
                        master.job_id, state["current_dataset_version"]) for step in level}
                for step in level:  # collecte dans l'ordre du plan (déterministe)
                    env, outcome = futures[step["task_type"]].result()
                    collect(step, env, outcome)

        validation_status = (results.get("validate", {}).get("result_reference") or {}).get("decision", "NON_EVALUEE")

        # ---------------- rapport : uniquement après validation favorable
        reporting_allowed = validation_status in ("APPROVED", "APPROVED_WITH_WARNINGS")
        reporting_step = next((s for s in steps if s["task_type"] == "report"), None)
        final_output: dict[str, Any] = {
            "validation_status": validation_status,
            "reporting_authorized": reporting_allowed,
            "report_blocked_reason": None,
            "deliverable": None,
        }
        if not reporting_allowed:
            final_output["report_blocked_reason"] = ("rapport non autorisé : validation non favorable "
                                                     f"({validation_status})")
        elif reporting_step is None:
            final_output["note"] = "validation favorable ; aucun rapport demandé dans cette demande"
        elif not reporting_step["available"]:
            final_output["report_blocked_reason"] = ("validation favorable, mais l'agent AGT-09 REPORTING "
                                                     "n'est pas encore implémenté (statut DECLARED)")
        else:
            final_output["deliverable"] = {"message": "prêt pour la production du rapport"}

        manager.audit("workflow_complete", f"validation = {validation_status}",
                      job_id=master.job_id, agent_id=MASTER_ID,
                      payload={"steps": len(steps), "errors": len(errors), "warnings": len(warnings)})

        return jsonable({
            "job_id": master.job_id,
            "objective": objective,
            "plan": steps,
            "agents_used": agents_used,
            "datasets_used": datasets_used,
            "results": {k: {kk: vv for kk, vv in v.items() if kk != "_contract"} for k, v in results.items()},
            "warnings": warnings,
            "errors": errors,
            "validation_status": validation_status,
            "final_output": final_output,
            "steps": step_logs,
            "database": {"schema": SCHEMA, "tables": len(schema_info["tables"]),
                         "views": len(schema_info["views"]), "database": schema_info["database"]},
            "implemented_agents": agents_registry.implemented(),
        })
    finally:
        if close_db:
            db.close()
