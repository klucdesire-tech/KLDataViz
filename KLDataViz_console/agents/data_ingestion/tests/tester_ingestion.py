# -*- coding: utf-8 -*-
"""Batterie de tests réels de l'agent DATA INGESTION (sources du projet KLDataViz)."""
import json
import sys
from pathlib import Path

BASE = Path(r"C:\Users\Luc Déisré KOUAKOU\OneDrive\Desktop\cours\AGENT IA\Projets\KLDataViz_console")
sys.path.insert(0, str(BASE))
import pandas as pd  # noqa: E402
from ingestion import ingest  # noqa: E402

PROJ = Path(r"C:\Users\Luc Déisré KOUAKOU\OneDrive\Desktop\cours\AGENT IA\Projets")
OUT = BASE / "storage" / "contracts"
OUT.mkdir(parents=True, exist_ok=True)
SCRATCH = Path(r"C:\Users\Luc Déisré KOUAKOU\AppData\Local\hermes\cache\scratch")

# Sources de test supplémentaires : Parquet et CSV point-virgule cp1252
sites = pd.read_csv(PROJ / "TA-MOH_analyse/data/sites.csv", encoding="utf-8-sig")
sites.head(50).to_csv(SCRATCH / "sites_pointvirgule.csv", index=False, sep=";", encoding="cp1252")

JOBS = [
    ("1_xlsx_multi_feuilles", dict(source=str(PROJ / "TA-MOH_analyse/rapport/tamoh_rapport.xlsx"),
                                   sheet="Indicateurs", dataset_name="tamoh_rapport_indicateurs")),
    ("2_csv_fact_district", dict(source=str(PROJ / "TA-MOH_analyse/data/fact_district.csv"),
                                 dataset_name="fact_district")),
    ("3_json_kpi_summary", dict(source=str(PROJ / "TA-MOH_analyse/data/kpi_summary.json"),
                                dataset_name="kpi_summary")),
    ("4_json_dashboard", dict(source=str(PROJ / "TA-MOH_analyse/data/dashboard_data.json"),
                              dataset_name="dashboard_data")),
    ("5_sqlite_fact", dict(source=str(PROJ / "TA-MOH_analyse/db/tamoh.db"), kind="sqlite",
                           table="fact_indicators", dataset_name="tamoh_fact_indicators")),
    ("6_postgres_table", dict(source="host=127.0.0.1 dbname=kldataviz", kind="postgres", table="fact_district",
                              schema_name="demo", dataset_name="fact_district_pg")),
    ("7_csv_pointvirgule_cp1252", dict(source=str(SCRATCH / "sites_pointvirgule.csv"),
                                       dataset_name="sites_pointvirgule")),
    ("8_csv_qualite", dict(source=str(PROJ / "TA-MOH_analyse/data/quality_issues.csv"),
                           dataset_name="quality_issues")),
    ("9_reprise_identique", dict(source=str(PROJ / "TA-MOH_analyse/rapport/tamoh_rapport.xlsx"),
                                 sheet="Indicateurs", dataset_name="tamoh_rapport_indicateurs")),
    ("10_source_absente", dict(source=str(PROJ / "TA-MOH_analyse/data/inexistant.csv"),
                               dataset_name="inexistant")),
]

rows = []
for label, kw in JOBS:
    c = ingest(**kw)
    (OUT / f"{label}.json").write_text(json.dumps(c, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    sm = c["source_metadata"]
    rows.append({
        "test": label, "statut": c["status"], "dataset_id": c["dataset_id"],
        "v": (c.get("version") or {}).get("version_no"), "lignes": c["row_count"], "col": c["column_count"],
        "format": sm.get("format"), "encodage": sm.get("encoding"), "sep": sm.get("separator"),
        "ids": len((c.get("schema") or {}).get("identifier_columns") or []),
        "source_intacte": (sm.get("integrity") or {}).get("source_unchanged"),
        "warn": len(c["warnings"]), "err": len(c["errors"]),
        "store": (c.get("metadata_store") or {}).get("backend"), "ms": c["duration_ms"],
    })

df = pd.DataFrame(rows)
pd.set_option("display.width", 260)
print(df.to_string(index=False))
print("\n===== détails =====")
for r in rows:
    c = json.loads((OUT / f"{r['test']}.json").read_text(encoding="utf-8"))
    print(f"\n--- {r['test']} : {c['status']}")
    if c["errors"]:
        for e in c["errors"]:
            print(f"    ✖ {e[:160]}")
    for w in (c["warnings"] or [])[:4]:
        print(f"    ⚠ {w[:170]}")
    ids = (c.get("schema") or {}).get("identifier_columns") or []
    if ids:
        print(f"    identifiants : {ids}")
