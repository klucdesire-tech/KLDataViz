# -*- coding: utf-8 -*-
"""
Interface en ligne de commande de l'agent DATA INGESTION.

Exemples :
    python -m ingestion "C:/data/rapport.xlsx" --sheet "TAB_IND"
    python -m ingestion "C:/data/sites.csv" --name sites_ta_moh
    python -m ingestion "host=127.0.0.1 dbname=kldataviz" --table fact_district --kind postgres
    python -m ingestion --list
    python -m ingestion --versions DS-XXXXXXXXXXXX
    python -m ingestion --summary
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import ingest
from .store import Registry


def _print_human(contract: dict, verbose: bool = False) -> None:
    sm = contract.get("source_metadata", {})
    print("=" * 78)
    print(f"INGESTION — {sm.get('filename') or sm.get('table') or sm.get('origin')}")
    print("=" * 78)
    print(f"  statut             : {contract['status']}")
    print(f"  dataset_id         : {contract['dataset_id']}")
    print(f"  dataset_version_id : {contract['dataset_version_id']}")
    print(f"  lignes × colonnes  : {contract['row_count']} × {contract['column_count']}")
    print(f"  format / encodage  : {sm.get('format')} / {sm.get('encoding') or '—'}"
          f" (séparateur « {sm.get('separator') or '—'} »)")
    if sm.get("sheet"):
        print(f"  feuille            : {sm['sheet']} (sur {sm.get('sheet_count')})")
    if sm.get("size_bytes"):
        print(f"  taille source      : {sm['size_bytes'] / 1024:.1f} Ko")
    integ = sm.get("integrity") or {}
    print(f"  source intacte     : {integ.get('source_unchanged')}  ({integ.get('mode')})")
    v = contract.get("version") or {}
    print(f"  version            : v{int(v.get('version_no') or 0):03d}"
          f"{'  (réutilisée — contenu identique)' if v.get('reused') else '  (nouvelle)'}")
    st = contract.get("storage") or {}
    print(f"  version de travail : {st.get('work_path')}")
    ms = contract.get("metadata_store") or {}
    print(f"  métadonnées        : {ms.get('backend')} — {json.dumps(ms.get('written'), ensure_ascii=False)}")
    print(f"  durée              : {contract['duration_ms']} ms")

    schema = contract.get("schema") or {}
    if schema:
        print(f"\n  Structure : {schema.get('structure', {}).get('structure')} — entité estimée : {schema.get('estimated_entity')}")
        print(f"  Identifiants potentiels : {schema.get('identifier_columns') or 'aucun'}"
              + (f" | clé composite : {schema.get('primary_key_candidate')}" if schema.get("composite_keys") else ""))
    print("\n  Variables :")
    print(f"    {'#':>3}  {'nom':<28}{'type':<11}{'rôle':<13}{'% vide':>7}{'uniques':>9}  id")
    for c in contract.get("variables", [])[:40]:
        print(f"    {c.get('ordinal', 0):>3}  {str(c['name'])[:27]:<28}{str(c.get('normalized_type')):<11}"
              f"{str(c.get('semantic_role')):<13}{c.get('null_pct', 0):>7}{c.get('distinct_count', 0):>9}  "
              f"{'OUI' if c.get('id_candidate') else ''}")
    if len(contract.get("variables", [])) > 40:
        print(f"    … {len(contract['variables']) - 40} colonne(s) supplémentaire(s)")
    w = contract.get("warnings") or []
    if w:
        print(f"\n  Avertissements ({len(w)}) :")
        for item in w[:15]:
            print(f"    ⚠ {item}")
        if len(w) > 15:
            print(f"    … {len(w) - 15} autre(s)")
    e = contract.get("errors") or []
    if e:
        print(f"\n  Erreurs ({len(e)}) :")
        for item in e:
            print(f"    ✖ {item}")
    if verbose:
        print("\n  Journal d'audit :")
        for s in contract.get("audit", []):
            print(f"    - {s['at']} {s['step']} : {json.dumps(s.get('detail'), ensure_ascii=False, default=str)[:150]}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Agent DATA INGESTION — import sans altération de la source")
    p.add_argument("sources", nargs="*", help="fichier, URL ou DSN PostgreSQL")
    p.add_argument("--kind", default="auto", choices=["auto", "fichier", "postgres", "sqlite"])
    p.add_argument("--sheet", default=None, help="feuille Excel à importer (nom ou index)")
    p.add_argument("--table", default=None, help="table source (PostgreSQL / SQLite)")
    p.add_argument("--schema", dest="schema_name", default="public", help="schéma PostgreSQL source")
    p.add_argument("--where", default=None, help="filtre SQL optionnel (lecture seule)")
    p.add_argument("--encoding", default=None)
    p.add_argument("--separator", default=None)
    p.add_argument("--header", type=int, default=None, help="index 0-based de la ligne d'en-tête")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--name", default=None, help="nom logique du dataset")
    p.add_argument("--force", action="store_true", help="créer une version même si le contenu est identique")
    p.add_argument("--work-format", default="parquet", choices=["parquet", "csv"])
    p.add_argument("--registry-dsn", default=None, help="DSN PostgreSQL du registre de métadonnées")
    p.add_argument("--sqlite", action="store_true", help="forcer le registre SQLite (sans PostgreSQL)")
    p.add_argument("--json", dest="json_out", default=None, help="écrire le contrat complet dans un fichier JSON")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--list", action="store_true", help="lister les datasets du registre")
    p.add_argument("--versions", default=None, metavar="DATASET_ID", help="versions d'un dataset")
    p.add_argument("--detail", default=None, metavar="DATASET_VERSION_ID", help="colonnes d'une version")
    p.add_argument("--summary", action="store_true", help="état du registre de métadonnées")
    args = p.parse_args(argv)

    if args.sheet is not None:
        try:
            args.sheet = int(args.sheet)
        except ValueError:
            pass

    reg = Registry(dsn=args.registry_dsn, force_sqlite=args.sqlite, verbose=args.verbose)

    if args.list or args.versions or args.detail or args.summary:
        reg.ensure()
        if args.summary:
            print(json.dumps(reg.summary(), ensure_ascii=False, indent=2, default=str))
        if args.list:
            rows = reg.latest_versions()
            if not rows:
                print("Registre vide.")
            else:
                print(f"{'dataset_id':<16}{'v':>4}{'lignes':>10}{'col':>5}  {'nom':<28}{'format':<10}")
                for r in rows:
                    print(f"{r['dataset_id']:<16}{r['version_no']:>4}{r['row_count']:>10}{r['column_count']:>5}  "
                          f"{str(r['dataset_name'])[:27]:<28}{str(r.get('source_format'))[:9]:<10}")
        if args.versions:
            for v in reg.list_versions(args.versions):
                print(f"v{v['version_no']:03d}  {v['dataset_version_id']}  {v['status']:<12} "
                      f"{v['row_count']}×{v['column_count']}  {v['ingested_at']}")
        if args.detail:
            for c in reg.columns_of(args.detail):
                print(f"{c['ordinal']:>3}  {c['column_name']:<30}{str(c['normalized_type']):<10}"
                      f"{str(c['semantic_role']):<14}{'ID' if c['id_candidate'] else ''}")
        return 0

    if not args.sources:
        p.print_help()
        return 2

    exit_code = 0
    contracts = []
    for src in args.sources:
        contract = ingest(
            src, kind=args.kind, sheet=args.sheet, table=args.table, schema_name=args.schema_name,
            where=args.where, encoding=args.encoding, separator=args.separator, header=args.header,
            limit=args.limit, dataset_name=args.name, registry_dsn=args.registry_dsn,
            force_sqlite=args.sqlite, force=args.force, prefer_work_format=args.work_format,
        )
        contracts.append(contract)
        _print_human(contract, verbose=args.verbose)
        if contract["errors"]:
            exit_code = 1

    payload = contracts[0] if len(contracts) == 1 else contracts
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nContrat JSON écrit : {args.json_out}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
