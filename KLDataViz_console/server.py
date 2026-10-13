# -*- coding: utf-8 -*-
"""
KLDataViz — Console d'utilisation de l'agent (serveur local).
Backend : proxy orchestrateUR CVLynk + moteur d'analyse (pandas) + exports PDF/Excel/HTML.
Sert l'interface web sur http://localhost:8642
"""
import http.server, socketserver, json, os, io, threading, time, tempfile, urllib.request, urllib.error, base64, sys, subprocess
import pandas as pd
import numpy as np
import openpyxl
from openpyxl.styles import Font, PatternFill

# --- Agent 2 MODÉLISATION (nettoyage, typage, imputation, doublons) ---
try:
    from modeling import clean_dataframe, bivariate_stats
    MODELING_OK = True
except Exception as _mod_exc:
    MODELING_OK = False
    MODELING_ERROR = str(_mod_exc)
    def clean_dataframe(df):
        raise RuntimeError(MODELING_ERROR)
    def bivariate_stats(df):
        return {}

# --- Agent DATA INGESTION (ingestion sans altération de la source) ---
try:
    from ingestion import ingest as ingest_agent, Registry as IngestionRegistry
    INGESTION_OK = True
    INGESTION_ERROR = None
except Exception as _ingest_exc:  # la console reste utilisable même si l'agent d'ingestion est indisponible
    INGESTION_OK = False
    INGESTION_ERROR = str(_ingest_exc)

BASE = os.path.dirname(os.path.abspath(__file__))
import studio
from urllib.parse import urlparse, parse_qs
PORT = int(os.environ.get("KLDAVIZ_PORT", "8642"))
ORCHESTRATOR = os.environ.get("CVLYNK_ORCHESTRATOR", "https://api.cvlynk.com")


def _load_agent_token():
    """Token d'agent CVLynk — jamais en dur dans le code publié.
    Ordre : variable d'environnement CVLYNK_AGENT_TOKEN, puis fichier local agent_token.txt
    (ignoré par Git), puis ~/.cvlynk-orchestrator.json."""
    tok = os.environ.get("CVLYNK_AGENT_TOKEN")
    if tok:
        return tok.strip()
    local = os.path.join(BASE, "agent_token.txt")
    if os.path.exists(local):
        with open(local, encoding="utf-8") as fh:
            return fh.read().strip()
    cfg = os.path.join(os.path.expanduser("~"), ".cvlynk-orchestrator.json")
    if os.path.exists(cfg):
        try:
            with open(cfg, encoding="utf-8") as fh:
                return (json.load(fh) or {}).get("access_token", "")
        except Exception:
            pass
    return ""


AGENT_TOKEN = _load_agent_token()
EXPORTS = os.path.join(BASE, "exports")
os.makedirs(EXPORTS, exist_ok=True)

LAST_RESULT = None   # dernière analyse (pour les exports)
CURRENT_PATH = None  # fichier de la dernière analyse (contexte du chat)
LAST_INGEST = None   # dernier contrat d'ingestion

def orchestrator(path, method="GET", body=None):
    req = urllib.request.Request(ORCHESTRATOR + path, method=method)
    req.add_header("Authorization", "Bearer " + AGENT_TOKEN)
    req.add_header("Accept", "application/json")
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}

def _auto_charts(df):
    """Génère un graphique adapté à chaque variable + nuages de points multi-variables."""
    charts = []
    numeric_cols = df.select_dtypes(include=[np.number]).columns

    # Nuages de points pour les paires numériques les plus corrélées (multi-variable)
    if len(numeric_cols) >= 2:
        try:
            cm = df[numeric_cols].corr()
            pairs = []
            for i in range(len(cm.columns)):
                for j in range(i):
                    pairs.append((str(cm.columns[i]), str(cm.columns[j]), float(cm.iloc[i, j])))
            pairs = sorted(pairs, key=lambda x: -abs(x[2]))[:3]
            for a, b, r in pairs:
                if abs(r) >= 0.3:
                    sub = df[[a, b]].dropna()
                    if len(sub) > 500:
                        sub = sub.sample(500, random_state=0)
                    if len(sub) > 5:
                        charts.append({
                            "title": f"{a} vs {b} (r={r:.2f})", "type": "scatter",
                            "labels": [str(x) for x in sub[a].tolist()],
                            "values": [[float(y) for y in sub[b].tolist()]],
                            "seriesNames": [b],
                        })
        except Exception:
            pass

    for col in df.columns:
        name = str(col)
        s = df[col]

        # Détection de date
        is_date = pd.api.types.is_datetime64_any_dtype(s)
        if not is_date and not pd.api.types.is_numeric_dtype(s):
            sample = s.dropna().head(30)
            if len(sample) >= 3:
                try:
                    parsed = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=True)
                    if parsed.notna().mean() >= 0.9:
                        is_date = True
                except Exception:
                    pass

        if is_date:
            try:
                dt = pd.to_datetime(s, errors="coerce", format="mixed", dayfirst=True)
                per = dt.dt.to_period("M").astype(str).value_counts().sort_index()
                charts.append({"title": name, "type": "line",
                               "labels": list(per.index), "values": [list(map(int, per.values))]})
            except Exception:
                pass
            continue

        if col in numeric_cols:
            vals = s.dropna()
            nunique = int(vals.nunique())
            if nunique <= 15:
                vc = vals.value_counts().sort_index()
                charts.append({"title": name, "type": "bar",
                               "labels": [str(k) for k in vc.index], "values": [list(map(int, vc.values))]})
            else:
                try:
                    counts, edges = np.histogram(vals, bins=10)
                    labels = [f"{edges[i]:.3g}-{edges[i+1]:.3g}" for i in range(len(counts))]
                    charts.append({"title": name, "type": "histogram",
                                   "labels": labels, "values": [list(map(int, counts))]})
                except Exception:
                    pass
            continue

        vc = s.value_counts(dropna=True)
        if len(vc) == 0:
            continue
        top = vc.head(12)
        charts.append({"title": name, "type": "bar",
                       "labels": [str(k)[:40] for k in top.index], "values": [list(map(int, top.values))]})

    return charts


def _read_df(file_path):
    """Lit un fichier local ou une URL en DataFrame (csv/xlsx/xls/xlsm/json)."""
    if file_path.startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(file_path, headers={"User-Agent": "KLDataViz/1.0"})
            data = urllib.request.urlopen(req, timeout=30).read()
            ext = os.path.splitext(file_path.split("?")[0])[1].lower() or ".csv"
            tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
            tmp.write(data); tmp.close()
            file_path = tmp.name
        except Exception as e:
            return None, f"Téléchargement impossible : {e}"
    if not os.path.exists(file_path):
        return None, f"Fichier introuvable : {file_path}"
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".csv":
            df = pd.read_csv(file_path)
        elif ext in (".xlsx", ".xls", ".xlsm"):
            df = pd.read_excel(file_path)
        elif ext == ".json":
            df = pd.read_json(file_path)
        elif ext == ".parquet":
            df = pd.read_parquet(file_path)
        else:
            return None, f"Format non supporté : {ext}"
    except Exception as e:
        return None, f"Lecture impossible : {e}"
    return df, None


def analyze(file_path):
    df, err = _read_df(file_path)
    if err:
        return {"error": err}

    size = os.path.getsize(file_path)
    rows, cols = df.shape
    total_cells = rows * cols

    columns = []
    missing_total = 0
    for c in df.columns:
        na = int(df[c].isna().sum())
        missing_total += na
        columns.append({
            "name": str(c),
            "dtype": str(df[c].dtype),
            "missing": na,
            "missing_pct": round(na / rows * 100, 2) if rows else 0,
            "unique": int(df[c].nunique(dropna=True)),
        })

    numeric = df.select_dtypes(include=[np.number])
    num_stats = {}
    anomalies = []
    for c in numeric.columns:
        s = numeric[c].dropna()
        if len(s) == 0:
            continue
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outliers = s[(s < lo) | (s > hi)]
        num_stats[str(c)] = {
            "mean": round(float(s.mean()), 4),
            "std": round(float(s.std()), 4),
            "min": round(float(s.min()), 4),
            "q25": round(float(q1), 4),
            "median": round(float(s.median()), 4),
            "q75": round(float(q3), 4),
            "max": round(float(s.max()), 4),
            "outliers": int(len(outliers)),
        }
        if len(outliers) > 0:
            anomalies.append({
                "column": str(c), "type": "valeur aberrante (IQR)",
                "count": int(len(outliers)),
                "detail": f"{len(outliers)} valeur(s) hors [lo={lo:.2f}, hi={hi:.2f}]",
            })

    cat = df.select_dtypes(exclude=[np.number])
    cat_top = {}
    for c in cat.columns:
        vc = df[c].value_counts(dropna=True).head(5)
        cat_top[str(c)] = {str(k): int(v) for k, v in vc.items()}

    corr_pairs = []
    if numeric.shape[1] >= 2:
        cm = numeric.corr()
        for i in range(len(cm.columns)):
            for j in range(i):
                corr_pairs.append((str(cm.columns[i]), str(cm.columns[j]), float(cm.iloc[i, j])))
        corr_pairs = sorted(corr_pairs, key=lambda x: -abs(x[2]))[:10]

    completeness = round((1 - missing_total / total_cells) * 100, 2) if total_cells else 100

    return {
        "file": os.path.basename(file_path),
        "size_kb": round(size / 1024, 1),
        "rows": int(rows),
        "columns": int(cols),
        "completeness_pct": completeness,
        "missing_total": int(missing_total),
        "columns_detail": columns,
        "numeric_stats": num_stats,
        "categorical_top": cat_top,
        "correlations": [{"a": a, "b": b, "r": round(r, 3)} for a, b, r in corr_pairs],
        "anomalies": anomalies,
        "bivariate": bivariate_stats(df),
        "charts": _auto_charts(df),
    }


def _cell_safe(v):
    """Convertit une cellule DataFrame en valeur JSON-safe."""
    if pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, bool):
        return bool(v)
    try:
        if hasattr(v, "item"):
            return v.item()
    except Exception:
        pass
    return v


def clean(file_path, skip=None):
    """Agent 2 — modélisation : nettoie, met en forme et sauvegarde un fichier.
    `skip` : corrections refusées par l'utilisateur (voir modeling.clean_dataframe)."""
    df, err = _read_df(file_path)
    if err:
        return {"error": err}
    df_clean, rapport = clean_dataframe(df, skip=skip) if skip else clean_dataframe(df)

    # Sauvegarde de la version nettoyée (c'est elle, et elle seule, qui sera ingérée)
    cleaned_dir = os.path.join(BASE, "storage", "cleaned")
    os.makedirs(cleaned_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(file_path))[0]
    cleaned_path = os.path.join(cleaned_dir, f"{stem}_nettoye.csv")
    df_clean.to_csv(cleaned_path, index=False, encoding="utf-8-sig")

    preview = df_clean.head(30)
    apercu = [[_cell_safe(x) for x in row]
              for row in preview.itertuples(index=False, name=None)]

    return {
        "file": os.path.basename(file_path),
        "cleaned_path": cleaned_path,
        "rows": int(len(df_clean)),
        "cols": int(len(df_clean.columns)),
        "colonnes": [str(c) for c in df_clean.columns],
        "types": {str(c): str(df_clean[c].dtype) for c in df_clean.columns},
        "apercu": apercu,
        "rapport": rapport,
    }

def fetch_source():
    code, tasks = orchestrator("/api/v1/agents/tasks")
    if code != 200 or not isinstance(tasks, list) or not tasks:
        return {"source": None, "message": "Aucune tâche assignée pour le moment."}
    task = tasks[0]
    payload = task.get("payload") or {}
    source = (payload.get("source") or payload.get("file") or payload.get("link")
              or payload.get("url") or task.get("source") or task.get("file"))
    if source:
        return {"source": source, "task_id": task.get("id") or task.get("task_id")}
    return {"source": None, "message": "La tâche assignée ne contient pas de lien source."}

def export_excel(result):
    wb = openpyxl.Workbook()
    hdr = PatternFill("solid", fgColor="1286B4"); hdr_font = Font(bold=True, color="FFFFFF")
    def sheet(ws, title, headers, rows):
        ws.title = title
        for j, h in enumerate(headers, 1):
            c = ws.cell(1, j, h); c.fill = hdr; c.font = hdr_font
        for i, row in enumerate(rows, 2):
            for j, v in enumerate(row, 1):
                ws.cell(i, j, v)
    ws = wb.active
    sheet(ws, "Synthèse", ["Fichier", "Taille (Ko)", "Lignes", "Colonnes", "Complétude %", "Manquantes", "Anomalies"],
          [[result.get("file"), result.get("size_kb"), result.get("rows"), result.get("columns"),
            result.get("completeness_pct"), result.get("missing_total"), len(result.get("anomalies", []))]])
    sheet(wb.create_sheet(), "Colonnes", ["Variable", "Type", "Uniques", "Manquantes", "% manquant"],
          [[c["name"], c["dtype"], c["unique"], c["missing"], c["missing_pct"]] for c in result.get("columns_detail", [])])
    sheet(wb.create_sheet(), "Statistiques", ["Variable", "Moyenne", "Écart-type", "Min", "Médiane", "Max", "Hors-normes"],
          [[k, v["mean"], v["std"], v["min"], v["median"], v["max"], v["outliers"]] for k, v in result.get("numeric_stats", {}).items()])
    sheet(wb.create_sheet(), "Corrélations", ["Variable A", "Variable B", "r"],
          [[c["a"], c["b"], c["r"]] for c in result.get("correlations", [])])
    sheet(wb.create_sheet(), "Anomalies", ["Colonne", "Type", "Nombre", "Détail"],
          [[a["column"], a["type"], a["count"], a["detail"]] for a in result.get("anomalies", [])])
    fn = f"analyse_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
    wb.save(os.path.join(EXPORTS, fn))
    return fn

def export_html(result):
    def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    cols_html = "".join(f"<tr><td>{esc(c['name'])}</td><td>{esc(c['dtype'])}</td><td>{c['unique']}</td><td>{c['missing']}</td><td>{c['missing_pct']}%</td></tr>"
                        for c in result.get("columns_detail", []))
    stats_html = "".join(f"<tr><td>{esc(k)}</td><td>{v['mean']}</td><td>{v['std']}</td><td>{v['min']}</td><td>{v['median']}</td><td>{v['max']}</td><td>{v['outliers']}</td></tr>"
                         for k, v in result.get("numeric_stats", {}).items())
    corr_html = "".join(f"<tr><td>{esc(c['a'])}</td><td>{esc(c['b'])}</td><td>{c['r']}</td></tr>" for c in result.get("correlations", []))
    anom_html = "".join(f"<li><b>{esc(a['column'])}</b> — {esc(a['type'])} ({a['count']}) : {esc(a['detail'])}</li>" for a in result.get("anomalies", []))
    html = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>Analyse — {esc(result.get('file'))}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:900px;margin:32px auto;padding:0 20px;color:#1a1a1a;}}
h1{{color:#1286b4;}} table{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0;}}
th,td{{border:1px solid #cbd5e0;padding:6px 9px;text-align:left;}} th{{background:#1286b4;color:#fff;}}
.sum{{display:flex;gap:18px;flex-wrap:wrap;}} .kpi{{background:#f0f7fb;border:1px solid #cbe4f2;border-radius:8px;padding:12px 18px;}}
.kpi b{{font-size:22px;color:#1286b4;display:block;}} li{{margin:4px 0;}}</style></head><body>
<h1>Analyse — {esc(result.get('file'))}</h1>
<div class="sum"><div class="kpi"><b>{result.get('rows'):,}</b>lignes</div><div class="kpi"><b>{result.get('columns')}</b>colonnes</div>
<div class="kpi"><b>{result.get('completeness_pct')}%</b>complétude</div><div class="kpi"><b>{len(result.get('anomalies', []))}</b>anomalies</div></div>
<h2>Colonnes</h2><table><tr><th>Variable</th><th>Type</th><th>Uniques</th><th>Manquantes</th><th>%</th></tr>{cols_html}</table>
<h2>Statistiques numériques</h2><table><tr><th>Variable</th><th>Moyenne</th><th>Écart-type</th><th>Min</th><th>Médiane</th><th>Max</th><th>Hors-normes</th></tr>{stats_html}</table>
<h2>Corrélations</h2><table><tr><th>A</th><th>B</th><th>r</th></tr>{corr_html}</table>
<h2>Anomalies</h2><ul>{anom_html or '<li>Aucune anomalie détectée.</li>'}</ul>
<p style="color:#888;font-size:11px;">Généré par KLDataViz — agent Data Analytics &amp; BI</p></body></html>"""
    fn = f"analyse_{time.strftime('%Y%m%d_%H%M%S')}.html"
    with open(os.path.join(EXPORTS, fn), "w", encoding="utf-8") as f:
        f.write(html)
    return fn

def export_pdf(result):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    fn = f"analyse_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path = os.path.join(EXPORTS, fn)
    doc = SimpleDocTemplate(path, pagesize=A4)
    styles = getSampleStyleSheet()
    blue = colors.HexColor("#1286b4")
    story = []
    story.append(Paragraph(f"Analyse — {result.get('file','')}", styles["Title"]))
    story.append(Paragraph(f"Lignes : {result.get('rows',0):,} &nbsp;|&nbsp; Colonnes : {result.get('columns',0)} &nbsp;|&nbsp; "
                          f"Complétude : {result.get('completeness_pct',0)}% &nbsp;|&nbsp; Anomalies : {len(result.get('anomalies',[]))}", styles["Normal"]))
    story.append(Spacer(1, 12))
    def table(headers, rows):
        t = Table([headers] + rows)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), blue),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#cbd5e0")),
            ("FONTSIZE", (0,0), (-1,-1), 8),
        ]))
        return t
    story.append(Paragraph("Colonnes", styles["Heading2"]))
    story.append(table(["Variable", "Type", "Uniques", "Manquantes", "%"],
                       [[c["name"], c["dtype"], c["unique"], c["missing"], c["missing_pct"]] for c in result.get("columns_detail", [])]))
    if result.get("numeric_stats"):
        story.append(Paragraph("Statistiques numériques", styles["Heading2"]))
        story.append(table(["Variable", "Moyenne", "Écart-type", "Min", "Médiane", "Max"],
                           [[k, v["mean"], v["std"], v["min"], v["median"], v["max"]] for k, v in result.get("numeric_stats", {}).items()]))
    if result.get("correlations"):
        story.append(Paragraph("Corrélations", styles["Heading2"]))
        story.append(table(["A", "B", "r"], [[c["a"], c["b"], c["r"]] for c in result.get("correlations", [])]))
    if result.get("anomalies"):
        story.append(Paragraph("Anomalies", styles["Heading2"]))
        for a in result.get("anomalies", []):
            story.append(Paragraph(f"<b>{a['column']}</b> — {a['type']} ({a['count']}) : {a['detail']}", styles["Normal"]))
    doc.build(story)
    return fn

# ---------- Chat / interrogation des données en langage naturel ----------
def _chat_load_work(path):
    if not path:
        return None
    p = str(path)
    cands = []
    if os.path.isfile(p):
        cands = [p]
    elif os.path.isdir(p):
        cands = [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.endswith((".parquet", ".csv"))][:1]
    else:
        for ext in (".parquet", ".csv"):
            if os.path.exists(p + ext):
                cands.append(p + ext)
    for cand in cands:
        try:
            if cand.endswith(".parquet"):
                return pd.read_parquet(cand)
            return pd.read_csv(cand)
        except Exception:
            continue
    return None

def chat_query(question):
    import re
    from ingestion.store import Registry
    reg = Registry()
    reg.ensure()
    datasets = reg.latest_versions()
    ql = (question or "").strip().lower()
    if not ql:
        return {"answer": "Posez une question sur les données (ex. « combien de lignes dans fact_district ? », « somme de value par district »).", "type": "text"}

    # --- liste des datasets
    if re.search(r"\b(liste|lister|quels|quelles|registre|datasets)\b", ql) and not any(
            (d.get("dataset_name") or "").lower() in ql for d in datasets):
        rows = [[d["dataset_id"], d["dataset_name"], d["row_count"], d["column_count"], d["source_format"]]
                for d in datasets]
        return {"answer": f"{len(datasets)} dataset(s) ingéré(s) :", "type": "table",
                "table": {"columns": ["dataset_id", "nom", "lignes", "colonnes", "format"], "rows": rows}}

    if not datasets:
        return {"answer": "Aucun dataset ingéré pour le moment. Ingérez d'abord un fichier (panneau Ingestion).", "type": "text"}

    # --- dataset cible : par nom, sinon par recouvrement de colonnes, sinon le plus récent
    dataset_cols = {}
    for d in datasets:
        try:
            dataset_cols[d["dataset_id"]] = [str(c.get("column_name") or "").lower()
                                             for c in reg.columns_of(d.get("dataset_version_id"))]
        except Exception:
            dataset_cols[d["dataset_id"]] = []
    target = None
    for d in datasets:
        nm = (d.get("dataset_name") or "").lower()
        if nm and nm in ql:
            target = d
            break
    if target is None:
        best = 0
        for d in datasets:
            score = sum(1 for c in dataset_cols.get(d["dataset_id"], []) if c and c in ql)
            if score > best:
                best, target = score, d
    if target is None:
        target = datasets[0]

    df = _chat_load_work(target.get("work_path"))
    if df is None:
        return {"answer": f"Impossible de charger les données de « {target.get('dataset_name')} ».", "type": "text"}

    cols = [str(c) for c in df.columns]
    colmap = {c.lower(): c for c in cols}
    mentioned = [colmap[k] for k in colmap if k and k in ql]
    numeric = [c for c in cols if str(df[c].dtype).startswith(("int", "float"))]

    # --- dimensions
    if re.search(r"combien de ligne|combien d.enregistrement|nb ligne|nombre de ligne", ql):
        return {"answer": f"« {target.get('dataset_name')} » contient {len(df):,} lignes.", "type": "text"}
    if re.search(r"combien de colonne|nb colonne|nombre de colonne", ql) or (
            re.search(r"\bcolonne", ql) and re.search(r"combien|nombre|liste", ql)):
        return {"answer": f"« {target.get('dataset_name')} » a {len(cols)} colonnes : {', '.join(cols)}", "type": "text"}

    # --- liste des colonnes / variables / schéma
    if re.search(r"\b(colonne|variable|champ|schéma|schema|structure)\b", ql) and not mentioned:
        return {"answer": f"Colonnes de « {target.get('dataset_name')} » : {', '.join(cols)}", "type": "text"}

    # --- agrégation
    agg = None
    if re.search(r"\b(somme|total|additionn)\b", ql): agg = "sum"
    elif re.search(r"\bmoyenne\b", ql): agg = "mean"
    elif re.search(r"\bmaximum|max\b", ql): agg = "max"
    elif re.search(r"\bminimum|min\b", ql): agg = "min"
    elif re.search(r"\bnombre de|combien de", ql): agg = "count"
    agg_label = {"sum": "Somme", "mean": "Moyenne", "max": "Maximum", "min": "Minimum", "count": "Nombre"}.get(agg)

    # --- colonne de regroupement (« par X »)
    group_col = None
    m = re.search(r"\bpar\s+([a-z0-9_ éàèêç\-]+)", ql)
    if m:
        cand = m.group(1).strip()
        for k, c in colmap.items():
            if k and (k in cand or cand in k):
                group_col = c
                break

    # --- colonne mesurée (mentionnée et numérique, sinon 1ère numérique)
    value_col = next((c for c in mentioned if c in numeric), None)
    if value_col is None and numeric:
        value_col = numeric[0]

    # --- top N
    topn = None
    m = re.search(r"\btop\s*(\d+)", ql)
    if m:
        topn = int(m.group(1))

    # --- exécution : groupement + agrégation
    if group_col and value_col and agg:
        g = df.groupby(group_col)[value_col].agg(agg).sort_values(ascending=False)
        rows = [[str(k), round(float(v), 2)] for k, v in g.items()]
        return {"answer": f"{agg_label} de « {value_col} » par « {group_col} » — dataset « {target.get('dataset_name')} » :",
                "type": "table", "table": {"columns": [group_col, f"{agg_label} {value_col}"], "rows": rows[:30]}}

    # --- agrégation simple
    if value_col and agg:
        try:
            v = getattr(df[value_col], agg)()
        except Exception:
            v = None
        if v is not None:
            return {"answer": f"{agg_label} de « {value_col} » = {round(float(v), 2)}", "type": "text"}

    # --- top N
    if topn and value_col:
        head = df.nlargest(topn, value_col)[[value_col] + ([group_col] if group_col else [])]
        rows = [[str(r[value_col]), str(r[group_col])] if group_col else [str(r[value_col])]
                for _, r in head.iterrows()]
        cols_out = [value_col] + ([group_col] if group_col else [])
        return {"answer": f"Top {topn} « {value_col} » :", "type": "table",
                "table": {"columns": cols_out, "rows": rows}}

    # --- aperçu du dataset par défaut
    head = df.head(5)
    rows = [[str(r.get(c)) for c in cols] for _, r in head.iterrows()]
    return {"answer": f"Dataset « {target.get('dataset_name')} » ({len(df):,} lignes × {len(cols)} colonnes). "
                     f"Aperçu (5 premières lignes) — posez par ex. « somme de {value_col or '…'} par {group_col or '…'} » :",
            "type": "table", "table": {"columns": cols, "rows": rows}}

# ---------- Chat LLM (DeepSeek, texte -> pandas + visualisation) ----------
def _llm_config():
    p = os.path.join(BASE, "llm_config.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def _llm_call(messages, max_tokens=8000, temperature=0):
    cfg = _llm_config()
    if not cfg or not cfg.get("api_key"):
        return None
    body = json.dumps({"model": cfg.get("model", "deepseek-v4-pro"), "messages": messages,
                       "temperature": temperature, "max_tokens": max_tokens}).encode("utf-8")
    for attempt in range(2):
        req = urllib.request.Request(cfg.get("base_url", "https://api.deepseek.com/chat/completions"),
                                     data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + cfg["api_key"]})
        try:
            r = urllib.request.urlopen(req, timeout=120)
            return json.loads(r.read().decode())["choices"][0]["message"]["content"]
        except Exception:
            if attempt == 0:
                time.sleep(2)
    return None

def _llm_context():
    from ingestion.store import Registry
    reg = Registry()
    reg.ensure()
    datasets = reg.latest_versions()
    out = []
    for d in datasets[:12]:
        df = _chat_load_work(d.get("work_path"))
        cols = []
        if df is not None:
            for c in df.columns:
                try:
                    vals = [str(v) for v in df[c].dropna().head(3).tolist()]
                except Exception:
                    vals = []
                cols.append({"name": str(c), "dtype": str(df[c].dtype), "exemples": vals})
        out.append({"name": d.get("dataset_name"), "id": d.get("dataset_id"),
                    "lignes": d.get("row_count"), "colonnes": cols})
    return out

def _llm_loader(datasets):
    idx = {}
    for d in datasets:
        nm = (d.get("dataset_name") or "").lower()
        idx[nm] = d
        for ext in (".csv", ".xlsx", ".xls", ".xlsm", ".json"):
            if nm.endswith(ext):
                idx[nm[: -len(ext)]] = d
    def load(name):
        key = str(name).lower()
        for ext in (".csv", ".xlsx", ".xls", ".xlsm", ".json"):
            if key.endswith(ext):
                key = key[: -len(ext)]
        d = idx.get(key)
        if d is None:
            for k, v in idx.items():
                if k and key and (key in k or k in key):
                    d = v
                    break
        if d is None:
            raise ValueError("dataset inconnu : " + str(name))
        df = _chat_load_work(d.get("work_path"))
        if df is None:
            raise ValueError("impossible de charger : " + str(name))
        return df
    return load

def _llm_format(result):
    if result is None:
        return None, None
    if isinstance(result, pd.DataFrame):
        cols = [str(c) for c in result.columns]
        rows = [[str(v) for v in row] for row in result.head(50).values.tolist()]
        return {"columns": cols, "rows": rows}, None
    if isinstance(result, pd.Series):
        if len(result) == 1:
            return None, str(result.iloc[0])
        rows = [[str(i), str(v)] for i, v in result.head(50).items()]
        return {"columns": [str(result.name or "valeur"), "valeur"], "rows": rows}, None
    if isinstance(result, (int, float, str, bool)):
        return None, str(result)
    try:
        return None, json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        return None, str(result)

def _code_safe(code):
    banned = ("__import__", "subprocess", "os.system", "os.popen", "import os", "import sys",
              "import shutil", "import socket", "import urllib", "import requests", "import pickle",
              "open(", "eval(", "exec(")
    return not any(b in code.lower() for b in banned)

def llm_chat(question):
    import re
    from ingestion.store import Registry
    reg = Registry()
    reg.ensure()
    datasets = reg.latest_versions()
    context = _llm_context()
    current_df, current_name = None, None
    if CURRENT_PATH:
        current_df, _err = _read_df(CURRENT_PATH)
        if current_df is not None:
            current_name = os.path.splitext(os.path.basename(CURRENT_PATH))[0]
            context.insert(0, {"name": current_name, "fichier_en_cours_d_analyse": True, "lignes": int(len(current_df)),
                               "colonnes": [{"name": str(c), "dtype": str(current_df[c].dtype),
                                             "exemples": [str(v) for v in current_df[c].dropna().head(3).tolist()]}
                                            for c in current_df.columns]})
    system = (
        "Tu es KLDataViz, assistant d'analyse de données. Réponds en français, de façon concise et fiable.\n"
        + studio.chat_preamble() +
        "Sauf mention contraire, la question porte sur le fichier en cours d'analyse (premier dataset de la liste).\n"
        "Datasets disponibles (nom, nombre de lignes, colonnes avec type et exemples) :\n"
        + json.dumps(context, ensure_ascii=False, default=str) + "\n\n"
        "Réponds en renvoyant UNIQUEMENT un objet JSON valide (aucun texte avant/après), avec ces champs :\n"
        '{"answer":"réponse courte en français","code":"...","chart":{...}}\n'
        "- \"code\" (OPTIONNEL) : du code Python (pandas/numpy UNIQUEMENT) qui répond précisément à la question "
        "(somme, moyenne, corrélation, groupby, top N, comparaison, tendance, distribution...). "
        "Accède aux données via le dict `data` (clé = nom du dataset, valeur = DataFrame pandas). "
        "Exemple : df = data['fact_district']; result = df.groupby('district')['value'].sum(). "
        "Assigne le résultat final à la variable `result` (DataFrame, Series ou scalaire).\n"
        "- \"chart\" (OPTIONNEL) : si une visualisation est utile ou demandée, calcule les valeurs avec pandas puis "
        "assigne DANS `code` une variable `chart` = {\"type\":\"...\",\"title\":\"...\",\"labels\":[...],\"values\":[[...]],\"seriesNames\":[...]}. "
        "Types disponibles : bar, line, area, pie, doughnut, scatter, histogram, box.\n"
        "- Choisis le type de graphique ADAPTÉ aux données : catégories/comparaison → bar ; évolution temporelle (trimestre, mois, année) → line ou area ; "
        "proportions/parts → pie ; relation entre deux variables numériques → scatter (labels = valeurs x, values = [valeurs y]) ; "
        "distribution d'une variable numérique → histogram (labels = intervalles, values = effectifs par intervalle) ; "
        "dispersion/outliers d'une variable numérique par groupe → box (values[i] = [min, Q1, médiane, Q3, max] pour chaque catégorie).\n"
        "- SI l'utilisateur demande « un graphique par variable/catégorie/indicateur » (chaque variable a sa propre "
        "représentation), alors `chart` doit être une LISTE de ces dicts : chart = [{...}, {...}, ...], un graphique "
        "par variable.\n"
        "- N'utilise AUCUNE bibliothèque de graphique (pas de matplotlib, plotly, seaborn). Le graphique se décrit "
        "UNIQUEMENT par le dict `chart`, jamais par du code de traçage.\n"
        "- N'invente JAMAIS de données ni de chiffres : tout nombre vient de `code` (réellement calculé). "
        "Dans \"answer\", résume le résultat de façon qualitative (ex. « les districts les plus élevés sont A, B et C »). "
        "Si les données ne permettent pas de répondre, dis-le clairement.\n"
        "- Pour une corrélation : df.corr(numeric_only=True). Pour une tendance temporelle, trie par période."
    )
    content = _llm_call([{"role": "system", "content": system},
                         {"role": "user", "content": question}])
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = None
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    if data is None:
        return {"answer": content, "type": "text"}
    answer = data.get("answer", "") or ""
    code = data.get("code")
    chart = data.get("chart")
    table = None
    if code:
        if not _code_safe(code):
            answer += "\n(requête bloquée par le filtre de sécurité)"
        else:
            try:
                ns = {"pd": pd, "np": np, "load_dataset": _llm_loader(datasets)}
                # précharger les datasets : dict `data` (clé = nom) + variables directes
                _data = {}
                for _d in datasets:
                    _nm = _d.get("dataset_name") or _d.get("dataset_id")
                    _df = _chat_load_work(_d.get("work_path"))
                    if _df is None:
                        continue
                    _data[_nm] = _df
                    _key = re.sub(r"[^0-9A-Za-z_]", "_", str(_nm)).strip("_").lower()
                    if _key and _key.isidentifier():
                        ns.setdefault(_key, _df)
                if current_df is not None:
                    _data[current_name] = current_df
                    ns.setdefault(re.sub(r"[^0-9A-Za-z_]", "_", current_name).strip("_").lower() or "df", current_df)
                ns["data"] = _data
                exec(code, ns)
                chart = ns.get("chart") or chart
                table, scalar = _llm_format(ns.get("result"))
                if scalar:
                    answer = (answer + "\n" + scalar).strip()
            except Exception as e:
                answer += "\n(erreur d'exécution : " + str(e) + ")"
    # normaliser chart(s) en liste (un ou plusieurs graphiques)
    charts = []
    if isinstance(chart, list):
        charts = [c for c in chart if isinstance(c, dict) and c.get("labels") and c.get("values")]
    elif isinstance(chart, dict) and chart.get("labels") and chart.get("values"):
        charts = [chart]
    return {"answer": answer, "type": "table" if table else "text", "table": table, "charts": charts}

def _json_safe(obj):
    import math
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if hasattr(obj, "dtype") and hasattr(obj, "item"):  # scalaire numpy
        try:
            return _json_safe(obj.item())
        except Exception:
            return str(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj

class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, (bytes, str)) else json.dumps(_json_safe(obj), ensure_ascii=False, default=str)
        if isinstance(obj, (dict, list)):
            body = json.dumps(_json_safe(obj), ensure_ascii=False, default=str)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(BASE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html")
        elif self.path in ("/classic", "/index_classic.html"):
            with open(os.path.join(BASE, "index_classic.html"), "rb") as f:
                self._send(200, f.read(), "text/html")
        elif self.path in ("/logo.png", "/favicon.png"):
            with open(os.path.join(BASE, "logo.png"), "rb") as f:
                self._send(200, f.read(), "image/png")
        elif self.path == "/chart.umd.min.js":
            with open(os.path.join(BASE, "chart.umd.min.js"), "rb") as f:
                self._send(200, f.read(), "application/javascript")
        elif self.path.startswith("/download/"):
            u = urlparse(self.path)
            fn = os.path.basename(urllib.request.url2pathname(u.path))
            inline = parse_qs(u.query).get("inline") == ["1"]
            fp = os.path.join(EXPORTS, fn)
            if os.path.exists(fp):
                ctype = "application/octet-stream"
                if fn.endswith(".pdf"): ctype = "application/pdf"
                elif fn.endswith(".html"): ctype = "text/html"
                elif fn.endswith(".xlsx"): ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                with open(fp, "rb") as f:
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Disposition", f'{"inline" if inline else "attachment"}; filename="{fn}"')
                    self.send_header("Content-Length", str(os.path.getsize(fp)))
                    self.end_headers()
                    self.wfile.write(f.read())
            else:
                self._send(404, {"error": "fichier introuvable"})
        elif self.path == "/api/status":
            code, r = orchestrator("/api/v1/agents/me")
            self._send(code if code else 502, r)
        elif self.path == "/api/tasks":
            code, r = orchestrator("/api/v1/agents/tasks")
            self._send(code if code else 502, r)
        elif self.path == "/api/exports":
            files = []
            for fn in os.listdir(EXPORTS):
                fp = os.path.join(EXPORTS, fn)
                if os.path.isfile(fp):
                    st = os.stat(fp)
                    files.append({"file": fn, "url": f"/download/{fn}", "size_kb": round(st.st_size / 1024, 1),
                                  "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))})
            files.sort(key=lambda x: x["modified"], reverse=True)
            self._send(200, files)
        elif self.path == "/api/settings":
            self._send(200, studio.public_settings())
        elif self.path == "/api/schedules":
            self._send(200, studio.list_schedules())
        elif self.path.startswith("/api/ingest/versions"):
            ds_id = (parse_qs(urlparse(self.path).query).get("id") or [""])[0]
            try:
                reg = IngestionRegistry()
                reg.ensure()
                self._send(200, reg.list_versions(ds_id))
            except Exception as exc:
                self._send(500, {"error": str(exc)})
        elif self.path == "/api/fetch-source":
            self._send(200, fetch_source())
        elif self.path == "/api/ingest/list":
            if not INGESTION_OK:
                self._send(500, {"error": f"Agent d'ingestion indisponible : {INGESTION_ERROR}"})
            else:
                try:
                    reg = IngestionRegistry()
                    info = reg.ensure()
                    self._send(200, {"backend": reg.backend, "connection": info,
                                     "summary": reg.summary(), "datasets": reg.latest_versions()})
                except Exception as exc:
                    self._send(500, {"error": str(exc)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/analyze":
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b""
            try:
                payload = json.loads(raw.decode("utf-8"))
                file_path = payload.get("file_path", "")
            except Exception:
                file_path = ""
            result = analyze(file_path)
            if "error" not in result:
                global LAST_RESULT, CURRENT_PATH
                LAST_RESULT = result
                CURRENT_PATH = file_path
            self._send(200, result)
        elif self.path == "/api/clean":
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b""
            try:
                payload = json.loads(raw.decode("utf-8"))
                file_path = payload.get("file_path", "")
                skip = payload.get("skip") or None
            except Exception:
                file_path, skip = "", None
            self._send(200, clean(file_path, skip))
        elif self.path == "/api/ingest":
            if not INGESTION_OK:
                self._send(500, {"error": f"Agent d'ingestion indisponible : {INGESTION_ERROR}"})
            else:
                ln = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(ln) if ln else b""
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception:
                    payload = {}
                source = str(payload.get("file_path") or payload.get("source") or "").strip()
                if not source:
                    self._send(400, {"error": "Indiquez un chemin de fichier, une URL ou un DSN PostgreSQL."})
                else:
                    kwargs = {}
                    sheet = payload.get("sheet")
                    if sheet not in (None, ""):
                        try:
                            sheet = int(sheet)
                        except (TypeError, ValueError):
                            pass
                        kwargs["sheet"] = sheet
                    if payload.get("kind"):
                        kwargs["kind"] = payload["kind"]
                    if payload.get("table"):
                        kwargs["table"] = payload["table"]
                    if payload.get("schema"):
                        kwargs["schema_name"] = payload["schema"]
                    if payload.get("name"):
                        kwargs["dataset_name"] = payload["name"]
                    if payload.get("limit"):
                        kwargs["limit"] = int(payload["limit"])
                    if payload.get("force"):
                        kwargs["force"] = True
                    try:
                        result = ingest_agent(source, **kwargs)
                    except Exception as exc:
                        result = {"error": f"{type(exc).__name__} : {exc}"}
                    if "error" not in result:
                        global LAST_INGEST
                        LAST_INGEST = result
                    self._send(200, result)
        elif self.path in ("/api/export/pdf", "/api/export/excel", "/api/export/html"):
            if LAST_RESULT is None:
                self._send(400, {"error": "Lancez d'abord une analyse."})
            else:
                kind = self.path.rsplit("/", 1)[-1]
                fn = {"pdf": export_pdf, "excel": export_excel, "html": export_html}[kind](LAST_RESULT)
                self._send(200, {"url": f"/download/{fn}", "file": fn})
        elif self.path == "/api/upload-source":
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b""
            try:
                payload = json.loads(raw.decode("utf-8"))
                name = os.path.basename(payload.get("name") or "source.dat")
                data = base64.b64decode(payload.get("data") or "")
                up = os.path.join(BASE, "uploads")
                os.makedirs(up, exist_ok=True)
                dest = os.path.join(up, name)
                with open(dest, "wb") as f:
                    f.write(data)
                self._send(200, {"path": dest, "name": name, "size": len(data)})
            except Exception as e:
                self._send(400, {"error": str(e)})
        elif self.path == "/api/chat":
            ln = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(ln) if ln else b""
            try:
                payload = json.loads(raw.decode("utf-8"))
                question = payload.get("question", "")
            except Exception:
                question = ""
            resp = llm_chat(question)
            if resp is None:
                resp = chat_query(question)  # repli règle-based si l'IA est indisponible
            self._send(200, resp)
        elif self.path.startswith(("/api/settings", "/api/schedules", "/api/insights", "/api/exports/delete")):
            ln = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads((self.rfile.read(ln) if ln else b"{}").decode("utf-8") or "{}")
            except Exception:
                payload = {}
            exporters = {"pdf": export_pdf, "excel": export_excel, "html": export_html}
            try:
                if self.path == "/api/settings":
                    self._send(200, studio.save_settings(payload))
                elif self.path == "/api/schedules":
                    self._send(200, studio.create_schedule(payload))
                elif self.path == "/api/schedules/update":
                    self._send(200, studio.update_schedule(payload.get("id"), on=payload.get("on")))
                elif self.path == "/api/schedules/delete":
                    studio.delete_schedule(payload.get("id"))
                    self._send(200, {"ok": True})
                elif self.path == "/api/schedules/run":
                    self._send(200, studio.run_schedule(payload.get("id"), analyze, exporters, EXPORTS))
                elif self.path == "/api/insights":
                    self._send(200, {"insights": studio.insights(LAST_RESULT, _llm_call)})
                elif self.path == "/api/exports/delete":
                    fp = os.path.join(EXPORTS, os.path.basename(str(payload.get("file") or "")))
                    if not os.path.isfile(fp):
                        raise FileNotFoundError("Fichier introuvable.")
                    os.remove(fp)
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"error": "not found"})
            except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
                self._send(400, {"error": str(exc).strip("'")})
            except Exception as exc:
                self._send(500, {"error": f"{type(exc).__name__} : {exc}"})
        elif self.path == "/api/heartbeat":
            code, r = orchestrator("/api/v1/agents/heartbeat", method="POST", body={})
            self._send(code if code else 502, r)
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass

def heartbeat_loop():
    while True:
        try:
            orchestrator("/api/v1/agents/heartbeat", method="POST", body={})
        except Exception:
            pass
        time.sleep(45)

if __name__ == "__main__":
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    studio.start_scheduler(analyze, {"pdf": export_pdf, "excel": export_excel, "html": export_html}, EXPORTS)
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"KLDataViz Console démarrée : http://localhost:{PORT}")
        httpd.serve_forever()
