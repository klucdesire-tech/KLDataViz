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
import sys
import urllib.request
from pathlib import Path
from typing import Any

from .. import artifacts, config
from ..contracts import new_job_id
from .quality import _load_dataset

SYSTEM = """Tu es le DATA VISUALIZATION & DASHBOARD AGENT d'Hermes (santé publique, BI).
On te donne le PROFIL d'un dataset (colonnes, types, valeurs d'exemple, répartition)
et un OBJECTIF MÉTIER. Propose les KPI les plus pertinents et les graphiques adaptés.
Réponds en JSON STRICT, sans aucun texte hors du JSON, exactement sous cette forme :
{"kpis":[{"nom":"...","agregation":"somme|moyenne|compte|max|min|taux","colonne":"...","dimension":"...","justification":"..."}],"graphiques":[{"colonne":"...","type":"bar|line|pie|hist|scatter|box","justification":"..."}]}
Règles absolues :
- Analyse les colonnes réellement disponibles ; ne suppose jamais une colonne ou dimension prédéfinie.
- Ne propose QUE des colonnes qui existent réellement dans le profil (respecte leur nom exact).
- N'invente jamais de valeur, de total, d'objectif ou de formule métier.
- Ne confonds jamais une valeur manquante avec zéro.
- 4 à 7 KPI pertinents pour l'objectif métier, pas plus ; évite les répétitions inutiles.
- « dimension » (optionnel) : une colonne catégorielle (ex. region, district, domaine, sexe) pour
  regrouper le KPI. Mets-en une quand le KPI gagne à être comparé par région/période/catégorie.
- Choisis chaque graphique selon sa pertinence analytique, pas parce qu'il est techniquement disponible.
- Justifie chaque choix en une phrase courte, en français.
- agrégation « somme » pour des comptages/volumes, « moyenne » pour des taux/intensités,
  « compte » pour des effectifs, « taux » pour des proportions (0 à 1, sens clair).
- « taux » UNIQUEMENT sur une colonne Oui/Non (vrai/faux, positif/négatif) ou déjà chiffrée entre 0 et 1 ;
  jamais sur une date, un texte libre ou un statut à plusieurs valeurs (utilise alors « compte » avec une dimension).
- « somme », « moyenne », « max », « min » UNIQUEMENT sur des colonnes chiffrées."""


def _llm_dashboard(profile_json: str, llm=None) -> dict | None:
    """Choisit KPI et graphiques via le LLM, ou None si indisponible.

    `llm(messages, max_tokens) -> texte` : accès à l'IA fourni par l'appelant (la console en ligne, où
    llm_config.json n'existe pas et où la clé vient de l'environnement). Sans lui : llm_config.json local."""
    if llm is False:  # IA explicitement écartée (repli par règles)
        return None
    if llm is not None:
        try:
            content = llm([{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": profile_json}], 8000) or ""
            start, end = content.find("{"), content.rfind("}")
            return json.loads(content[start:end + 1]) if start >= 0 and end > start else None
        except Exception:
            return None
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


_YES = {"oui", "yes", "true", "vrai", "o", "y", "positif", "positive", "1", "1.0"}
_NO = {"non", "no", "false", "faux", "n", "négatif", "negatif", "négative", "negative", "0", "0.0"}


def _rate_values(series):
    """Valeurs d'un « taux » : colonne Oui/Non (et assimilés) → 1/0, sinon valeurs numériques."""
    import pandas as pd
    s = pd.to_numeric(series, errors="coerce")
    t = series.dropna().astype(str).str.strip().str.lower()
    if len(t) and t.isin(_YES | _NO).all():
        return series.astype(str).str.strip().str.lower().map(lambda x: 1.0 if x in _YES else 0.0 if x in _NO else None).astype(float)
    return s


def _agg(df, colonne: str | None, agregation: str) -> float | int | None:
    import pandas as pd
    a = (agregation or "").lower()
    if colonne is None or colonne not in df.columns:
        return int(len(df)) if a in ("compte", "count") else None
    if a in ("compte", "count", "effectif"):
        return int(df[colonne].notna().sum())
    if a in ("taux", "rate", "pct", "proportion"):
        s = _rate_values(df[colonne])
        return round(float(s.mean()), 4) if s.notna().any() else None
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
    s = _rate_values(df[colonne]) if a in ("taux", "rate", "pct", "proportion") else pd.to_numeric(df[colonne], errors="coerce")
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


_MOIS = {"janv": 1, "jan": 1, "fev": 2, "fevr": 2, "feb": 2, "mars": 3, "mar": 3, "avr": 4, "apr": 4, "mai": 5,
         "may": 5, "juin": 6, "jun": 6, "juil": 7, "jul": 7, "aout": 8, "aug": 8, "sept": 9, "sep": 9, "oct": 10,
         "nov": 11, "dec": 12}
_MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def _fr_month(v):
    """« Avril 2025 », « avr.-25 » → « 2025-04 » ; None sinon."""
    t = str(v).strip().lower()
    for a, b in (("é", "e"), ("è", "e"), ("û", "u"), ("ô", "o")):
        t = t.replace(a, b)
    m = re.match(r"^([a-z]+)\.?[\s\-_/']*(\d{2}|\d{4})$", t)
    if not m:
        return None
    mo = _MOIS.get(m.group(1)) or _MOIS.get(m.group(1)[:4]) or _MOIS.get(m.group(1)[:3])
    if not mo:
        return None
    y = int(m.group(2))
    return f"{y + 2000 if y < 100 else y:04d}-{mo:02d}"


def _period(df):
    """Première colonne de période (dates, « 2025-04 », « Avril 2025 ») → (nom, série « AAAA-MM ») ; (None, None) sinon."""
    import pandas as pd
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_datetime64_any_dtype(s):
            continue
        sample = s.dropna().astype(str).head(60)
        if len(sample) < 2:
            continue
        if pd.api.types.is_datetime64_any_dtype(s):
            return str(c), s.dt.strftime("%Y-%m-%d")
        fr = sample.map(_fr_month)
        if fr.notna().mean() >= 0.9:
            return str(c), s.map(lambda v: _fr_month(v) if v == v else None)
        if sample.str.match(r"^\d{4}-\d{2}$").mean() >= 0.9:  # « 2025-04 » : déjà un mois
            return str(c), s.map(lambda v: str(v)[:7] if v == v and v is not None else None)
        try:
            dt = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=True)
        except Exception:
            continue
        if dt.notna().mean() >= 0.9 and sample.str.contains(r"\d{4}|\d{1,2}[/-]\d{1,2}", regex=True).mean() >= 0.9:
            full = pd.to_datetime(s.astype(str), errors="coerce", format="mixed", dayfirst=True)
            return str(c), full.dt.strftime("%Y-%m-%d")
    return None, None


def _meta(df, kpis, spec, filter_cols, period_col, period_series):
    """Ce que le gabarit dessine : mesure principale, période, catégories empilées, graphiques choisis."""
    import pandas as pd
    cols = set(df.columns)
    labels = {"somme": "Somme", "moyenne": "Moyenne", "compte": "Nombre", "taux": "Taux moyen", "max": "Maximum",
              "min": "Minimum"}
    measure = None
    for k in kpis:
        col = k.get("colonne")
        if col in cols and pd.api.types.is_numeric_dtype(pd.to_numeric(df[col], errors="coerce")) \
                and (k.get("agregation") or "").lower() in ("somme", "moyenne", "taux", "max", "min"):
            measure = k
            break
    measure = measure or (kpis[0] if kpis else None)
    m = None
    if measure:
        m = {"nom": measure["nom"], "colonne": measure.get("colonne"), "agregation": measure.get("agregation"),
             "sous_titre": (labels.get((measure.get("agregation") or "").lower(), "") + " par période").strip()}
    # Filtres utiles : catégories texte de 2 à 30 valeurs ; jamais un identifiant, une date, une mesure ni un montant
    nulls = {"", "nan", "none", "null", "nat", "na", "n/a"}
    idlike = re.compile(r"(^|[\s_])(n°|no|num(ero|éro)?|id|code|matricule|ref|référence|reference)($|[\s_.])", re.I)
    cands = []
    for c in df.columns:
        if c in (period_col, (m or {}).get("colonne")) or pd.api.types.is_numeric_dtype(df[c]) \
                or pd.api.types.is_datetime64_any_dtype(df[c]) or idlike.search(re.sub(r"\(.*?\)", "", str(c))):
            continue
        vals = [str(v).strip() for v in df[c].dropna().astype(str).unique() if str(v).strip().lower() not in nulls]
        if not 2 <= len(vals) <= 30:
            continue
        if pd.to_numeric(pd.Series(vals).str.replace(",", ".", regex=False), errors="coerce").notna().mean() >= 0.8:
            continue  # montants ou codes chiffrés
        if pd.to_datetime(pd.Series(vals[:30]), errors="coerce", format="mixed").notna().mean() >= 0.8:
            continue  # dates (gérées par le sélecteur de période)
        cands.append((len(vals), str(c), sorted(vals)))
    cands.sort()
    filters = [{"col": c, "values": v} for _, c, v in cands[:6]]
    stack = next((f["col"] for f in filters if 2 <= len(f["values"]) <= 5), None)
    charts = [{"colonne": str(g["colonne"]), "type": str(g.get("type") or "bar"),
               "justification": str(g.get("justification") or "")[:160]}
              for g in spec.get("graphiques", [])[:8] if g.get("colonne") in cols]
    rng, gran, grans = None, "M", ["M", "Q", "Y"]
    if period_series is not None:
        ks = sorted(k for k in period_series.dropna().unique() if isinstance(k, str) and len(k) in (7, 10))
        if ks:
            mo = lambda k: f"{_MOIS_COURTS[int(k[5:7]) - 1]} {k[:4]}"
            day = lambda k: f"{int(k[8:10])} {_MOIS_COURTS[int(k[5:7]) - 1]} {k[:4]}"
            if len(ks[0]) == 10:
                rng = day(ks[0]) if ks[0] == ks[-1] else (
                    f"{int(ks[0][8:10])} – {day(ks[-1])}" if ks[0][:7] == ks[-1][:7] else f"{day(ks[0])} – {day(ks[-1])}")
                span = (pd.Timestamp(ks[-1]) - pd.Timestamp(ks[0])).days
                if span <= 62:  # quelques semaines : regrouper par jour ou par semaine
                    grans, gran = ["D", "W", "M"], ("D" if len(ks) <= 40 else "W")
                elif span <= 731:
                    grans, gran = ["W", "M", "Q"], "M"
                else:
                    grans, gran = ["M", "Q", "Y"], "M"
            else:
                rng = mo(ks[0]) if ks[0] == ks[-1] else f"{mo(ks[0])} – {mo(ks[-1])}"
                if len(ks) > 36:
                    gran = "Q"
    return {"measure": m, "period": period_col, "stack": stack, "charts": charts, "range": rng,
            "filters": filters, "gran": gran, "grans": grans}


# ---- Domaine / programme : détection + indicateurs clés + écarts (gaps)
# Chaque indicateur clé : « tous » (mots tous présents), « un » (au moins un, si non vide), « sauf » (aucun),
# « bon » = sens souhaité (+1 : une hausse est un progrès ; -1 : une hausse est une dégradation).
# Les mots sont comparés sur des libellés normalisés (minuscules, sans accents, ponctuation → espace).
def _ki(nom, tous, un=(), sauf=(), bon=1):
    return {"nom": nom, "tous": list(tous), "un": list(un), "sauf": list(sauf), "bon": bon}


DOMAINES = {
    "vih": {
        "nom": "VIH / Sida",
        "mots": ["vih", "hiv", "arv", "tarv", "seropositif", "seropositifs", "charge virale", "depistage", "depistes",
                 "index testing", "pvvih"],
        "indicateurs": [
            _ki("Personnes dépistées (résultat reçu)", ["depist"], sauf=["positif"]),
            _ki("Cas positifs dépistés", ["positif"], sauf=["charge virale"]),
            _ki("Nouveaux sous TARV", ["initi"]),
            _ki("Résultats de charge virale reçus", ["charge virale"], un=["recu", "realise"], sauf=["1000", "supprim"]),
            _ki("Charge virale supprimée", ["charge virale"], un=["supprim", "indetect", "< 1000", "<1000"],
                sauf=["non supprim"]),
            _ki("Charge virale ≥ 1 000 copies/mL", ["charge virale", "1000"], sauf=["supprim", "<"], bon=-1),
            _ki("Décès", ["deces"], bon=-1),
        ],
    },
    "paludisme": {
        "nom": "Paludisme",
        "mots": ["palu", "paludisme", "malaria", "moustiquaire", "moustiquaires", "mild", "tpi", "tdr", "rdt", "cta"],
        "indicateurs": [_ki("Cas confirmés", ["cas", "confirm"]), _ki("Décès", ["deces"], bon=-1),
                        _ki("Moustiquaires distribuées", [], un=["mild", "moustiquaire"])],
    },
    "tuberculose": {
        "nom": "Tuberculose",
        "mots": ["tuberculose", "tb", "tpm", "bacteriologiquement"],
        "indicateurs": [_ki("Cas notifiés", ["cas"], un=["notifi", "tb", "tuberculose"]), _ki("Décès", ["deces"], bon=-1)],
    },
    "vaccination": {
        "nom": "Vaccination / PEV",
        "mots": ["vaccin", "vaccination", "vaccines", "pev", "penta", "penta3", "rougeole", "var", "bcg", "antigene"],
        "indicateurs": [_ki("Doses administrées", ["dose"]), _ki("Enfants vaccinés", ["enfant", "vaccin"])],
    },
    "sante_maternelle": {
        "nom": "Santé maternelle & néonatale",
        "mots": ["maternel", "maternelle", "cpn", "cpn1", "cpn4", "anc", "accouchement", "accouchements",
                 "cesarienne", "neonatal", "postnatal"],
        "indicateurs": [_ki("Consultations prénatales", [], un=["cpn", "prenatal"]),
                        _ki("Accouchements assistés", ["accouch"]), _ki("Décès maternels", ["deces", "matern"], bon=-1)],
    },
}
_IND_COLS = ("indicator", "indicateur", "indicateurs", "code", "code indicateur", "libelle", "dataname", "data")
_VAL_COLS = ("value", "valeur", "val", "montant", "mesure", "total")
_PER_COLS = ("quarter", "period", "periode", "periodname", "periodid", "periodcode", "trimestre", "mois", "date", "year", "annee")


def _norm(t) -> str:
    """« Séropositifs (≥1000 copies/mL) » → « seropositifs 1000 copies ml »."""
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9<>]+", " ", t).split())


def _col(df, names, numeric=False):
    import pandas as pd
    for c in df.columns:
        if _norm(c) not in names:
            continue
        if numeric and not pd.to_numeric(df[c], errors="coerce").notna().any():
            continue
        return c
    return None


def _detect_domaine(df) -> dict | None:
    """Domaine/programme le plus représenté dans les noms de colonnes et les libellés d'indicateurs (mots entiers)."""
    words = " " + _norm(" ".join(str(c) for c in df.columns))
    ic = _col(df, _IND_COLS)
    if ic is not None:
        words += " " + _norm(" ".join(df[ic].dropna().astype(str).unique()[:300]))
    words += " "
    best, score = None, 0
    for cle, dom in DOMAINES.items():
        n = sum(words.count(" " + m + " ") for m in dom["mots"])
        if n > score:
            best, score = cle, n
    if not best:
        return None
    return {"cle": best, "nom": DOMAINES[best]["nom"], "indicateurs": DOMAINES[best]["indicateurs"]}


def _match(label_norm: str, ki: dict) -> bool:
    return (all(m in label_norm for m in ki["tous"]) and (not ki["un"] or any(m in label_norm for m in ki["un"]))
            and not any(m in label_norm for m in ki["sauf"]))


def _period_key(df, period_col, period_series):
    """Clé de période triable pour comparer « dernière » et « précédente » : AAAA-MM, AAAA-Tn ou AAAA."""
    import pandas as pd
    if period_series is not None:
        return period_series.map(lambda v: v[:7] if isinstance(v, str) else None)
    c = period_col or _col(df, _PER_COLS)
    if c is None:
        return None

    def key(v):
        if v is None or v != v:
            return None
        t = _norm(v)
        m = re.match(r"^(\d{4}) ?[qt]([1-4])$", t)
        if m:
            return f"{m.group(1)}-T{m.group(2)}"
        m = re.match(r"^[qt]([1-4]) ?(\d{4})$", t)
        if m:
            return f"{m.group(2)}-T{m.group(1)}"
        if re.match(r"^\d{4}$", t):
            return t
        fr = _fr_month(v)
        if fr:
            return fr
        d = pd.to_datetime(str(v), errors="coerce", dayfirst=True)
        return None if pd.isna(d) else d.strftime("%Y-%m")
    return df[c].map(key)


def _keyind(df, domaine):
    """Indicateurs clés présents dans le fichier, un seul par indicateur clé (jamais une somme de libellés différents).

    Format long (DHIS2 : colonnes Indicateur + Valeur) → libellé le plus fréquent ;
    format large (une colonne chiffrée par indicateur, ex. « PVVIH avec charge virale ≥1000 ») → la colonne."""
    import pandas as pd
    if not domaine:
        return None, None, []
    ic = _col(df, _IND_COLS)
    vc = _col(df, _VAL_COLS, numeric=True)
    out, used = [], set()
    if ic is not None and vc is not None:
        counts = df[ic].dropna().astype(str).str.strip().value_counts()
        for ki in domaine["indicateurs"]:
            lab = next((l for l in counts.index if l not in used and _match(_norm(l), ki)), None)
            if lab is not None:
                used.add(lab)
                out.append({"nom": ki["nom"], "libelle": lab, "bon": ki["bon"]})
        if out:
            return ic, vc, out
    nums = [c for c in df.columns if pd.to_numeric(df[c], errors="coerce").notna().sum() >= max(1, int(df[c].notna().sum() * .9))
            and df[c].notna().any() and not pd.api.types.is_datetime64_any_dtype(df[c])]
    # Libellé d'indicateur (« Nombre de PVVIH nouvellement initiés sous TARV »), pas une variable patient (« delai_initiation »)
    nums = [c for c in nums if len(_norm(c).split()) >= 4 and not any(m in _norm(c).split() for m in ("delai", "duree", "age", "jours", "date"))]
    for ki in domaine["indicateurs"]:
        col = next((c for c in nums if c not in used and _match(_norm(c), ki)), None)
        if col is not None:
            used.add(col)
            out.append({"nom": ki["nom"], "libelle": str(col), "col": str(col), "bon": ki["bon"]})
    return None, None, out


def _gaps(df, keys, ic, vc, kis):
    """Par indicateur clé : valeur de la dernière période et écart avec la précédente (sur toutes les lignes).
    Une seule période (ou pas de période) : valeur seule, sans écart."""
    import pandas as pd
    if not kis:
        return []
    ps = sorted(k for k in keys.dropna().unique()) if keys is not None else []
    last, prev = (ps[-1], ps[-2]) if len(ps) >= 2 else (ps[-1] if ps else None, None)
    lab = df[ic].astype(str).str.strip() if ic is not None else None
    gaps = []
    for k in kis:
        if k.get("col"):
            sel, val = pd.Series(True, index=df.index), pd.to_numeric(df[k["col"]], errors="coerce")
        else:
            sel, val = lab == k["libelle"], pd.to_numeric(df[vc], errors="coerce")
        cur = val[sel & (keys == last)] if last is not None else val[sel]
        if not cur.notna().any():
            continue
        g = {"indicateur": k["nom"], "libelle": k["libelle"], "derniere": float(cur.sum()), "bon": k["bon"],
             "periode": last, "periode_prec": prev, "pct": None, "sens": None}
        pre = val[sel & (keys == prev)] if prev is not None else None
        if pre is not None and pre.notna().any() and float(pre.sum()) != 0:
            p = float(pre.sum())
            d = g["derniere"] - p
            g.update({"precedente": p, "delta": d, "pct": d / p * 100,
                      "sens": "hausse" if d > 0 else "baisse" if d < 0 else "stable"})
        gaps.append(g)
    return gaps


# Fichier « une ligne par patient » (pas de colonne indicateur/valeur) : on compare les deux derniers mois
# de la colonne de date — nouveaux enregistrements et indicateurs « taux »/« moyenne » choisis pour le tableau.
_MAUVAIS = ("deces", "decede", "perdu", "abandon", "arret", "echec", "non supprim", "1000", "delai", "attente", "rupture")


def _bon(nom) -> int:
    """Sens souhaité d'un indicateur d'après son nom : -1 si une hausse est une dégradation (décès, délai…)."""
    n = _norm(nom)
    return -1 if any(m in n for m in _MAUVAIS) else 1


def _partial_month(period_series):
    """Dernier mois incomplet (dates au jour qui s'arrêtent avant les 5 derniers jours du mois) → sa clé « AAAA-MM »."""
    import calendar
    days = sorted(v for v in period_series.dropna().unique() if isinstance(v, str) and len(v) == 10)
    if not days:
        return None
    y, m, d = (int(x) for x in days[-1].split("-"))
    return days[-1][:7] if d <= calendar.monthrange(y, m)[1] - 5 else None


def _patient_items(df, kpi_defs, domaine, period_col):
    items = [{"nom": "Nouveaux patients" if domaine else "Nouveaux enregistrements", "kind": "count", "bon": 1,
              "libelle": f"Lignes par mois de « {period_col} »"}]
    for k in kpi_defs:
        a, col = (k.get("agregation") or "").lower(), k.get("colonne")
        if col in df.columns and col != period_col and a in ("taux", "rate", "pct", "proportion", "moyenne", "mean"):
            items.append({"nom": k["nom"], "kind": "agg", "col": str(col), "agg": a, "bon": _bon(k["nom"]),
                          "libelle": f"{k['nom']} ({col})", "pts": a in ("taux", "rate", "pct", "proportion")})
    return items


def _patient_gaps(df, keys, items):
    """Dernier mois vs précédent : effectif, taux (écart en points) ou moyenne (écart en %)."""
    if keys is None:
        return []
    ps = sorted(k for k in keys.dropna().unique())
    if len(ps) < 2:
        return []
    last, prev = ps[-1], ps[-2]
    gaps = []
    for k in items:
        if k["kind"] == "count":
            cur, pre = float((keys == last).sum()), float((keys == prev).sum())
        else:
            cur, pre = _agg(df[keys == last], k["col"], k["agg"]), _agg(df[keys == prev], k["col"], k["agg"])
            if cur is None or pre is None:
                continue
            if k.get("pts"):
                cur, pre = cur * 100, pre * 100
        d = cur - pre
        if not k.get("pts") and pre == 0:
            continue
        gaps.append({"indicateur": k["nom"], "libelle": k["libelle"], "derniere": cur, "precedente": pre, "delta": d,
                     "pct": d if k.get("pts") else d / pre * 100, "pts": bool(k.get("pts")), "bon": k["bon"],
                     "periode": last, "periode_prec": prev, "sens": "hausse" if d > 0 else "baisse" if d < 0 else "stable"})
    return gaps


TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tableau de bord KLDataViz</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fira+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<script>__CHARTJS__</script>
<script>window.Chart||document.write('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"><\/script>')</script>
<style>
/* Style « Analytics » : fond lavande, cartes blanches très arrondies, tuiles KPI pastel, accent bleu marine */
/* Palette « Sales dashboard » : bleu profond, bleu roi, azur, cyan, vert d'eau ; cartes blanches sur gris très clair */
:root{--bg:#F3F6F9;--card:#FFFFFF;--ink:#102A43;--muted:#52667A;--line:#E3EAF1;--navy:#0B4F8F;--navy-2:#1565C0;
  --t1:#DCEBFA;--t2:#D7F0FA;--t3:#D3F4F6;--t4:#D5F4EA;--t5:#E4EEF8;--radius:16px}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Fira Sans',system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--bg);color:var(--ink);padding:24px;min-height:100vh;font-size:14px}
.top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.top h1{font-size:26px;font-weight:700;letter-spacing:-.4px}
.top p{color:var(--muted);font-size:13px;margin-top:4px;max-width:640px}
.chip{display:inline-flex;align-items:center;gap:8px;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:8px 14px;font-size:12.5px;color:var(--muted);white-space:nowrap}
.chip svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.8}
.filters{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px 20px;margin-bottom:16px}
.more{border:0;background:none;color:var(--navy);font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;min-height:32px;padding:0 4px;text-decoration:underline;text-underline-offset:3px}
.extra{display:contents}.extra[hidden]{display:none}
.f{display:flex;flex-direction:column;gap:6px}
.f > span{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;color:var(--muted);max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pills{display:flex;flex-wrap:wrap;gap:6px}
.pill{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:6px 12px;font:inherit;font-size:12.5px;cursor:pointer;min-height:32px;transition:background .15s,color .15s,border-color .15s}
.pill:hover{border-color:var(--navy)}
.pill[aria-pressed="true"]{background:var(--navy);border-color:var(--navy);color:#fff}
.pill:focus-visible,select:focus-visible,.seg button:focus-visible,.more:focus-visible{outline:3px solid #7FD3EE;outline-offset:2px}
select{appearance:none;border:1px solid var(--line);background:var(--card) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%235B6478' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E") no-repeat right 12px center;border-radius:999px;padding:7px 34px 7px 14px;font:inherit;font-size:12.5px;color:var(--ink);min-height:32px;cursor:pointer;min-width:150px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.kpi{border-radius:var(--radius);padding:14px 16px 16px;min-height:104px;display:flex;flex-direction:column;justify-content:space-between;gap:10px}
.kpi .n{font-size:12.5px;font-weight:500;line-height:1.3}
.kpi .row{display:flex;align-items:flex-end;justify-content:space-between;gap:8px}
.kpi .v{font-size:24px;font-weight:700;letter-spacing:-.3px;line-height:1}
.kpi .s{font-size:11px;text-align:right;line-height:1.3;max-width:55%}
.kpi.k0{background:linear-gradient(135deg,var(--navy),var(--navy-2));color:#fff}.kpi.k0 .s{color:#BFE3F5}
.kpi.k1{background:var(--t1)}.kpi.k2{background:var(--t2)}.kpi.k3{background:var(--t3)}.kpi.k4{background:var(--t4)}.kpi.k5{background:var(--t5)}
.kpi:not(.k0) .s{color:var(--muted)}
.row2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:14px}
.row3{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}
.card{background:var(--card);border-radius:var(--radius);padding:16px 18px;box-shadow:0 1px 2px rgba(22,33,62,.04);min-width:0}
.card-h{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.card-h > div:first-child{min-width:0;flex:1 1 180px}
.card-h h2{font-size:15px;font-weight:600;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-h small{display:block;color:var(--muted);font-size:11.5px;font-weight:400;margin-top:2px}
.seg{display:inline-flex;background:#F1F3F9;border-radius:999px;padding:3px;gap:2px}
.seg button{border:0;background:none;font:inherit;font-size:12px;color:var(--muted);padding:5px 11px;border-radius:999px;cursor:pointer;min-height:28px}
.seg button[aria-pressed="true"]{background:var(--navy);color:#fff}
.box{position:relative;height:250px}
.donut{display:grid;grid-template-columns:150px 1fr;gap:14px;align-items:center}
.donut .box{height:150px}
.legend{list-style:none;display:grid;gap:7px}
.legend li{display:grid;grid-template-columns:10px 1fr auto;gap:8px;align-items:center;font-size:12.5px}
.legend i{width:10px;height:10px;border-radius:50%;flex:none}
.legend b{font-weight:600}
.rank{list-style:none;display:grid;gap:9px}
.rank li{display:grid;grid-template-columns:minmax(80px,38%) 1fr auto;gap:10px;align-items:center;font-size:12.5px}
.rank .lab{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink)}
.rank .bar{height:12px;border-radius:6px;background:#F1F3F9;overflow:hidden}
.rank .bar i{display:block;height:100%;border-radius:6px}
.rank b{font-weight:600;font-variant-numeric:tabular-nums}
.empty{color:var(--muted);font-size:13px;padding:24px 0;text-align:center}
@media (max-width:900px){.row2{grid-template-columns:minmax(0,1fr)}}
/* Affiché dans l'application (cadre) : le titre est déjà sur la page */
.embedded body,body.embedded{padding:4px 2px 16px;background:transparent}
body.embedded{min-height:0;overflow:hidden}html:has(body.embedded){overflow:hidden}body.embedded .top .title{display:none}body.embedded .top{justify-content:flex-end;margin-bottom:8px}
@media (max-width:560px){body{padding:16px}.donut{grid-template-columns:1fr}.donut .box{height:170px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
.gaps{margin:0 0 18px;padding:16px;background:#fff;border:1px solid #e3e9f0;border-radius:14px;box-shadow:0 2px 6px rgba(15,23,42,.05)}
.gaps-h{font-size:15px;font-weight:700;margin-bottom:12px;color:#0f172a}
.gaps-dom{color:#0B4F8F}
.gaps-sub{font-size:11.5px;color:#667085;font-weight:400;margin-left:8px}
.gaps-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.gap{background:#f8fafc;border:1px solid #eef1f5;border-radius:10px;padding:12px 14px}
.gap b{display:block;font-size:12.5px;font-weight:600;color:#334155;margin-bottom:4px;line-height:1.3}
.gap-v{display:block;font-size:21px;font-weight:800;color:#0f172a}
.gap-d{display:inline-block;font-size:12px;font-weight:600;margin-top:5px;padding:2px 8px;border-radius:20px}
.gap-d.up{background:#e6f7ed;color:#157a3f}
.gap-d.down{background:#fdeaea;color:#c0392b}
.gap-d.flat{background:#eef1f4;color:#667085}
.gaps-top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}.gaps-top .gaps-h{margin:0}
.gaps-ctl{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.gaps-ctl label{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px}.gaps-ctl select{min-width:130px}
.gaps-warn{font-size:12px;color:#9A5B00;background:#FFF6E5;border-radius:8px;padding:6px 10px;margin:0 0 10px}
.gaps-body{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:14px;align-items:start}.gaps-body.solo{grid-template-columns:minmax(0,1fr)}
.gaps-chart .box{height:220px}.gaps-ct{font-size:12px;color:var(--muted);margin:0 0 6px}
.gap{border-left:4px solid #E3EAF1}.gap.up{border-left-color:#12BF9A}.gap.down{border-left-color:#E5484D}.gap.flat{border-left-color:#9AA8B6}
.gap-p{display:block;font-size:11.5px;color:var(--muted);margin-top:4px}
@media (max-width:900px){.gaps-body{grid-template-columns:minmax(0,1fr)}}
</style></head><body>
<header class="top"><div class="title"><h1>Tableau de bord</h1><p id="objective"></p></div>
  <span class="chip" id="range" hidden><svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg><span></span></span></header>
<div class="filters" id="filters"></div>
<section class="kpis" id="kpis" aria-label="Indicateurs clés"></section>
<section class="gaps" id="gaps" hidden></section>
<section class="row2" id="row2"></section>
<section class="row3" id="row3"></section>
<script>
window.DATA=__DATA__;
window.KPIDEFS=__KPIDEFS__;
window.FILTERCOLS=__FILTERCOLS__;
window.META=__META__;
window.OBJECTIVE="__OBJECTIVE__";
var NAVY='#0B4F8F',PASTEL=['#0B4F8F','#0091D0','#10BCD0','#12BF9A','#1565C0','#7FDCC4'],
    DONUT=['#0B4F8F','#0091D0','#10BCD0','#12BF9A','#1565C0','#5FD3E6','#7FDCC4','#9CC3EA'],
    BLUES=['#0B4F8F','#0E63A8','#0078C0','#0091D0','#10A7D0','#10BCD0','#12BF9A','#7FDCC4'];
var state={},gran=(META.gran||'M'),charts=[],showMore=false,SHOWN={},KCOUNT={};
var MOIS=['janv.','févr.','mars','avr.','mai','juin','juil.','août','sept.','oct.','nov.','déc.'];
if(window.Chart){Chart.defaults.font.family="'Fira Sans',system-ui,sans-serif";Chart.defaults.color='#5B6478';
  if(matchMedia('(prefers-reduced-motion: reduce)').matches)Chart.defaults.animation=false;}
function nice(s){return String(s==null?'':s).replace(/_/g,' ')}
// Nom de colonne court pour les titres : sans parenthèse, 32 caractères au plus
function short(s){var t=nice(s).replace(/\s*\([^)]*\)\s*/g,' ').replace(/\s+/g,' ').trim()||nice(s);return t.length>32?t.slice(0,31)+'…':t}
var NULLS={'':1,'nan':1,'none':1,'null':1,'nat':1,'na':1,'n/a':1};
function isNull(v){return v==null||NULLS[String(v).trim().toLowerCase()]===1}
// Valeur lisible : « 2024-04-03 00:00:00 » → « 03/04/2024 »
function label(v){var t=String(v==null?'':v),m=t.match(/^(\d{4})-(\d{2})-(\d{2})([ T]00:00(:00)?)?$/);return m?m[3]+'/'+m[2]+'/'+m[1]:t}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function fmt(v){if(v==null||isNaN(v))return '—';var n=Number(v),a=Math.abs(n);
  if(a>=1e6)return (n/1e6).toLocaleString('fr-FR',{maximumFractionDigits:1})+' M';
  if(a>=1e5)return (n/1e3).toLocaleString('fr-FR',{maximumFractionDigits:0})+' k';
  return n.toLocaleString('fr-FR',{maximumFractionDigits:a>=100?0:a>=10?1:2})}
function num(v){var n=parseFloat(v);return isNaN(n)?null:n}
var FCOLS=(META.filters||[]).map(function(f){return f.col});
function filtered(){return DATA.filter(function(r){return FCOLS.every(function(c){return state[c]==null||state[c]==='all'||String(r[c]).trim()===state[c]})})}
// Taux : colonne Oui/Non (et assimilés) → 1/0 ; affiché en % (0 à 100)
var YES={'oui':1,'yes':1,'true':1,'vrai':1,'o':1,'y':1,'positif':1,'positive':1,'1':1},NO={'non':1,'no':1,'false':1,'faux':1,'n':1,'négatif':1,'negatif':1,'négative':1,'negative':1,'0':1};
function isRate(a){a=(a||'').toLowerCase();return a==='taux'||a==='rate'||a==='pct'||a==='proportion'}
function rate(rows,col){var t=rows.filter(function(r){return !isNull(r[col])}).map(function(r){return String(r[col]).trim().toLowerCase()});if(!t.length)return null;
  if(t.every(function(x){return YES[x]||NO[x]}))return t.filter(function(x){return YES[x]}).length/t.length*100;
  var v=t.map(num).filter(function(x){return x!=null});if(!v.length)return null;var m=v.reduce(function(p,q){return p+q},0)/v.length;return Math.max.apply(null,v)<=1?m*100:m}
function agg(rows,col,a){a=(a||'').toLowerCase();
  if(a==='compte'||a==='count'||!col)return col?rows.filter(function(r){return r[col]!=null&&r[col]!==''}).length:rows.length;
  if(isRate(a))return rate(rows,col);
  var v=rows.map(function(r){return num(r[col])}).filter(function(x){return x!=null});if(!v.length)return null;
  var s=v.reduce(function(p,q){return p+q},0);
  if(a==='moyenne'||a==='taux')return s/v.length;if(a==='max')return Math.max.apply(null,v);if(a==='min')return Math.min.apply(null,v);return s}
function groupBy(rows,key){var g={};rows.forEach(function(r){var k=typeof key==='function'?key(r):r[key];if(isNull(k))return;k=String(k);(g[k]=g[k]||[]).push(r)});return g}
function ranked(rows,col,a,dim,top){var g=groupBy(rows,dim);return Object.keys(g).map(function(k){return [k,agg(g[k],col,a)]}).filter(function(e){return e[1]!=null}).sort(function(x,y){return y[1]-x[1]}).slice(0,top||8)}
// Périodes : __p = « AAAA-MM » ; regroupement par mois, trimestre ou année
function monday(p){var d=new Date(p.slice(0,10)+'T00:00:00Z'),w=(d.getUTCDay()+6)%7;d.setUTCDate(d.getUTCDate()-w);return d.toISOString().slice(0,10)}
function pkey(p){if(!p)return null;if(gran==='Y')return p.slice(0,4);if(gran==='Q')return p.slice(0,4)+'-T'+(Math.floor((+p.slice(5,7)-1)/3)+1);
  if(gran==='D')return p.length>=10?p.slice(0,10):p;if(gran==='W')return p.length>=10?monday(p):p;return p.slice(0,7)}
function plabel(k){if(gran==='Y')return k;if(gran==='Q')return k.slice(5)+' '+k.slice(2,4);
  if(k.length===10)return (gran==='W'?'sem. ':'')+(+k.slice(8,10))+' '+MOIS[+k.slice(5,7)-1];return MOIS[+k.slice(5,7)-1]+' '+k.slice(2,4)}
var GRAN={D:'Jour',W:'Semaine',M:'Mois',Q:'Trimestre',Y:'Année'};
var navyTip={backgroundColor:NAVY,titleColor:'#fff',bodyColor:'#D7F0FA',padding:10,cornerRadius:10,displayColors:false,titleFont:{weight:'600'}};
var axes={x:{grid:{display:false},border:{display:false},ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:8}},
          y:{grid:{color:'#EEF0F6'},border:{display:false},ticks:{maxTicksLimit:6,callback:function(v){return fmt(v)}}}};

// Filtres : 3 visibles, les autres derrière « Plus de filtres » ; pastilles jusqu'à 5 valeurs, liste au-delà
function buildFilters(){var el=document.getElementById('filters');el.innerHTML='';var F=META.filters||[];if(!F.length){el.style.display='none';return}
  var extra=document.createElement('div');extra.className='extra';extra.hidden=!showMore;
  F.forEach(function(f,idx){var c=f.col,vals=f.values;
    var box=document.createElement('div');box.className='f';box.innerHTML='<span title="'+esc(nice(c))+'">'+esc(short(c))+'</span>';
    if(vals.length<=5){var p=document.createElement('div');p.className='pills';p.setAttribute('role','group');p.setAttribute('aria-label',nice(c));
      ['all'].concat(vals).forEach(function(v){var b=document.createElement('button');b.className='pill';b.type='button';b.textContent=v==='all'?'Tous':label(v);
        b.setAttribute('aria-pressed',String((state[c]||'all')===v));b.onclick=function(){state[c]=v;[].forEach.call(p.children,function(x){x.setAttribute('aria-pressed',String(x===b))});render()};p.appendChild(b)});box.appendChild(p)}
    else{var s=document.createElement('select');s.setAttribute('aria-label',nice(c));s.innerHTML='<option value="all">Tous</option>'+vals.map(function(v){return '<option value="'+esc(v)+'"'+(state[c]===v?' selected':'')+'>'+esc(label(v))+'</option>'}).join('');
      s.onchange=function(){state[c]=s.value;render()};box.appendChild(s)}
    (idx<3?el:extra).appendChild(box)});
  if(F.length>3){el.appendChild(extra);var mb=document.createElement('button');mb.type='button';mb.className='more';mb.setAttribute('aria-expanded',String(showMore));
    mb.textContent=showMore?'Moins de filtres':'Plus de filtres ('+(F.length-3)+')';mb.onclick=function(){showMore=!showMore;buildFilters()};el.appendChild(mb)}}

function renderKpis(rows){document.getElementById('kpis').innerHTML=KPIDEFS.slice(0,5).map(function(k,i){var v=agg(rows,k.colonne,k.agregation),sub='';
  var u=isRate(k.agregation)&&v!=null?' %':'';
  if(k.dimension){var t=ranked(rows,k.colonne,k.agregation,k.dimension,1)[0];if(t)sub='En tête : '+esc(label(t[0]))+'<br>'+fmt(t[1])+u}
  return '<div class="kpi k'+i+'"><div class="n">'+esc(k.nom)+'</div><div class="row"><div class="v">'+fmt(v)+u+'</div><div class="s">'+sub+'</div></div></div>'}).join('')}

function card(id,title,sub,extra){return '<div class="card"><div class="card-h"><div><h2>'+esc(nice(title))+'</h2>'+(sub?'<small>'+esc(sub)+'</small>':'')+'</div>'+(extra||'')+'</div>'+id+'</div>'}
function seg(){return '<div class="seg" role="group" aria-label="Regrouper par">'+(META.grans||['M','Q','Y']).map(function(g){return '<button type="button" data-g="'+g+'" aria-pressed="'+(gran===g)+'">'+GRAN[g]+'</button>'}).join('')+'</div>'}
function canvas(id,label){return '<div class="box"><canvas id="'+id+'" role="img" aria-label="'+esc(label)+'"></canvas></div>'}

// Barres par catégorie avec ligne de moyenne (au-dessus de la moyenne : bleu foncé)
function catBar(id,e,title,u){var mean=e.reduce(function(a,x){return a+x[1]},0)/e.length;return card(canvas(id,title),title,'Sur la période affichée · ligne : moyenne '+fmt(mean)+(u||''))}
function catBarDraw(id,e,u){return function(){var mean=e.reduce(function(a,x){return a+x[1]},0)/e.length,h=e.length>6;
  charts.push(new Chart(document.getElementById(id),{type:'bar',data:{labels:e.map(function(x){return label(x[0])}),
    datasets:[{data:e.map(function(x){return x[1]}),backgroundColor:e.map(function(x){return x[1]>=mean?NAVY:'#10BCD0'}),borderRadius:8,borderSkipped:false,barPercentage:.6,maxBarThickness:56},
              {type:'line',data:e.map(function(){return mean}),borderColor:'#12BF9A',borderDash:[6,4],borderWidth:2,pointRadius:0}]},
    options:{responsive:true,maintainAspectRatio:false,indexAxis:h?'y':'x',plugins:{legend:{display:false},tooltip:Object.assign({filter:function(t){return t.datasetIndex===0},callbacks:{label:function(c){return fmt(h?c.parsed.x:c.parsed.y)+(u||'')}}},navyTip)},
      scales:h?{x:axes.y,y:{grid:{display:false},border:{display:false}}}:axes}}))}}
function renderMain(rows){var M=META.measure,P=META.period,S=META.stack,el=document.getElementById('row2'),html='',todo=[];SHOWN={};
  var gP=P?groupBy(rows,function(r){return pkey(r.__p)}):{};
  if(P&&M&&Object.keys(gP).length<2)P=null;  // un seul point : pas de courbe vide
  if(P&&M){var g=gP,ks=Object.keys(g).sort();
    html+=card(canvas('c_trend','Évolution de '+M.nom),'Évolution de '+M.nom,(M.sous_titre||'').replace('période',GRAN[gran].toLowerCase()),seg());
    todo.push(function(){var cv=document.getElementById('c_trend'),ctx=cv.getContext('2d'),grad=ctx.createLinearGradient(0,0,0,250);grad.addColorStop(0,'rgba(0,145,208,.28)');grad.addColorStop(1,'rgba(16,188,208,0)');
      charts.push(new Chart(cv,{type:'line',data:{labels:ks.map(plabel),datasets:[{data:ks.map(function(k){return agg(g[k],M.colonne,M.agregation)}),borderColor:NAVY,backgroundColor:grad,fill:true,tension:.35,borderWidth:2.5,spanGaps:true,pointRadius:ks.length<=15?3:0,pointHoverRadius:5,pointBackgroundColor:NAVY}]},
        options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:false},tooltip:Object.assign({callbacks:{label:function(c){return M.nom+' : '+fmt(c.parsed.y)+(isRate(M.agregation)?' %':'')}}},navyTip)},scales:axes}}))})}
  if(P&&M&&S){var add=/^(somme|compte|count|sum)$/.test((M.agregation||'').toLowerCase()),pct=isRate(M.agregation)?' %':'';
    if(add){var g2=groupBy(rows,function(r){return pkey(r.__p)}),ks2=Object.keys(g2).sort(),cats=ranked(rows,M.colonne,M.agregation,S,5).map(function(e){return e[0]});
      html+=card(canvas('c_stack',M.nom+' par '+short(S)),M.nom+' par '+short(S),'Par '+GRAN[gran].toLowerCase()+', empilé',seg());
      todo.push(function(){charts.push(new Chart(document.getElementById('c_stack'),{type:'bar',data:{labels:ks2.map(plabel),datasets:cats.map(function(c,i){return {label:label(c),
          data:ks2.map(function(k){return agg(g2[k].filter(function(r){return String(r[S])===c}),M.colonne,M.agregation)||0}),backgroundColor:PASTEL[i%PASTEL.length],borderRadius:6,borderSkipped:false,barPercentage:.55}})},
        options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{usePointStyle:true,pointStyle:'circle',boxWidth:8,padding:14}},tooltip:Object.assign({},navyTip,{displayColors:true})},scales:{x:Object.assign({},axes.x,{stacked:true}),y:Object.assign({},axes.y,{stacked:true})}}}))})}
    // Un taux ou une moyenne ne s'additionne pas et, mois par mois, des courbes par catégorie se croisent sans cesse :
    // on compare les catégories sur toute la période (l'évolution est déjà dans la courbe de gauche)
    else{var e0=ranked(rows,M.colonne,M.agregation,S,8);if(e0.length>=2){SHOWN[M.colonne+'|'+S]=1;html+=catBar('c_stack',e0,M.nom+' par '+short(S),pct);todo.push(catBarDraw('c_stack',e0,pct))}}}
  // Sans période (ex. un export par structure) : l'indicateur principal par catégorie, en barres et en part du total
  var C=META.stack||((META.filters||[])[0]||{}).col;
  if(!P&&M&&M.colonne&&C){var e=ranked(rows,M.colonne,M.agregation,C,12);
    if(e.length>=2){SHOWN[M.colonne+'|'+C]=1;var mean=e.reduce(function(a,x){return a+x[1]},0)/e.length;
      html+=card(canvas('c_cat',M.nom+' par '+short(C)),M.nom+' par '+short(C),'Ligne : moyenne '+fmt(mean));
      todo.push(function(){charts.push(new Chart(document.getElementById('c_cat'),{type:'bar',data:{labels:e.map(function(x){return label(x[0])}),
        datasets:[{data:e.map(function(x){return x[1]}),backgroundColor:e.map(function(x){return x[1]>=mean?NAVY:'#10BCD0'}),borderRadius:8,borderSkipped:false,barPercentage:.6,maxBarThickness:46},
                  {type:'line',data:e.map(function(){return mean}),borderColor:'#12BF9A',borderDash:[6,4],borderWidth:2,pointRadius:0}]},
        options:{responsive:true,maintainAspectRatio:false,indexAxis:e.length>6?'y':'x',plugins:{legend:{display:false},tooltip:Object.assign({filter:function(t){return t.datasetIndex===0},callbacks:{label:function(c){return fmt(e.length>6?c.parsed.x:c.parsed.y)}}},navyTip)},
          scales:e.length>6?{x:axes.y,y:{grid:{display:false},border:{display:false}}}:axes}}))});
      var tot=e.reduce(function(a,x){return a+Math.max(0,x[1])},0)||1;
      if((M.agregation||'').toLowerCase()==='somme'||(M.agregation||'').toLowerCase()==='compte'){
        html+=card('<div class="donut">'+canvas('c_catd','Part de '+M.nom)+'<ul class="legend">'+e.slice(0,8).map(function(x,j){return '<li><i style="background:'+DONUT[j%DONUT.length]+'"></i><span>'+esc(label(x[0]))+'</span><b>'+Math.round(Math.max(0,x[1])/tot*100)+' %</b></li>'}).join('')+'</ul></div>','Part de '+M.nom,'Par '+short(C));
        todo.push(function(){charts.push(new Chart(document.getElementById('c_catd'),{type:'doughnut',data:{labels:e.slice(0,8).map(function(x){return label(x[0])}),datasets:[{data:e.slice(0,8).map(function(x){return Math.max(0,x[1])}),backgroundColor:DONUT,borderColor:'#fff',borderWidth:3,hoverOffset:4}]},options:{responsive:true,maintainAspectRatio:false,cutout:'68%',plugins:{legend:{display:false},tooltip:navyTip}}}))})}}}
  el.innerHTML=html;el.style.display=html?'':'none';el.style.gridTemplateColumns=todo.length===1?'minmax(0,1fr)':'';
  [].forEach.call(el.querySelectorAll('.seg button'),function(b){b.onclick=function(){gran=b.dataset.g;render()}});
  return todo}

function rankList(entries,u){var max=Math.max.apply(null,entries.map(function(e){return e[1]}).concat([0]))||1;
  return '<ul class="rank">'+entries.map(function(e,i){return '<li><span class="lab" title="'+esc(label(e[0]))+'">'+esc(label(e[0]))+'</span><span class="bar"><i style="width:'+Math.max(2,e[1]/max*100)+'%;background:'+BLUES[Math.min(i,BLUES.length-1)]+'"></i></span><b>'+fmt(e[1])+(u||'')+'</b></li>'}).join('')+'</ul>'}

// Ordre naturel des classes : « 5-14 » avant « 15-24 », « < 15 » en premier
function natural(a,b){var x=parseFloat(String(a).replace(/^[^0-9]*/,'')),y=parseFloat(String(b).replace(/^[^0-9]*/,''));if(/^\s*</.test(a))x=-1;if(/^\s*</.test(b))y=-1;return isNaN(x)||isNaN(y)?String(a).localeCompare(String(b)):x-y}
// Pyramide : classes en lignes, les deux catégories (ex. F / M) de part et d'autre
function pyramid(id,rows,col,C,cs){return card(canvas(id,short(col)+' par '+short(C)),short(col)+' par '+short(C),'Pyramide des effectifs : '+esc(label(cs[0]))+' à gauche, '+esc(label(cs[1]))+' à droite')}
function pyramidDraw(id,rows,col,C,cs){return function(){var ks=Object.keys(groupBy(rows,col)).sort(natural).reverse(),cnt=function(k,c){return rows.filter(function(r){return String(r[col])===k&&String(r[C])===c}).length};
  charts.push(new Chart(document.getElementById(id),{type:'bar',data:{labels:ks.map(label),datasets:[{label:label(cs[0]),data:ks.map(function(k){return -cnt(k,cs[0])}),backgroundColor:NAVY,borderRadius:6,borderSkipped:false},
      {label:label(cs[1]),data:ks.map(function(k){return cnt(k,cs[1])}),backgroundColor:'#10BCD0',borderRadius:6,borderSkipped:false}]},
    options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{position:'bottom',labels:{usePointStyle:true,pointStyle:'circle',boxWidth:8,padding:14}},tooltip:Object.assign({},navyTip,{displayColors:true,callbacks:{label:function(c){return c.dataset.label+' : '+fmt(Math.abs(c.parsed.x))}}})},
      scales:{x:{stacked:true,grid:{color:'#EEF1F5'},border:{display:false},ticks:{callback:function(v){return fmt(Math.abs(v))}}},y:{stacked:true,grid:{display:false},border:{display:false}}}}}))}}
function renderCards(rows){var el=document.getElementById('row3'),html='',todo=[],n=0,used={};
  // Effectifs déjà classés par un KPI « compte » (ex. « Patients dépistés par région ») : pas de second classement identique
  KCOUNT={};KPIDEFS.forEach(function(k){if(/^(compte|count|effectif)$/.test((k.agregation||'').toLowerCase())&&k.dimension)KCOUNT[k.dimension]=1});
  function add(h){if(n<6){html+=h;n++}}
  (META.charts||[]).forEach(function(c,i){var col=c.colonne;if(used[col+c.type])return;used[col+c.type]=1;
    var Cc=META.stack||((META.filters||[])[0]||{}).col,numeric=rows.length&&rows.every(function(r){return isNull(r[col])||num(r[col])!=null}),nm=nice(col).toLowerCase();
    // Classes (tranche d'âge, groupe…) ou code chiffré à peu de valeurs : une catégorie, pas une quantité à additionner
    var classes=/tranche|classe|groupe|categ|age/.test(nm)||(numeric&&Object.keys(groupBy(rows,col)).length<=12&&!/nombre|nb|effectif|total|montant|valeur|quantit/.test(nm));
    if(c.type==='bar'&&classes&&Cc&&col!==Cc){var cs=Object.keys(groupBy(rows,Cc)).sort();if(cs.length===2){if(SHOWN['#p|'+col])return;SHOWN['#p|'+col]=1;add(pyramid('c_py'+i,rows,col,Cc,cs));todo.push(pyramidDraw('c_py'+i,rows,col,Cc,cs));return}}
    if(c.type==='bar'&&classes){var ec=ranked(rows,null,'compte',col,15).sort(function(x,y){return natural(x[0],y[0])});if(ec.length<2||SHOWN['#n|'+col]||KCOUNT[col])return;SHOWN['#n|'+col]=1;add(card(rankList(ec),'Effectifs par '+short(col),'Par classe, dans l\'ordre'));return}
    if(c.type==='pie'){var e=ranked(rows,null,'compte',col,8),tot=e.reduce(function(p,q){return p+q[1]},0)||1;if(e.length<2||e.every(function(x){return x[1]===e[0][1]}))return;
      add(card('<div class="donut">'+canvas('c_d'+i,'Répartition par '+col)+'<ul class="legend">'+e.map(function(x,j){return '<li><i style="background:'+DONUT[j%DONUT.length]+'"></i><span>'+esc(label(x[0]))+'</span><b>'+Math.round(x[1]/tot*100)+' %</b></li>'}).join('')+'</ul></div>','Répartition par '+short(col),c.justification));
      todo.push(function(){charts.push(new Chart(document.getElementById('c_d'+i),{type:'doughnut',data:{labels:e.map(function(x){return x[0]}),datasets:[{data:e.map(function(x){return x[1]}),backgroundColor:DONUT,borderColor:'#fff',borderWidth:3,hoverOffset:4}]},options:{responsive:true,maintainAspectRatio:false,cutout:'68%',plugins:{legend:{display:false},tooltip:navyTip}}}))})}
    else if(c.type==='hist'||c.type==='box'||(c.type==='line'&&!META.period)){var v=rows.map(function(r){return num(r[col])}).filter(function(x){return x!=null}).sort(function(a,b){return a-b});if(v.length<10)return;
      var q=function(p){return v[Math.min(v.length-1,Math.floor(p*(v.length-1)))]},med=q(.5),lo=q(.01),hi=q(.99),labels,bins,nm=nice(col).toLowerCase(),
          clin=/cd4/.test(nm)?[[200,'< 200'],[350,'200 – 349'],[500,'350 – 499'],[Infinity,'≥ 500']]
              :/charge.?virale|\bcv\b/.test(nm)?[[50,'< 50 (indétectable)'],[1000,'50 – 999'],[Infinity,'≥ 1 000']]
              :/d[ée]lai|jours|duree|dur[ée]e/.test(nm)&&v[0]>=0?[[1,'0 (même jour)'],[8,'1 – 7 j'],[15,'8 – 14 j'],[31,'15 – 30 j'],[Infinity,'> 30 j']]:null;
      // Seuils de référence (CD4, charge virale, délai en jours) quand la colonne est reconnue
      if(clin){labels=clin.map(function(x){return x[1]});bins=clin.map(function(){return 0});v.forEach(function(x){for(var j=0;j<clin.length;j++)if(x<clin[j][0]){bins[j]++;break}})}
      else
      // Très asymétrique (ex. charge virale : beaucoup de valeurs basses, quelques très hautes) → classes par puissance de 10
      if(v[0]>=0&&hi>20*Math.max(med,1)){var top=Math.ceil(Math.log10(Math.max(v[v.length-1],10))),bot=Math.max(1,Math.floor(Math.log10(Math.max(q(.05),1))));
        labels=[];bins=[];if(v[0]<Math.pow(10,bot)){labels.push('< '+fmt(Math.pow(10,bot)));bins.push(0)}
        for(var e=bot;e<top;e++){labels.push(fmt(Math.pow(10,e))+' – '+fmt(Math.pow(10,e+1)));bins.push(0)}
        v.forEach(function(x){var j=x<Math.pow(10,bot)?0:Math.min(labels.length-1,(v[0]<Math.pow(10,bot)?1:0)+Math.floor(Math.log10(x))-bot);bins[j]++})}
      else{var w=(hi-lo)/10||1;bins=new Array(10).fill(0);v.forEach(function(x){if(x<lo||x>hi)return;bins[Math.min(9,Math.floor((x-lo)/w))]++});labels=bins.map(function(_,j){return fmt(lo+j*w)})}
      var sub='Médiane '+fmt(med)+' · moitié centrale '+fmt(q(.25))+' – '+fmt(q(.75))+' · max '+fmt(v[v.length-1]);
      // Une même valeur très fréquente (hors 0) trahit souvent une imputation (ex. médiane recopiée)
      var fq={},mx=null;v.forEach(function(x){fq[x]=(fq[x]||0)+1;if(mx==null||fq[x]>fq[mx])mx=x});
      if(mx!==0&&fq[mx]/v.length>=.2&&Object.keys(fq).length>10)sub+=' · ⚠ '+Math.round(fq[mx]/v.length*100)+' % des valeurs = '+fmt(mx)+' (valeur imputée ?)';
      add(card(canvas('c_h'+i,'Distribution de '+nice(col)),'Distribution de '+short(col),sub));
      todo.push(function(){charts.push(new Chart(document.getElementById('c_h'+i),{type:'bar',data:{labels:labels,datasets:[{data:bins,backgroundColor:'#10BCD0',hoverBackgroundColor:NAVY,borderRadius:6,borderSkipped:false,categoryPercentage:.9,barPercentage:.92}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:navyTip},scales:axes}}))})}
    else if(c.type==='bar'&&numeric&&Cc&&col!==Cc){if(SHOWN[col+'|'+Cc])return;SHOWN[col+'|'+Cc]=1;var e3=ranked(rows,col,'somme',Cc,8);if(e3.length<2)return;add(card(rankList(e3),short(col)+' par '+short(Cc),c.justification))}
    else if(c.type==='bar'&&!numeric){if(SHOWN['#n|'+col]||KCOUNT[col])return;SHOWN['#n|'+col]=1;var e2=ranked(rows,null,'compte',col,8);if(e2.length<2||e2.every(function(x){return x[1]===e2[0][1]}))return;add(card(rankList(e2),'Effectifs par '+short(col),c.justification))}});
  // Classements par dimension (pas par date : la courbe les montre déjà)
  KPIDEFS.forEach(function(k){if(!k.dimension||k.dimension===META.period||used['k'+k.nom]||SHOWN[k.colonne+'|'+k.dimension])return;used['k'+k.nom]=1;SHOWN[k.colonne+'|'+k.dimension]=1;var e=ranked(rows,k.colonne,k.agregation,k.dimension,8);if(e.length<2)return;
    add(card(rankList(e,isRate(k.agregation)?' %':''),k.nom+' par '+short(k.dimension),'Classement'))});
  el.innerHTML=html||'';return todo}

// « 2025-04 » → « avr. 2025 », « 2025-T1 » → « T1 2025 »
function gkey(k){k=String(k||'');var m=k.match(/^(\d{4})-(\d{2})$/);if(m)return MOIS[+m[2]-1]+' '+m[1];m=k.match(/^(\d{4})-T(\d)$/);return m?'T'+m[2]+' '+m[1]:k}
// Écarts : période analysée et comparaison choisies par l'utilisateur ; tuiles + graphique des écarts
var GAP={p:null,mode:'prev'},gapChart=null;
function gYearBefore(k){var m=String(k).match(/^(\d{4})(.*)$/);return m?(+m[1]-1)+m[2]:null}
function gval(k,rows,key){var K=META.keyind,R=key==null?rows:rows.filter(function(r){return r.__g===key});
  if(k.kind==='count')return R.length;if(k.kind==='agg')return agg(R,k.col,k.agg);
  var s=null;R.forEach(function(r){if(!k.col&&String(r[K.ind]).trim()!==k.libelle)return;var v=num(k.col?r[k.col]:r[K.val]);if(v!=null)s=(s||0)+v});return s}
function renderGaps(rows){var el=document.getElementById('gaps'),K=META.keyind,D=META.domaine,G=META.gaps;if(gapChart){gapChart.destroy();gapChart=null}
  if(!G||!G.length||!K){el.hidden=true;return}
  var P=K.periods||[],cur=K.last,ref=K.prev,mode=GAP.mode;
  if(K.live&&P.length){// Par défaut : dernière période complète (un mois en cours fausserait la comparaison)
    if(GAP.p==null||P.indexOf(GAP.p)<0)GAP.p=(K.partial===P[P.length-1]&&P.length>=3)?P[P.length-2]:P[P.length-1];
    cur=GAP.p;var i=P.indexOf(cur);ref=mode==='n1'?gYearBefore(cur):(i>0?P[i-1]:null);if(ref!=null&&P.indexOf(ref)<0)ref=null;
    G=K.items.map(function(k){var c=gval(k,rows,cur);if(c==null)return null;var p=ref==null?null:gval(k,rows,ref),pts=!!k.pts;
      if(pts){c=c;p=p}var ok=p!=null&&(pts||p!==0),d=ok?c-p:null;
      return {indicateur:k.nom,libelle:k.libelle,derniere:c,precedente:p,pct:ok?(pts?d:d/p*100):null,pts:pts,bon:k.bon,sens:d==null?null:d>0?'hausse':d<0?'baisse':'stable'}}).filter(Boolean)}
  else if(K.live&&!P.length){G=K.items.map(function(k){var c=gval(k,rows,null);return c==null?null:{indicateur:k.nom,libelle:k.libelle,derniere:c,pct:null,pts:!!k.pts,bon:k.bon}}).filter(Boolean);cur=null;ref=null}
  if(!G.length){el.hidden=true;return}el.hidden=false;
  var t=D?'<span class="gaps-dom">'+esc(D.nom)+'</span>':'',hasN1=P.some(function(k){return P.indexOf(gYearBefore(k))>=0});
  var ctl=K.live&&P.length>1?'<div class="gaps-ctl"><label>Période <select id="gapP">'+P.slice().reverse().map(function(k){return '<option value="'+esc(k)+'"'+(k===cur?' selected':'')+'>'+esc(gkey(k))+(k===K.partial?' (en cours)':'')+'</option>'}).join('')+'</select></label>'
    +'<div class="seg" role="group" aria-label="Comparer avec"><button data-m="prev" aria-pressed="'+(mode==='prev')+'">Période précédente</button>'+(hasN1?'<button data-m="n1" aria-pressed="'+(mode==='n1')+'">Même période N-1</button>':'')+'</div></div>':'';
  var sub=cur==null?'pas de période dans le fichier, pas d\'écart':ref==null?esc(gkey(cur))+' · pas de période de comparaison':esc(gkey(cur))+' vs '+esc(gkey(ref));
  var warn=K.partial&&cur===K.partial?'<p class="gaps-warn">⚠ '+esc(gkey(cur))+' est incomplet : les effectifs sont sous-estimés.</p>':'';
  // Vert = évolution souhaitée (ex. baisse des décès), rouge = dégradation
  var tone=function(g){if(g.pct==null)return '';var good=g.pct*(g.bon||1);return g.sens==='stable'?'flat':good>0?'up':'down'};
  var tiles=G.map(function(g){var badge='',prev='';
    if(g.pct!=null){var a=g.sens==='hausse'?'▲':g.sens==='baisse'?'▼':'—';badge='<span class="gap-d '+tone(g)+'">'+a+' '+(g.pct>0?'+':'')+Number(g.pct).toFixed(1).replace('.',',')+(g.pts?' pts':' %')+'</span>';
      prev='<span class="gap-p">'+esc(gkey(ref))+' : '+fmt(g.precedente)+(g.pts?' %':'')+'</span>'}
    return '<div class="gap '+tone(g)+'" title="'+esc(g.libelle||'')+'"><b>'+esc(g.indicateur)+'</b><span class="gap-v">'+fmt(g.derniere)+(g.pts?' %':'')+'</span>'+badge+prev+'</div>'}).join('');
  var withGap=G.filter(function(g){return g.pct!=null});
  el.innerHTML='<div class="gaps-top"><h2 class="gaps-h">Indicateurs clés de suivi '+t+'<span class="gaps-sub">'+sub+'</span></h2>'+ctl+'</div>'+warn
    +'<div class="gaps-body'+(withGap.length>=2?'':' solo')+'"><div class="gaps-grid">'+tiles+'</div>'
    +(withGap.length>=2?'<div class="gaps-chart"><p class="gaps-ct">Écarts vs '+esc(gkey(ref))+'</p><div class="box"><canvas id="c_gap" role="img" aria-label="Écarts par indicateur"></canvas></div></div>':'')+'</div>';
  var sel=document.getElementById('gapP');if(sel)sel.onchange=function(){GAP.p=sel.value;renderGaps(filtered())};
  [].forEach.call(el.querySelectorAll('.gaps-ctl .seg button'),function(b){b.onclick=function(){GAP.mode=b.dataset.m;renderGaps(filtered())}});
  if(withGap.length>=2&&window.Chart){var col={up:'#12BF9A',down:'#E5484D',flat:'#9AA8B6'};
    gapChart=new Chart(document.getElementById('c_gap'),{type:'bar',data:{labels:withGap.map(function(g){return g.indicateur}),datasets:[{data:withGap.map(function(g){return g.pct}),backgroundColor:withGap.map(function(g){return col[tone(g)]}),borderRadius:6,borderSkipped:false,barPercentage:.6,maxBarThickness:22}]},
      options:{responsive:true,maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false},tooltip:Object.assign({},navyTip,{callbacks:{label:function(c){var g=withGap[c.dataIndex];return (g.pct>0?'+':'')+Number(g.pct).toFixed(1).replace('.',',')+(g.pts?' pts':' %')+' ('+fmt(g.precedente)+' → '+fmt(g.derniere)+')'}}})},
        scales:{x:{grid:{color:function(c){return c.tick&&c.tick.value===0?'#9AA8B6':'#EEF1F5'}},border:{display:false},ticks:{callback:function(v){return (v>0?'+':'')+v}}},y:{grid:{display:false},border:{display:false},ticks:{callback:function(v){var l=this.getLabelForValue(v);return l.length>26?l.slice(0,25)+'…':l}}}}}})}}
function render(){charts.forEach(function(c){c.destroy()});charts=[];var rows=filtered();renderKpis(rows);renderGaps(rows);
  var todo=renderMain(rows).concat(renderCards(rows));if(window.Chart)todo.forEach(function(f){f()})}
document.getElementById('objective').textContent=window.OBJECTIVE;
try{if(window.self!==window.top)document.body.classList.add('embedded')}catch(e){document.body.classList.add('embedded')}
// Intégré : la page parente ajuste la hauteur du cadre au contenu (pas d'ascenseur interne)
if(document.body.classList.contains('embedded')){var lastH=0,sendH=function(){var h=Math.ceil(document.body.getBoundingClientRect().height);if(Math.abs(h-lastH)>2){lastH=h;parent.postMessage({kldvHeight:h},'*')}};
  if(window.ResizeObserver)new ResizeObserver(sendH).observe(document.body);window.addEventListener('load',sendH);setTimeout(sendH,300)}
if(META.range){var r=document.getElementById('range');r.hidden=false;r.querySelector('span').textContent=META.range}
buildFilters();render();
</script>
</body></html>
"""


def _render_html(objective: str, data_json: str, kpi_defs: list, filter_cols: list, meta: dict | None = None) -> str:
    """Tableau de bord interactif (style « Analytics » : tuiles KPI pastel, accent marine) ; recalcul côté client."""
    obj = (objective or "Analyse de données").replace("\\", "\\\\").replace('"', '\\"').replace("</", "<\\/")
    data_safe = data_json.replace("</", "<\\/")
    # Image Docker : la console est dans /app et hermes_da dans /HERMES_DA, donc config.CONSOLE_DIR
    # (/KLDataViz_console) n'existe pas ; on cherche aussi à côté du serveur lancé.
    chartjs = ""
    main_dir = os.path.dirname(os.path.abspath(getattr(sys.modules.get("__main__"), "__file__", "") or "."))
    for d in (config.CONSOLE_DIR, Path(main_dir), Path.cwd()):
        chartjs_path = Path(d) / "chart.umd.min.js"
        if chartjs_path.is_file():
            try:
                chartjs = chartjs_path.read_text(encoding="utf-8", errors="ignore").replace("</", "<\\/")
                break
            except Exception:
                chartjs = ""
    meta_json = json.dumps(_json_safe(meta or {}), ensure_ascii=False).replace("</", "<\\/")
    return (TEMPLATE
            .replace("__CHARTJS__", chartjs)
            .replace("__DATA__", data_safe)
            .replace("__KPIDEFS__", json.dumps(kpi_defs, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__FILTERCOLS__", json.dumps(filter_cols, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__META__", meta_json)
            .replace("__OBJECTIVE__", obj))


def generate_dashboard(df, objective: str = "", job_id: str | None = None, llm=None) -> dict:
    """Génère un tableau de bord (KPI + HTML interactif) depuis un DataFrame.

    Réutilisable par l'orchestrateur HERMES_DA ET par la console KLDataViz (bouton Dashboard).
    Retourne : html (le tableau de bord autonome), path (fichier écrit si job_id), kpis, charts, ia.
    """
    profile = _profile(df)
    profile_json = json.dumps(profile, ensure_ascii=False, default=str)[:20000]
    llm_spec = _llm_dashboard(profile_json, llm)
    ia = llm_spec is not None
    spec = llm_spec or _rule_dashboard(profile)

    cols = set(df.columns)
    kpis = []
    for k in spec.get("kpis", [])[:8]:
        col = k.get("colonne")
        if col is not None and col not in cols:
            continue
        v = _agg(df, col, k.get("agregation", "compte"))
        if v is None:  # incalculable sur ces données (ex. « taux » sur un texte) : pas de tuile « — » ni de courbe vide
            continue
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

    if not kpis and ia:  # aucune proposition de l'IA n'est calculable : repli par règles
        return generate_dashboard(df, objective, job_id, llm=False)

    charts_count = 0
    for g in spec.get("graphiques", [])[:8]:
        col = g.get("colonne")
        if col is None or col not in cols:
            continue
        if _chart_data(df, col, g.get("type", "bar")).get("labels"):
            charts_count += 1
    charts_count += sum(1 for k in kpis if k.get("breakdown"))

    kpi_defs = [{"nom": k["nom"], "agregation": k["agregation"], "colonne": k["colonne"],
                 "dimension": k.get("dimension")} for k in kpis]
    filter_cols = [str(c) for c in df.columns if 1 < int(df[c].nunique(dropna=True)) <= 30]
    period_col, period_series = _period(df)
    meta = _meta(df, kpi_defs, spec, filter_cols, period_col, period_series)
    domaine = _detect_domaine(df)
    ic, vc, kis = _keyind(df, domaine)
    keys = _period_key(df, period_col, period_series) if kis else None
    gaps = _gaps(df, keys, ic, vc, kis)
    if not kis and period_series is not None:  # une ligne par patient : comparaison des deux derniers mois
        kis = _patient_items(df, kpi_defs, domaine, period_col)
        keys = _period_key(df, period_col, period_series)
        gaps = _patient_gaps(df, keys, kis)
    if domaine:
        meta["domaine"] = {"cle": domaine["cle"], "nom": domaine["nom"]}
    data = df.assign(__p=period_series) if period_series is not None else df
    if gaps:
        # Recalculés dans la page selon les filtres (sauf si l'échantillon de 6000 lignes fausserait les sommes)
        meta["gaps"] = _json_safe(gaps)
        periods = sorted(str(k) for k in keys.dropna().unique()) if keys is not None else []
        meta["keyind"] = {"ind": None if ic is None else str(ic), "val": None if vc is None else str(vc), "items": kis,
                          "last": gaps[0]["periode"], "prev": gaps[0]["periode_prec"], "live": len(data) <= 6000,
                          "periods": periods, "partial": _partial_month(period_series) if period_series is not None else None}
        if keys is not None:
            data = data.assign(__g=keys)
    sample = data if len(data) <= 6000 else data.sample(6000, random_state=0)
    data_json = json.dumps(sample.to_dict(orient="records"), ensure_ascii=False, default=str)

    html = _render_html(objective, data_json, kpi_defs, filter_cols, meta)
    path = None
    if job_id:
        config.ensure_dirs()
        path = config.ARTIFACTS_DIR / f"{job_id}__dashboard.html"
        path.write_text(html, encoding="utf-8")

    return {"html": html, "path": str(path) if path else None,
            "kpis": _json_safe(kpis), "charts": charts_count, "ia": ia,
            "filter_cols": filter_cols, "kpi_defs": kpi_defs,
            "domaine": _json_safe(domaine), "gaps": _json_safe(gaps)}


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
    result = generate_dashboard(df, objective, job_id=env.job_id)

    artifacts.write_contract(env.job_id, "agent11_dashboard",
                             {"kpis": result["kpis"], "charts": result["charts"], "ia": result["ia"]})

    return {
        "status": "DONE",
        "result_reference": {"dashboard": result["path"], "kpis": result["kpis"],
                             "charts": result["charts"], "ia": result["ia"]},
        "warnings": [] if result["ia"] else ["LLM indisponible — tableau de bord généré par règles."],
        "errors": [],
    }
