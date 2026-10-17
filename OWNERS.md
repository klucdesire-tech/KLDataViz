# OWNERS — qui possède quoi

Deux agents IA travaillent en parallèle sur ce monorepo :
- **Claude** → interface / UI / déploiement cloud
- **Hermes** → backend / agents / analyse

Ce fichier sert de référence pour éviter qu'on écrase le travail de l'autre.
Règle d'or : **avant d'éditer un fichier, vérifier son propriétaire ci-dessous.**

## Claude — interface & cloud

| Fichier | Rôle |
| --- | --- |
| `KLDataViz_console/index.html` | interface principale (Studio) |
| `KLDataViz_console/index_classic.html` | interface classique |
| `KLDataViz_console/landing.html` | page d'accueil publique |
| `KLDataViz_console/login.html` | page de connexion |
| `KLDataViz_console/admin.html` | panneau d'administration |
| `KLDataViz_console/studio.py` | logique du mode Studio |
| `KLDataViz_console/cloud.py` | mode cloud / multi-tenant |
| `KLDataViz_console/tenancy.py` | tenancy / équipes / rôles |
| `KLDataViz_console/Dockerfile` | image Render |

## Hermes — backend & agents

| Fichier | Rôle |
| --- | --- |
| `HERMES_DA/` (tout) | système multi-agents (orchestration, adaptateurs, PostgreSQL) |
| `KLDataViz_console/ingestion/` | agent d'ingestion |
| `KLDataViz_console/modeling.py` | nettoyage / modélisation |
| `KLDataViz_console/architect.py` + `data_architect.py` | architecte de données |
| `KLDataViz_console/hermes_charts.py` | graphiques adaptatifs |
| `KLDataViz_console/hermes_clean.py` | nettoyage |
| `KLDataViz_console/hermes_report.py` + `pdf_report.py` | rapports |
| `KLDataViz_console/registry_meta.py` | registre de métadonnées |
| `KLDataViz_console/sandbox.py` + `sandbox_runner.py` | exécution sécurisée |
| `KLDataViz_console/netguard.py` | garde-fous réseau |

## Partagé — à coordonner

| Fichier | Pourquoi c'est partagé |
| --- | --- |
| `KLDataViz_console/server.py` | **point chaud** : endpoints backend (Hermes) + sert l'UI (Claude) |
| `KLDataViz_console/llm_config.json` | config LLM (clé, modèle) |
| `README.md`, `requirements.txt`, `.gitignore` | fichiers transverses |

## Règles de coordination

1. **Ne pas éditer en même temps** un fichier de l'autre (surtout `server.py`).
2. **`git status` + `git pull` avant d'éditer** un fichier partagé.
3. **Jamais `git push --force`** sans prévenir l'autre agent / l'utilisateur.
4. Si un changement de l'un casse l'autre (endpoint, format), **le signaler dans le commit**.
5. En cas de conflit de merge, **réconcilier les deux versions** (ne pas écraser).
