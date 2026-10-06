# -*- coding: utf-8 -*-
"""
HERMES DATA ANALYTICS — fondation du système multi-agents.

    from hermes_da import orchestrator
    reponse = orchestrator.run("ingérer puis contrôler la qualité", source=r"C:\\data\\x.csv")
"""
from . import agents_registry, artifacts, config, contracts, db, jobs, orchestrator  # noqa: F401

__all__ = ["orchestrator", "agents_registry", "contracts", "db", "jobs", "artifacts", "config"]
__version__ = "0.2.0"
