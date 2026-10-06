# ARCHITECTURE — HERMES DATA ANALYTICS SYSTEM

Document d'architecture (phase 1 : conception) puis plan d'implémentation.
Établi après analyse de l'existant : **Agent 01 DATA INGESTION est déjà implémenté et vérifié**
(`KLDataViz_console/ingestion/`, registre PostgreSQL `kldataviz` schéma `ingestion`) — il n'est **pas**
modifié, il est branché via un adaptateur.

---

## 1. Architecture globale

```text
                        DEMANDE UTILISATEUR (langage naturel)
                                     │
                     ┌───────────────▼────────────────┐
                     │  00 MASTER ORCHESTRATOR        │  planifie, dispatche, collecte,
                     │  (plan / dispatch / collecte)  │  journalise — ne calcule rien
                     └───────────────┬────────────────┘
                                     │  enveloppe de tâche (job_id, agent_id, dataset_id…)
     ┌───────────────┬───────────────┼───────────────┬───────────────┬───────────────┐
     ▼               ▼               ▼               ▼               ▼               ▼
 01 INGESTION   02 QUALITÉ      03 ENGINEERING   04 INDICATEUR   05 STATISTIQUE   06 ANOMALIES
 (implémenté)   (à venir)       (à venir)        (à venir)       (à venir)        (à venir)
     │               │               │               │               │               │
     └───────────────┴───────────────┴───────┬───────┴───────────────┴───────────────┘
                                             ▼
                              07 INTERPRÉTATION → 08 VISUALISATION → 09 REPORTING
                                             │
                                             ▼
                                    10 VALIDATOR (indépendant)  ← décision :
                                    APPROVED / APPROVED_WITH_WARNINGS /
                                    REQUIRES_CORRECTION / REJECTED
                                             │
                                             ▼
                                  POSTGRESQL = source de vérité unique
                (agents, jobs, datasets, dictionnaire, qualité, indicateurs,
                 analyses, anomalies, visualisations, rapports, validations, audit)
```

**Séparation stricte** : le LLM raisonne (planification, interprétation, rédaction) ; **Python/SQL calcule**
(tout chiffre stocké est produit par du code déterministe).

## 2. Couches

| Couche | Contenu | Rôle |
| --- | --- | --- |
| Connaissance | `hermes.agents`, `hermes.data_dictionary`, `hermes.indicators` | qui fait quoi, quel sens a une variable, quelle formule d'indicateur |
| Exécution | `hermes.jobs`, `hermes.audit_logs` | enveloppes de tâches, reprise, traçabilité |
| Données | `ingestion.*` (agent 01), `hermes.data_sources`, `hermes.datasets`, `hermes.dataset_versions`, `hermes.variables` | sources immuables + versions de travail |
| Résultats | `quality_checks`, `cleaning_operations`, `indicator_results`, `analysis_results`, `anomalies`, `visualizations`, `reports`, `validation_results` | preuves et livrables |
| Présentation | `KLDataViz_console` (console web), exports PDF/Excel/HTML | usage final |

## 3. Agents : responsabilités, outils, statut

| ID | Agent | Responsabilités | Outils | État |
| --- | --- | --- | --- | --- |
| 00 | Master Orchestrator | plan, dispatch, suivi, collecte, cohérence, reprise, appel du Validator, réponse finale | `task_planner`, `agent_dispatcher`, `job_manager`, `postgres_read/write`, `agent_status`, `result_collector`, `validation_request`, `audit_logger` | **implémenté** |
| 01 | Data Ingestion | détection format/encodage/séparateur/feuilles/colonnes/types/structure/volumétrie/identifiants, version de travail, métadonnées | lecteurs CSV/Excel/JSON/Parquet/SQL, file/encoding/schema detectors, dataset versioner | **implémenté** |
| 02 | Data Quality | complétude, unicité, validité, cohérence, conformité, doublons, formats, incohérences temporelles/inter-variables | `missing_detector`, `duplicate_detector`, `type/range/format_validator`, `consistency_checker`, `business_rule_engine`, `outlier_detector`, `quality_scorer` | déclaré |
| 03 | Data Engineering | nettoyage, standardisation, typage, normalisation, dédoublonnage, jointures, agrégations, variables dérivées — tout tracé | pandas/polars/numpy/SQLAlchemy, `data_transformer/cleaner/joiner/aggregator` | déclaré |
| 04 | Indicator Agent | définitions d'indicateurs (code, numérateur, dénominateur, formule, population, période, dimensions, exclusions), calcul multi-dimensions | `indicator_registry`, `formula_engine`, `dimension_engine`, `aggregation_engine`, `SQL_engine`, `indicator_validator` | déclaré |
| 05 | Statistical Analysis | analyses univariées / bivariées / multivariées avec vérification des hypothèses | numpy, pandas, scipy, statsmodels, scikit-learn | déclaré |
| 06 | Anomaly Detection | IQR, z-score, Isolation Forest, LOF, séries temporelles, règles métier, comparaison inter-indicateurs | scipy, sklearn, détecteurs temporels | déclaré |
| 07 | Interpretation | distinguer fait observé / résultat calculé / interprétation / hypothèse / limite | LLM + résultats validés uniquement | déclaré |
| 08 | Visualization | choix du graphique, variables, agrégation, dimensions, filtres, titres, unités, légendes | Plotly, Matplotlib, générateur HTML | déclaré |
| 09 | Reporting | rapports HTML/PDF/DOCX/Excel/JSON sans recalcul | moteurs d'export | déclaré |
| 10 | Validator | contrôle indépendant des calculs, filtres, périodes, dimensions, numérateurs/dénominateurs, cohérence conclusions/données | contrôles déterministes + journal | **implémenté** (ingestion) |

Le statut fait partie des données (`hermes.agents.status`) : un agent déclaré mais non implémenté est
**explicitement marqué `DECLARED`** et tout dispatch le concernant échoue proprement (`SKIPPED`,
`errors = ["agent non implémenté"]`) — jamais de faux résultat.

## 4. Contrat d'échange (protocole de communication)

Chaque tâche est une **enveloppe** persistée dans `hermes.jobs` :

```json
{
  "job_id": "JOB-20261001T195500-A1B2C3",
  "agent_id": "AGT-01",
  "task_type": "ingest",
  "objective": "ingérer fact_district.csv",
  "dataset_id": "DS-CAAE21558C41",
  "dataset_version_id": "DV-8A957527DF8959-v001",
  "input_reference": {"kind": "file", "path": "C:/…/fact_district.csv"},
  "parameters": {"sheet": null, "limit": null},
  "status": "DONE",
  "result_reference": {"dataset_id": "…", "work_path": "storage/work/…/v001/x.parquet"},
  "warnings": [],
  "errors": [],
  "created_at": "…", "completed_at": "…"
}
```

* Les agents **échangent des références** (chemins, ids, clés JSON), jamais de gros volumes dans les prompts.
* Statuts : `QUEUED` → `RUNNING` → `DONE` | `FAILED` | `SKIPPED` | `REQUIRES_CORRECTION`.
* Chaque transition écrit une ligne dans `hermes.audit_logs` (qui, quoi, quand, payload).
* Le workflow complet d'un job est reconstituable : `hermes.jobs` (hiérarchie parent/enfant) + `audit_logs`.

## 5. Schéma PostgreSQL

Base : `kldataviz`. Schémas : `ingestion` (registre de l'agent 01, inchangé) et `hermes` (système).

| Table | Rôle | Clés principales |
| --- | --- | --- |
| `hermes.agents` | catalogue des 11 agents : rôle, outils, statut, permissions | `agent_id` |
| `hermes.jobs` | enveloppe de tâche + hiérarchie + reprise (`attempts`, `max_attempts`) | `job_id`, `parent_job_id`, `agent_id` |
| `hermes.data_sources` | sources déclarées (fichier, DSN, API) et leur immuabilité | `source_id` |
| `hermes.datasets` | dataset logique, aligné sur `ingestion.datasets` | `dataset_id` |
| `hermes.dataset_versions` | version de travail : empreinte, chemins RAW/WORK, volumétrie, statut | `dataset_version_id` |
| `hermes.data_dictionary` | description documentée des variables (jamais inventée) | (`dataset_id`, `variable`) |
| `hermes.variables` | profil par version : type, rôle sémantique, complétude, unicité, identifiant | (`dataset_version_id`, `ordinal`) |
| `hermes.quality_checks` | contrôles qualité, gravité, catégorie, enregistrements affectés | `check_id` |
| `hermes.cleaning_operations` | transformations : type, avant/après, paramètres, auteur | `operation_id` |
| `hermes.indicators` | définitions : code, formule, numérateur, dénominateur, population, période, exclusions | `indicator_id` |
| `hermes.indicator_dimensions` | dimensions supportées par indicateur (SEXE, AGE, SITE, DISTRICT, REGION, MOIS, TRIMESTRE, ANNEE) | (`indicator_id`, `dimension`, `value`) |
| `hermes.indicator_results` | valeurs calculées, par dimension et période | `result_id` |
| `hermes.analysis_jobs` | demandes d'analyse (méthode, variables, hypothèses) | `analysis_job_id` |
| `hermes.analysis_results` | statistiques produites, hypothèses, limites | `analysis_result_id` |
| `hermes.anomalies` | anomalies classées (STATISTICAL/TEMPORAL/BUSINESS_RULE/DATA_QUALITY/CROSS_INDICATOR) avec preuve et confiance | `anomaly_id` |
| `hermes.visualizations` | graphiques générés (type, source, fichier, spécification) | `visualization_id` |
| `hermes.reports` | rapports produits (format, sections, références validées) | `report_id` |
| `hermes.validation_results` | décisions du Validator : erreurs, avertissements, preuves, actions requises | `validation_id` |
| `hermes.audit_logs` | journal complet : job, agent, événement, message, payload | `audit_id` |

Vues : `hermes.v_job_pipeline` (jobs + agent + dataset, ordonné par création),
`hermes.v_dataset_lineage` (jointure `hermes.datasets` ↔ `ingestion.datasets/versions`),
`hermes.v_agent_status` (agents + nombre de jobs, dernier job).

## 6. Arborescence Python

```text
Projets/
├── KLDataViz_console/            # existant, NON modifié
│   ├── server.py  index.html     # console web (+ panneau d'ingestion)
│   ├── ingestion/                # AGENT 01 (implémenté)
│   └── .venv/                    # environnement (pandas, pyarrow, psycopg)
└── HERMES_DA/                    # système multi-agents (nouveau)
    ├── ARCHITECTURE.md           # ce document
    ├── hermes_da/
    │   ├── config.py             # chemins, DSN, découverte de l'existant
    │   ├── contracts.py          # enveloppe de tâche, statuts, validation du contrat
    │   ├── db.py                 # DDL complet (17 tables + vues), connexion, requêtes
    │   ├── agents_registry.py    # catalogue des 11 agents + synchronisation en base
    │   ├── jobs.py               # job_manager : création, transitions, reprise, audit
    │   ├── orchestrator.py       # planner, dispatcher, collector, validation_request
    │   ├── artifacts.py          # écriture/lecture des artefacts JSON (contrats, résultats)
    │   ├── adapters/
    │   │   ├── ingestion.py      # adaptateur → agent 01 existant (zéro modification)
    │   │   └── validator.py      # AGENT 10 : contrôles déterministes + décision
    │   └── cli.py                # exécution, inspection, reprise
    └── tests/                    # vérifications rejouables
```

## 7. API (couche d'exposition)

| Route | Méthode | Usage |
| --- | --- | --- |
| `/api/status` | GET | état de l'agent (console existante) |
| `/api/ingest` | POST | ingestion directe (agent 01) |
| `/api/ingest/list` | GET | registre des datasets/versions |
| `/api/da/run` | POST | **demande analytique** : plan + dispatch + validation + réponse finale |
| `/api/da/jobs` | GET | jobs récents (statut, agent, dataset, durée) |
| `/api/da/jobs/<job_id>` | GET | détail d'un job : enfants, résultats, audit, validation |
| `/api/da/agents` | GET | catalogue des agents et leur statut |
| `/api/da/schema` | GET | schéma PostgreSQL exposé (tables, colonnes, volumétrie) |

L'API reste **mince** : elle lit et écrit dans PostgreSQL, elle ne calcule pas.

## 8. Stratégie de tests

1. **Tests unitaires** par outil (détecteurs, lecteurs, validateurs) : entrées synthétiques et réelles.
2. **Tests d'intégration** par agent : chaque agent rejoué sur les données du projet TA-MOH
   (csv, xlsx multi-feuilles, json imbriqué, sqlite, table PostgreSQL).
3. **Test de bout en bout** : une demande → plan → exécution → validation → réponse, avec vérification
   en base (`psql`) du nombre de jobs, transitions, artefacts et lignes de résultat.
4. **Test de reprise** : job forcé en échec → `attempts` incrémenté → reprise → succès.
5. **Test d'intégrité** : SHA-256 des sources recalculé indépendamment après exécution.
6. **Test de non-régression de l'existant** : la console et l'agent 01 restent fonctionnels.

## 9. Journalisation

* `hermes.audit_logs` : un événement par transition (`job_created`, `job_started`, `job_done`,
  `job_failed`, `validation_requested`, `validation_done`, `artifact_written`, `retry`).
* Chaque agent conserve en plus son propre journal métier (ex. `ingestion.ingestion_runs`).
* Aucun résultat n'est présenté sans ligne d'audit correspondante.

## 10. Reprise après erreur

* `hermes.jobs.attempts` / `max_attempts` (défaut 2) ; erreur stockée dans `errors` (jsonb).
* Politique de l'orchestrateur : `RETRY` (mêmes paramètres), `RETRY_WITH_PARAMS` (paramètres corrigés),
  `CHANGE_METHOD` (autre agent/méthode), `ABORT` (arrêt du workflow) — la décision est journalisée.
* Idempotence : l'ingestion ne crée pas de version en double (statut `DEJA_INGERE`) ;
  un job rejoué réutilise l'existant plutôt que de dupliquer.
* Reprise manuelle : `python -m hermes_da retry --job <job_id>`.

## 11. Sécurité et permissions

* `hermes.agents.permissions` : chaque agent déclare `read` / `write` (schémas et tables).
  L'agent 01 n'écrit que dans `ingestion` et `storage/` ; les agents analytiques n'écrivent que leurs
  tables de résultats ; aucun agent n'écrit dans les sources.
* Les sources restent **immuables** (copie RAW + empreinte vérifiée avant/après).
* Les agents analytiques travaillent sur les **versions de travail** (`storage/work/...`), jamais en direct.

## 12. Plan d'intégration avec l'existant (à appliquer sans rien casser)

1. **Ne rien modifier** dans `KLDataViz_console/ingestion/` ni dans `server.py` existant : l'agent 01 est
   appelé via `hermes_da/adapters/ingestion.py` (import par chemin, `sys.path`).
2. Le schéma `ingestion` reste la référence de l'agent 01 ; `hermes.datasets` / `hermes.dataset_versions`
   en sont le miroir enrichi (mêmes identifiants `DS-…` / `DV-…`) — jointure directe entre schémas.
3. La console (`index.html`) reçoit un panneau supplémentaire branché sur `/api/da/*` ; les routes
   existantes (`/api/analyze`, `/api/export/*`, `/api/ingest`) restent inchangées.
4. Toute nouvelle dépendance est ajoutée dans le `.venv` existant (uv), jamais dans un environnement parallèle.

## 13. Feuille de route (implémentation par étapes)

| Étape | Contenu | État |
| --- | --- | --- |
| 1 | Architecture (ce document) | **fait** |
| 2 | Fondation : contrats, schéma PostgreSQL complet, registre des agents, job_manager, orchestrateur, adaptateur agent 01, Validator (contrôles d'ingestion) | **fait** |
| 3 | Agent 02 Data Quality (+ `quality_checks`, `quality_scorer`) puis Validator qualité | à faire |
| 4 | Agent 03 Data Engineering (+ `cleaning_operations`, transformations tracées) | à faire |
| 5 | Agent 04 Indicator (registre, formules, dimensions multiples) | à faire |
| 6 | Agents 05/06 (statistiques, anomalies) | à faire |
| 7 | Agents 07/08/09 (interprétation, visualisation, reporting) | à faire |
| 8 | Console : panneau « Demande analytique », suivi des jobs, rapports | à faire |

Critère de passage d'une étape : l'agent tourne sur les données réelles du projet, ses écritures sont
vérifiées en base (`psql`), et le Validator rend une décision motivée.
