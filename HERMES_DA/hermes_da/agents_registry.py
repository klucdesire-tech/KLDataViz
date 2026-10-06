# -*- coding: utf-8 -*-
"""
Catalogue des agents du système HERMES DATA ANALYTICS.

`status` = IMPLEMENTED (code réel disponible et branché) ou DECLARED (spécifié, pas encore codé).
Un agent DECLARED ne produit JAMAIS de résultat : tout dispatch le concernant échoue proprement.
"""
from __future__ import annotations

from typing import Any

AGENTS: list[dict[str, Any]] = [
    {
        "agent_id": "AGT-00", "ordinal": 0, "name": "MASTER ORCHESTRATOR",
        "role": "Coordonner les agents spécialisés ; planifier, dispatcher, surveiller, collecter, "
                "demander la validation. Ne calcule jamais à la place des agents.",
        "responsibilities": [
            "comprendre la demande et identifier l'objectif analytique",
            "identifier les données et opérations nécessaires",
            "construire le plan d'exécution et sélectionner les agents",
            "transmettre les tâches et surveiller leur exécution",
            "récupérer les résultats et détecter les incohérences",
            "décider d'une reprise (RETRY / RETRY_WITH_PARAMS / CHANGE_METHOD / ABORT)",
            "envoyer les résultats au Validator avant tout rapport",
            "retourner la réponse finale structurée",
        ],
        "tools": ["task_planner", "agent_dispatcher", "job_manager", "postgres_read", "postgres_write",
                  "agent_status", "result_collector", "validation_request", "audit_logger"],
        "permissions": {"read": ["hermes.*", "ingestion.*"], "write": ["hermes.jobs", "hermes.audit_logs"],
                        "forbidden": ["modifier une source", "écrire des résultats métier"]},
        "status": "IMPLEMENTED",
    },
    {
        "agent_id": "AGT-01", "ordinal": 1, "name": "DATA INGESTION AGENT",
        "role": "Importer fichiers et bases sans modifier les sources ; créer une version de travail ; "
                "enregistrer les métadonnées.",
        "responsibilities": ["détecter format, encodage, séparateur, feuilles, colonnes, types, structure",
                             "compter lignes et colonnes", "identifier les identifiants potentiels",
                             "créer la version de travail", "enregistrer les métadonnées PostgreSQL"],
        "tools": ["csv_reader", "excel_reader", "json_reader", "parquet_reader", "postgres_connector",
                  "file_detector", "encoding_detector", "schema_detector", "dataset_versioner"],
        "permissions": {"read": ["sources (lecture seule)"], "write": ["ingestion.*", "storage/raw", "storage/work"],
                        "forbidden": ["écrire dans la source"]},
        "status": "IMPLEMENTED",
    },
    {
        "agent_id": "AGT-02", "ordinal": 2, "name": "DATA QUALITY AGENT",
        "role": "Évaluer objectivement la qualité d'un dataset (complétude, unicité, validité, cohérence, conformité).",
        "responsibilities": ["détecter valeurs manquantes, doublons, formats incorrects",
                             "contrôler plages, incohérences temporelles, inter-variables, inter-indicateurs",
                             "classer INFO / WARNING / ERROR / CRITICAL",
                             "distinguer anomalie statistique, erreur confirmée, erreur potentielle",
                             "produire quality_summary / metrics / recommendations"],
        "tools": ["missing_detector", "duplicate_detector", "type_validator", "range_validator",
                  "format_validator", "consistency_checker", "business_rule_engine", "outlier_detector",
                  "quality_scorer", "quality_reporter"],
        "permissions": {"read": ["hermes.variables", "storage/work"], "write": ["hermes.quality_checks"]},
        "status": "DECLARED",
    },
    {
        "agent_id": "AGT-03", "ordinal": 3, "name": "DATA ENGINEERING AGENT",
        "role": "Préparer les données pour l'analyse sans détruire l'original : nettoyage, standardisation, "
                "typage, déduplication, jointures, agrégations, variables dérivées.",
        "responsibilities": ["exécuter les transformations demandées",
                             "tracer chaque opération (avant/après, paramètres)"],
        "tools": ["pandas", "polars", "numpy", "SQLAlchemy", "PostgreSQL", "data_transformer",
                  "data_cleaner", "data_joiner", "data_aggregator"],
        "permissions": {"read": ["storage/work"], "write": ["hermes.cleaning_operations", "storage/work"]},
        "status": "DECLARED",
    },
    {
        "agent_id": "AGT-04", "ordinal": 4, "name": "INDICATOR ANALYTICS AGENT",
        "role": "Construire, calculer, vérifier et analyser des indicateurs (numérateur, dénominateur, "
                "formule, population, période, dimensions, exclusions).",
        "responsibilities": ["enregistrer et vérifier les définitions d'indicateurs",
                             "calculer par dimension et par période",
                             "ne jamais inventer une formule"],
        "tools": ["indicator_registry", "formula_engine", "dimension_engine", "aggregation_engine",
                  "SQL_engine", "indicator_validator"],
        "permissions": {"read": ["hermes.variables", "storage/work"], "write": ["hermes.indicators",
                        "hermes.indicator_dimensions", "hermes.indicator_results"]},
        "status": "DECLARED",
    },
    {
        "agent_id": "AGT-05", "ordinal": 5, "name": "STATISTICAL ANALYSIS AGENT",
        "role": "Analyses univariées, bivariées et multivariées avec vérification des hypothèses.",
        "responsibilities": ["décrire, comparer, modéliser", "justifier chaque méthode et documenter ses limites"],
        "tools": ["numpy", "pandas", "scipy", "statsmodels", "scikit-learn"],
        "permissions": {"read": ["storage/work", "hermes.variables"],
                        "write": ["hermes.analysis_jobs", "hermes.analysis_results"]},
        "status": "DECLARED",
    },
    {
        "agent_id": "AGT-06", "ordinal": 6, "name": "ANOMALY DETECTION AGENT",
        "role": "Identifier observations, tendances et relations inhabituelles.",
        "responsibilities": ["appliquer IQR, z-score, Isolation Forest, LOF, détection temporelle, règles métier",
                             "classer les anomalies et fournir des preuves", "ne jamais inventer une cause"],
        "tools": ["scipy", "scikit-learn", "iqr", "zscore", "isolation_forest", "lof",
                  "time_series_detector", "business_rule_engine"],
        "permissions": {"read": ["storage/work", "hermes.indicator_results"], "write": ["hermes.anomalies"]},
        "status": "DECLARED",
    },
    {
        "agent_id": "AGT-07", "ordinal": 7, "name": "INTERPRETATION AGENT",
        "role": "Transformer des résultats validés en interprétations objectives, en distinguant fait, "
                "résultat calculé, interprétation, hypothèse et limite.",
        "responsibilities": ["rédiger observations, constats, hypothèses, limites",
                             "ne jamais transformer une corrélation en causalité",
                             "dire explicitement quand les données ne permettent pas de conclure"],
        "tools": ["llm_reasoning", "result_reader"],
        "permissions": {"read": ["hermes.analysis_results", "hermes.indicator_results", "hermes.anomalies"],
                        "write": ["hermes.analysis_results.interpretation"]},
        "status": "DECLARED",
    },
    {
        "agent_id": "AGT-08", "ordinal": 8, "name": "VISUALIZATION AGENT",
        "role": "Choisir et produire les visualisations adaptées (bar, line, stacked bar, heatmap, scatter, "
                "histogram, boxplot, KPI, tableau).",
        "responsibilities": ["choisir type, variables, agrégation, filtres, titres, unités, légendes",
                             "produire des graphiques interactifs HTML"],
        "tools": ["plotly", "matplotlib", "pandas", "excel_chart_engine", "html_generator"],
        "permissions": {"read": ["hermes.indicator_results", "hermes.analysis_results", "hermes.anomalies"],
                        "write": ["hermes.visualizations", "storage/artifacts"]},
        "status": "DECLARED",
    },
    {
        "agent_id": "AGT-09", "ordinal": 9, "name": "REPORTING AGENT",
        "role": "Produire des rapports professionnels à partir de résultats validés, sans recalculer.",
        "responsibilities": ["assembler résumé exécutif, méthodologie, qualité, indicateurs, analyses, "
                             "anomalies, graphiques, limites, annexes",
                             "exporter HTML/PDF/DOCX/Excel/JSON"],
        "tools": ["html_renderer", "pdf_renderer", "docx_writer", "xlsx_writer", "json_writer"],
        "permissions": {"read": ["hermes.* (résultats validés)"], "write": ["hermes.reports", "storage/artifacts"]},
        "status": "DECLARED",
    },
    {
        "agent_id": "AGT-10", "ordinal": 10, "name": "VALIDATOR AGENT",
        "role": "Contrôle indépendant : calculs, filtres, périodes, dimensions, numérateurs/dénominateurs, "
                "agrégations, cohérence entre conclusions et données.",
        "responsibilities": ["chercher activement les erreurs plutôt qu'approuver",
                             "rendre une décision : APPROVED / APPROVED_WITH_WARNINGS / REQUIRES_CORRECTION / REJECTED",
                             "désigner l'agent responsable en cas de correction"],
        "tools": ["contract_validator", "integrity_checker", "consistency_checker", "recomputation_probe",
                  "evidence_builder"],
        "permissions": {"read": ["hermes.*", "ingestion.*", "storage/*"], "write": ["hermes.validation_results"]},
        "status": "IMPLEMENTED",
    },
]


def by_id(agent_id: str) -> dict[str, Any] | None:
    return next((a for a in AGENTS if a["agent_id"] == agent_id), None)


def by_task_type(task_type: str) -> dict[str, Any] | None:
    mapping = {
        "plan": "AGT-00", "ingest": "AGT-01", "quality_check": "AGT-02", "clean": "AGT-03",
        "indicator": "AGT-04", "analyze": "AGT-05", "detect_anomalies": "AGT-06",
        "interpret": "AGT-07", "visualize": "AGT-08", "report": "AGT-09", "validate": "AGT-10",
    }
    return by_id(mapping.get(task_type, ""))


def implemented() -> list[str]:
    return [a["agent_id"] for a in AGENTS if a["status"] == "IMPLEMENTED"]


def sync(db) -> dict:
    """Enregistre / met à jour le catalogue en base."""
    from . import contracts

    written = 0
    for agent in AGENTS:
        db.insert_json(
            "agents",
            {
                "agent_id": agent["agent_id"], "ordinal": agent["ordinal"], "name": agent["name"],
                "role": agent["role"], "responsibilities": agent["responsibilities"],
                "tools": agent["tools"], "permissions": agent["permissions"], "status": agent["status"],
                "updated_at": contracts.now_iso(),
            },
            jsonb_fields=("responsibilities", "tools", "permissions"),
            on_conflict="""ON CONFLICT (agent_id) DO UPDATE SET
                ordinal = EXCLUDED.ordinal, name = EXCLUDED.name, role = EXCLUDED.role,
                responsibilities = EXCLUDED.responsibilities, tools = EXCLUDED.tools,
                permissions = EXCLUDED.permissions, status = EXCLUDED.status, updated_at = now()""",
        )
        written += 1
    return {"agents_synced": written, "implemented": implemented()}
