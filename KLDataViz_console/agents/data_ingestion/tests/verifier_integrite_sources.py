# -*- coding: utf-8 -*-
"""Ingestion Parquet + vérification indépendante de l'intégrité des sources."""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Luc Déisré KOUAKOU\OneDrive\Desktop\cours\AGENT IA\Projets\KLDataViz_console")
sys.path.insert(0, str(BASE))
import pandas as pd  # noqa: E402
from ingestion import ingest  # noqa: E402

PROJ = Path(r"C:\Users\Luc Déisré KOUAKOU\OneDrive\Desktop\cours\AGENT IA\Projets")
OUT = BASE / "storage" / "contracts"

# 1) Ingestion d'une source Parquet (produite depuis sites.csv du projet)
pq = Path(r"C:\Users\Luc Déisré KOUAKOU\AppData\Local\hermes\cache\scratch\sites.parquet")
pd.read_csv(PROJ / "TA-MOH_analyse/data/sites.csv", encoding="utf-8-sig").to_parquet(pq, index=False)
c = ingest(str(pq), dataset_name="sites_parquet")
(OUT / "11_parquet_source.json").write_text(json.dumps(c, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"PARQUET  : {c['status']}  {c['row_count']}×{c['column_count']}  {c['dataset_version_id']}  "
      f"compression={c['source_metadata']['read'].get('compression')}  "
      f"row_groups={c['source_metadata']['read'].get('row_groups')}")

# 2) Vérification indépendante : les sources n'ont pas bougé après ingestion
print("\n--- Vérification indépendante des sources (recalcul SHA-256 maintenant) ---")
rows = []
for f in sorted(OUT.glob("*.json")):
    c = json.loads(f.read_text(encoding="utf-8"))
    sm = c.get("source_metadata", {})
    integ = sm.get("integrity") or {}
    p = sm.get("path")
    if sm.get("kind") != "fichier" or not p:
        rows.append({"test": f.stem, "type": sm.get("kind"), "source": sm.get("table") or "-",
                     "sha_before": "-", "sha_apres_ingestion": "-", "sha_maintenant": "-",
                     "identique": "n/a (lecture seule SQL)", "mtime_inchange": "-"})
        continue
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    now = h.hexdigest()
    mtime_now = datetime.fromtimestamp(Path(p).stat().st_mtime).astimezone().isoformat(timespec="seconds")
    rows.append({
        "test": f.stem, "type": "fichier", "source": Path(p).name,
        "sha_before": (integ.get("sha256_before") or "")[:12],
        "sha_apres_ingestion": (integ.get("sha256_after") or "")[:12],
        "sha_maintenant": now[:12],
        "identique": "OUI" if now == integ.get("sha256_after") == integ.get("sha256_before") else "NON",
        "mtime_inchange": "OUI" if mtime_now == sm.get("modified_at") else f"NON ({mtime_now} vs {sm.get('modified_at')})",
    })
df = pd.DataFrame(rows)
pd.set_option("display.width", 240)
print(df.to_string(index=False))
print("\nSources conformes :", int((df["identique"] == "OUI").sum()), "/",
      int((df["identique"].isin(["OUI"])).sum()), "fichiers  |  non-fichiers (SQL) :",
      int((df["identique"] == "n/a (lecture seule SQL)").sum()))
