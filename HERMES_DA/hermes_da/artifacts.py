# -*- coding: utf-8 -*-
"""Écriture et lecture des artefacts du système (contrats, résultats, preuves) en JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ARTIFACTS_DIR, CONTRACTS_DIR, ensure_dirs


def _write(directory: Path, job_id: str, kind: str, payload: Any) -> Path:
    ensure_dirs()
    path = directory / f"{job_id}__{kind}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def write_artifact(job_id: str, kind: str, payload: Any) -> str:
    """Artefact de résultat (contract d'agent, preuve de validation…)."""
    return str(_write(ARTIFACTS_DIR, job_id, kind, payload))


def write_contract(job_id: str, kind: str, payload: Any) -> str:
    """Contrat brut renvoyé par un agent (conservé tel quel pour audit)."""
    return str(_write(CONTRACTS_DIR, job_id, kind, payload))


def read(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
