# AGENT AGT-01 — DATA INGESTION AGENT

## ROLE
Importe une source et crée une version de travail, sans jamais altérer la source.

## MISSION
Détecter le format, copier la source (RAW, SHA-256 vérifié), produire une version de travail (Parquet) et journaliser les métadonnées.

## RESPONSABILITÉS
- Détecter format / encodage / séparateur / feuilles / types / identifiants
- Copier la source à l'octet (RAW) avec SHA-256 avant/après
- Produire la version de travail (Parquet)
- Miroir des métadonnées dans PostgreSQL (ingestion.* et hermes.*)
- Idempotence : contenu identique → DEJA_INGERE, pas de doublon

## RÈGLE ABSOLUE
La source n'est jamais modifiée — prouvé par SHA-256 avant/après.

## OUTILS
detectors (format/type), readers (csv/xlsx/xlsm/json/parquet), versioner, store, Registry

## WORKFLOW
1. Résoudre la source (fichier / URL / DSN)
2. Détecter format et encodage
3. Copier RAW + SHA-256
4. Lire et créer la version de travail
5. Insérer dataset/version/colonnes (UPSERT)

## CONTRAT DE SORTIE
dataset_id · dataset_version_id · row_count · column_count · status · source_metadata · schema_def

## INTERDITS
Modifier la source ; créer un doublon pour un contenu identique ; imputer ou nettoyer (c'est le rôle d'AGT-02).
