# -*- coding: utf-8 -*-
"""Adaptateur AGT-11 DASHBOARD AGENT → tableau de bord dynamique et intuitif.

Pipeline :
1. Profile les données (types, répartition, valeurs d'exemple, relations).
2. Le LLM comprend l'objectif métier et choisit les KPI + graphiques les plus
   pertinents (repli déterministe par règles si le LLM est indisponible).
3. Les valeurs des KPI sont CALCULÉES sur les données (jamais inventées).
4. Génère un tableau de bord HTML autonome (cartes KPI + graphiques Chart.js),
   inspiré d'un design clair : fond clair, cartes blanches, accents bleu/teal + violet.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any

from .. import artifacts, config
from ..contracts import new_job_id
from .quality import _load_dataset

SYSTEM = """Tu es Hermes, architecte de tableaux de bord (santé publique, BI).
On te donne le PROFIL d'un dataset (colonnes, types, valeurs d'exemple, répartition)
et un OBJECTIF MÉTIER. Propose les KPI les plus pertinents et les graphiques adaptés.
Réponds en JSON STRICT, sans aucun texte hors du JSON, exactement sous cette forme :
{"kpis":[{"nom":"...","agregation":"somme|moyenne|compte|max|min|taux","colonne":"...","dimension":"...","justification":"..."}],"graphiques":[{"colonne":"...","type":"bar|line|pie|hist|scatter|box","justification":"..."}]}
Règles absolues :
- Ne propose QUE des colonnes qui existent réellement dans le profil (respecte leur nom exact).
- 4 à 7 KPI pertinents pour l'objectif métier, pas plus.
- « dimension » (optionnel) : une colonne catégorielle (ex. region, district, domaine, sexe) pour
  regrouper le KPI. Mets-en une quand le KPI gagne à être comparé par région/période/catégorie.
- Justifie chaque choix en une phrase courte, en français.
- agrégation « somme » pour des comptages/volumes, « moyenne » pour des taux/intensités,
  « compte » pour des effectifs, « taux » pour des proportions (0 à 1)."""


def _llm_dashboard(profile_json: str) -> dict | None:
    """Choisit KPI et graphiques via le LLM, ou None si indisponible."""
    cfg_path = os.path.join(config.CONSOLE_DIR, "llm_config.json")
    if not os.path.isfile(cfg_path):
        return None
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        key = cfg.get("api_key", "")
        if not key or "VOTRE_CLE" in str(key):
            return None
        body = json.dumps({
            "model": cfg.get("model", "deepseek-v4-pro"),
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": profile_json}],
            "max_tokens": 8000, "temperature": 0,
        }).encode("utf-8")
        req = urllib.request.Request(cfg["base_url"], data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key}"})
        r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
        content = (r.get("choices") or [{}])[0].get("message", {}).get("content", "")
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start:end + 1])
        return None
    except Exception:
        return None


def _profile(df) -> dict:
    """Profil compact du dataset (colonnes, types, exemples, répartition)."""
    import pandas as pd
    cols = []
    for c in df.columns:
        s = df[c]
        info: dict[str, Any] = {"colonne": str(c), "type": str(s.dtype), "n_manquants": int(s.isna().sum())}
        try:
            info["n_distinct"] = int(s.nunique(dropna=True))
        except Exception:
            info["n_distinct"] = None
        try:
            info["exemples"] = [str(x) for x in s.dropna().unique()[:3]]
        except Exception:
            info["exemples"] = []
        if pd.api.types.is_numeric_dtype(s):
            try:
                info["min"] = float(s.min()); info["max"] = float(s.max()); info["moyenne"] = round(float(s.mean()), 2)
            except Exception:
                pass
        cols.append(info)
    return {"lignes": int(len(df)), "colonnes": cols}


def _rule_dashboard(profile: dict) -> dict:
    """Repli déterministe : KPI sur les colonnes numériques, graphiques sur les catégorielles."""
    import pandas as pd
    kpis, graphiques = [], []
    nums = [c for c in profile["colonnes"] if c["type"] in ("int64", "float64", "int32", "float32")
            or re.match(r"int|float", c["type"])]
    cats = [c for c in profile["colonnes"] if c not in nums]
    kpis.append({"nom": "Total de lignes", "agregation": "compte", "colonne": None,
                 "justification": "effectif global du jeu de données"})
    for c in nums[:3]:
        agg = "somme" if (c.get("min") is not None and c["min"] >= 0) else "moyenne"
        kpis.append({"nom": f"{agg.title()} de {c['colonne']}", "agregation": agg,
                     "colonne": c["colonne"], "justification": "indicateur de volume"})
    for c in cats[:3]:
        if (c.get("n_distinct") or 0) > 1 and (c.get("n_distinct") or 0) <= 20:
            graphiques.append({"colonne": c["colonne"], "type": "bar",
                               "justification": "répartition par catégorie"})
    for c in nums[:2]:
        graphiques.append({"colonne": c["colonne"], "type": "hist",
                           "justification": "distribution de la variable numérique"})
    return {"kpis": kpis, "graphiques": graphiques}


def _agg(df, colonne: str | None, agregation: str) -> float | int | None:
    import pandas as pd
    a = (agregation or "").lower()
    if colonne is None or colonne not in df.columns:
        return int(len(df)) if a in ("compte", "count") else None
    if a in ("compte", "count", "effectif"):
        return int(df[colonne].notna().sum())
    s = pd.to_numeric(df[colonne], errors="coerce")
    if a in ("somme", "sum"):
        return float(s.sum())
    if a in ("moyenne", "mean", "avg", "moyenne de"):
        return round(float(s.mean()), 2) if s.notna().any() else None
    if a in ("max", "maximum"):
        return float(s.max()) if s.notna().any() else None
    if a in ("min", "minimum"):
        return float(s.min()) if s.notna().any() else None
    if a in ("taux", "rate", "pct", "proportion"):
        return round(float(s.mean()), 4) if s.notna().any() else None
    return int(s.notna().sum())


def _agg_grouped(df, colonne: str | None, agregation: str, dimension: str, top: int = 6) -> dict | None:
    """Agrégation d'un KPI regroupé par une dimension (labels + valeurs triés décroissants)."""
    import pandas as pd
    a = (agregation or "").lower()
    if dimension is None or dimension not in df.columns:
        return None
    if colonne is None or colonne not in df.columns:
        vc = df[dimension].astype(str).value_counts().head(top)
        return {"labels": [str(x) for x in vc.index], "values": [int(x) for x in vc.values]}
    if a in ("compte", "count", "effectif"):
        vc = df.groupby(dimension)[colonne].count().sort_values(ascending=False).head(top)
        return {"labels": [str(x) for x in vc.index], "values": [int(x) for x in vc.values]}
    s = pd.to_numeric(df[colonne], errors="coerce")
    if a in ("moyenne", "mean", "avg"):
        g = df.assign(_v=s).groupby(dimension)["_v"].mean()
    elif a in ("max", "maximum"):
        g = df.assign(_v=s).groupby(dimension)["_v"].max()
    elif a in ("min", "minimum"):
        g = df.assign(_v=s).groupby(dimension)["_v"].min()
    elif a in ("taux", "rate", "pct", "proportion"):
        g = df.assign(_v=s).groupby(dimension)["_v"].mean()
    else:
        g = df.assign(_v=s).groupby(dimension)["_v"].sum()
    g = g.sort_values(ascending=False).head(top)
    return {"labels": [str(x) for x in g.index], "values": [round(float(x), 2) for x in g.values]}


def _chart_data(df, colonne: str, ctype: str) -> dict:
    import pandas as pd
    import numpy as np
    s = df[colonne]
    if ctype == "pie":
        vc = s.dropna().astype(str).value_counts().head(8)
        return {"labels": [str(x) for x in vc.index], "values": [int(x) for x in vc.values]}
    if ctype == "bar":
        if pd.api.types.is_numeric_dtype(s) and s.dropna().nunique() > 15:
            vc = pd.cut(s.dropna(), bins=8).astype(str).value_counts().sort_index()
        else:
            vc = s.dropna().astype(str).value_counts().head(12)
        return {"labels": [str(x) for x in vc.index], "values": [int(x) for x in vc.values]}
    if ctype in ("hist", "line"):
        num = pd.to_numeric(s, errors="coerce").dropna()
        if ctype == "line":
            num = num.reset_index(drop=True)
            step = max(1, len(num) // 40)
            num = num.iloc[::step]
        hist = np.histogram(num, bins=10) if len(num) else (np.array([]), np.array([]))
        return {"labels": [round(float(x), 2) for x in hist[1][:-1]],
                "values": [int(x) for x in hist[0]]}
    if ctype == "scatter":
        num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        other = num_cols[0] if num_cols[0] != colonne else (num_cols[1] if len(num_cols) > 1 else None)
        if other:
            sub = df[[colonne, other]].dropna().head(500)
            return {"labels": [float(x) for x in sub[colonne]],
                    "values": [float(x) for x in sub[other]], "axe_y": str(other)}
        return {"labels": [], "values": [], "axe_y": None}
    if ctype == "box":
        num = pd.to_numeric(s, errors="coerce").dropna()
        q = num.quantile([0, 0.25, 0.5, 0.75, 1.0]).tolist() if len(num) else []
        return {"labels": [str(colonne)], "values": [round(float(x), 2) for x in q]}
    return {"labels": [], "values": []}


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.0f}".replace(",", " ")
        return f"{v:,.2f}".replace(",", " ")
    return f"{v:,}".replace(",", " ")


def _json_safe(o: Any) -> Any:
    import math
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_safe(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


ACCENTS = ["#0ea5e9", "#1286b4", "#22c55e", "#7c6fd0", "#f59e0b", "#ef4444", "#14b8a6", "#8b5cf6"]


def _render_html(objective: str, kpis: list[dict], charts: list[dict]) -> str:
    """Génère un tableau de bord HTML autonome (cartes KPI + graphiques Chart.js)."""
    kpi_cards = ""
    for i, k in enumerate(kpis):
        color = ACCENTS[i % len(ACCENTS)]
        sub = k.get("justification", "")
        if k.get("top"):
            top_label, top_val = k["top"]
            sub = f"{k.get('dimension')} · top : {top_label} ({_fmt(top_val)}) · {sub}"
        kpi_cards += (
            f'<div class="kpi" style="border-top:3px solid {color}">'
            f'<div class="kpi-v">{_fmt(k.get("valeur"))}</div>'
            f'<div class="kpi-n">{k.get("nom", "KPI")}</div>'
            f'<div class="kpi-s">{sub}</div></div>'
        )

    charts_html = ""
    for i, ch in enumerate(charts):
        cid = f"ch{i}"
        ctype = ch.get("type", "bar")
        title = ch.get("titre") or ch.get("colonne", "Graphique")
        charts_html += (
            f'<div class="card"><div class="card-h">{title}'
            f'<span class="card-s">{ch.get("justification", "")}</span></div>'
            f'<div class="chart-box"><canvas id="{cid}"></canvas></div></div>'
        )

    js_charts = ""
    for i, ch in enumerate(charts):
        cid = f"ch{i}"
        data = ch.get("data", {})
        ctype = ch.get("type", "bar")
        labels = json.dumps(data.get("labels", []), ensure_ascii=False)
        values = json.dumps(data.get("values", []), ensure_ascii=False)
        ctype_js = {"hist": "bar", "box": "bar", "pie": "pie"}.get(ctype, ctype)
        datasets = json.dumps([{"label": ch.get("colonne", ""), "data": data.get("values", []),
                                "backgroundColor": ACCENTS[i % len(ACCENTS)]}], ensure_ascii=False)
        js_charts += (
            f'new Chart(document.getElementById("{cid}"),{{type:"{ctype_js}",'
            f'data:{{labels:{labels},datasets:{datasets}}},'
            f'options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}}}}}});'
        )

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tableau de bord KLDataViz</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#eef1f4;color:#1f2937;padding:24px}}
.head{{margin-bottom:18px}}
.head h1{{font-size:22px;font-weight:700}}
.head p{{color:#667085;font-size:13px;margin-top:4px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:18px}}
.kpi{{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.05)}}
.kpi-v{{font-size:28px;font-weight:700;letter-spacing:-.5px}}
.kpi-n{{font-size:13px;font-weight:600;margin-top:4px}}
.kpi-s{{font-size:11.5px;color:#667085;margin-top:4px;line-height:1.4}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}}
.card{{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.05)}}
.card-h{{font-size:14px;font-weight:600;margin-bottom:10px}}
.card-s{{display:block;font-size:11.5px;color:#667085;font-weight:400;margin-top:2px}}
.chart-box{{height:280px;position:relative}}
</style></head><body>
<div class="head"><h1>Tableau de bord</h1><p>{objective or "Analyse de données"}</p></div>
<div class="kpis">{kpi_cards}</div>
<div class="grid">{charts_html}</div>
<script>{js_charts}</script>
</body></html>"""


def run(env, db, jobs) -> dict:
    dv = env.dataset_version_id or (env.parameters or {}).get("dataset_version_id") \
        or (env.input_reference or {}).get("dataset_version_id")
    if not dv:
        return {"status": "FAILED", "result_reference": {}, "warnings": [],
                "errors": ["dataset_version_id manquant : impossible de générer le tableau de bord."]}

    df, err = _load_dataset(db, dv)
    if df is None or err:
        return {"status": "FAILED", "result_reference": {}, "warnings": [], "errors": [err or "dataset illisible"]}

    objective = (env.parameters or {}).get("objective") or env.objective or ""

    # 1. profil des données
    profile = _profile(df)

    # 2. choix des KPI + graphiques (LLM, sinon règles)
    profile_json = json.dumps(profile, ensure_ascii=False, default=str)[:20000]
    llm_spec = _llm_dashboard(profile_json)
    ia = llm_spec is not None
    spec = llm_spec or _rule_dashboard(profile)

    # 3. calcul des KPI (déterministe) + données des graphiques
    cols = set(df.columns)
    kpis = []
    for k in spec.get("kpis", [])[:8]:
        col = k.get("colonne")
        if col is not None and col not in cols:
            continue
        v = _agg(df, col, k.get("agregation", "compte"))
        kpi = {"nom": k.get("nom") or (col or "Total"), "valeur": v,
               "agregation": k.get("agregation"), "colonne": col,
               "dimension": k.get("dimension"), "justification": k.get("justification", "")}
        dim = k.get("dimension")
        if dim and dim in cols and dim != col:
            bd = _agg_grouped(df, col, k.get("agregation", "compte"), dim)
            if bd and bd.get("labels"):
                kpi["breakdown"] = bd
                kpi["top"] = (bd["labels"][0], bd["values"][0])
        kpis.append(kpi)

    charts = []
    for g in spec.get("graphiques", [])[:8]:
        col = g.get("colonne")
        if col is None or col not in cols:
            continue
        data = _chart_data(df, col, g.get("type", "bar"))
        if not data.get("labels"):
            continue
        charts.append({"colonne": col, "type": g.get("type", "bar"),
                       "titre": g.get("nom") or f"{col}", "data": data,
                       "justification": g.get("justification", "")})

    # graphiques de répartition pour les KPI regroupés par dimension
    for kpi in kpis:
        if kpi.get("breakdown"):
            charts.append({"colonne": kpi["colonne"], "type": "bar",
                           "titre": f"{kpi['nom']} par {kpi['dimension']}",
                           "data": kpi["breakdown"],
                           "justification": kpi.get("justification", "")})

    # 4. rendu HTML (design inspiré du modèle fourni)
    html = _render_html(objective, kpis, charts)
    config.ensure_dirs()
    html_path = config.ARTIFACTS_DIR / f"{env.job_id}__dashboard.html"
    html_path.write_text(html, encoding="utf-8")

    artifacts.write_contract(env.job_id, "agent11_dashboard",
                             {"kpis": _json_safe(kpis), "charts": len(charts), "ia": ia})

    return {
        "status": "DONE",
        "result_reference": {"dashboard": str(html_path), "kpis": _json_safe(kpis),
                             "charts": len(charts), "ia": ia},
        "warnings": [] if ia else ["LLM indisponible — tableau de bord généré par règles."],
        "errors": [],
    }
