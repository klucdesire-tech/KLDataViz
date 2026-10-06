# HERMES_DA — Système multi-agents d'analyse de données

Fondation du système décrit dans `ARCHITECTURE.md` : orchestration, protocole d'échange,
schéma PostgreSQL complet, agent 01 (ingestion, déjà existant) branché par adaptateur,
et agent 10 (validation indépendante).

## Environnement

Python de la console KLDataViz (pandas, pyarrow, psycopg déjà installés) :

```bash
PY="C:/Users/Luc Déisré KOUAKOU/OneDrive/Desktop/cours/AGENT IA/Projets/KLDataViz_console/.venv/Scripts/python.exe"
cd "C:/Users/Luc Déisré KOUAKOU/OneDrive/Desktop/cours/AGENT IA/Projets/HERMES_DA"
```

PostgreSQL 18 local — base `kldataviz`, schémas `ingestion` (agent 01) et `hermes` (système).
Connexion surchargeable : `HERMES_DA_DSN` ou `KLINGEST_PG_DSN`, `HERMES_DA_DB`.

## Commandes

```bash
$PY -m hermes_da init                     # crée le schéma hermes + synchronise les 11 agents
$PY -m hermes_da info                     # configuration, découverte de l'existant
$PY -m hermes_da sync                     # miroir des versions ingérées hors orchestrateur
$PY -m hermes_da plan --objective "…" --source "…"      # plan seul (sans exécution)
$PY -m hermes_da run --objective "…" --source "C:/data/x.csv" [--sheet NOM] [--json out.json]
$PY -m hermes_da agents                   # catalogue + statut + volumétrie des jobs
$PY -m hermes_da schema                   # tables, colonnes, volumétrie
$PY -m hermes_da jobs -n 20               # derniers jobs
$PY -m hermes_da job <job_id> [--audit]   # détail d'un job : enfants, journal d'audit
$PY tests/verifier_systeme.py             # vérification bout en bout (15 contrôles)
```

## Exemple réel

```bash
$PY -m hermes_da run \
  --objective "ingérer les districts, contrôler la qualité et détecter les anomalies" \
  --source "C:/…/TA-MOH_analyse/data/fact_district.csv" --name fact_district
```

Sortie : plan (agents disponibles / non disponibles), exécution étape par étape, datasets utilisés,
décision du Validator, avertissements, erreurs, autorisation de rapport.

## Contrat d'échange

`hermes_da/contracts.py` — enveloppe de tâche persistée dans `hermes.jobs` :
`job_id, parent_job_id, agent_id, task_type, objective, dataset_id, dataset_version_id,
input_reference, parameters, status, result_reference, warnings, errors, attempts, max_attempts,
created_at, started_at, completed_at, duration_ms`.

Statuts : `QUEUED → RUNNING → DONE | FAILED | SKIPPED | REQUIRES_CORRECTION`.

## Comportements garantis

* Un agent `DECLARED` (non implémenté) ne produit **jamais** de résultat : l'étape est `SKIPPED`
  avec un motif explicite, et le Validator le voit.
* Le Validator est **indépendant** : il relit l'artefact, le fichier de travail sur disque et
  PostgreSQL, recalcule lignes/colonnes et empreintes, puis décide
  `APPROVED / APPROVED_WITH_WARNINGS / REQUIRES_CORRECTION / REJECTED`.
* Le rapport (AGT-09) n'est autorisé qu'après validation favorable.
* Aucune source n'est modifiée : l'agent 01 copie en zone RAW et vérifie le SHA-256 avant/après ;
  HERMES_DA le revérifie une seconde fois dans `tests/verifier_systeme.py`.
* Toute transition écrit une ligne dans `hermes.audit_logs`.

## Arborescence

```text
HERMES_DA/
├── ARCHITECTURE.md                 # conception, responsabilités, schéma, roadmap
├── hermes_da/
│   ├── config.py  contracts.py  db.py  jobs.py  artifacts.py  agents_registry.py
│   ├── orchestrator.py             # AGT-00 : plan, dispatch, collecte, reprise
│   ├── reconcile.py                # rattrapage du miroir hermes ↔ ingestion
│   ├── detectors_helper.py         # empreintes + relecture indépendante des versions
│   ├── adapters/ingestion.py       # AGT-01 → package existant (non modifié)
│   └── adapters/validator.py       # AGT-10 : contrôles déterministes + décision
├── storage/artifacts/              # réponses complètes, artefacts de validation
└── tests/verifier_systeme.py       # vérification bout en bout
```

## État d'implémentation

| Agent | Statut |
| --- | --- |
| AGT-00 Master Orchestrator | IMPLEMENTED |
| AGT-01 Data Ingestion | IMPLEMENTED (code existant, branché par adaptateur) |
| AGT-10 Validator | IMPLEMENTED |
| AGT-02 … AGT-09 | DECLARED (schéma PostgreSQL et plan prêts ; dispatch refusé proprement) |
