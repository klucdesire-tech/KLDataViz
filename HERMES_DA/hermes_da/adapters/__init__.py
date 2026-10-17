# -*- coding: utf-8 -*-
"""Adaptateurs : pont entre les agents du système et le code réellement disponible."""
from . import ingestion, validator, quality, stats, anomalies, viz, report, indicators, interpretation, dashboard  # noqa: F401

ADAPTERS = {"AGT-01": ingestion.run, "AGT-02": quality.run, "AGT-04": indicators.run,
            "AGT-05": stats.run, "AGT-06": anomalies.run, "AGT-07": interpretation.run,
            "AGT-08": viz.run, "AGT-09": report.run, "AGT-10": validator.run,
            "AGT-11": dashboard.run}


def for_agent(agent_id: str):
    return ADAPTERS.get(agent_id)
