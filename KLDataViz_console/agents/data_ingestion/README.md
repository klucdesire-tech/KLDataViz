# AGENT DATA INGESTION — KLDataViz

Implémentation exécutable du **Data Ingestion Agent** : import de fichiers et de bases de
données **sans jamais modifier la source**, détections automatiques, création d'une
**version de travail** et enregistrement des **métadonnées dans PostgreSQL**.

* Prompt de l'agent : `agents/data_ingestion/PROMPT.md`
* Package Python : `ingestion/`
* Zones de stockage : `storage/raw` (copie immuable) · `storage/work` (versions de travail) · `storage/versions` (schéma, variables, manifeste) · `storage/contracts` (contrats JSON)

## Règle absolue : la source n'est jamais modifiée

1. empreinte SHA-256 de la source **avant** lecture ;
2. copie à l'octet dans `storage/raw/<dataset_id>/vNNN/` (copie marquée lecture seule) ;
3. lecture des données **depuis la copie RAW**, jamais depuis la source vivante ;
4. empreinte SHA-256 de la source **après** ingestion → `source_metadata.integrity.source_unchanged = true` ;
5. si l'intégrité est douteuse, l'ingestion est interrompue (`status = ERREUR`).

## Outils (module → fonction)

| Outil | Emplacement |
| --- | --- |
| CSV reader | `readers.read_csv_file` |
| Excel reader | `readers.read_excel_file` |
| JSON reader | `readers.read_json_file` |
| Parquet reader | `readers.read_parquet_file` |
| PostgreSQL connector | `readers.read_postgres_table` (+ `read_sqlite_table`) |
| File detector | `detectors.detect_format` |
| Encoding detector | `detectors.detect_encoding` |
| Schema detector | `detectors.build_schema` / `analyze_column` / `detect_composite_keys` / `detect_structure` |
| Dataset versioner | `versioner.Versioner` |
| Registre PostgreSQL | `store.Registry` |

## Utilisation en ligne de commande

```bash
cd KLDataViz_console
.venv/Scripts/python.exe -m ingestion "C:/chemin/fichier.xlsx" --sheet "Indicateurs"
.venv/Scripts/python.exe -m ingestion "C:/chemin/fact_district.csv" --name fact_district
.venv/Scripts/python.exe -m ingestion "host=127.0.0.1 dbname=kldataviz" --kind postgres --table fact_district --schema demo
.venv/Scripts/python.exe -m ingestion --list
.venv/Scripts/python.exe -m ingestion --summary
.venv/Scripts/python.exe -m ingestion --versions DS-XXXXXXXXXXXX
.venv/Scripts/python.exe -m ingestion --detail DV-XXXXXXXXXXXXXX-v001
```

Options utiles : `--encoding`, `--separator`, `--header`, `--limit`, `--force`,
`--work-format csv`, `--json contrat.json`, `--sqlite` (registre de repli).

## Utilisation programmatique

```python
from ingestion import ingest

contrat = ingest(r"C:\data\fact_district.csv", dataset_name="fact_district")
print(contrat["dataset_id"], contrat["dataset_version_id"], contrat["row_count"])
```

Contrat de sortie : `dataset_id`, `dataset_version_id`, `schema`, `row_count`, `column_count`,
`variables`, `source_metadata`, `warnings`, `errors` (+ `status`, `version`, `storage`,
`metadata_store`, `audit`, `semantic_hints`).

## Versionnage

* `dataset_id` (`DS-…`) : identité logique stable — même origine ⇒ mêmes versions successives.
* `dataset_version_id` (`DV-…-vNNN`) : version immuable, identifiée par son empreinte de contenu.
* Contenu identique à la dernière version ⇒ `statut = DEJA_INGERE`, aucune version en double
  (sauf `--force`).
* Sources SQL : l'empreinte porte sur l'instantané (colonnes + valeurs).

## Métadonnées PostgreSQL

Base `kldataviz` (créée automatiquement si absente), schéma `ingestion` :

| Objet | Contenu |
| --- | --- |
| `ingestion.datasets` | dataset logique, origine, nombre de versions |
| `ingestion.dataset_versions` | version, empreintes, chemins RAW/WORK, volumétrie, `source_metadata` (jsonb), `schema_def` (jsonb) |
| `ingestion.dataset_columns` | une ligne par variable : type, rôle, complétude, unicité, identifiant |
| `ingestion.ingestion_runs` | exécution : statut, durée, avertissements, erreurs, journal d'audit |
| `ingestion.v_latest_dataset_versions` | vue des dernières versions |

Connexion paramétrable : `KLINGEST_PG_DSN`, `KLINGEST_PG_HOST`, `KLINGEST_PG_PORT`,
`KLINGEST_PG_DB`, `KLINGEST_PG_USER`, `KLINGEST_PG_PASSWORD`.
Si PostgreSQL est injoignable, le registre bascule sur SQLite (`storage/registry.sqlite`)
avec un avertissement explicite dans le contrat.

## Console web

`server.py` expose :

* `POST /api/ingest` — corps `{file_path, sheet, table, schema, name, limit, force, kind}` → contrat complet ;
* `GET /api/ingest/list` — registre : datasets, dernières versions, compteurs.

Le panneau « Ingestion » de `index.html` affiche statut, `dataset_id`, `dataset_version_id`,
format/encodage, version de travail, variables et avertissements.

## Dépendances

`pandas`, `numpy`, `openpyxl`, `pyarrow`, `psycopg[binary]`, `charset_normalizer`, `chardet`
(installées dans `.venv`) — voir `requirements-ingestion.txt`.
