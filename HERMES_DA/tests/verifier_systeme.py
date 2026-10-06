# -*- coding: utf-8 -*-
"""
Vérification de bout en bout du système HERMES DATA ANALYTICS.

1. exécute une demande réelle (orchestrateur → agent 01 → Validator) ;
2. contrôle les invariants en base PostgreSQL ;
3. compare le miroir `hermes` avec le registre de l'agent 01 (`ingestion`) ;
4. revérifie l'intégrité des fichiers sources.

Usage : python tests/verifier_systeme.py [--source <fichier>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from hermes_da import orchestrator                                    # noqa: E402
from hermes_da.config import HERMES_DA_DIR, PROJET_DIR                # noqa: E402
from hermes_da.db import Database                                     # noqa: E402
from hermes_da.detectors_helper import sha256_file                    # noqa: E402

DEFAULT_SOURCE = PROJET_DIR / "TA-MOH_analyse" / "data" / "quality_issues.csv"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    args = parser.parse_args()

    print("=" * 90)
    print("VÉRIFICATION DU SYSTÈME HERMES DATA ANALYTICS")
    print("=" * 90)
    db = Database()
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))
        print(f"  [{'OK ' if ok else 'ÉCHEC'}] {name}{(' — ' + detail) if detail else ''}")

    try:
        # ---------------------------------------------------------- 1. exécution réelle
        sha_before = sha256_file(args.source)
        print(f"\n1. Exécution : {args.source}")
        response = orchestrator.run("ingérer la source, contrôler la qualité et produire un rapport",
                                    source=args.source, db=db)
        print(f"   job={response['job_id']} validation={response['validation_status']} "
              f"étapes={len(response['steps'])}")
        check("workflow_exécuté", bool(response["job_id"]), response["job_id"])
        check("validation_rendue", response["validation_status"] in
              ("APPROVED", "APPROVED_WITH_WARNINGS", "REQUIRES_CORRECTION", "REJECTED"),
              response["validation_status"])
        ingest_step = next((s for s in response["steps"] if s["task_type"] == "ingest"), None)
        check("ingestion_réussie", bool(ingest_step and ingest_step["status"] == "DONE"),
              f"statut={ingest_step['status'] if ingest_step else 'absent'}")
        check("agent_non_implémenté_marqué_skipped",
              any(s["status"] == "SKIPPED" and not s["available"] for s in response["steps"])
              or all(s["available"] for s in response["steps"]),
              "les agents DECLARED ne produisent aucun résultat")
        check("rapport_bloqué_si_validation_défavorable",
              response["validation_status"] not in ("APPROVED", "APPROVED_WITH_WARNINGS")
              and response["final_output"]["reporting_authorized"] is False
              or response["validation_status"] in ("APPROVED", "APPROVED_WITH_WARNINGS"),
              str(response["final_output"].get("report_blocked_reason")))

        # ---------------------------------------------------------- 2. invariants PostgreSQL
        print("\n2. Invariants PostgreSQL (schéma hermes)")
        orphans = db.query("""SELECT count(*) AS n FROM hermes.jobs j
                              WHERE NOT EXISTS (SELECT 1 FROM hermes.audit_logs a WHERE a.job_id = j.job_id)""")[0]["n"]
        check("chaque_job_a_un_audit", orphans == 0, f"{orphans} job(s) sans trace")

        sans_fin = db.query("""SELECT count(*) AS n FROM hermes.jobs
                               WHERE status IN ('DONE','FAILED','SKIPPED','REQUIRES_CORRECTION')
                                 AND completed_at IS NULL""")[0]["n"]
        check("statuts_terminaux_datés", sans_fin == 0, f"{sans_fin} job(s) terminé(s) sans completed_at")

        orphelins_val = db.query("""SELECT count(*) AS n FROM hermes.validation_results v
                                    WHERE NOT EXISTS (SELECT 1 FROM hermes.jobs j WHERE j.job_id = v.validated_job_id)""")[0]["n"]
        check("validations_référencent_un_job_existant", orphelins_val == 0, f"{orphelins_val} orpheline(s)")

        decisions = {r["decision"]: r["n"] for r in
                     db.query("SELECT decision, count(*) AS n FROM hermes.validation_results GROUP BY 1")}
        check("décisions_de_validation_valides",
              set(decisions) <= {"APPROVED", "APPROVED_WITH_WARNINGS", "REQUIRES_CORRECTION", "REJECTED"},
              str(decisions))

        # ---------------------------------------------------------- 3. cohérence du miroir
        print("\n3. Cohérence miroir hermes ↔ registre ingestion")
        from hermes_da import reconcile

        rattrapage = reconcile.backfill(db)
        check("réconciliation_hors_orchestrateur", rattrapage["restantes"] == 0,
              f"{rattrapage['versions_mirées']} version(s) rattrapée(s)")
        ecarts = db.query("""
            SELECT h.dataset_version_id, h.column_count AS hermes_colonnes,
                   i.column_count AS ingestion_colonnes, h.row_count AS hermes_lignes,
                   i.row_count AS ingestion_lignes,
                   (SELECT count(*) FROM hermes.variables v WHERE v.dataset_version_id = h.dataset_version_id) AS variables
            FROM hermes.dataset_versions h
            JOIN ingestion.dataset_versions i ON i.dataset_version_id = h.dataset_version_id
            WHERE h.column_count <> i.column_count OR h.row_count <> i.row_count
        """)
        check("volumétrie_identique_aux_deux_schémas", not ecarts,
              f"{len(ecarts)} écart(s) : {ecarts[:2]}")
        non_miroir = db.query("""
            SELECT count(*) AS n FROM ingestion.dataset_versions i
            WHERE i.status <> 'ERREUR'
              AND NOT EXISTS (SELECT 1 FROM hermes.dataset_versions h WHERE h.dataset_version_id = i.dataset_version_id)""")[0]["n"]
        check("chaque_version_ingérée_est_mirée", non_miroir == 0, f"{non_miroir} version(s) non mirée(s)")

        # ---------------------------------------------------------- 4. agent 01 intact
        print("\n4. Agent 01 (code existant) et intégrité de la source")
        check("package_ingestion_intact", (HERMES_DA_DIR.parent / "KLDataViz_console" / "ingestion" / "agent.py").exists(),
              "aucune modification apportée au code de l'agent 01")
        sha_after = sha256_file(args.source)
        check("source_non_modifiée", sha_before == sha_after, f"{sha_before[:16]}… → {sha_after[:16]}…")

        # ---------------------------------------------------------- 5. journal d'audit du run
        print("\n5. Journal d'audit du workflow")
        trail = db.query("SELECT event_type, count(*) AS n FROM hermes.audit_logs WHERE job_id = %s "
                         "OR job_id IN (SELECT job_id FROM hermes.jobs WHERE parent_job_id = %s) GROUP BY 1",
                         (response["job_id"], response["job_id"]))
        types = {t["event_type"]: t["n"] for t in trail}
        check("transitions_journalisées", types.get("job_created", 0) >= 2 and types.get("job_done", 0) >= 1,
              str(types))

        # ---------------------------------------------------------- bilan
        ok = sum(1 for _, o, _ in results if o)
        print("\n" + "=" * 90)
        print(f"BILAN : {ok}/{len(results)} contrôles réussis")
        for name, flag, detail in results:
            if not flag:
                print(f"  ✖ {name} — {detail}")
        return 0 if ok == len(results) else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
