# -*- coding: utf-8 -*-
"""Configuration du système HERMES DATA ANALYTICS.

Découvre l'existant (console KLDataViz + agent 01 d'ingestion) sans le modifier.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERMES_DA_DIR = Path(__file__).resolve().parent.parent            # .../HERMES_DA
PROJET_DIR = HERMES_DA_DIR.parent                                  # .../Projets
CONSOLE_DIR = PROJET_DIR / "KLDataViz_console"                     # existe déjà
INGESTION_PKG = CONSOLE_DIR / "ingestion"                          # agent 01 (implémenté)
STORAGE_DIR = HERMES_DA_DIR / "storage"                            # artefacts du système
ARTIFACTS_DIR = STORAGE_DIR / "artifacts"
CONTRACTS_DIR = STORAGE_DIR / "contracts"
TESTS_DIR = HERMES_DA_DIR / "tests"

DB_NAME = os.environ.get("HERMES_DA_DB", os.environ.get("KLINGEST_PG_DB", "kldataviz"))
DB_HOST = os.environ.get("KLINGEST_PG_HOST", "127.0.0.1")
DB_PORT = os.environ.get("KLINGEST_PG_PORT", "5432")
SCHEMA = "hermes"
INGESTION_SCHEMA = "ingestion"


def dsn(dbname: str | None = None) -> str:
    """DSN PostgreSQL (variable d'environnement prioritaire)."""
    if os.environ.get("HERMES_DA_DSN"):
        return os.environ["HERMES_DA_DSN"]
    if os.environ.get("KLINGEST_PG_DSN"):
        return os.environ["KLINGEST_PG_DSN"]
    parts = [f"host={DB_HOST}", f"port={DB_PORT}", f"dbname={dbname or DB_NAME}"]
    if os.environ.get("KLINGEST_PG_USER"):
        parts.append(f"user={os.environ['KLINGEST_PG_USER']}")
    return " ".join(parts)


def register_existing_ingestion_agent() -> bool:
    """Rend importable l'agent 01 existant (aucune copie, aucune modification)."""
    if not INGESTION_PKG.exists():
        return False
    path = str(CONSOLE_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    return True


def ensure_dirs() -> None:
    for d in (STORAGE_DIR, ARTIFACTS_DIR, CONTRACTS_DIR, TESTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def describe() -> dict:
    return {
        "hermes_da_dir": str(HERMES_DA_DIR),
        "console_dir": str(CONSOLE_DIR),
        "console_present": CONSOLE_DIR.exists(),
        "ingestion_agent_present": INGESTION_PKG.exists(),
        "postgres": {"host": DB_HOST, "port": DB_PORT, "database": DB_NAME, "schema": SCHEMA},
    }
