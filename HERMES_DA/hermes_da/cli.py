# -*- coding: utf-8 -*-
"""
Interface en ligne de commande du système HERMES DATA ANALYTICS.

    python -m hermes_da run --objective "…" --source "C:/data/x.csv" [--sheet NOM] [--json out.json]
    python -m hermes_da plan --objective "…" [--source …]
    python -m hermes_da agents           # catalogue + statut + volumétrie de jobs
    python -m hermes_da schema           # schéma PostgreSQL hermes (tables, colonnes, volumétrie)
    python -m hermes_da jobs [-n 20]     # derniers jobs
    python -m hermes_da job <job_id>     # détail : enfants, audit, résultat
    python -m hermes_da init             # crée le schéma et synchronise les agents
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import agents_registry, orchestrator
from .config import describe
from .db import Database, as_json
from .jobs import JobManager


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _cmd_run(args, db: Database) -> int:
    parameters = {"sheet": args.sheet, "limit": args.limit, "force": args.force,
                  "dataset_name": args.name, "kind": args.kind, "table": args.table}
    parameters = {k: v for k, v in parameters.items() if v not in (None, False)}
    response = orchestrator.run(args.objective, source=args.source, parameters=parameters, db=db)
    print("=" * 84)
    print(f"DEMANDE  : {response['objective']}")
    print(f"JOB      : {response['job_id']}")
    print("=" * 84)
    print("PLAN")
    for s in response["plan"]:
        flag = "OK " if s["available"] else "!! "
        print(f"  {flag}{s['order']}. {s['agent_id']} {s['agent_name']:<26} {s['task_type']:<18} — {s['reason']}")
    print("\nEXÉCUTION")
    for s in response["steps"]:
        print(f"  {s['status']:<12} {s['agent_id']} {s['task_type']:<18} job={s['job_id']} "
              f"tentatives={s['attempts']}")
        ref = s["result_reference"]
        if ref.get("dataset_version_id"):
            print(f"               dataset={ref['dataset_id']} version={ref['dataset_version_id']} "
                  f"{ref.get('row_count')}×{ref.get('column_count')}")
        if ref.get("decision"):
            print(f"               validation={ref['decision']} contrôles={ref.get('checks')} "
                  f"échecs={ref.get('checks_failed')}")
    print(f"\nDATASETS UTILISÉS : {json.dumps(response['datasets_used'], ensure_ascii=False)}")
    print(f"VALIDATION        : {response['validation_status']}")
    print(f"RAPPORT           : {response['final_output'].get('deliverable') or response['final_output'].get('report_blocked_reason')}")
    if response["warnings"]:
        print(f"\nAVERTISSEMENTS ({len(response['warnings'])})")
        for w in response["warnings"][:12]:
            print(f"  ⚠ {w}")
    if response["errors"]:
        print(f"\nERREURS ({len(response['errors'])})")
        for e in response["errors"][:12]:
            print(f"  ✖ {e}")
    if args.json:
        Path(args.json).write_text(json.dumps(response, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nRéponse complète écrite : {args.json}")
    failed = [s for s in response["steps"] if s["status"] == "FAILED"]
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HERMES DATA ANALYTICS — orchestration multi-agents")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="exécuter une demande analytique complète")
    p_run.add_argument("--objective", required=True)
    p_run.add_argument("--source", default=None)
    p_run.add_argument("--sheet", default=None)
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--kind", default=None, choices=["fichier", "postgres", "sqlite", "auto"])
    p_run.add_argument("--table", default=None)
    p_run.add_argument("--name", default=None)
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--json", default=None)

    p_plan = sub.add_parser("plan", help="afficher le plan sans l'exécuter")
    p_plan.add_argument("--objective", required=True)
    p_plan.add_argument("--source", default=None)

    sub.add_parser("init", help="créer le schéma hermes et synchroniser les agents")
    sub.add_parser("sync", help="mirer dans hermes les versions ingérées hors orchestrateur")
    sub.add_parser("agents", help="catalogue des agents")
    sub.add_parser("schema", help="schéma PostgreSQL hermes")
    sub.add_parser("info", help="configuration et découverte de l'existant")

    p_jobs = sub.add_parser("jobs", help="derniers jobs")
    p_jobs.add_argument("-n", type=int, default=20)

    p_job = sub.add_parser("job", help="détail d'un job")
    p_job.add_argument("job_id")
    p_job.add_argument("--audit", action="store_true")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    db = Database()
    try:
        if args.command == "run":
            return _cmd_run(args, db)
        if args.command == "plan":
            _print(orchestrator.plan(args.objective, source=args.source))
            return 0
        if args.command == "init":
            info = db.ensure_schema()
            sync = agents_registry.sync(db)
            print(json.dumps({"schema": info, "agents": sync}, ensure_ascii=False, indent=2, default=str))
            return 0
        if args.command == "sync":
            db.ensure_schema()
            from . import reconcile

            report = reconcile.backfill(db)
            print(json.dumps({"miroir": report["versions_mirées"], "restantes": report["restantes"],
                              "versions": report["dataset_version_ids"]}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "agents":
            db.ensure_schema()
            agents_registry.sync(db)
            rows = db.query("SELECT agent_id, ordinal, name, status, nb_jobs, jobs_reussis, jobs_echoues "
                            "FROM hermes.v_agent_status ORDER BY ordinal")
            print(f"{'id':<8}{'agent':<30}{'statut':<13}{'jobs':>5}{'ok':>5}{'ko':>5}")
            for r in rows:
                print(f"{r['agent_id']:<8}{r['name'][:29]:<30}{r['status']:<13}{r['nb_jobs']:>5}"
                      f"{r['jobs_reussis']:>5}{r['jobs_echoues']:>5}")
            return 0
        if args.command == "schema":
            report = db.schema_report()
            print(f"schéma {report['schema']} — {report['table_count']} tables, {len(report['views'])} vues")
            for t in report["tables"]:
                print(f"  {t['table']:<24} {len(t['columns']):>2} colonnes  {t['rows']:>5} lignes")
            print("  vues :", ", ".join(report["views"]))
            return 0
        if args.command == "info":
            _print(describe())
            return 0
        if args.command == "jobs":
            db.ensure_schema()
            rows = JobManager(db).recent(args.n)
            print(f"{'job_id':<28}{'agent':<8}{'tâche':<18}{'statut':<20}{'dataset':<16}{'ms':>7}")
            for r in rows:
                print(f"{r['job_id']:<28}{r['agent_id']:<8}{r['task_type']:<18}{r['status']:<20}"
                      f"{str(r['dataset_id'] or '-')[:15]:<16}{r['duration_ms'] if r['duration_ms'] is not None else '-':>7}")
            return 0
        if args.command == "job":
            db.ensure_schema()
            manager = JobManager(db)
            job = manager.get(args.job_id)
            if not job:
                print(f"job inconnu : {args.job_id}")
                return 1
            _print(job)
            children = manager.children(args.job_id)
            if children:
                print("\nJOBS ENFANTS")
                for c in children:
                    print(f"  {c['status']:<12} {c['agent_id']} {c['task_type']:<18} {c['job_id']}")
            if args.audit:
                print("\nJOURNAL D'AUDIT")
                for a in manager.audit_trail(args.job_id):
                    print(f"  {a['created_at']}  {a['event_type']:<22} {a['agent_id'] or '-':<8} {a['message']}")
            return 0
    finally:
        db.close()
    return 2


if __name__ == "__main__":
    sys.exit(main())
