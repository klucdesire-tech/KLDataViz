# -*- coding: utf-8 -*-
"""Adaptateurs : pont entre les agents du système et le code réellement disponible."""
from . import ingestion, validator  # noqa: F401

ADAPTERS = {"AGT-01": ingestion.run, "AGT-10": validator.run}


def for_agent(agent_id: str):
    return ADAPTERS.get(agent_id)
