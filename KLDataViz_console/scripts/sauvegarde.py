# -*- coding: utf-8 -*-
"""Sauvegarde de KLDataViz : base PostgreSQL + données locales (DATA_DIR).

Usage :
    python scripts/sauvegarde.py                 # sauvegarde dans ./sauvegardes, garde 7
    python scripts/sauvegarde.py --garder 14
    python scripts/sauvegarde.py --dossier /var/backups/kldataviz

Ce que fait le script :
- `pg_dump` de la base (format custom, restaurable avec `pg_restore`) ;
- archive `tar.gz` de `KLDV_DATA_DIR` (storage, tenants, exports) ;
- rotation : supprime les sauvegardes au-delà du nombre demandé ;
- vérifie que les deux fichiers ne sont pas vides avant de rendre la main.

La DSN vient de `DATABASE_URL` (Render, Supabase), sinon de `ingestion.store.default_dsn()`
(base locale `kldataviz`). Aucun secret n'est écrit dans le script.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))


def _dsn() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    from ingestion.store import default_dsn
    return default_dsn()


def _pg_dump() -> str:
    """Chemin de pg_dump : PATH, sinon installation Windows habituelle."""
    trouve = shutil.which("pg_dump")
    if trouve:
        return trouve
    for version in ("18", "17", "16", "15"):
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            p = Path(base) / f"PostgreSQL/{version}/bin/pg_dump.exe"
            if p.is_file():
                return str(p)
    raise SystemExit("pg_dump introuvable : installez PostgreSQL ou ajoutez son dossier bin au PATH.")


def _data_dir() -> Path:
    from tenancy import DATA_DIR
    return Path(DATA_DIR)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sauvegarde KLDataViz (base + fichiers).")
    ap.add_argument("--dossier", default=str(RACINE / "sauvegardes"))
    ap.add_argument("--garder", type=int, default=7, help="nombre de sauvegardes conservées")
    ap.add_argument("--sans-base", action="store_true", help="ne sauvegarder que les fichiers")
    args = ap.parse_args(argv)

    sortie = Path(args.dossier)
    sortie.mkdir(parents=True, exist_ok=True)
    horodatage = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dump = sortie / f"base_{horodatage}.dump"
    archive = sortie / f"donnees_{horodatage}.tar.gz"

    dsn = _dsn()
    if not args.sans_base:
        print(f"[sauvegarde] base -> {dump}")
        cmd = [_pg_dump(), "--format=custom", "--no-owner", "--file", str(dump), "--dbname", dsn]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            message = (r.stderr or r.stdout or "pg_dump a échoué sans message").strip()
            print(f"[sauvegarde] échec de la base : {message[-800:]}", file=sys.stderr)
            dump.unlink(missing_ok=True)
            return 2
        if dump.stat().st_size == 0:
            print("dump vide : sauvegarde annulée.", file=sys.stderr)
            dump.unlink(missing_ok=True)
            return 3

    data = _data_dir()
    print(f"[sauvegarde] fichiers {data} -> {archive}")
    if data.is_dir():
        with tarfile.open(archive, "w:gz") as tar:
            exclus = {"sauvegardes", "__pycache__", ".venv", "tests", ".git", "node_modules"}
            for enfant in data.iterdir():
                if enfant.name in exclus:
                    continue
                tar.add(enfant, arcname=enfant.name)
        if archive.stat().st_size == 0:
            print("archive vide : sauvegarde annulée.", file=sys.stderr)
            archive.unlink(missing_ok=True)
            return 4
    else:
        print(f"[sauvegarde] {data} absent : archive ignorée.", file=sys.stderr)

    # rotation : les plus anciennes d'abord
    for motif in ("base_*.dump", "donnees_*.tar.gz"):
        fichiers = sorted(sortie.glob(motif), key=lambda p: p.stat().st_mtime, reverse=True)
        for vieux in fichiers[args.garder:]:
            vieux.unlink(missing_ok=True)

    faits = [p.name for p in (dump, archive) if p.exists()]
    print("[sauvegarde] terminé : " + (", ".join(faits) if faits else "aucun fichier"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
