# KLDataViz

**Agent d'analyse de données intelligent** — Plateforme locale d'ingestion, nettoyage et visualisation de données avec chat IA.

## Fonctionnalités

- 📊 **Analyse automatique** : profiling statistique, détection de types, anomalies
- 🧹 **Nettoyage intelligent** : typage, doublons, valeurs manquantes (médiane/mode)
- 📈 **Graphiques adaptatifs** : bar, line, area, pie, scatter, histogram, boxplot
- 💬 **Chat IA** : interrogez vos données en langage naturel (DeepSeek)
- 📤 **Exports** : Excel, PDF, HTML, SQLite
- 🔗 **Intégration CVLynk** : orchestration multi-agents

## Installation

```bash
# Créer l'environnement
python -m venv .venv

# Activer (Windows)
.venv\Scripts\activate

# Installer les dépendances
pip install pandas numpy openpyxl reportlab chardet pyarrow psycopg matplotlib pillow

# Configurer le LLM (optionnel)
# Créer llm_config.json avec votre clé DeepSeek

# Lancer
python server.py
```

## Configuration LLM

Créer `llm_config.json` :
```json
{
  "base_url": "https://api.deepseek.com/chat/completions",
  "api_key": "VOTRE_CLE",
  "model": "deepseek-v4-pro"
}
```

## Utilisation

Ouvrir http://localhost:8642 dans votre navigateur.

## Licence

MIT
