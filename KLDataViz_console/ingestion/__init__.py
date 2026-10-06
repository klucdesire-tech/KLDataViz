# -*- coding: utf-8 -*-
"""
Agent Data Ingestion de KLDataViz.

Point d'entrée programmatique :
    from ingestion import ingest
    contrat = ingest(r"C:\\chemin\\fichier.xlsx", sheet="Données")

Ligne de commande :
    python -m ingestion <source> [--sheet NOM] [--table T] [--limit N] [--json sortie.json]
"""
from .agent import INGESTED_BY, ingest, ingest_to_json  # noqa: F401
from .store import Registry  # noqa: F401
from .versioner import Versioner  # noqa: F401

__all__ = ["ingest", "ingest_to_json", "Registry", "Versioner", "INGESTED_BY"]
__version__ = "1.0.0"
