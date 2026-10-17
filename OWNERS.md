# Qui travaille sur quoi

Deux agents travaillent sur ce dépôt pour Luc Désiré KOUAKOU : **Hermes Agent** et **Claude** (Claude Code).
Ce fichier évite qu'ils s'écrasent mutuellement. En cas de doute, c'est Luc qui tranche.

## Propriétaires

| Chemin | Propriétaire | Contenu |
|---|---|---|
| `HERMES_DA/` | Hermes Agent | Agent d'analyse de données Hermes |
| `KLDataViz_console/ingestion/` | Hermes Agent | Agent d'ingestion : registre, versions, intégrité des sources |
| `KLDataViz_console/agents/` | Hermes Agent | Agents techniques (ingestion de données) |
| `KLDataViz_console/index.html`, `index_classic.html`, `landing.html`, `login.html`, `admin.html` | Claude | Interface (Studio, pages publiques, administration) |
| `KLDataViz_console/pdf_report.py` | Claude | Rapport PDF |
| `KLDataViz_console/hermes_charts.py`, `hermes_clean.py`, `hermes_report.py`, `registry_meta.py` | Claude | Interventions de l'IA dans l'application (graphiques, corrections, rapports planifiés, registre décrit) |
| `KLDataViz_console/architect.py`, `data_architect.py` | Claude | Architecte de données (« Proposer une structure ») |

## Fichiers partagés

Les deux agents y touchent : **`KLDataViz_console/server.py`**, `studio.py`, `modeling.py`, `cloud.py`, `tenancy.py`,
`requirements.txt`, `Dockerfile`, `render.yaml`, `tests/`.

## Règles

1. **Jamais de push direct sur `main`.** Toute modification passe par une branche et une pull request ; c'est Luc
   qui demande la fusion. Si deux branches touchent les mêmes lignes, GitHub signale le conflit au lieu d'écraser.
2. **Avant de commencer** : `git status` puis `git pull` sur `main`, et créer la branche à partir de là.
   Juste avant la PR, se remettre à jour avec `main` si elle a bougé.
3. **Dans le périmètre de l'autre** : ne modifier qu'en cas de nécessité, le dire dans la PR, et prévenir Luc.
4. **Contrats entre l'interface et le serveur** : toute route `/api/...` appelée par l'interface est couverte par
   un test dans `KLDataViz_console/tests/`. Une modification qui change un format de réponse met à jour le test
   et l'interface dans la même PR.
5. **`pytest tests` doit passer** avant chaque PR (commande : `.venv/Scripts/python.exe -m pytest tests -q`
   depuis `KLDataViz_console`).
6. **Jamais de `push --force`** sur une branche partagée ni sur `main`.
7. **Données réelles** : aucun fichier de santé réel (TA-MOH) n'est committé ni envoyé à l'IA pendant les tests ;
   utiliser des données fictives.
