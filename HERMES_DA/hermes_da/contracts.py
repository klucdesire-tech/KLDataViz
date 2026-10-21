# -*- coding: utf-8 -*-
"""
Contrat d'échange entre agents (protocole de communication).

Toute tâche circule sous forme d'ENVELOPPE (dict) et est persistée dans `hermes.jobs`.
Les agents échangent des RÉFÉRENCES (ids, chemins, clés JSON), jamais de gros volumes.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

# ------------------------------------------------------------------ vocabulaire
TASK_TYPES = {
    "plan": "Établir le plan d'exécution",
    "ingest": "Importer une source et créer une version de travail",
    "quality_check": "Contrôler la qualité d'un dataset",
    "clean": "Nettoyer / transformer une version de travail",
    "indicator": "Construire et calculer des indicateurs",
    "analyze": "Analyse statistique (univariée / bivariée / multivariée)",
    "detect_anomalies": "Détecter les anomalies",
    "interpret": "Interpréter des résultats validés",
    "visualize": "Produire des visualisations",
    "report": "Produire un rapport",
    "validate": "Valider indépendamment des résultats",
    "dashboard": "Générer un tableau de bord dynamique et intuitif",
}

STATUSES = {"QUEUED", "RUNNING", "DONE", "FAILED", "SKIPPED", "REQUIRES_CORRECTION"}
TERMINAL_STATUSES = {"DONE", "FAILED", "SKIPPED", "REQUIRES_CORRECTION"}

VALIDATION_DECISIONS = {"APPROVED", "APPROVED_WITH_WARNINGS", "REQUIRES_CORRECTION", "REJECTED"}

# Politique de reprise décidée par l'orchestrateur après un échec
RETRY_POLICIES = {"RETRY", "RETRY_WITH_PARAMS", "CHANGE_METHOD", "ABORT"}

# Clés obligatoires de l'enveloppe, conformément à la spécification
ENVELOPE_KEYS = [
    "job_id", "dataset_id", "agent_id", "task_type", "input_reference", "parameters",
    "status", "result_reference", "warnings", "errors", "created_at", "completed_at",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_job_id(prefix: str = "JOB") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6].upper()}"


@dataclass
class Envelope:
    """Enveloppe de tâche échangée entre agents."""

    agent_id: str
    task_type: str
    objective: str = ""
    job_id: str = field(default_factory=new_job_id)
    parent_job_id: str | None = None
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    input_reference: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    status: str = "QUEUED"
    result_reference: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 2
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        if self.task_type not in TASK_TYPES:
            raise ValueError(f"task_type inconnu : {self.task_type} (attendus : {sorted(TASK_TYPES)})")
        if self.status not in STATUSES:
            raise ValueError(f"status inconnu : {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def missing_contract_keys(self) -> list[str]:
        data = self.to_dict()
        return [k for k in ENVELOPE_KEYS if k not in data]

    def transition(self, status: str, **kwargs: Any) -> "Envelope":
        if status not in STATUSES:
            raise ValueError(f"status inconnu : {status}")
        self.status = status
        if status == "RUNNING" and not self.started_at:
            self.started_at = now_iso()
        if status in TERMINAL_STATUSES:
            self.completed_at = now_iso()
            if self.started_at:
                start = datetime.fromisoformat(self.started_at)
                end = datetime.fromisoformat(self.completed_at)
                self.duration_ms = int((end - start).total_seconds() * 1000)
        for key, value in kwargs.items():
            if not hasattr(self, key):
                raise AttributeError(f"champ inconnu dans l'enveloppe : {key}")
            setattr(self, key, value)
        return self


def validate_envelope(envelope: dict[str, Any]) -> list[str]:
    """Validation structurelle d'une enveloppe reçue (utilisée par les agents et le Validator)."""
    problems: list[str] = []
    for key in ENVELOPE_KEYS:
        if key not in envelope:
            problems.append(f"clé manquante : {key}")
    if envelope.get("task_type") and envelope["task_type"] not in TASK_TYPES:
        problems.append(f"task_type invalide : {envelope['task_type']}")
    if envelope.get("status") and envelope["status"] not in STATUSES:
        problems.append(f"status invalide : {envelope['status']}")
    for key in ("input_reference", "parameters", "result_reference"):
        if key in envelope and not isinstance(envelope[key], (dict, type(None))):
            problems.append(f"{key} doit être un objet JSON")
    for key in ("warnings", "errors"):
        if key in envelope and not isinstance(envelope[key], list):
            problems.append(f"{key} doit être une liste")
    if envelope.get("status") in TERMINAL_STATUSES and not envelope.get("completed_at"):
        problems.append("completed_at requis pour un statut terminal")
    return problems


def make_result(status: str, *, warnings: list[str] | None = None, errors: list[str] | None = None,
                **result_reference: Any) -> dict[str, Any]:
    """Bloc de retour normalisé d'un agent."""
    if status not in STATUSES:
        raise ValueError(f"status invalide : {status}")
    return {"status": status, "result_reference": result_reference,
            "warnings": list(warnings or []), "errors": list(errors or []), "completed_at": now_iso()}
