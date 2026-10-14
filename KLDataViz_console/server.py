# -*- coding: utf-8 -*-
"""
KLDataViz — Console d'utilisation de l'agent (serveur local).
Backend : proxy orchestrateUR CVLynk + moteur d'analyse (pandas) + exports PDF/Excel/HTML.
Sert l'interface web sur http://localhost:8642
"""
import http.server, socketserver, json, os, io, threading, time, tempfile, urllib.request, urllib.error, base64, sys, subprocess, traceback
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
sys.path.insert(0, BASE)

# --- Agent ARCHITECTE DE DONNÉES (compréhension + structure cible) ---
try:
    import architect, data_architect
    ARCHITECT_ERROR = None
except Exception as _arch_exc:
    architect = None
    ARCHITECT_ERROR = str(_arch_exc)

import hashlib, hmac, re, secrets
import tenancy as T
import sandbox, netguard
import studio
if T.CLOUD:
    import cloud
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

# Dernière analyse, fichier en cours et dernier contrat d'ingestion : par utilisateur, voir tenancy.state().

def orchestrator(path, method="GET", body=None):
    if T.CLOUD:  # l'agent CVLynk n'est utilisé que par la console locale
        return 503, {"error": "Orchestrateur désactivé dans la version en ligne."}
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


def normalize_source_url(url):
    """Transforme un lien de partage (Google Sheets, Google Drive) en lien de téléchargement direct."""
    import re
    from urllib.parse import urlparse, parse_qs
    u = urlparse(url)
    if "docs.google.com" in u.netloc and "/spreadsheets/d/" in u.path:
        m = re.search(r"/spreadsheets/d/([^/]+)", u.path)
        if m and m.group(1) != "e":  # /d/e/... = lien « publié sur le web », déjà téléchargeable
            gid = (parse_qs(u.query).get("gid") or parse_qs(u.fragment).get("gid") or [None])[0]
            return (f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
                    + (f"&gid={gid}" if gid else ""))
    if "drive.google.com" in u.netloc:
        m = re.search(r"/file/d/([^/]+)", u.path) or re.search(r"[?&]id=([^&]+)", url)
        if m:
            return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def source_label(file_path):
    """Nom lisible et sans caractère interdit pour une source (fichier ou URL)."""
    import re
    from urllib.parse import urlparse
    if file_path.startswith(("http://", "https://")):
        u = urlparse(file_path)
        if "docs.google.com" in u.netloc and "/spreadsheets/" in u.path:
            m = re.search(r"/d/([^/]+)", u.path)
            name = "google_sheet_" + (m.group(1)[:8] if m else "")
        else:
            name = os.path.splitext(os.path.basename(u.path))[0] or u.netloc
    else:
        name = os.path.splitext(os.path.basename(file_path))[0]
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip("._ ") or "source"


def _csv_options(file_path):
    """Séparateur et encodage d'un CSV ; options par défaut de pandas si la détection échoue."""
    try:
        enc, sep = data_architect._sniff_text(data_architect.Path(file_path))
        return {"sep": sep, "encoding": enc}
    except Exception:
        return {}


def _header_row(sample):
    """Indice de la vraie ligne d'en-têtes dans un tableau « rapport » (titres, totaux au-dessus).

    `sample` : les 20 premières lignes lues sans en-tête. On garde la ligne 0 sauf si une
    ligne suivante porte nettement plus de libellés texte et couvre au moins la moitié des colonnes.
    """
    def labels(row):
        n = 0
        for v in row:
            if isinstance(v, str) and v.strip():
                try:
                    float(v.replace(",", ".").replace(" ", ""))
                except ValueError:
                    n += 1
        return n

    scores = [labels(r) for r in sample.itertuples(index=False, name=None)]
    if len(scores) < 2:
        return 0
    best = max(range(len(scores)), key=lambda i: (scores[i], -i))
    if best and scores[best] >= 1.5 * scores[0] + 1 and scores[best] >= 0.5 * sample.shape[1]:
        return best
    return 0


def _tidy(df):
    """Noms de colonnes lisibles (en-têtes vides ou sur plusieurs lignes) et lignes vides retirées."""
    names, seen = [], {}
    for j, c in enumerate(df.columns):
        nm = " ".join(str(c).split())
        if not nm or nm.startswith("Unnamed:"):
            nm = f"colonne_{j + 1}"
        if nm in seen:
            seen[nm] += 1
            nm = f"{nm}_{seen[nm]}"
        else:
            seen[nm] = 1
        names.append(nm)
    df.columns = names
    return df.dropna(how="all").reset_index(drop=True)


def _read_df(file_path):
    """Lit un fichier local ou une URL en DataFrame (csv/xlsx/xls/xlsm/json/parquet).

    En ligne : seuls les fichiers de l'organisation et les liens https publics sont acceptés."""
    tmp_to_delete = None
    is_url = file_path.startswith(("http://", "https://"))
    if T.CLOUD and not is_url and not T.current().owns(file_path):
        return None, "Fichier introuvable."
    if is_url:
        url = normalize_source_url(file_path)
        try:
            data, ctype = netguard.fetch(url, allow_private=not T.CLOUD, https_only=T.CLOUD)
        except netguard.BlockedURL as e:
            return None, str(e)
        except Exception as e:
            if "google.com" in url and getattr(e, "code", None) in (401, 403, 404):
                return None, ("Google refuse l'accès à cette feuille. Vérifiez le lien, puis dans Google Sheets "
                              "cliquez sur Partager → « Tous les utilisateurs disposant du lien » (Lecteur).")
            return None, f"Téléchargement impossible : {e}"
        if "text/html" in ctype or data[:300].lstrip().lower().startswith((b"<!doctype html", b"<html")):
            if "google.com" in url:
                return None, ("Google renvoie une page de connexion au lieu des données. Dans Google Sheets, "
                              "cliquez sur Partager puis « Tous les utilisateurs disposant du lien » (Lecteur), "
                              "et réessayez avec le même lien.")
            return None, ("Ce lien renvoie une page web, pas un fichier de données. Utilisez un lien direct "
                          "vers un fichier CSV, Excel ou JSON.")
        ext = os.path.splitext(url.split("?")[0])[1].lower()
        if ext not in (".csv", ".xlsx", ".xls", ".xlsm", ".json", ".parquet"):
            ext = (".xlsx" if "spreadsheetml" in ctype or data[:2] == b"PK"
                   else ".json" if "json" in ctype or data[:1] in (b"{", b"[") else ".csv")
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        tmp.write(data); tmp.close()
        file_path = tmp_to_delete = tmp.name
    if not os.path.exists(file_path):
        return None, f"Fichier introuvable : {file_path}"
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".csv":
            # Séparateur et encodage détectés : les exports français et DHIS2 utilisent « ; » (et parfois cp1252)
            opts = _csv_options(file_path)
            h = _header_row(pd.read_csv(file_path, header=None, nrows=20, dtype=str, on_bad_lines="skip", **opts))
            df = _tidy(pd.read_csv(file_path, skiprows=h, **opts)) if h else pd.read_csv(file_path, **opts)
        elif ext in (".xlsx", ".xls", ".xlsm"):
            h = _header_row(pd.read_excel(file_path, header=None, nrows=20, dtype=str))
            df = _tidy(pd.read_excel(file_path, header=h)) if h else pd.read_excel(file_path)
        elif ext == ".json":
            df = pd.read_json(file_path)
        elif ext == ".parquet":
            df = pd.read_parquet(file_path)
        else:
            return None, f"Format non supporté : {ext}"
    except Exception as e:
        return None, f"Lecture impossible : {e}"
    finally:
        if tmp_to_delete:
            try:
                os.remove(tmp_to_delete)
            except OSError:
                pass
    return df, None


_PERIOD_HINT = None


def _pretty(name):
    s = str(name).replace("_", " ").strip()
    return s[:1].upper() + s[1:]


def _smart_charts(df, max_charts=6):
    """Graphiques « décisionnels » choisis automatiquement : évolution dans le temps, classement par
    groupe (avec ligne de moyenne), répartition, distribution et relation. Chaque graphique porte
    son agrégation (somme ou moyenne) pour que l'interface puisse l'expliquer."""
    import re
    n = len(df)
    if n == 0:
        return []
    id_hint = re.compile(r"(^|[_\s])(id|code|key|ref|numero|num|uid)($|[_\s])", re.I)
    rate_hint = re.compile(r"(pct|percent|pourcent|taux|rate|ratio|couverture|%|moyenne|avg|mean|score)", re.I)
    period_hint = re.compile(r"(date|mois|month|semaine|week|annee|année|year|periode|période|period|trimestre|quarter|jour|day)", re.I)

    numeric_all = list(df.select_dtypes(include=[np.number]).columns)

    def is_year(s):
        v = s.dropna()
        return len(v) > 0 and v.between(1900, 2100).all() and (v % 1 == 0).all() and v.nunique() <= 60

    num = [c for c in numeric_all if not id_hint.search(str(c)) and df[c].nunique() > 1 and not is_year(df[c])]
    num.sort(key=lambda c: (-round(df[c].notna().mean(), 1), -float(df[c].abs().sum())))

    def agg_of(c):
        v = df[c].dropna()
        frac = len(v) > 0 and v.between(0, 1).all() and (v % 1 != 0).any()
        return "mean" if rate_hint.search(str(c)) or frac else "sum"

    def unit_of(c):
        v = df[c].dropna()
        return "%" if re.search(r"(pct|percent|pourcent|%|taux|couverture)", str(c), re.I) and len(v) and v.between(0, 100).all() else ""

    # ---- colonne de période (date réelle ou libellé de période)
    period, period_dt = None, None
    for c in df.columns:
        if c in numeric_all:
            continue
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            period, period_dt = c, s
            break
        sample = s.dropna().astype(str).head(40)
        if len(sample) >= 3:
            try:
                parsed = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=True)
                if parsed.notna().mean() >= 0.9:
                    period, period_dt = c, pd.to_datetime(s.astype(str), errors="coerce", format="mixed", dayfirst=True)
                    break
            except Exception:
                pass
    if period is None:
        for c in df.columns:
            if c not in numeric_all and period_hint.search(str(c)) and 3 <= df[c].nunique() <= 60:
                period = c
                break

    group_hint = re.compile(r"(r[ée]gion|district|site|type|cat[ée]g|groupe|group|sexe|sex|genre|province|d[ée]partement|"
                            r"zone|structure|[ée]tablissement|facility|ville|commune|pays|country|service|produit|product|"
                            r"segment|canal|ip_?s?name|agence|magasin|client)", re.I)
    bool_vocab = {"true", "false", "vrai", "faux", "oui", "non", "yes", "no", "0", "1", "y", "n"}

    def is_bool(c):
        return set(df[c].dropna().astype(str).str.strip().str.lower().unique()) <= bool_vocab

    cats = [c for c in df.columns if c not in numeric_all and c != period and not id_hint.search(str(c))
            and 2 <= df[c].nunique() <= 40 and df[c].notna().mean() >= 0.6 and not is_bool(c)]
    # Noms « métier » d'abord, puis les regroupements de 3 à 20 valeurs, puis les colonnes « une ligne = une entité ».
    cats.sort(key=lambda c: (0 if group_hint.search(str(c)) else 1, 0 if 3 <= df[c].nunique() <= 20 else 1,
                             df[c].nunique() >= 0.9 * n, df[c].nunique()))

    charts = []

    def series(grouped):
        return [None if pd.isna(v) else round(float(v), 4) for v in grouped]

    # 1. Évolution
    if period is not None and num:
        same = [c for c in num[1:] if agg_of(c) == agg_of(num[0])]
        metrics = [num[0]] + same[:1]
        if period_dt is not None:
            span = (period_dt.max() - period_dt.min()).days if period_dt.notna().any() else 0
            freq = "D" if span <= 62 else "W" if span <= 200 else "M"
            key = period_dt.dt.to_period(freq)
        else:
            key = df[period].astype(str)
        g = df.assign(_k=key).dropna(subset=["_k"]).groupby("_k")
        vals, names, aggs = [], [], []
        for m in metrics:
            a = agg_of(m)
            vals.append(series(getattr(g[m], a)()))
            names.append(_pretty(m))
            aggs.append(a)
        labels = [str(k) for k in g.size().index]
        if len(labels) >= 3:
            mx = [max([abs(x) for x in v if x is not None] or [0]) for v in vals]
            two_axes = len(vals) == 2 and min(mx) > 0 and max(mx) / min(mx) > 4
            charts.append({"kind": "trend", "type": "line", "title": "Évolution de " + " et ".join(names).lower(),
                           "subtitle": ("Somme" if aggs[0] == "sum" else "Moyenne") + " par " + str(period).replace("_", " "),
                           "labels": labels, "values": vals, "seriesNames": names, "axis2": two_axes,
                           "unit": unit_of(metrics[0])})

    # 2. Classements par groupe
    pairs = []
    if cats and num:
        pairs.append((cats[0], num[0]))
        if len(cats) > 1:
            pairs.append((cats[1], num[0]))
        if len(num) > 1:
            pairs.append((cats[0], num[1]))
    for cat, m in pairs[:2]:
        a = agg_of(m)
        g = getattr(df.groupby(cat)[m], a)().dropna().sort_values(ascending=False)
        if len(g) < 2:
            continue
        top = g.head(12)
        charts.append({"kind": "ranking", "type": "bar", "title": f"{_pretty(m)} par {str(cat).replace('_', ' ')}",
                       "subtitle": ("Somme" if a == "sum" else "Moyenne") + (f" · {len(top)} premiers sur {len(g)}" if len(g) > 12 else "")
                       + " · ligne : moyenne", "labels": [str(x) for x in top.index], "values": [series(top.values)],
                       "seriesNames": [_pretty(m)], "ref": {"value": round(float(g.mean()), 4), "label": "Moyenne"},
                       "horizontal": True, "unit": unit_of(m)})

    # 3. Répartition
    small = [c for c in cats if 2 <= df[c].nunique() <= 6]
    if small:
        c = small[0]
        if num and agg_of(num[0]) == "sum":
            g = df.groupby(c)[num[0]].sum().sort_values(ascending=False)
            title = f"Répartition de {str(num[0]).replace('_', ' ')} par {str(c).replace('_', ' ')}"
        else:
            g = df[c].value_counts()
            title = f"Répartition des lignes par {str(c).replace('_', ' ')}"
        if len(g) >= 2 and float(g.sum()) > 0:
            charts.append({"kind": "share", "type": "doughnut", "title": title, "subtitle": "Part du total",
                           "labels": [str(x) for x in g.index], "values": [series(g.values)]})

    # 4. Distribution
    if num:
        m = num[0]
        v = df[m].dropna()
        if v.nunique() >= 8:
            lo, hi = v.quantile(0.01), v.quantile(0.99)
            v2 = v[(v >= lo) & (v <= hi)]
            raw_step = (float(v2.max()) - float(v2.min())) / 10 or 1.0
            mag = 10 ** np.floor(np.log10(raw_step))
            step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw_step)  # pas « rond »
            start = np.floor(float(v2.min()) / step) * step
            edges = np.arange(start, float(v2.max()) + step, step)
            if len(edges) < 3:
                edges = np.array([start, start + step, start + 2 * step])
            counts, edges = np.histogram(v2, bins=edges)

            def _f(x):
                x = round(float(x), 6)
                return (f"{x:,.0f}" if x == int(x) else f"{x:,.2f}".rstrip("0")).replace(",", " ").replace(".", ",")
            charts.append({"kind": "distribution", "type": "histogram", "title": f"Distribution de {str(m).replace('_', ' ')}",
                           "subtitle": "Nombre de lignes par tranche" + (" · valeurs extrêmes (1 %) écartées" if len(v2) < len(v) else ""),
                           "labels": [f"{_f(edges[i])} – {_f(edges[i + 1])}" for i in range(len(counts))],
                           "values": [[int(x) for x in counts]], "seriesNames": ["Lignes"]})

    # 5. Relation (corrélation nette mais pas triviale)
    if len(num) >= 2:
        sub = df[num[:12]]
        cm = sub.corr()
        best = None
        for i in range(len(cm.columns)):
            for j in range(i):
                r = cm.iloc[i, j]
                if pd.notna(r) and 0.5 <= abs(r) < 0.97 and (best is None or abs(r) > abs(best[2])):
                    best = (cm.columns[j], cm.columns[i], float(r))
        if best:
            a, b, r = best
            pts = df[[a, b]].dropna()
            if len(pts) > 400:
                pts = pts.sample(400, random_state=0)
            charts.append({"kind": "relation", "type": "scatter", "title": f"{_pretty(a)} et {str(b).replace('_', ' ')}",
                           "subtitle": f"Chaque point est une ligne · corrélation r = {r:.2f}".replace(".", ",", 1),
                           "labels": [float(x) for x in pts[a]], "values": [[float(y) for y in pts[b]]],
                           "seriesNames": [_pretty(b)], "xTitle": _pretty(a), "yTitle": _pretty(b), "trend": True})

    return charts[:max_charts]


def analyze(file_path):
    df, err = _read_df(file_path)
    if err:
        return {"error": err}

    size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
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
        "file": os.path.basename(file_path) if os.path.exists(file_path) else source_label(file_path),
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
        "smart_charts": _smart_charts(df),
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
    cleaned_dir = T.cleaned_dir()
    os.makedirs(cleaned_dir, exist_ok=True)
    stem = source_label(file_path)
    cleaned_path = os.path.join(cleaned_dir, f"{stem}_nettoye.csv")
    df_clean.to_csv(cleaned_path, index=False, encoding="utf-8-sig")

    preview = df_clean.head(30)
    apercu = [[_cell_safe(x) for x in row]
              for row in preview.itertuples(index=False, name=None)]

    return {
        "file": source_label(file_path),
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
    wb.save(os.path.join(T.exports_dir(), fn))
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
    with open(os.path.join(T.exports_dir(), fn), "w", encoding="utf-8") as f:
        f.write(html)
    return fn

def export_pdf(result):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    fn = f"analyse_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
    path = os.path.join(T.exports_dir(), fn)
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
    datasets = _registry_datasets()
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
    reg = T.registry()  # joignable : il vient de fournir `datasets`
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
    if T.CLOUD:
        key = os.environ.get("DEEPSEEK_API_KEY")
        return {"api_key": key, "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"),
                "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions")} if key else None
    p = os.path.join(BASE, "llm_config.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def _llm_call(messages, max_tokens=8000, temperature=0, timeout=120, model=None, effort=None, attempts=2):
    cfg = _llm_config()
    if not cfg or not cfg.get("api_key"):
        return None
    payload = {"model": model or cfg.get("model", "deepseek-v4-pro"), "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    if effort:
        payload["reasoning_effort"] = effort
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(attempts):
        req = urllib.request.Request(cfg.get("base_url", "https://api.deepseek.com/chat/completions"),
                                     data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + cfg["api_key"]})
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(r.read().decode())["choices"][0]["message"]["content"]
        except Exception:
            if attempt == 0:
                time.sleep(2)
    return None

def _registry_datasets():
    """Jeux de données du registre ; liste vide (et trace dans les journaux) s'il est injoignable,
    pour que le chat réponde quand même sur le fichier en cours."""
    try:
        reg = T.registry()
        reg.ensure()
        return reg.latest_versions()
    except Exception as e:
        sys.stderr.write(f"[registre] indisponible pour le chat : {type(e).__name__} : {e}\n")
        return []


def _llm_context(datasets=None):
    datasets = _registry_datasets() if datasets is None else datasets
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

def _work_file(path):
    """Fichier de travail (parquet/csv) d'une version du registre, ou None."""
    if not path:
        return None
    p = str(path)
    if os.path.isfile(p):
        return p
    if os.path.isdir(p):
        c = [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.endswith((".parquet", ".csv"))]
        return c[0] if c else None
    for ext in (".parquet", ".csv"):
        if os.path.exists(p + ext):
            return p + ext
    return None


def _parse_llm_json(content):
    """Objet JSON renvoyé par l'IA (éventuellement entouré de ```), ou None."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _run_ai_code(code, paths):
    try:
        return sandbox.run(code, paths)
    except sandbox.UnsafeCode as e:
        return {"error": f"calcul refusé par le contrôle de sécurité ({e})"}


def llm_chat(question):
    import re
    datasets = _registry_datasets()
    context = _llm_context(datasets)
    current_df, current_name = None, None
    cur_path = T.state()["current_path"]
    if cur_path:
        current_df, _err = _read_df(cur_path)
        if current_df is not None:
            current_name = source_label(cur_path)
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
        "Assigne le résultat final à la variable `result` (DataFrame, Series ou scalaire). "
        "Écris les noms de colonnes ENTRE GUILLEMETS DOUBLES, exactement comme listés : "
        "df[\"Nom d'utilisateur\"] (une apostrophe dans un nom casse les guillemets simples).\n"
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
    messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
    content = _llm_call(messages)
    if not content:
        return None
    data = _parse_llm_json(content)
    if data is None:
        return {"answer": content, "type": "text"}
    answer = data.get("answer", "") or ""
    code = data.get("code")
    chart = data.get("chart")
    table = None
    if code:
        # Le code de l'IA s'exécute dans un processus isolé (sandbox.py), sans secrets ni accès fichiers.
        paths = {}
        for _d in datasets:
            _p = _work_file(_d.get("work_path"))
            if _p and T.current().owns(_p):
                paths[_d.get("dataset_name") or _d.get("dataset_id")] = _p
        if current_df is not None:
            _cur = os.path.join(T.current().dir("tmp"), f"courant_{T.current().key[:12]}.parquet")
            try:
                current_df.to_parquet(_cur)
            except Exception:
                current_df.astype(str).to_parquet(_cur)
            paths[current_name] = _cur
        out = _run_ai_code(code, paths)
        if out.get("error"):
            # Seconde chance : l'IA reçoit l'erreur (code invalide, colonne inconnue…) et corrige son code.
            fixed = _parse_llm_json(_llm_call(messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"Ton code a échoué : {out['error']}\nCorrige-le (noms de colonnes "
                                            "exacts, entre guillemets doubles) et renvoie le même objet JSON complet."}]))
            if isinstance(fixed, dict) and fixed.get("code"):
                out2 = _run_ai_code(fixed["code"], paths)
                if not out2.get("error"):
                    out, answer, chart = out2, fixed.get("answer") or answer, fixed.get("chart") or chart
        if out.get("error"):
            answer += "\n(" + out["error"] + ")"
        else:
            chart = out.get("chart") or chart
            res = out.get("result") or {}
            if res.get("kind") == "table":
                table = {"columns": res["columns"], "rows": res["rows"]}
            elif res.get("kind") == "scalar":
                answer = (answer + "\n" + res["value"]).strip()
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

def _architect_df(file_path):
    """Lit la source pour l'architecte : détection d'encodage et de séparateur (« ; » des exports FR/DHIS2)."""
    if file_path.startswith(("http://", "https://")):
        df, err = _read_df(file_path)
        return df, {"fichier": source_label(file_path)}, err
    if T.CLOUD and not T.current().owns(file_path):
        return None, None, "Fichier introuvable."
    if not os.path.isfile(file_path):
        return None, None, f"Fichier introuvable : {file_path}"
    try:
        df, info = data_architect.load_table(file_path)
    except Exception as e:
        return None, None, f"Lecture impossible : {e}"
    return df, info, None


MAX_ARCHITECT_FILES = 6


def _architect_paths(p):
    """Chemins envoyés par l'interface : `file_paths` (plusieurs fichiers) ou `file_path` (un seul)."""
    paths = p.get("file_paths")
    return [str(x) for x in paths] if isinstance(paths, list) else [str(p.get("file_path") or "")]


def _architect_tables(paths):
    """{nom de fichier unique: (DataFrame, info)} pour une liste de chemins, ou (None, erreur)."""
    paths = [str(p) for p in (paths or []) if p][:MAX_ARCHITECT_FILES]
    if not paths:
        return None, "Aucun fichier."
    tables = {}
    for p in paths:
        df, info, err = _architect_df(p)
        if err:
            return None, f"{os.path.basename(p)} : {err}"
        name = (info or {}).get("fichier") or os.path.basename(p)
        while name in tables:
            name = "_" + name
        tables[name] = (df, info)
    return tables, None


def architect_propose(paths):
    if architect is None:
        return {"error": f"Agent architecte indisponible : {ARCHITECT_ERROR}"}
    tables, err = _architect_tables(paths)
    if err:
        return {"error": err}
    cfg = _llm_config()
    # Modèle rapide par défaut : deepseek-flash répond en ~1 min contre 3 à 7 min pour deepseek-v4-pro,
    # avec une qualité équivalente sur cette tâche (le moteur fournit déjà les faits mesurés).
    # Si l'API traîne, on réessaie une fois avec moins de réflexion (~30 s), puis repli par règles.
    model = os.environ.get("KLDV_ARCHITECT_MODEL") or (cfg or {}).get("architect_model") or "deepseek-flash"

    def llm(messages, max_tokens):
        return (_llm_call(messages, max_tokens, model=model, effort="high", timeout=120, attempts=1)
                or _llm_call(messages, max_tokens, model=model, effort="low", timeout=90, attempts=1))
    if len(tables) == 1:
        df, info = next(iter(tables.values()))
        return _json_safe(architect.propose(df, info, llm if cfg else None))
    return _json_safe(architect.propose_many(tables, llm if cfg else None))


# L'analyse IA dure plusieurs minutes : elle tourne en tâche de fond et le navigateur
# interroge son état, pour ne pas dépasser le délai des proxys (Render coupe vers 100 s).
_arch_jobs, _arch_lock = {}, threading.Lock()


def architect_start(paths):
    ctx = T.current()
    job_id = secrets.token_hex(8)

    def run():
        with T.use(ctx):
            try:
                result = architect_propose(paths)
            except Exception as e:
                result = {"error": f"Analyse impossible : {e}"}
        with _arch_lock:
            _arch_jobs[job_id].update(status="done", result=result)

    with _arch_lock:
        for k in [k for k, j in _arch_jobs.items() if time.time() - j["t"] > 3600]:
            del _arch_jobs[k]
        _arch_jobs[job_id] = {"key": ctx.key, "status": "running", "result": None, "t": time.time()}
    threading.Thread(target=run, daemon=True).start()
    return {"job": job_id}


def architect_job(job_id):
    with _arch_lock:
        j = _arch_jobs.get(job_id)
        if not j or j["key"] != T.current().key:  # une tâche n'est visible que par son auteur
            return {"error": "Tâche introuvable ou expirée."}
        return {"status": j["status"], "result": j["result"]}


def architect_apply(paths, spec):
    if architect is None:
        return {"error": f"Agent architecte indisponible : {ARCHITECT_ERROR}"}
    if not isinstance(spec, dict) or not spec:
        return {"error": "Aucune structure à générer."}
    tables, err = _architect_tables(paths)
    if err:
        return {"error": err}
    try:
        if len(tables) == 1:
            (name, (df, _)), = tables.items()
            r = architect.apply(df, spec, T.exports_dir(), name, views_dir=T.cleaned_dir())
        else:
            r = architect.apply_many(tables, spec, T.exports_dir(), views_dir=T.cleaned_dir())
    except Exception as e:
        return {"error": f"Restructuration impossible : {e}"}
    return {**r, "url": download_url(r["file"])}


# ---------------------------------------------------------------- serveur HTTP
SECRET = (os.environ.get("KLDV_SECRET") or secrets.token_hex(32)).encode()  # signe les liens de téléchargement
MAX_UPLOAD = int(os.environ.get("KLDV_MAX_UPLOAD_MB", "50")) * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
ALLOWED_EXT = (".csv", ".xlsx", ".xls", ".xlsm", ".json", ".parquet")
EXPORTERS = {"pdf": export_pdf, "excel": export_excel, "html": export_html}
STATIC = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"),
          "/login": ("login.html", "text/html"), "/logo.png": ("logo.png", "image/png"),
          "/favicon.png": ("logo.png", "image/png"), "/chart.umd.min.js": ("chart.umd.min.js", "application/javascript")}
if T.CLOUD:  # en ligne : page d'accueil publique, application sur /app, administration sur /admin
    STATIC.update({"/": ("landing.html", "text/html"), "/app": ("index.html", "text/html"),
                   "/admin": ("admin.html", "text/html")})
else:
    STATIC.update({"/classic": ("index_classic.html", "text/html"), "/index_classic.html": ("index_classic.html", "text/html")})
VIEWER_POST = {"/api/chat", "/api/insights"}  # seules actions permises au rôle « lecteur »
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data: blob:; connect-src 'self' " + (os.environ.get("SUPABASE_URL", "") or "") +
       "; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")

_rate, _rate_lock = {}, threading.Lock()


def _rate_ok(key, limit=30, window=60):
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate.get(key, []) if now - t < window]
        ok = len(hits) < limit
        if ok:
            hits.append(now)
        _rate[key] = hits
        return ok


def download_url(fn):
    """Lien de téléchargement. En ligne, il est signé et valable 2 heures pour l'organisation courante."""
    from urllib.parse import quote
    if not T.CLOUD:
        return f"/download/{quote(fn)}"
    org, exp = T.current().org_id, int(time.time()) + 7200
    sig = hmac.new(SECRET, f"{org}|{fn}|{exp}".encode(), hashlib.sha256).hexdigest()[:40]
    return f"/download/{quote(fn)}?k={org}.{exp}.{sig}"


def _list_exports():
    d, files = T.exports_dir(), []
    for fn in os.listdir(d):
        fp = os.path.join(d, fn)
        if os.path.isfile(fp):
            st = os.stat(fp)
            files.append({"file": fn, "url": download_url(fn), "size_kb": round(st.st_size / 1024, 1),
                          "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))})
    return sorted(files, key=lambda x: x["modified"], reverse=True)


def _safe_name(name):
    base = os.path.basename(str(name or "source"))
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^\w\-. ]+", "_", stem, flags=re.UNICODE).strip("._ ")[:80] or "source"
    return stem + ext.lower()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "KLDataViz"
    sys_version = ""
    timeout = 120

    # ---- réponses
    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if T.CLOUD:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", CSP)

    def _send(self, code, obj, ctype="application/json"):
        if isinstance(obj, (dict, list)):
            obj = json.dumps(_json_safe(obj), ensure_ascii=False, default=str)
        data = obj.encode("utf-8") if isinstance(obj, str) else obj
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        if not T.CLOUD:
            self.send_header("Access-Control-Allow-Origin", "*")
        if ctype == "application/json":
            self.send_header("Cache-Control", "no-store")
        elif ctype in ("text/html", "application/javascript"):  # toujours la dernière version après une mise à jour
            self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _payload(self, limit=MAX_JSON):
        ln = int(self.headers.get("Content-Length") or 0)
        if ln > limit:
            raise ValueError(f"Envoi trop volumineux (maximum {limit // (1024 * 1024)} Mo).")
        raw = self.rfile.read(ln) if ln else b""
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def log_message(self, *a):
        pass

    # ---- aiguillage : fichiers publics, puis connexion, puis organisation
    def _dispatch(self, method):
        path = urlparse(self.path).path
        try:
            if method == "GET" and path in STATIC:
                fn, ctype = STATIC[path]
                with open(os.path.join(BASE, fn), "rb") as f:
                    return self._send(200, f.read(), ctype)
            if method == "GET" and path == "/healthz":
                return self._send(200, {"ok": True})
            if method == "GET" and path == "/config.js":
                cfg = {"cloud": T.CLOUD}
                if T.CLOUD:
                    cfg.update(supabaseUrl=cloud.SUPABASE_URL, supabaseAnonKey=cloud.SUPABASE_ANON_KEY)
                return self._send(200, "window.KLDV=" + json.dumps(cfg) + ";", "application/javascript")
            if method == "GET" and path.startswith("/download/"):
                return self._download()
            if not path.startswith("/api/"):
                return self._send(404, {"error": "Page introuvable."})
            if T.CLOUD and method == "GET" and path.startswith("/api/public/"):
                return self._public(path)
            if not T.CLOUD:
                with T.use(T.LOCAL):
                    return self._route(method, path, self._payload(MAX_UPLOAD) if method == "POST" else {})
            auth = self.headers.get("Authorization") or ""
            user = cloud.verify_token(auth[7:] if auth.startswith("Bearer ") else "")
            payload = self._payload(MAX_UPLOAD if path == "/api/upload-source" else MAX_JSON) if method == "POST" else {}
            if path.startswith("/api/admin/"):  # administration de la plateforme : pas besoin d'espace
                actx = T.Ctx(key=user["id"], user_id=user["id"], email=user["email"],
                             is_admin=cloud.is_platform_admin(user["email"]))
                return self._admin(method, path, payload, actx)
            ctx = cloud.resolve(user)
            if ctx is None:  # compte sans espace : création de l'organisation
                if path == "/api/onboarding" and method == "POST":
                    cloud.create_org(user, payload.get("org"))
                    return self._send(200, {"ok": True})
                if path == "/api/me":
                    return self._send(200, {"onboarding": True, "email": user["email"]})
                return self._send(409, {"error": "Créez d'abord votre espace.", "onboarding": True})
            if method == "POST" and ctx.role == "viewer" and path not in VIEWER_POST:
                raise T.Forbidden("Votre rôle (lecteur) permet de consulter et de poser des questions uniquement.")
            with T.use(ctx):
                return self._route(method, path, payload)
        except T.AuthError as e:
            self._send(401, {"error": str(e), "auth": True})
        except T.Forbidden as e:
            self._send(403, {"error": str(e)})
        except T.QuotaExceeded as e:
            self._send(402, {"error": str(e), "quota": True})
        except T.Unavailable as e:
            self._send(503, {"error": str(e), "unavailable": True})
        except PermissionError:
            self._send(403, {"error": "Accès refusé."})
        except (ValueError, KeyError, FileNotFoundError, netguard.BlockedURL) as e:
            self._send(400, {"error": str(e).strip("'")})
        except Exception as e:
            # En ligne, le détail reste dans les journaux ; la référence permet de l'y retrouver.
            ref = secrets.token_hex(3)
            sys.stderr.write(f"[erreur {ref}] {method} {path} : {type(e).__name__} : {e}\n"
                             + "".join(traceback.format_exception(e)[-4:]))
            self._send(500, {"error": f"Erreur interne (réf. {ref}). Réessayez." if T.CLOUD else f"{type(e).__name__} : {e}"})

    def _download(self):
        u = urlparse(self.path)
        fn = os.path.basename(urllib.request.url2pathname(u.path))
        qs = parse_qs(u.query)
        inline = qs.get("inline") == ["1"]
        if T.CLOUD:
            try:
                org, exp, sig = (qs.get("k") or [""])[0].split(".")
                good = hmac.new(SECRET, f"{org}|{fn}|{exp}".encode(), hashlib.sha256).hexdigest()[:40]
                if not hmac.compare_digest(sig, good) or int(exp) < time.time():
                    raise ValueError
            except ValueError:
                return self._send(403, {"error": "Lien expiré. Rouvrez la page Rapports."})
            ctx = T.Ctx(key="download", org_id=org)
        else:
            ctx = T.LOCAL
        with T.use(ctx):
            fp = os.path.join(T.exports_dir(), fn)
        if not os.path.isfile(fp):
            return self._send(404, {"error": "fichier introuvable"})
        ctype = {".pdf": "application/pdf", ".html": "text/html",
                 ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(
            os.path.splitext(fn)[1], "application/octet-stream")
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'{"inline" if inline else "attachment"}; filename="{fn}"')
        self._security_headers()
        if T.CLOUD and ctype == "text/html":  # rapport HTML ouvert seul : aucun script exécuté
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src data:")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- routes de l'API (contexte de l'organisation déjà posé)
    def _route(self, method, path, p):
        ctx, st = T.current(), T.state()
        if method == "GET":
            if path == "/api/me":
                return self._send(200, cloud.me(ctx) if T.CLOUD else {"local": True})
            if path == "/api/status":
                if T.CLOUD:
                    return self._send(200, {"status": "CLOUD"})
                code, r = orchestrator("/api/v1/agents/me")
                return self._send(code if code else 502, r)
            if path == "/api/tasks":
                if T.CLOUD:
                    return self._send(200, [])
                code, r = orchestrator("/api/v1/agents/tasks")
                return self._send(code if code else 502, r)
            if path == "/api/exports":
                return self._send(200, _list_exports())
            if path == "/api/architect/job":
                return self._send(200, architect_job((parse_qs(urlparse(self.path).query).get("id") or [""])[0]))
            if path == "/api/settings":
                return self._send(200, studio.public_settings())
            if path == "/api/schedules":
                return self._send(200, studio.list_schedules())
            if path == "/api/ingest/versions":
                ds_id = (parse_qs(urlparse(self.path).query).get("id") or [""])[0]
                reg = T.registry()
                reg.ensure()
                return self._send(200, reg.list_versions(ds_id))
            if path == "/api/fetch-source":
                if T.CLOUD:
                    return self._send(200, {"source": None, "message": "Indisponible dans la version en ligne."})
                return self._send(200, fetch_source())
            if path == "/api/ingest/list":
                if not INGESTION_OK:
                    return self._send(500, {"error": f"Agent d'ingestion indisponible : {INGESTION_ERROR}"})
                reg = T.registry()
                info = reg.ensure()
                return self._send(200, {"backend": reg.backend, "connection": {} if T.CLOUD else info,
                                        "summary": reg.summary(), "datasets": reg.latest_versions()})
            if T.CLOUD and path == "/api/team":
                return self._send(200, cloud.team(ctx))
            if T.CLOUD and path == "/api/announcements":
                return self._send(200, cloud.announcements("app"))
            return self._send(404, {"error": "not found"})

        # ---- POST
        if path in ("/api/analyze", "/api/chat", "/api/insights", "/api/clean", "/api/architect",
                    "/api/architect/apply") and not _rate_ok(f"{ctx.key}:{path}"):
            return self._send(429, {"error": "Trop de demandes en peu de temps. Patientez une minute."})
        if path == "/api/analyze":
            file_path = str(p.get("file_path") or "")
            if T.CLOUD and not os.path.realpath(file_path).startswith(os.path.realpath(T.cleaned_dir()) + os.sep):
                cloud.consume(ctx, "analyses")  # la réanalyse d'une copie corrigée n'est pas décomptée
            result = analyze(file_path)
            if "error" not in result:
                st["last_result"], st["current_path"] = result, file_path
            return self._send(200, result)
        if path == "/api/clean":
            return self._send(200, clean(str(p.get("file_path") or ""), p.get("skip") or None))
        if path == "/api/architect":
            if T.CLOUD and _llm_config():
                cloud.consume(ctx, "ai")
            return self._send(200, architect_start(_architect_paths(p)))
        if path == "/api/architect/apply":
            return self._send(200, architect_apply(_architect_paths(p), p.get("spec")))
        if path == "/api/ingest":
            return self._ingest(p)
        if path in ("/api/export/pdf", "/api/export/excel", "/api/export/html"):
            if st["last_result"] is None:
                return self._send(400, {"error": "Lancez d'abord une analyse."})
            fn = EXPORTERS[path.rsplit("/", 1)[-1]](st["last_result"])
            return self._send(200, {"url": download_url(fn), "file": fn})
        if path == "/api/upload-source":
            name = _safe_name(p.get("name"))
            if T.CLOUD and not name.lower().endswith(ALLOWED_EXT):
                raise ValueError("Format non pris en charge. Utilisez CSV, Excel, JSON ou Parquet.")
            data = base64.b64decode(p.get("data") or "")
            if len(data) > MAX_UPLOAD:
                raise ValueError(f"Fichier trop volumineux (maximum {MAX_UPLOAD // (1024 * 1024)} Mo).")
            dest = os.path.join(T.uploads_dir(), name)
            with open(dest, "wb") as f:
                f.write(data)
            return self._send(200, {"path": dest, "name": name, "size": len(data)})
        if path == "/api/chat":
            question = str(p.get("question") or "")[:2000]
            # Fichier en cours envoyé par le navigateur : le chat ne dépend plus de la mémoire du
            # serveur, effacée à chaque redémarrage/déploiement. _read_df vérifie qu'il appartient à l'organisation.
            if p.get("file_path"):
                st["current_path"] = str(p["file_path"])
            if T.CLOUD and _llm_config():
                cloud.consume(ctx, "ai")
            resp = llm_chat(question)
            if resp is None:
                resp = chat_query(question)  # repli par règles si l'IA est indisponible
            return self._send(200, resp)
        if path == "/api/insights":
            if st["last_result"] is not None and T.CLOUD and _llm_config():
                cloud.consume(ctx, "ai")
            return self._send(200, {"insights": studio.insights(st["last_result"], _llm_call)})
        if path == "/api/settings":
            return self._send(200, studio.save_settings(p))
        if path == "/api/schedules":
            return self._send(200, studio.create_schedule(p))
        if path == "/api/schedules/update":
            return self._send(200, studio.update_schedule(p.get("id"), on=p.get("on")))
        if path == "/api/schedules/delete":
            studio.delete_schedule(p.get("id"))
            return self._send(200, {"ok": True})
        if path == "/api/schedules/run":
            if T.CLOUD:
                cloud.consume(ctx, "analyses")
            return self._send(200, studio.run_schedule(p.get("id"), analyze, EXPORTERS, T.exports_dir()))
        if path == "/api/exports/delete":
            fp = os.path.join(T.exports_dir(), os.path.basename(str(p.get("file") or "")))
            if not os.path.isfile(fp):
                raise FileNotFoundError("Fichier introuvable.")
            os.remove(fp)
            return self._send(200, {"ok": True})
        if T.CLOUD and path == "/api/onboarding":
            raise ValueError("Votre espace existe déjà.")
        if T.CLOUD and path == "/api/team/invite":
            return self._send(200, cloud.invite(ctx, p.get("email"), p.get("role") or "analyst"))
        if T.CLOUD and path == "/api/team/role":
            cloud.set_role(ctx, str(p.get("user_id")), p.get("role"))
            return self._send(200, {"ok": True})
        if T.CLOUD and path == "/api/team/remove":
            cloud.remove_member(ctx, str(p.get("user_id")))
            return self._send(200, {"ok": True})
        if T.CLOUD and path == "/api/team/cancel":
            cloud.cancel_invite(ctx, str(p.get("id")))
            return self._send(200, {"ok": True})
        if path == "/api/heartbeat" and not T.CLOUD:
            code, r = orchestrator("/api/v1/agents/heartbeat", method="POST", body={})
            return self._send(code if code else 502, r)
        return self._send(404, {"error": "not found"})

    def _public(self, path):
        """Données publiques de la page d'accueil (sans connexion)."""
        if path == "/api/public/plans":
            return self._send(200, cloud.public_plans())
        if path == "/api/public/site":
            return self._send(200, cloud.site_settings())
        if path == "/api/public/announcements":
            return self._send(200, cloud.announcements("public"))
        return self._send(404, {"error": "not found"})

    def _admin(self, method, path, p, ctx):
        """API de l'administration de la plateforme (réservée aux administrateurs)."""
        if not ctx.is_admin:
            raise T.Forbidden("Réservé aux administrateurs de la plateforme.")
        q = parse_qs(urlparse(self.path).query)
        arg = lambda k: (q.get(k) or [""])[0]
        if method == "GET":
            routes = {"/api/admin/me": lambda: {"email": ctx.email, "is_admin": True},
                      "/api/admin/stats": lambda: cloud.admin_stats(ctx),
                      "/api/admin/orgs": lambda: cloud.admin_orgs(ctx, arg("q")),
                      "/api/admin/org": lambda: cloud.admin_org_detail(ctx, arg("id")),
                      "/api/admin/plans": lambda: cloud.admin_plans(ctx),
                      "/api/admin/admins": lambda: cloud.admin_admins(ctx),
                      "/api/admin/announcements": lambda: cloud.admin_announcements(ctx),
                      "/api/admin/site": lambda: cloud.site_settings()}
        else:
            routes = {"/api/admin/plan": lambda: cloud.admin_set_plan(ctx, str(p.get("org_id")), p.get("plan"), p.get("trial_days")),
                      "/api/admin/extend": lambda: cloud.admin_extend_trial(ctx, str(p.get("org_id")), p.get("days") or 14),
                      "/api/admin/suspend": lambda: cloud.admin_suspend(ctx, str(p.get("org_id")), p.get("suspended")),
                      "/api/admin/invite-client": lambda: cloud.admin_invite_client(ctx, p.get("org"), p.get("email"), p.get("plan")),
                      "/api/admin/plans/save": lambda: cloud.admin_plan_save(ctx, p),
                      "/api/admin/plans/delete": lambda: cloud.admin_plan_delete(ctx, str(p.get("key"))),
                      "/api/admin/admins/add": lambda: cloud.admin_admin_add(ctx, p.get("email")),
                      "/api/admin/admins/remove": lambda: cloud.admin_admin_remove(ctx, p.get("email")),
                      "/api/admin/announcements/save": lambda: cloud.admin_announcement_save(ctx, p),
                      "/api/admin/announcements/delete": lambda: cloud.admin_announcement_delete(ctx, str(p.get("id"))),
                      "/api/admin/site": lambda: cloud.admin_site_save(ctx, p)}
        fn = routes.get(path)
        if fn is None:
            return self._send(404, {"error": "not found"})
        out = fn()
        return self._send(200, out if out is not None else {"ok": True})

    def _ingest(self, p):
        if not INGESTION_OK:
            return self._send(500, {"error": f"Agent d'ingestion indisponible : {INGESTION_ERROR}"})
        source = str(p.get("file_path") or p.get("source") or "").strip()
        if not source:
            raise ValueError("Indiquez un fichier ou un lien.")
        kwargs = {"registry_schema": T.current().schema}
        if T.storage_root():
            kwargs["storage_root"] = T.storage_root()
        if source.startswith(("http://", "https://")):
            source = normalize_source_url(source)
            if T.CLOUD:  # téléchargé par le serveur (protection SSRF), puis ingéré comme fichier
                data, ctype = netguard.fetch(source)
                ext = ".xlsx" if data[:2] == b"PK" else ".json" if data[:1] in (b"{", b"[") else ".csv"
                local = os.path.join(T.uploads_dir(), source_label(source) + ext)
                with open(local, "wb") as f:
                    f.write(data)
                kwargs["dataset_name"] = source_label(source) + ext
                source = local
        elif T.CLOUD and not T.current().owns(source):
            raise PermissionError("source hors de l'espace")
        if T.CLOUD and p.get("kind") in ("postgres", "sqlite"):
            raise ValueError("L'import depuis une base de données n'est pas disponible dans la version en ligne.")
        sheet = p.get("sheet")
        if sheet not in (None, ""):
            try:
                sheet = int(sheet)
            except (TypeError, ValueError):
                pass
            kwargs["sheet"] = sheet
        for key, arg in (("kind", "kind"), ("table", "table"), ("schema", "schema_name"), ("name", "dataset_name")):
            if p.get(key):
                kwargs[arg] = p[key]
        if p.get("limit"):
            kwargs["limit"] = int(p["limit"])
        if p.get("force"):
            kwargs["force"] = True
        try:
            result = ingest_agent(source, **kwargs)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__} : {exc}"}
        if "error" not in result:
            T.state()["last_ingest"] = result
        return self._send(200, result)


def heartbeat_loop():
    while True:
        try:
            orchestrator("/api/v1/agents/heartbeat", method="POST", body={})
        except Exception:
            pass
        time.sleep(45)


if __name__ == "__main__":
    if not T.CLOUD:
        threading.Thread(target=heartbeat_loop, daemon=True).start()
    studio.start_scheduler(analyze, EXPORTERS, T.LOCAL.dir("exports") if not T.CLOUD else None)
    host = "0.0.0.0" if T.CLOUD else "127.0.0.1"
    port = int(os.environ.get("PORT") or PORT)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    with socketserver.ThreadingTCPServer((host, port), Handler) as httpd:
        print(f"KLDataViz {'en ligne' if T.CLOUD else 'Console'} démarrée : http://localhost:{port}")
        httpd.serve_forever()
