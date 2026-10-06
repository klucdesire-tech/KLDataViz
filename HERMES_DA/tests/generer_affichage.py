# -*- coding: utf-8 -*-
"""Génère un affichage HTML (widget) du dernier workflow exécuté, lu directement dans PostgreSQL."""
import json
import sys
from pathlib import Path

BASE = Path(r"C:\Users\Luc Déisré KOUAKOU\OneDrive\Desktop\cours\AGENT IA\Projets\HERMES_DA")
sys.path.insert(0, str(BASE))
from hermes_da.db import Database  # noqa: E402

resp = json.loads((BASE / "storage/artifacts/demo_affichage.json").read_text(encoding="utf-8"))
master = resp["job_id"]
db = Database()

jobs = db.query("""SELECT job_id, agent_id, task_type, status, duration_ms, attempts,
                          jsonb_array_length(warnings) AS w, jsonb_array_length(errors) AS e
                   FROM hermes.jobs WHERE job_id = %s OR parent_job_id = %s
                   ORDER BY created_at""", (master, master))
dataset = db.query("""SELECT * FROM hermes.v_dataset_lineage WHERE dataset_id = %s LIMIT 1""",
                   (resp["datasets_used"][0]["dataset_id"],))[0] if resp["datasets_used"] else {}
val = db.query("""SELECT * FROM hermes.validation_results
                  WHERE job_id IN (SELECT job_id FROM hermes.jobs WHERE job_id = %s OR parent_job_id = %s)
                  ORDER BY created_at DESC LIMIT 1""", (master, master))
val = val[0] if val else {}
counts = {t: db.query(f"SELECT count(*) AS n FROM hermes.{t}")[0]["n"] for t in
          ("jobs", "audit_logs", "validation_results", "datasets", "dataset_versions", "variables",
           "quality_checks", "indicators", "analysis_results", "anomalies", "reports")}
db.close()

AGENT_NAMES = {"AGT-00": "Master Orchestrator", "AGT-01": "Data Ingestion", "AGT-02": "Data Quality",
               "AGT-03": "Data Engineering", "AGT-04": "Indicator", "AGT-05": "Statistical Analysis",
               "AGT-06": "Anomaly Detection", "AGT-07": "Interpretation", "AGT-08": "Visualization",
               "AGT-09": "Reporting", "AGT-10": "Validator"}
COLORS = {"DONE": "var(--green, #8aff71)", "SKIPPED": "#d29922", "FAILED": "#f85149",
          "RUNNING": "#6cc9ee", "QUEUED": "var(--muted-foreground)"}
DECISION_COLORS = {"APPROVED": "var(--green, #8aff71)", "APPROVED_WITH_WARNINGS": "#d29922",
                   "REQUIRES_CORRECTION": "#f85149", "REJECTED": "#f85149"}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


steps = []
for st in resp["steps"]:
    j = next((x for x in jobs if x["job_id"] == st["job_id"]), None)
    ms = j["duration_ms"] if j else None
    steps.append(f"""
    <div class="step">
      <span class="dot" style="background:{COLORS.get(st['status'], 'var(--muted-foreground)')}"></span>
      <span class="num">{st['order']}</span>
      <span class="ag">{esc(st['agent_id'])} · {esc(st['agent_name'])}</span>
      <span class="task">{esc(st['task_type'])}</span>
      <span class="status" style="color:{COLORS.get(st['status'], 'inherit')}">{esc(st['status'])}</span>
      <span class="ms">{ms if ms is not None else '—'} ms</span>
    </div>""")

checks = val.get("checks_performed") or []
check_rows = "".join(
    f"""<div class="check {'ko' if not c['ok'] else ''}">
          <span class="mark">{'✖' if not c['ok'] else '✔'}</span>
          <span class="cname">{esc(c['check'])}</span>
          <span class="cdetail">{esc(str(c['detail'])[:110])}</span>
          <span class="sev">{esc(c.get('severity') or '')}</span>
        </div>""" for c in checks)

decision = val.get("decision") or resp["validation_status"]
warnings = resp.get("warnings") or []
errors = resp.get("errors") or []
warn_html = "".join(f'<li>{esc(w)}</li>' for w in warnings) or "<li class='muted'>aucun</li>"
err_html = "".join(f'<li class="err">{esc(e)}</li>' for e in errors) or "<li class='muted'>aucun</li>"

foot = " ".join(f"<span class='cnt'><b>{v}</b> {k}</span>" for k, v in counts.items())

html = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}
body{{color:var(--foreground);font-family:inherit;font-size:13px;margin:0}}
h3{{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--muted-foreground);
   font-weight:600;margin:18px 0 8px}}
.head{{display:flex;flex-wrap:wrap;gap:10px;align-items:center}}
.badge{{border:1px solid var(--border);border-radius:20px;padding:5px 13px;font-size:12px;font-weight:600}}
.badge.dec{{border-color:{DECISION_COLORS.get(decision,'var(--border)')};
   color:{DECISION_COLORS.get(decision,'inherit')};background:color-mix(in srgb, {DECISION_COLORS.get(decision,'#888')} 12%, transparent)}}
.kv{{display:flex;gap:8px;padding:4px 0;border-bottom:1px solid var(--border);font-size:12.5px}}
.kv .k{{color:var(--muted-foreground);min-width:170px}}
.kv .v{{font-weight:600;word-break:break-all}}
.step{{display:grid;grid-template-columns:10px 18px 210px 150px 90px 1fr;gap:8px;align-items:center;
   padding:7px 0;border-bottom:1px solid var(--border)}}
.dot{{width:9px;height:9px;border-radius:50%}}
.num{{color:var(--muted-foreground)}}
.ag{{font-weight:600}}
.task{{color:var(--muted-foreground)}}
.status{{font-weight:700;font-size:11.5px}}
.ms{{color:var(--muted-foreground);text-align:right}}
.check{{display:grid;grid-template-columns:20px 230px 1fr 70px;gap:8px;padding:4px 0;
   border-bottom:1px solid var(--border);font-size:12px}}
.check .mark{{color:var(--green,#8aff71);font-weight:700}}
.check.ko .mark{{color:#f85149}}
.check.ko{{background:color-mix(in srgb, #f85149 6%, transparent)}}
.cdetail{{color:var(--muted-foreground);word-break:break-word}}
.sev{{color:#d29922;font-size:11px;text-align:right}}
ul{{margin:6px 0 0 18px;padding:0;font-size:12.5px;line-height:1.7}}
li.err{{color:#f85149}}
.muted{{color:var(--muted-foreground);list-style:none;margin-left:-18px}}
.cnt{{display:inline-block;border:1px solid var(--border);border-radius:8px;padding:4px 9px;margin:0 6px 6px 0;font-size:11.5px}}
.cnt b{{color:#6cc9ee}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:0 26px}}
button{{font-family:inherit;background:transparent;border:1px solid var(--border);color:var(--foreground);
   border-radius:8px;padding:7px 13px;font-size:12.5px;cursor:pointer}}
button:hover{{border-color:var(--accent)}}
</style></head><body>
<div class="head">
  <span class="badge dec">VALIDATION : {esc(decision)}</span>
  <span class="badge">{esc(resp['job_id'])}</span>
  <span class="badge">{esc(len(resp['steps']))} étapes</span>
  <button data-hermes-send="affiche le journal d'audit du job {esc(master)}">Journal d'audit</button>
</div>
<div style="margin-top:12px">
  <div class="kv"><span class="k">demande</span><span class="v">{esc(resp['objective'])}</span></div>
  <div class="kv"><span class="k">dataset / version</span><span class="v">{esc(dataset.get('dataset_id','—'))} · {esc(dataset.get('dataset_version_id','—'))}</span></div>
  <div class="kv"><span class="k">volumétrie version de travail</span><span class="v">{dataset.get('row_count','—')} lignes × {dataset.get('column_count','—')} colonnes — {esc(dataset.get('source_format','—'))}</span></div>
  <div class="kv"><span class="k">version de travail</span><span class="v" style="font-weight:400">{esc(dataset.get('work_path','—'))}</span></div>
  <div class="kv"><span class="k">source</span><span class="v" style="font-weight:400">{esc(dataset.get('origin','—'))}</span></div>
</div>

<h3>Chaîne d'exécution</h3>
{''.join(steps)}

<h3>Contrôles du Validator ({sum(1 for c in checks if c['ok'])}/{len(checks)} conformes)</h3>
{check_rows or '<div class="muted">aucun contrôle enregistré</div>'}

<div class="grid2">
  <div><h3>Avertissements ({len(warnings)})</h3><ul>{warn_html}</ul></div>
  <div><h3>Erreurs / blocages ({len(errors)})</h3><ul>{err_html}</ul></div>
</div>

<h3>Empreinte en base (schéma hermes)</h3>
<div>{foot}</div>
</body></html>"""

out = BASE / "storage/artifacts/affichage_workflow.html"
out.write_text(html, encoding="utf-8")
print("HTML écrit :", out)
print("étapes:", len(resp["steps"]), "| contrôles:", len(checks),
      "| conformes:", sum(1 for c in checks if c["ok"]), "| décision:", decision)
print("jobs du workflow:", [(j["agent_id"], j["status"], j["duration_ms"]) for j in jobs])
