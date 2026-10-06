# -*- coding: utf-8 -*-
"""
job_manager : persistance des enveloppes de tâches, transitions d'état, reprise et journal d'audit.
"""
from __future__ import annotations

import uuid
from typing import Any

from . import contracts
from .config import SCHEMA
from .contracts import Envelope


class JobManager:
    """Gère le cycle de vie des jobs dans `hermes.jobs` et `hermes.audit_logs`."""

    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ audit
    def audit(self, event_type: str, message: str, *, job_id: str | None = None,
              agent_id: str | None = None, payload: dict | None = None) -> str:
        audit_id = f"AUD-{uuid.uuid4().hex[:12].upper()}"
        self.db.insert_json(
            "audit_logs",
            {"audit_id": audit_id, "job_id": job_id, "agent_id": agent_id, "event_type": event_type,
             "message": message, "payload": payload or {}},
            jsonb_fields=("payload",),
        )
        return audit_id

    # ------------------------------------------------------------------ écriture
    def create(self, env: Envelope) -> Envelope:
        problems = env.missing_contract_keys()
        if problems:
            raise ValueError(f"enveloppe incomplète : {problems}")
        self.db.insert_json(
            "jobs",
            {
                "job_id": env.job_id, "parent_job_id": env.parent_job_id, "agent_id": env.agent_id,
                "task_type": env.task_type, "objective": env.objective, "dataset_id": env.dataset_id,
                "dataset_version_id": env.dataset_version_id, "input_reference": env.input_reference,
                "parameters": env.parameters, "status": env.status, "result_reference": env.result_reference,
                "warnings": env.warnings, "errors": env.errors, "attempts": env.attempts,
                "max_attempts": env.max_attempts, "created_at": env.created_at,
            },
            jsonb_fields=("input_reference", "parameters", "result_reference", "warnings", "errors"),
        )
        self.audit("job_created", f"{env.agent_id} ← {env.task_type}", job_id=env.job_id, agent_id=env.agent_id,
                   payload={"objective": env.objective, "parameters": env.parameters})
        return env

    def start(self, env: Envelope) -> Envelope:
        import time as _time

        env._perf0 = _time.perf_counter()  # mesure de durée à précision suffisante
        env.transition("RUNNING", attempts=env.attempts + 1)
        self.db.execute(
            f"""UPDATE {SCHEMA}.jobs SET status=%s, started_at=now(), attempts=%s WHERE job_id=%s""",
            (env.status, env.attempts, env.job_id))
        self.audit("job_started", f"{env.agent_id} démarre {env.task_type} (tentative {env.attempts})",
                   job_id=env.job_id, agent_id=env.agent_id)
        return env

    def finish(self, env: Envelope, result: dict[str, Any], *, artifact_path: str | None = None) -> Envelope:
        import time as _time

        env.transition(result.get("status", "DONE"),
                       result_reference=result.get("result_reference") or {},
                       warnings=list(result.get("warnings") or []),
                       errors=list(result.get("errors") or []))
        perf0 = getattr(env, "_perf0", None)
        if perf0 is not None:
            env.duration_ms = int((_time.perf_counter() - perf0) * 1000)
        if env.dataset_id is None and env.result_reference.get("dataset_id"):
            env.dataset_id = env.result_reference["dataset_id"]
        if env.dataset_version_id is None and env.result_reference.get("dataset_version_id"):
            env.dataset_version_id = env.result_reference["dataset_version_id"]
        self.db.execute(
            f"""UPDATE {SCHEMA}.jobs SET status=%s, completed_at=now(), duration_ms=%s, result_reference=%s,
                    warnings=%s, errors=%s, dataset_id=%s, dataset_version_id=%s, artifact_path=%s
                WHERE job_id=%s""",
            (env.status, env.duration_ms, _j(env.result_reference), _j(env.warnings), _j(env.errors),
             env.dataset_id, env.dataset_version_id, artifact_path, env.job_id))
        event = {"DONE": "job_done", "FAILED": "job_failed", "SKIPPED": "job_skipped",
                 "REQUIRES_CORRECTION": "job_requires_correction"}.get(env.status, "job_finished")
        self.audit(event, f"{env.agent_id} : {env.status} ({env.duration_ms} ms)", job_id=env.job_id,
                   agent_id=env.agent_id,
                   payload={"result_reference": env.result_reference, "warnings": env.warnings, "errors": env.errors})
        return env

    def attach_artifact(self, job_id: str, path: str) -> None:
        self.db.execute(f"UPDATE {SCHEMA}.jobs SET artifact_path=%s WHERE job_id=%s", (path, job_id))

    # ------------------------------------------------------------------ reprise
    def retry_policy(self, error_class: str, *, attempt: int, max_attempts: int,
                     retryable: bool = True) -> dict[str, Any]:
        """Décision de reprise documentée (journalisée par l'orchestrateur)."""
        if not retryable:
            return {"policy": "ABORT", "reason": f"erreur non reprenable ({error_class})"}
        if attempt < max_attempts:
            if error_class in ("SystemExit", "KeyboardInterrupt"):
                return {"policy": "ABORT", "reason": "interruption utilisateur"}
            return {"policy": "RETRY", "reason": f"{error_class} — nouvelle tentative {attempt + 1}/{max_attempts}"}
        return {"policy": "ABORT", "reason": f"{error_class} — {attempt}/{max_attempts} tentatives épuisées"}

    # ------------------------------------------------------------------ lecture
    def get(self, job_id: str) -> dict | None:
        return self.db.one(f"SELECT * FROM {SCHEMA}.v_job_pipeline WHERE job_id = %s", (job_id,))

    def get_raw(self, job_id: str) -> dict | None:
        return self.db.one(f"SELECT * FROM {SCHEMA}.jobs WHERE job_id = %s", (job_id,))

    def children(self, parent_job_id: str) -> list[dict]:
        return self.db.query(
            f"SELECT * FROM {SCHEMA}.v_job_pipeline WHERE parent_job_id = %s ORDER BY created_at", (parent_job_id,))

    def recent(self, limit: int = 25) -> list[dict]:
        return self.db.query(f"SELECT * FROM {SCHEMA}.v_job_pipeline LIMIT %s", (limit,))

    def audit_trail(self, job_id: str | None = None, limit: int = 100) -> list[dict]:
        if job_id:
            return self.db.query(
                f"SELECT * FROM {SCHEMA}.audit_logs WHERE job_id = %s ORDER BY created_at", (job_id,))
        return self.db.query(f"SELECT * FROM {SCHEMA}.audit_logs ORDER BY created_at DESC LIMIT %s", (limit,))


def _j(value) -> Any:
    import psycopg.types.json

    return psycopg.types.json.Jsonb(value if value is not None else {})


def envelope_from_row(row: dict) -> Envelope:
    """Reconstruit une enveloppe à partir d'une ligne de `hermes.jobs` (reprise, inspection)."""
    return Envelope(
        agent_id=row["agent_id"], task_type=row["task_type"], objective=row.get("objective") or "",
        job_id=row["job_id"], parent_job_id=row.get("parent_job_id"),
        dataset_id=row.get("dataset_id"), dataset_version_id=row.get("dataset_version_id"),
        input_reference=row.get("input_reference") or {}, parameters=row.get("parameters") or {},
        status=row.get("status") or "QUEUED", result_reference=row.get("result_reference") or {},
        warnings=row.get("warnings") or [], errors=row.get("errors") or [],
        attempts=row.get("attempts") or 0, max_attempts=row.get("max_attempts") or 2,
        created_at=row["created_at"].isoformat() if hasattr(row.get("created_at"), "isoformat") else contracts.now_iso(),
    )
