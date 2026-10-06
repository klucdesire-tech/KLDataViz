# KLDataViz — Agents d'analyse de données & BI

Système multi-agents d'analyse de données (ingestion, qualité, analyse, visualisation, reporting),
construit autour de PostgreSQL et exposé via une console web locale.

## Contenu

| Dossier | Rôle |
| --- | --- |
| `KLDataViz_console/` | Console web locale + **agent 01 Data Ingestion** (`ingestion/`, package Python exécutable) |
| `HERMES_DA/` | Fondation multi-agents : orchestrateur (AGT-00), adaptateur ingestion (AGT-01), Validator (AGT-10), schéma PostgreSQL `hermes` (19 tables), protocole d'échange |
| `IDEA.md` | Spécification fonctionnelle initiale du système |

## Démarrage rapide

```bash
# Console web (agent d'ingestion) — port 8642
cd KLDataViz_console
.venv/Scripts/python.exe -m ingestion "chemin/fichier.csv" --name mon_dataset

# Système multi-agents
cd HERMES_DA
.venv/Scripts/python.exe -m hermes_da run --objective "…" --source "chemin/fichier.csv"
```

Détails : `KLDataViz_console/agents/data_ingestion/README.md` et `HERMES_DA/README.md`.

## Sécurité — à lire avant tout déploiement

* **Aucun secret n'est stocké dans ce dépôt.** Les identifiants se configurent via :
  - `CVLYNK_AGENT_TOKEN` (ou `KLDataViz_console/agent_token.txt`, ignoré par Git) ;
  - `KLDataViz_console/llm_config.json` (copier depuis `llm_config.example.json`, ignoré par Git) ;
  - `HERMES_DA_DSN` / `KLINGEST_PG_*` pour PostgreSQL.
* Les données réelles (jeu de santé TA-MOH) et les copies de travail sont **exclues** de ce dépôt.
* L'API locale lit des chemins de fichiers : ne jamais l'exposer sur Internet sans couche
  d'authentification.
