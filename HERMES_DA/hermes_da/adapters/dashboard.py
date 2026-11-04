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
from . import cube, rendu_composition
from .quality import _load_dataset

SYSTEM = """Tu es le DATA VISUALIZATION & DASHBOARD AGENT d'Adifa’ (santé publique, BI).
On te donne le PROFIL d'un dataset (colonnes, types, valeurs d'exemple, répartition)
et un OBJECTIF MÉTIER. Propose les KPI les plus pertinents et les graphiques adaptés.
Réponds en JSON STRICT, sans aucun texte hors du JSON, exactement sous cette forme :
{"kpis":[{"nom":"...","agregation":"somme|moyenne|mediane|compte|max|min|taux","colonne":"...","dimension":"...","justification":"..."}],"graphiques":[{"colonne":"...","type":"bar|line|pie|hist|scatter|box","justification":"..."}]}
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
- « somme », « moyenne », « mediane », « max », « min » UNIQUEMENT sur des colonnes chiffrées.
- JAMAIS « somme » d'un prix unitaire, d'un taux, d'une note, d'un âge ou d'une autre grandeur « par unité » :
  « moyenne » (ou « mediane » si les valeurs sont très dispersées, ex. délais, charges virales, montants).
- Une année, un mois ou un numéro de période n'est JAMAIS un indicateur (ni somme ni moyenne) : c'est un axe.
- Un stock (population, effectif présent, file active, superficie, solde) ne s'additionne pas d'une période à
  l'autre : « somme » est alors ramenée automatiquement à la dernière période.
- Pas de camembert (« pie ») pour une période ni pour plus de 6 catégories."""


def _parse_spec(content: str) -> dict | None:
    """JSON de l'IA → dict. Répare les erreurs fréquentes des modèles rapides (virgule finale,
    « } » oubliée avant « ] ») ; None si toujours illisible."""
    start, end = (content or "").find("{"), (content or "").rfind("}")
    if start < 0 or end <= start:
        return None
    raw = content[start:end + 1]
    for fix in (lambda t: t, lambda t: re.sub(r",\s*([\]}])", r"\1", t),
                lambda t: re.sub(r'"\s*\]', '"}]', re.sub(r",\s*([\]}])", r"\1", t))):
        try:
            spec = json.loads(fix(raw))
            return spec if isinstance(spec, dict) and spec.get("kpis") else None
        except Exception:
            continue
    return None


def _llm_dashboard(profile_json: str, llm=None) -> dict | None:
    """Choisit KPI et graphiques via le LLM, ou None si indisponible.

    `llm(messages, max_tokens) -> texte` : accès à l'IA fourni par l'appelant (la console en ligne, où
    llm_config.json n'existe pas et où la clé vient de l'environnement). Sans lui : llm_config.json local."""
    if llm is False:  # IA explicitement écartée (repli par règles)
        return None
    if llm is not None:
        for _ in range(2):  # un JSON illisible (rare) : on redemande une fois avant le repli par règles
            try:
                spec = _parse_spec(llm([{"role": "system", "content": SYSTEM},
                                        {"role": "user", "content": profile_json}], 8000) or "")
            except Exception:
                spec = None
            if spec:
                return spec
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
        return _parse_spec(content)
    except Exception:
        return None


def _resolve_llm(llm):
    """Transforme le paramètre `llm` en callable (messages, max_tokens) -> texte, ou None si IA indisponible.

    `llm` = False → pas d'IA ; None → clé dans llm_config.json ; callable → utilisé tel quel."""
    if llm is False:
        return None
    if callable(llm):
        return llm
    cfg_path = os.path.join(config.CONSOLE_DIR, "llm_config.json")
    if not os.path.isfile(cfg_path):
        return None
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        key = cfg.get("api_key", "")
        if not key or "VOTRE_CLE" in str(key):
            return None
        def call(messages, max_tokens):
            body = json.dumps({"model": cfg.get("model", "deepseek-v4-pro"), "messages": messages,
                               "max_tokens": max_tokens, "temperature": 0}).encode("utf-8")
            req = urllib.request.Request(cfg["base_url"], data=body, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {key}"})
            r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
            return (r.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return call
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
                info["mediane"] = round(float(s.median()), 2)
            except Exception:
                pass
        cols.append(info)
    return {"lignes": int(len(df)), "colonnes": cols}


_ID_RE = re.compile(r"(^|[\s_])(id|code|n°|no|num|numero|identifiant|matricule)([\s_]|$)", re.I)
_MESURE_RE = re.compile(r"delai|duree|(^|\s)age\b|cd4|cv\b|charge|copies|score|poids|taille|taux|pourcent|moyenne|imc|"
                        r"prix|tarif|unitaire|\bpar\b|_kg\b|\bkg\b|pct|ratio|note|humidite|temperature|anciennete|"
                        r"latitude|longitude|densite|rendement", re.I)
# Colonnes de temps chiffrées (année, mois…) : un axe, jamais un indicateur (« moyenne des années » n'a pas de sens)
_TEMPS_RE = re.compile(r"^(annee|an|year|mois|month|trimestre|quarter|semaine|week|jour|day|periode|period)s?\b", re.I)
_VOLUME_RE = re.compile(r"nombre|\bnb\b|effectif|total|volume|montant|quantite|valeur|cout|recette|depense|chiffre|"
                        r"affaires|marge|revenu|vente|benefice|naissance|deces|cas\b|visite|consultation|admission|"
                        r"nuit|livraison|tonnage|production|salaire", re.I)


def _joli(name) -> str:
    from hermes_da.libelles import joli
    return joli(name)


def _joli_min(name) -> str:
    from hermes_da.libelles import joli_min
    return joli_min(name)


def _rule_dashboard(profile: dict) -> dict:
    """Repli déterministe quand l'IA est indisponible : effectif, taux sur les colonnes Oui/Non,
    moyenne pour les mesures (délai, âge, CD4…), total pour les volumes, comparaison par région/sexe."""
    kpis, graphiques = [], []
    cols = profile["colonnes"]
    name = lambda c: _norm(c["colonne"])  # noqa: E731
    is_num = lambda c: bool(re.match(r"int|float", c["type"]))  # noqa: E731
    is_id = lambda c: bool(_ID_RE.search(name(c))) or (not is_num(c) and (c.get("n_distinct") or 0) >= 0.95 * max(profile["lignes"], 1) > 20)  # noqa: E731
    is_date = lambda c: "datetime" in c["type"] or re.search(r"\bdate\b", name(c))  # noqa: E731
    yesno = [c for c in cols if not is_num(c) and c.get("exemples") and (c.get("n_distinct") or 0) <= 2
             and all(_norm(x) in _YES | _NO for x in c["exemples"])]
    cats = [c for c in cols if not is_num(c) and not is_id(c) and not is_date(c) and c not in yesno
            and 2 <= (c.get("n_distinct") or 0) <= 30 and not _TEMPS_RE.search(name(c))
            and not re.search(r"period|periode|mois|trimestre", name(c))]
    # « part de … (%) » : une part d'un total par ligne — ni sa somme ni sa moyenne ne sont des indicateurs
    nums = [c for c in cols if is_num(c) and not is_id(c) and not _TEMPS_RE.search(name(c))
            and not re.search(r"(^|\s)part(\s|$)", name(c))]
    dim = next((c["colonne"] for c in sorted(cats, key=lambda c: 0 if re.search(r"region|district|sexe|genre", name(c)) else 1)), None)
    periodique = any(_TEMPS_RE.search(name(c)) or re.search(r"periode|period|mois|date", name(c)) for c in cols)
    is_vol = lambda c: bool(re.match(r"(nombre|nb|effectif|total|volume)\b", name(c))) or (  # noqa: E731
        bool(_VOLUME_RE.search(name(c))) and not _MESURE_RE.search(name(c))) or (
        # rapport périodique (centre × mois) : une colonne d'entiers ≥ 0 sans unité de mesure est un comptage (CPN1)
        periodique and "int" in c["type"] and (c.get("min") or 0) >= 0 and not _MESURE_RE.search(name(c))
        and not _is_stock(name(c)))
    # Données déjà agrégées (« Nombre de … », « Total … ») : compter les lignes n'a pas de sens. Un fichier de
    # transactions ou de personnes avec des montants (salaires, ventes) garde son effectif.
    # (une colonne de stock comme « population » signale aussi un fichier par strate, pas par individu)
    agreg = sum(1 for c in nums if re.match(r"(nombre|nb|effectif|total)\b", name(c))) >= 2 or any(_is_stock(name(c)) for c in nums)
    if not agreg:
        kpis.append({"nom": "Effectif total", "agregation": "compte", "colonne": None, "dimension": dim,
                     "justification": "nombre de lignes du fichier"})
    for c in yesno[:2]:
        kpis.append({"nom": f"Taux « {_joli_min(c['colonne'])} »", "agregation": "taux", "colonne": c["colonne"],
                     "dimension": dim, "justification": "part de « Oui »"})
    descr = re.compile(r"(^|\s)(age|anciennete|poids|taille|imc|latitude|longitude)\b")
    nums = sorted(nums, key=lambda c: 0 if is_vol(c) or _is_stock(name(c)) else 2 if descr.search(name(c)) else 1)
    for c in nums:
        if len(kpis) >= 5:
            break
        lib = re.sub(r"^\s*(nombre|nb)\s+(de |d')\s*", "", _joli(c["colonne"]), flags=re.I)
        if _is_stock(name(c)) and not _MESURE_RE.search(name(c)):  # « part de la population (%) » = un taux
            # Stock (population, superficie…) : jamais additionné d'une période à l'autre → « dernier » plus loin
            kpis.append({"nom": lib[:1].upper() + lib[1:], "agregation": "somme", "colonne": c["colonne"],
                         "dimension": dim, "justification": "stock : valeur de la dernière période"})
            continue
        vol = is_vol(c)
        skew = (not vol and c.get("moyenne") is not None and c.get("mediane") is not None
                and abs(c["moyenne"]) > 1.5 * abs(c["mediane"]) + 1e-9)
        agg = "somme" if vol else "mediane" if skew else "moyenne"
        nom = "Total" if vol else "Médiane" if skew else "Moyenne"
        lib = lib if lib[:2].isupper() else lib[:1].lower() + lib[1:]
        kpis.append({"nom": f"{nom} : {lib}", "agregation": agg, "colonne": c["colonne"], "dimension": dim,
                     "justification": "volume" if vol else
                     "valeurs très dispersées : la médiane résume mieux que la moyenne" if skew else "mesure moyenne"})
    for c in cats[:3]:
        graphiques.append({"colonne": c["colonne"], "type": "pie" if (c.get("n_distinct") or 0) <= 5 else "bar",
                           "justification": "répartition par catégorie"})
    for c in [c for c in nums if not is_vol(c)][:2]:
        graphiques.append({"colonne": c["colonne"], "type": "hist", "justification": "distribution de la mesure"})
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
    if a in ("mediane", "médiane", "median"):
        return round(float(s.median()), 2) if s.notna().any() else None
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
    elif a in ("mediane", "médiane", "median"):
        g = df.assign(_v=s).groupby(dimension)["_v"].median()
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


_STOCK_RE = re.compile(r"population|stock|solde|encours|effectif total|superficie|capacite|parc|cheptel|habitants|"
                       r"charge virale|cv supprim|tx pvls|tx curr|file active|actuellement")
_FLUX_RE = re.compile(r"nouve|initi|deces|depist")


def _is_stock(n: str) -> bool:
    """Stock (valeur à une date : population, file active, PVVIH avec CV supprimée) : jamais additionné dans le temps."""
    return bool(_STOCK_RE.search(n)) and not _FLUX_RE.search(n)


def _plabel(k: str, yearly: bool = False) -> str:
    """« 2024-01 » d'une colonne année → « 2024 » ; « 2025-09 » → « sept. 2025 »."""
    return k[:4] if yearly else (f"{_MOIS_COURTS[int(k[5:7]) - 1]} {k[:4]}" if len(k) >= 7 else k)


def _period(df):
    """Première colonne de période (dates, « 2025-04 », « Avril 2025 ») → (nom, série « AAAA-MM ») ; (None, None) sinon."""
    import pandas as pd
    idlike = re.compile(r"(^|[\s_.])(n°|no|num(ero|éro)?|id|code|matricule|ref|référence|reference|labno|datim)($|[\s_.])", re.I)
    dateword = re.compile(r"\b(date|periode|quarter|trimestre|mois|annee|an|year|month)\b", re.I)
    attribut = re.compile(r"naissance|birth|embauche|hire|creation|ouverture|expiration|peremption")
    for c in sorted(df.columns, key=lambda c: 1 if attribut.search(_norm(c)) else 0):
        n = _norm(c)
        if attribut.search(n) and any(re.search(r"date|mois|periode|annee", _norm(o)) and not attribut.search(_norm(o))
                                      for o in df.columns):
            continue  # une autre colonne de temps existe : la date de naissance/d'embauche n'est pas la période suivie
        # une colonne « date … » (même « date de référence », « date_debut_pec ») n'est jamais un identifiant
        if not dateword.search(n) and idlike.search(re.sub(r"\(.*?\)", "", n)):
            continue  # identifiant (code site, labno, code patient…) : jamais une période
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_datetime64_any_dtype(s):
            v = pd.to_numeric(s, errors="coerce").dropna()
            if re.search(r"(^|[\s_])(annee|an|year|exercice)([\s_]|$)", _norm(c)) and len(v) >= 2 \
                    and (v % 1 == 0).all() and v.between(1900, 2100).all() and v.nunique() >= 2:
                return str(c), s.map(lambda x: f"{int(x)}-01" if x == x and x is not None else None)
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
        # Dates ISO « 2025-08-01 » (fichiers nettoyés, exports) : jamais lues « jour en premier »
        # (sinon 2025-08-01 devient le 8 janvier et tous les mois s'écrasent sur janvier)
        iso = sample.str.match(r"^\d{4}-\d{2}-\d{2}").mean() >= 0.9
        try:
            dt = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=not iso)
        except Exception:
            continue
        if dt.notna().mean() >= 0.9 and sample.str.contains(r"\d{4}|\d{1,2}[/-]\d{1,2}", regex=True).mean() >= 0.9:
            full = pd.to_datetime(s.astype(str), errors="coerce", format="mixed", dayfirst=not iso)
            return str(c), full.dt.strftime("%Y-%m-%d")
    return None, None


def _meta(df, kpis, spec, filter_cols, period_col, period_series):
    """Ce que le gabarit dessine : mesure principale, période, catégories empilées, graphiques choisis."""
    import pandas as pd
    cols = set(df.columns)
    labels = {"somme": "Somme", "moyenne": "Moyenne", "mediane": "Médiane", "compte": "Nombre", "taux": "Taux moyen",
              "max": "Maximum", "min": "Minimum"}
    measure = None
    for k in kpis:
        col = k.get("colonne")
        if col in cols and pd.api.types.is_numeric_dtype(pd.to_numeric(df[col], errors="coerce")) \
                and (k.get("agregation") or "").lower() in ("somme", "moyenne", "mediane", "taux", "max", "min", "dernier"):
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
    def _outcome(f):  # colonne Oui/Non = un résultat à mesurer, pas une façon de découper la population
        return all(_norm(v) in _YES | _NO for v in f["values"])

    def _rank(f):
        n = _norm(f["col"])
        return 0 if re.search(r"\b(sexe|genre|gender|sex)\b", n) else 1 if re.search(r"region|district|age|tranche|milieu", n) else 2
    cands = sorted((f for f in filters if 2 <= len(f["values"]) <= 5 and not _outcome(f)), key=_rank)
    stack = cands[0]["col"] if cands else None
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
                    grans, gran = ["M", "Q", "Y"], ("Y" if span > 3650 else "M")
            elif period_col in df.columns and pd.api.types.is_numeric_dtype(df[period_col]):  # années
                rng, gran, grans = f"{ks[0][:4]} – {ks[-1][:4]}", "Y", ["Y"]
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


# Mots choisis à la main : un seul suffit à reconnaître le domaine. Ceux d'un dictionnaire importé viennent de libellés.
_MOTS_SURS = {m for d in DOMAINES.values() for m in d["mots"]}
_MOTS_PATIENT = _MOTS_SURS | {"patient", "patients", "beneficiaire", "beneficiaires", "dossier"}


def _preuve_suffisante(trouves) -> bool:
    """Un mot de dictionnaire isolé ne prouve rien : deux mots, une expression ou un mot sûr, oui."""
    return len(trouves) >= 2 or any(" " in m or m in _MOTS_SURS for m in trouves)


def _detect_domaine(df, catalogue: dict | None = None) -> dict | None:
    """Domaine/programme le plus représenté dans les noms de colonnes et les libellés d'indicateurs (mots entiers).

    `catalogue` : domaines tirés d'un dictionnaire d'indicateurs importé (Mode B) ; normes intégrées sinon (Mode A)."""
    words = " " + _norm(" ".join(str(c) for c in df.columns))
    ic = _col(df, _IND_COLS)
    if ic is not None:
        words += " " + _norm(" ".join(df[ic].dropna().astype(str).unique()[:300]))
    # Fichiers « une ligne par patient » (liste) : le domaine se lit aussi dans les VALEURS des
    # colonnes catégorielles (motif, régime, statut…), pas seulement dans les noms de colonnes.
    import pandas as pd
    for c in df.columns:
        if c == ic or pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_datetime64_any_dtype(df[c]):
            continue
        vals = df[c].dropna().astype(str).unique()
        if len(vals) > 60:
            continue
        words += " " + _norm(" ".join(vals[:60]))
    words += " "
    cat = catalogue or DOMAINES
    best, score = None, 0
    for cle, dom in cat.items():
        trouves = [m for m in dom["mots"] if " " + m + " " in words]
        if catalogue and not _preuve_suffisante(trouves):
            continue
        n = sum(words.count(" " + m + " ") for m in trouves)
        if n > score:
            best, score = cle, n
    if not best:
        return None
    return {"cle": best, "nom": cat[best]["nom"], "indicateurs": cat[best]["indicateurs"]}


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
        d = pd.to_datetime(str(v), errors="coerce", dayfirst=not re.match(r"^\d{4}-\d{2}", str(v)))
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
_MAUVAIS = ("deces", "decede", "perdu", "abandon", "arret", "echec", "non supprim", "1000", "delai", "attente", "rupture",
            "annul", "absence", "absenteisme", "impaye", "plainte", "reclamation", "incident",
            "sans contact", "interruption", "tx ml", "tx iit", "tx died")  # TX_ML : patients perdus de vue (MER)
# Mots entiers seulement : « départ » mais pas « département », « panne » mais pas « panneau »
_MAUVAIS_MOTS = {"depart", "departs", "panne", "pannes", "retour", "retours"}


def _bon(nom) -> int:
    """Sens souhaité d'un indicateur d'après son nom : -1 si une hausse est une dégradation (décès, délai…)."""
    n = _norm(nom)
    return -1 if any(m in n for m in _MAUVAIS) or _MAUVAIS_MOTS & set(n.split()) else 1


def _partial_month(period_series):
    """Dernier mois incomplet → sa clé « AAAA-MM ». Il s'arrête avant les 5 derniers jours du mois et compte moins de
    jours saisis qu'un mois habituel : un fichier daté du 1er ou du 15 de chaque mois n'a pas de mois incomplet."""
    import calendar
    from statistics import median
    days = sorted(v for v in period_series.dropna().unique() if isinstance(v, str) and len(v) == 10)
    if not days:
        return None
    y, m, d = (int(x) for x in days[-1].split("-"))
    if d > calendar.monthrange(y, m)[1] - 5:
        return None
    dernier = days[-1][:7]
    par_mois = {}
    for j in days:
        par_mois[j[:7]] = par_mois.get(j[:7], 0) + 1
    avant = [n for k, n in par_mois.items() if k != dernier]
    return dernier if len(avant) < 2 or par_mois[dernier] < median(avant) else None  # sans historique : pas de comparaison


def _patient_items(df, kpi_defs, domaine, period_col, aggregated=False):
    # Données déjà agrégées (DHIS2, export converti) : une ligne = une unité × une période, pas un patient →
    # pas de compteur de lignes, mais les sommes du mois restent comparées au mois précédent
    patients = domaine and _MOTS_PATIENT & set(_norm(" ".join(str(c) for c in df.columns)).split())
    items = [] if aggregated else [{"nom": "Nouveaux patients" if patients else "Nouveaux enregistrements",
                                    "kind": "count", "bon": 1, "libelle": f"Lignes par mois de « {period_col} »"}]
    kept = ("taux", "rate", "pct", "proportion", "moyenne", "mean", "mediane") + (("somme", "sum") if aggregated else ())
    for k in kpi_defs:
        a, col = (k.get("agregation") or "").lower(), k.get("colonne")
        if col in df.columns and col != period_col and a in kept:
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
.chip[hidden]{display:none}button.chip{font:inherit;font-size:12.5px;cursor:pointer}button.chip:hover{border-color:var(--navy)}
button.chip.on{background:var(--navy);border-color:var(--navy);color:#fff}
.rangew{position:relative}.perpop{position:absolute;right:0;top:calc(100% + 6px);z-index:30;background:var(--card);border:1px solid var(--line);
  border-radius:14px;box-shadow:0 10px 30px rgba(22,33,62,.14);padding:12px 14px;display:grid;gap:8px;min-width:250px}
.perpop .acts{display:flex;justify-content:space-between;gap:8px}
.perpop{max-width:calc(100vw - 32px)}@media (max-width:600px){body:not(.embedded) .perpop{left:0;right:auto}}
.perpop .acts button{border:0;background:none;font:inherit;font-size:12px;color:var(--navy);font-weight:600;cursor:pointer;padding:4px 0}
.filters{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px 20px;margin-bottom:16px}
.more{border:0;background:none;color:var(--navy);font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;min-height:32px;padding:0 4px;text-decoration:underline;text-underline-offset:3px}
.extra{display:contents}.extra[hidden]{display:none}
.f{display:flex;flex-direction:column;gap:6px}
.per{display:flex;align-items:center;gap:6px}.per i{font-style:normal;color:var(--muted);font-size:12px}
.f > span{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;color:var(--muted);max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pills{display:flex;flex-wrap:wrap;gap:6px}
.pill{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:6px 12px;font:inherit;font-size:12.5px;cursor:pointer;min-height:32px;transition:background .15s,color .15s,border-color .15s}
.pill:hover{border-color:var(--navy)}
.pill[aria-pressed="true"]{background:var(--navy);border-color:var(--navy);color:#fff}
.pill:focus-visible,select:focus-visible,.seg button:focus-visible,.more:focus-visible{outline:3px solid #7FD3EE;outline-offset:2px}
select{appearance:none;border:1px solid var(--line);background:var(--card) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%235B6478' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E") no-repeat right 12px center;border-radius:999px;padding:7px 34px 7px 14px;font:inherit;font-size:12.5px;color:var(--ink);min-height:32px;cursor:pointer;min-width:150px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:20px}
.kpi{border-radius:var(--radius);padding:14px 16px 16px;min-height:104px;display:flex;flex-direction:column;justify-content:space-between;gap:10px}
.kpi .n{font-size:12.5px;font-weight:500;line-height:1.3}
.kpi .info{position:relative;display:inline-grid;place-items:center;width:15px;height:15px;border-radius:50%;font:700 10px/1 Georgia,serif;
  font-style:italic;border:1px solid currentColor;opacity:.65;cursor:help;margin-left:4px;vertical-align:1px}
.kpi .info:hover,.kpi .info:focus-visible{opacity:1;outline:none}
.kpi .info .tip{display:none;position:absolute;z-index:20;left:50%;top:22px;transform:translateX(-50%);width:260px;padding:10px 12px;
  border-radius:10px;background:#0E2A47;color:#fff;font:400 12px/1.45 'Fira Sans',system-ui,-apple-system,'Segoe UI',sans-serif;font-style:normal;text-align:left;box-shadow:0 10px 24px rgba(0,0,0,.22)}
.kpi .info:hover .tip,.kpi .info:focus .tip{display:block}
.kpis .kpi:last-child .info .tip{left:auto;right:-8px;transform:none}
.kpi .row{display:flex;align-items:flex-end;justify-content:space-between;gap:8px}
.kpi .v{font-size:24px;font-weight:700;letter-spacing:-.3px;line-height:1;white-space:nowrap}
.kpi .s{font-size:11px;text-align:right;line-height:1.3;max-width:55%}
.kpi.k0{background:linear-gradient(135deg,var(--navy),var(--navy-2));color:#fff}.kpi.k0 .s{color:#BFE3F5}
.kpi.k1{background:var(--t1)}.kpi.k2{background:var(--t2)}.kpi.k3{background:var(--t3)}.kpi.k4{background:var(--t4)}.kpi.k5{background:var(--t5)}
.kpi:not(.k0) .s{color:var(--muted)}
.row2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin-bottom:20px}
.row3{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:18px;margin-bottom:20px}.row3:empty,.row2:empty{display:none}
.card{background:var(--card);border-radius:var(--radius);padding:16px 18px;box-shadow:0 1px 2px rgba(22,33,62,.04);min-width:0}
.card-h{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.card-h > div:first-child{min-width:0;flex:1 1 180px}
.card-h h2{font-size:15px;font-weight:600;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-h small{display:block;color:var(--muted);font-size:11.5px;font-weight:400;margin-top:2px}
.seg{display:inline-flex;background:#F1F3F9;border-radius:999px;padding:3px;gap:2px}
.seg button{border:0;background:none;font:inherit;font-size:12px;color:var(--muted);padding:5px 11px;border-radius:999px;cursor:pointer;min-height:28px}
.seg button[aria-pressed="true"]{background:var(--navy);color:#fff}
.box{position:relative;height:250px}
.donut{display:flex;flex-direction:column;align-items:center;gap:14px}
.donut .box{height:220px;width:100%;max-width:260px}
.donut .legend{display:flex;flex-wrap:wrap;justify-content:center;gap:8px 18px;width:100%}
.donut .legend li{display:flex;gap:6px;align-items:center}
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
@media (max-width:900px){.gaps-body{grid-template-columns:minmax(0,1fr)}}
@media (max-width:560px){.gaps-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}}
/* Affiché dans l'application (cadre) : le titre est déjà sur la page */
.embedded body,body.embedded{padding:4px 2px 16px;background:transparent}
body.embedded{min-height:0;overflow:hidden}html:has(body.embedded){overflow:hidden}body.embedded .top .title{display:none}body.embedded .top{justify-content:flex-end;margin-bottom:8px}
@media (max-width:560px){body{padding:16px}.donut .box{height:200px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
/* Écarts : même langage que le reste (carte blanche, tuiles pastel comme les KPI, palette bleu / cyan / vert d'eau) */
.gaps{margin-bottom:20px}.gaps[hidden]{display:none}
.gaps-ctl{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.gaps-ctl select{min-width:140px}
.gaps-note{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--navy);background:var(--t5);border-radius:999px;padding:4px 12px;margin:0 0 12px}
.gaps-body{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:16px;align-items:stretch}.gaps-body.solo{grid-template-columns:minmax(0,1fr)}
.gaps-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;align-content:start}
.gap.kpi{min-height:112px}.gap .v small{font-size:14px;font-weight:600;margin-left:2px}
.gap-d{display:inline-flex;align-items:center;gap:3px;font-size:11.5px;font-weight:600;padding:2px 9px;border-radius:999px;background:#fff;white-space:nowrap}
.gap-d.up{color:#0B7A62}.gap-d.down{color:#B4232C}.gap-d.flat{color:var(--muted)}
.sv .gap-d.up,.sv-r .gap-d.up{background:#E3F7F1}.sv .gap-d.down,.sv-r .gap-d.down{background:#FDECEA}.sv .gap-d.flat,.sv-r .gap-d.flat{background:#F1F5FA}
.gap-p{display:block;font-size:11px;color:var(--muted);margin-top:4px}
.gaps-chart{display:flex;flex-direction:column;min-width:0}.gaps-chart .box{height:auto;flex:1;min-height:200px}
.gaps-ct{font-size:11.5px;color:var(--muted);margin:0 0 6px}
.rkp{margin-bottom:20px}.vide{font-size:13px;color:var(--muted);margin:18px 0;text-align:center}.rkp[hidden]{display:none}
.rk-bar{display:flex;flex-wrap:wrap;gap:10px 18px;margin:0 0 12px}.rk-g{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.rk-g span{font-size:12px;color:var(--muted)}
.rk-c{font:inherit;font-size:12px;padding:5px 12px;border-radius:999px;border:1px solid #DCE6F0;background:#fff;color:#33475B;cursor:pointer}
.rk-c[aria-pressed="true"]{background:#E6F1FB;border-color:#85B7EB;color:#0C447C;font-weight:600}
.rk-body{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:18px;align-items:start}
@media (max-width:760px){.rk-body{grid-template-columns:minmax(0,1fr)}}
.rk-l{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.rk-i{display:grid;grid-template-columns:22px minmax(0,1fr) auto;gap:10px;align-items:center;padding:6px 8px;border-radius:10px;cursor:pointer;font-size:13px}
.rk-i:hover,.rk-i:focus-visible{background:#F3F7FB;outline:none}.rk-i.on{background:#E6F1FB;box-shadow:inset 3px 0 0 #0C447C}
.rk-n{font-size:11px;color:var(--muted);text-align:right}.rk-t{display:flex;flex-direction:column;gap:4px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rk-b{height:7px;border-radius:4px;background:#F1F5FA;overflow:hidden}.rk-b i{display:block;height:100%;border-radius:4px}
.rk-i b{font-variant-numeric:tabular-nums;color:#0B2545}
.rk-side{display:flex;flex-direction:column;gap:12px;position:sticky;top:12px}
.rk-f{border:1px solid #E6EDF5;border-radius:14px;padding:14px;min-height:132px;background:#FBFDFF}
.rk-h{font-size:12.5px;color:var(--muted);margin:0 0 6px}.rk-fn{font-size:13px;font-weight:600;color:var(--navy)}.rk-fv{font-size:28px;font-weight:700;color:#0B2545;margin:2px 0 6px}
.rk-fr{font-size:12.5px;color:#33475B;margin:2px 0}.rk-fa{font-size:12px;color:#0091D0;margin-top:8px;font-weight:600}
.rk-lg{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--muted)}.rk-lg i{flex:1;height:7px;border-radius:4px}
.sv-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:22px 24px}
.sv{border:1px solid #E6EDF5;border-radius:14px;padding:16px 18px;display:flex;flex-direction:column;gap:8px;min-width:0;background:#fff}
.sv-h{font-size:12.5px;font-weight:600;color:var(--navy);line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:2.6em}
.sv-v{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}.sv-v b{font-size:24px;font-weight:700;color:#0B2545;line-height:1}
.sv-b{display:grid;grid-template-columns:62px minmax(0,1fr) auto;gap:8px;align-items:center;font-size:11.5px;color:var(--muted)}
.sv-bt{height:10px;background:#F1F5FA;border-radius:5px;overflow:hidden}.sv-bt i{display:block;height:100%;border-radius:5px}
.sv-bt i.prev{background:#C9D6E5}.sv.up .sv-bt i.cur{background:#12BF9A}.sv.down .sv-bt i.cur{background:#E8776A}.sv.flat .sv-bt i.cur,.sv .sv-bt i.cur{background:#0091D0}
.sv-bv{font-variant-numeric:tabular-nums;color:#0B2545;font-weight:600}
.sv-sp{border-top:1px dashed #E6EDF5;padding-top:6px}.sv-sp svg{width:100%;height:40px;display:block;overflow:visible}
.sv-sp polyline{stroke:#0091D0}.sv-sp circle{fill:#0091D0}.sv.up .sv-sp polyline{stroke:#12BF9A}.sv.up .sv-sp circle{fill:#12BF9A}.sv.down .sv-sp polyline{stroke:#E8776A}.sv.down .sv-sp circle{fill:#E8776A}
.sv-sp span{font-size:10.5px;color:var(--muted)}
.sv.etat{background:#FBFDFF}.sv-sub{font-size:11px;color:var(--muted);margin-top:2px}.sv.etat .sv-bt i.cur{background:#0B4F8F}
.sv-r{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 10px}.sv-n{font-size:11.5px;color:var(--muted);margin:10px 0 0}
@media (max-width:900px){.gaps-body{grid-template-columns:minmax(0,1fr)}}
.dc{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.02em;color:var(--navy);background:rgba(255,255,255,.75);border-radius:6px;padding:1px 7px;margin:0 0 4px}
#dict .n{display:flex;flex-direction:column;align-items:flex-start}
.dl{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:12px;line-height:1.3}
.hd{font-size:10px;color:#B45309;background:#FEF3C7;border-radius:10px;padding:1px 7px;margin-left:6px;font-weight:600;vertical-align:1px}
.dict{margin-bottom:20px}.dict[hidden]{display:none}
.dict-wrap>summary{cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0}
.dict-wrap>summary::-webkit-details-marker{display:none}
.dict-wrap>summary h2{margin:0}
.dict-tog::after{content:'Afficher';font-size:12px;font-weight:600;color:#0B4F8F;border:1px solid #cfe0f1;border-radius:999px;padding:4px 12px;white-space:nowrap}
.dict-wrap[open] .dict-tog::after{content:'Masquer'}
.dict-wrap[open]>summary{margin-bottom:12px}
.dict-wrap>summary:focus-visible{outline:2px solid #0091D0;outline-offset:4px;border-radius:6px}
.dict-abs{margin-top:12px;font-size:12px;color:var(--muted,#667085)}
.dict-abs summary{cursor:pointer;font-weight:600;padding:6px 0;list-style-position:inside}
.dict-abs ul{margin:6px 0 0;padding-left:18px;columns:2 320px;column-gap:24px}
.dict-abs li{margin:2px 0;break-inside:avoid;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
__COMPO_CSS__</style></head><body>
<header class="top"><div class="title"><h1>Tableau de bord</h1><p id="objective"></p></div>
  <div class="rangew"><button type="button" class="chip" id="range" hidden aria-haspopup="true" aria-expanded="false" title="Choisir la période"><svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg><span></span></button>
  <div class="perpop" id="perpop" hidden></div></div></header>
<div class="filters" id="filters"></div>
<section class="kpis" id="kpis" aria-label="Indicateurs clés"></section>
<section class="gaps card" id="gaps" hidden></section>
<section class="row2" id="row2"></section>
<section class="row3" id="row3"></section>
__COMPO_HTML__<section class="rkp card" id="rank" hidden></section>
<section class="dict card" id="dict" hidden></section>
<script>
window.DATA=__DATA__;
window.KPIDEFS=__KPIDEFS__;
window.FILTERCOLS=__FILTERCOLS__;
window.META=__META__;
window.OBJECTIVE="__OBJECTIVE__";
var NAVY='#0B4F8F',PASTEL=['#0B4F8F','#0091D0','#10BCD0','#12BF9A','#1565C0','#7FDCC4'],
    DONUT=['#0B4F8F','#0091D0','#10BCD0','#12BF9A','#1565C0','#5FD3E6','#7FDCC4','#9CC3EA'],
    BLUES=['#0B4F8F','#0E63A8','#0078C0','#0091D0','#10A7D0','#10BCD0','#12BF9A','#7FDCC4'];
var state={},gran=(META.gran||'M'),charts=[],showMore=false,SHOWN={},KCOUNT={},dictOpen=null;
var MOIS=['janv.','févr.','mars','avr.','mai','juin','juil.','août','sept.','oct.','nov.','déc.'];
if(window.Chart){Chart.defaults.font.family="'Fira Sans',system-ui,sans-serif";Chart.defaults.color='#5B6478';
  if(matchMedia('(prefers-reduced-motion: reduce)').matches)Chart.defaults.animation=false;}
function nice(s){var t=String(s==null?'':s);return (window.META&&META.libelles&&META.libelles[t])||t.replace(/\b([A-Z0-9]+(?:_[A-Z0-9]+)+)\b|_/g,function(m,c){return c||' '})}
// Nom de colonne court pour les titres : sans parenthèse, 32 caractères au plus
function ititle(s){var t=nice(s).replace(/^\s*(nombre|nb)\s+(de |d')\s*/i,'').replace(/\s+/g,' ').trim();t=t.charAt(0).toUpperCase()+t.slice(1);return t.length>70?t.slice(0,69)+'…':t}
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
// Filtre de période : mois de début et de fin (« AAAA-MM ») ; tout le fichier quand ils sont vides
var PER={du:null,au:null};
var MOISDISPO=(function(){var s={};DATA.forEach(function(r){if(r.__p)s[String(r.__p).slice(0,7)]=1});return Object.keys(s).sort()})();
function perActive(){return !!(PER.du||PER.au)}
function inPer(r){if(!perActive())return true;var m=r.__p?String(r.__p).slice(0,7):null;if(!m)return false;
  return (!PER.du||m>=PER.du)&&(!PER.au||m<=PER.au)}
function filtered(){return DATA.filter(function(r){return inPer(r)&&FCOLS.every(function(c){return state[c]==null||state[c]==='all'||String(r[c]).trim()===state[c]})})}
// Taux : colonne Oui/Non (et assimilés) → 1/0 ; affiché en % (0 à 100)
var YES={'oui':1,'yes':1,'true':1,'vrai':1,'o':1,'y':1,'positif':1,'positive':1,'1':1},NO={'non':1,'no':1,'false':1,'faux':1,'n':1,'négatif':1,'negatif':1,'négative':1,'negative':1,'0':1};
function isRate(a){a=(a||'').toLowerCase();return a==='taux'||a==='rate'||a==='pct'||a==='proportion'}
function rate(rows,col){var t=rows.filter(function(r){return !isNull(r[col])}).map(function(r){return String(r[col]).trim().toLowerCase()});if(!t.length)return null;
  if(t.every(function(x){return YES[x]||NO[x]}))return t.filter(function(x){return YES[x]}).length/t.length*100;
  var v=t.map(num).filter(function(x){return x!=null});if(!v.length)return null;var m=v.reduce(function(p,q){return p+q},0)/v.length;return Math.max.apply(null,v)<=1?m*100:m}
function agg(rows,col,a){a=(a||'').toLowerCase();
  if(a==='dernier'){var lp=null;rows.forEach(function(r){if(r.__p&&(lp==null||r.__p>lp))lp=r.__p});rows=rows.filter(function(r){return r.__p===lp});a='somme'}
  if(a==='compte'||a==='count'||!col)return col?rows.filter(function(r){return r[col]!=null&&r[col]!==''}).length:rows.length;
  if(isRate(a))return rate(rows,col);
  var v=rows.map(function(r){return num(r[col])}).filter(function(x){return x!=null});if(!v.length)return null;
  var s=v.reduce(function(p,q){return p+q},0);
  if(a==='moyenne'||a==='taux')return s/v.length;if(a==='mediane'||a==='médiane'||a==='median'){v.sort(function(x,y){return x-y});var h=v.length>>1;return v.length%2?v[h]:(v[h-1]+v[h])/2}if(a==='max')return Math.max.apply(null,v);if(a==='min')return Math.min.apply(null,v);return s}
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
function moisNom(k){return MOIS[+k.slice(5,7)-1]+' '+k.slice(0,4)}
function boitePeriode(){var box=document.createElement('div');box.className='f';box.innerHTML='<span>Période</span>';
  var w=document.createElement('div');w.className='per';
  var opt=function(cur,vide){return '<option value="">'+vide+'</option>'+MOISDISPO.map(function(k){return '<option value="'+k+'"'+(cur===k?' selected':'')+'>'+esc(moisNom(k))+'</option>'}).join('')};
  var du=document.createElement('select'),au=document.createElement('select');du.setAttribute('aria-label','Début de période');au.setAttribute('aria-label','Fin de période');
  du.innerHTML=opt(PER.du,'Début');au.innerHTML=opt(PER.au,'Fin');
  var maj=function(){PER.du=du.value||null;PER.au=au.value||null;if(PER.du&&PER.au&&PER.du>PER.au){var t=PER.du;PER.du=PER.au;PER.au=t;du.value=PER.du;au.value=PER.au}
    periodeChangee()};
  du.onchange=maj;au.onchange=maj;w.appendChild(du);var fl=document.createElement('i');fl.textContent='→';w.appendChild(fl);w.appendChild(au);box.appendChild(w);return box}
function buildFilters(){var el=document.getElementById('filters');el.innerHTML='';var F=META.filters||[];
  if(!F.length&&MOISDISPO.length<2){el.style.display='none';return}
  if(MOISDISPO.length>=2)el.appendChild(boitePeriode());
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

// « Comment ce chiffre est calculé ? » : la méthode de chaque tuile, en clair (survol, clic ou clavier)
function howCalc(k,rows){if(k.formule){var t0=(k.description||'')+(k.statut==='propose'?' Formule proposée par Adifa’, à valider.':' Formule validée.');
    if(k.dimension)t0+=' « En tête » : la catégorie de « '+nice(k.dimension)+' » qui a la valeur la plus haute.';
    return t0+(rows.length<DATA.length?' Calculé sur les '+fmt(rows.length)+' lignes retenues par les filtres.':'')}
  var a=(k.agregation||'').toLowerCase(),c=k.colonne?'« '+nice(k.colonne)+' »':'',n=rows.length,t;
  var nb=k.colonne?rows.filter(function(r){return !isNull(r[k.colonne])}).length:n;
  if(a==='dernier'){var lp=null;rows.forEach(function(r){if(r.__p&&(lp==null||r.__p>lp))lp=r.__p});
    t='Somme de '+c+' au dernier mois disponible'+(lp?' ('+label(lp)+')':'')+'. C’est un stock (valeur à une date) : il ne s’additionne pas d’un mois à l’autre.'}
  else if(isRate(a))t='Part de « Oui » parmi les '+fmt(nb)+' lignes où '+c+' est renseignée. Les cases vides ne comptent ni pour « Oui » ni pour « Non ».';
  else if(a==='somme'||a==='sum')t='Somme de '+c+' sur les '+fmt(nb)+' lignes renseignées.';
  else if(a==='moyenne'||a==='mean')t='Moyenne de '+c+' sur '+fmt(nb)+' valeurs renseignées.';
  else if(a==='mediane'||a==='médiane'||a==='median')t='Médiane de '+c+' sur '+fmt(nb)+' valeurs : la moitié des valeurs est au-dessous, la moitié au-dessus. Utilisée car les valeurs sont très dispersées.';
  else if(!k.colonne)t='Nombre de lignes du fichier ('+fmt(n)+').';
  else t=(a==='max'?'Maximum':a==='min'?'Minimum':'Nombre de valeurs renseignées')+' de '+c+'.';
  if(k.hors)t+=' Lignes où « '+nice(k.hors.colonne)+' » = Oui exclues.';
  if(k.dimension)t+=' « En tête » : la catégorie de « '+nice(k.dimension)+' » qui a la valeur la plus haute.';
  if(n<DATA.length)t+=' Calculé sur les '+fmt(n)+' lignes retenues par les filtres.';
  return t}
// ---- Formules de correspondance (mêmes règles que hermes_da.correspondance.evaluer)
function condF(r,c){if(!c)return true;var v=r[c.colonne];if(isNull(v))return false;var t=String(v).trim().toLowerCase();
  return (c.valeurs||[]).some(function(x){return String(x).trim().toLowerCase()===t})}
function evalF(rows,f){if(!f)return null;var R=f.filtre?rows.filter(function(r){return condF(r,f.filtre)}):rows;
  if(f.stock){var lp=null;R.forEach(function(r){if(r.__p&&(lp==null||r.__p.slice(0,7)>lp))lp=r.__p.slice(0,7)});if(lp!=null)R=R.filter(function(r){return r.__p&&r.__p.slice(0,7)===lp})}
  var m=f.mode,N=f.numerateur;
  if(m==='comptage')return N?R.filter(function(r){return condF(r,N)}).length:R.length;
  if(m==='taux'){if(!N)return null;var B=f.denominateur?R.filter(function(r){return condF(r,f.denominateur)}):R.filter(function(r){return !isNull(r[N.colonne])});
    return B.length?Math.round(B.filter(function(r){return condF(r,N)}).length/B.length*1000)/10:null}
  if(m==='rapport'){var sm=function(c){var t=null;R.forEach(function(r){if(condF(r,c)){var x=num(r[f.colonne]);if(x!=null)t=(t||0)+x}});return t};
    var n=sm(N),d=sm(f.denominateur);return n!=null&&d?Math.round(n/d*1000)/10:null}
  var v=(N?R.filter(function(r){return condF(r,N)}):R).map(function(r){return num(r[f.colonne])}).filter(function(x){return x!=null});
  if(!v.length)return null;var s=v.reduce(function(a,b){return a+b},0);
  if(m==='somme')return s;if(m==='moyenne')return s/v.length;v.sort(function(a,b){return a-b});var h=v.length>>1;return v.length%2?v[h]:(v[h-1]+v[h])/2}
function rankedF(rows,f,dim,top){var g=groupBy(rows,dim);return Object.keys(g).map(function(k){return [k,evalF(g[k],f)]}).filter(function(e){return e[1]!=null}).sort(function(a,b){return b[1]-a[1]}).slice(0,top||8)}
function kpiGros(k){var E=META.gros,a=(k.agregation||'').toLowerCase(),ech=!!E&&E.mode==='echantillon',filtre=perActive()||FCOLS.some(function(c){return state[c]!=null&&state[c]!=='all'});
  return {v:E&&!filtre&&k.exact!=null&&(ech||/^m[ée]dian/.test(a))?k.exact:null,t:ech&&!filtre?k.tete:null,f:ech&&(!k.colonne||/^(somme|sum|compte|count|dernier|comptage)$/.test(a))?E.lignes/DATA.length:1,a:ech&&(filtre||k.exact==null)}}
function grosNote(){var E=META.gros,k=document.getElementById('kpis');if(!E||document.getElementById('gros-note'))return;var p=document.createElement('p');p.id='gros-note';p.style.cssText='font-size:13px;color:var(--muted);margin:0 0 10px';
  p.textContent=E.mode==='echantillon'?'Fichier de '+fmt(E.lignes)+' lignes : les tuiles sont exactes sans filtre, estimées avec un filtre (≈). Les graphiques reposent sur un échantillon de '+fmt(DATA.length)+' lignes : lisez-y les proportions, pas les totaux.'+(E.trop_detaille?' Ce fichier est trop détaillé pour la page composée.':''):'Calculé sur les '+fmt(E.lignes)+' lignes du fichier.'+(E.mensuel?' Détail limité au mois pour ce volume.':'')+(E.retires&&E.retires.length?' Filtre retiré car trop détaillé pour cette page : '+E.retires.map(nice).join(', ')+'.':'');
  k.parentNode.insertBefore(p,k)}
function renderKpis(all){grosNote();document.getElementById('kpis').innerHTML=KPIDEFS.slice(0,5).map(function(k,i){
  var rows=k.hors?all.filter(function(r){return k.hors.valeurs.indexOf(String(r[k.hors.colonne]).trim().toLowerCase())<0}):all,
    G=kpiGros(k),v=G.v!=null?G.v:(k.formule?evalF(rows,k.formule):agg(rows,k.colonne,k.agregation)),sub='';if(G.v==null&&v!=null)v*=G.f;
  var u=(k.formule?k.pct:isRate(k.agregation))&&v!=null?' %':'';
  if(k.dimension){var t=G.t||(k.formule?rankedF(rows,k.formule,k.dimension,1):ranked(rows,k.colonne,k.agregation,k.dimension,1))[0];if(t)sub='En tête : '+esc(label(t[0]))+'<br>'+(G.a?'≈ ':'')+fmt(G.t?t[1]:t[1]*G.f)+u}
  var how=howCalc(k,rows);
  var badge=k.statut==='propose'?' <span class="hd" title="Formule proposée par Adifa’ : à valider dans KLDataViz">à valider</span>':'';
  return '<div class="kpi k'+i+'"><div class="n">'+esc(k.nom)+badge+' <span class="info" tabindex="0" role="note" aria-label="Comment ce chiffre est calculé : '+esc(how)+'">i<span class="tip">'+esc(how)+'</span></span></div><div class="row"><div class="v">'+(G.a?'≈ ':'')+fmt(v)+u+'</div><div class="s">'+sub+'</div></div></div>'}).join('')}

function card(id,title,sub,extra){return '<div class="card"><div class="card-h"><div><h2>'+esc(nice(title))+'</h2>'+(sub?'<small>'+esc(sub)+'</small>':'')+'</div>'+(extra||'')+'</div>'+id+'</div>'}
// Granularités qui donnent au moins 2 points avec les filtres actuels : une granularité trop large (1 seul point)
// faisait disparaître la courbe ET ce sélecteur avec elle, sans moyen de revenir en arrière
var GOK=null;
function gransOK(rows){var all=META.grans||['M','Q','Y'],save=gran;if(!META.period)return all;
  var ok=all.filter(function(g){gran=g;var s={},n=0;for(var i=0;i<rows.length&&n<2;i++){var k=pkey(rows[i].__p);if(k&&!s[k]){s[k]=1;n++}}return n>=2});gran=save;return ok.length?ok:all}
function seg(){return '<div class="seg" role="group" aria-label="Regrouper par">'+(GOK||META.grans||['M','Q','Y']).map(function(g){return '<button type="button" data-g="'+g+'" aria-pressed="'+(gran===g)+'">'+GRAN[g]+'</button>'}).join('')+'</div>'}
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
    // Empiler seulement s'il y a au moins 2 catégories où la mesure existe (ex. recettes « par type » : une seule)
    var cats0=add?ranked(rows,M.colonne,M.agregation,S,5).filter(function(e){return e[1]}).map(function(e){return e[0]}):[];if(cats0.length<2)add=false;
    if(add){SHOWN[M.colonne+'|'+S]=1;var g2=groupBy(rows,function(r){return pkey(r.__p)}),ks2=Object.keys(g2).sort(),cats=cats0;
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
      html+=card(canvas('c_cat',M.nom+' par '+short(C)),M.nom+' par '+short(C),'Ligne : moyenne '+fmt(mean)+(isRate(M.agregation)?' %':''));
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
  function add(h){if(n<4){html+=h;n++}}
  (META.charts||[]).forEach(function(c,i){var col=c.colonne;
    if(c.type==='bar'&&Object.keys(groupBy(rows,col)).length<=3&&rows.some(function(r){return !isNull(r[col])&&num(r[col])==null}))c=Object.assign({},c,{type:'pie'});
    // Anneau demandé sur une colonne chiffrée (ex. « Nombre de dépistés ») : total par catégorie, pas une répartition des valeurs
    if(c.type==='pie'&&rows.length&&rows.every(function(r){return isNull(r[col])||num(r[col])!=null}))c=Object.assign({},c,{type:'bar'});
    if(used[col+c.type])return;used[col+c.type]=1;
    var Cc=META.stack||((META.filters||[])[0]||{}).col,numeric=rows.length&&rows.every(function(r){return isNull(r[col])||num(r[col])!=null}),nm=nice(col).toLowerCase();
    // Classes (tranche d'âge, groupe…) ou code chiffré à peu de valeurs : une catégorie, pas une quantité à additionner
    var pyr=/(^|[\s_])(tranche|age|ages)([\s_]|$)/.test(nm),classes=pyr||/(^|[\s_])(classe|groupe)([\s_]|$)/.test(nm)||(numeric&&Object.keys(groupBy(rows,col)).length<=12&&!/nombre|nb|effectif|total|montant|valeur|quantit/.test(nm));
    if(c.type==='bar'&&pyr&&Cc&&col!==Cc){var cs=Object.keys(groupBy(rows,Cc)).sort();if(cs.length===2){if(SHOWN['#p|'+col])return;SHOWN['#p|'+col]=1;add(pyramid('c_py'+i,rows,col,Cc,cs));todo.push(pyramidDraw('c_py'+i,rows,col,Cc,cs));return}}
    if(META.agrege&&!numeric&&(c.type==='bar'||c.type==='pie'))return;  // lignes = district × mois : pas des effectifs
    if(c.type==='bar'&&classes){var ec=ranked(rows,null,'compte',col,15).sort(function(x,y){return natural(x[0],y[0])});if(ec.length<2||SHOWN['#n|'+col]||KCOUNT[col])return;SHOWN['#n|'+col]=1;add(card(rankList(ec),'Effectifs par '+short(col),'Par classe, dans l\'ordre'));return}
    if(c.type==='pie'){var e=ranked(rows,null,'compte',col,8),tot=e.reduce(function(p,q){return p+q[1]},0)||1;if(e.length<2||e.every(function(x){return x[1]===e[0][1]}))return;
      add(card('<div class="donut">'+canvas('c_d'+i,'Répartition par '+col)+'<ul class="legend">'+e.map(function(x,j){return '<li><i style="background:'+DONUT[j%DONUT.length]+'"></i><span>'+esc(label(x[0]))+'</span><b>'+Math.round(x[1]/tot*100)+' %</b></li>'}).join('')+'</ul></div>','Répartition par '+short(col),c.justification));
      todo.push(function(){charts.push(new Chart(document.getElementById('c_d'+i),{type:'doughnut',data:{labels:e.map(function(x){return x[0]}),datasets:[{data:e.map(function(x){return x[1]}),backgroundColor:DONUT,borderColor:'#fff',borderWidth:3,hoverOffset:4}]},options:{responsive:true,maintainAspectRatio:false,cutout:'68%',plugins:{legend:{display:false},tooltip:navyTip}}}))})}
    else if((c.type==='hist'||c.type==='box')&&numeric&&Cc&&col!==Cc&&/^(nombre|nb|effectif|total|volume)\b/.test(nm)){
      if(SHOWN[col+'|'+Cc])return;SHOWN[col+'|'+Cc]=1;var er=ranked(rows,col,'somme',Cc,8);if(er.length<2)return;add(card(rankList(er),ititle(col)+' par '+short(Cc),'Total sur la période affichée'))}
    else if(c.type==='hist'||c.type==='box'||(c.type==='line'&&!META.period)){var v=rows.map(function(r){return num(r[col])}).filter(function(x){return x!=null}).sort(function(a,b){return a-b});if(v.length<10)return;
      var q=function(p){return v[Math.min(v.length-1,Math.floor(p*(v.length-1)))]},med=q(.5),lo=q(.01),hi=q(.99),labels,bins,nm=nice(col).toLowerCase(),
          clin=/^(nombre|nb|effectif|total|volume)\b/.test(nm)?null:/cd4/.test(nm)?[[200,'< 200'],[350,'200 – 349'],[500,'350 – 499'],[Infinity,'≥ 500']]
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
    else if(c.type==='bar'&&numeric&&Cc&&col!==Cc){if(SHOWN[col+'|'+Cc])return;SHOWN[col+'|'+Cc]=1;var e3=ranked(rows,col,'somme',Cc,8);if(e3.length<2)return;add(card(rankList(e3),ititle(col)+' par '+short(Cc),c.justification))}
    else if(c.type==='bar'&&!numeric){if(SHOWN['#n|'+col]||KCOUNT[col])return;SHOWN['#n|'+col]=1;var e2=ranked(rows,null,'compte',col,8);if(e2.length<2||e2.every(function(x){return x[1]===e2[0][1]}))return;add(card(rankList(e2),'Effectifs par '+short(col),c.justification))}});
  // Classements par dimension (pas par date : la courbe les montre déjà)
  KPIDEFS.forEach(function(k){if(!k.dimension||k.dimension===META.period||used['k'+k.nom]||SHOWN[k.colonne+'|'+k.dimension])return;used['k'+k.nom]=1;SHOWN[k.colonne+'|'+k.dimension]=1;var e=ranked(rows,k.colonne,k.agregation,k.dimension,8);if(e.length<2)return;
    add(card(rankList(e,isRate(k.agregation)?' %':''),k.nom.toLowerCase().indexOf(nice(k.dimension).toLowerCase())>=0?k.nom:k.nom+' par '+short(k.dimension),'Classement'))});
  el.innerHTML=html||'';return todo}

// « 2025-04 » → « avr. 2025 », « 2025-T1 » → « T1 2025 »
function gkey(k){k=String(k||'');if(META.keyind&&META.keyind.yearly)return k.slice(0,4);var m=k.match(/^(\d{4})-(\d{2})$/);if(m)return MOIS[+m[2]-1]+' '+m[1];m=k.match(/^(\d{4})-T(\d)$/);return m?'T'+m[2]+' '+m[1]:k}
// Écarts : période analysée et comparaison choisies par l'utilisateur ; tuiles + graphique des écarts
var GAP={p:null,mode:'prev'},gapChart=null;
function gYearBefore(k){var m=String(k).match(/^(\d{4})(.*)$/);return m?(+m[1]-1)+m[2]:null}
function gval(k,rows,key){var K=META.keyind,R=key==null?rows:rows.filter(function(r){return r.__g===key});
  if(k.kind==='count')return R.length;if(k.kind==='agg')return agg(R,k.col,k.agg);if(k.kind==='formule')return evalF(R,k.f);
  var s=null;R.forEach(function(r){if(!k.col&&String(r[K.ind]).trim()!==k.libelle)return;var v=num(k.col?r[k.col]:r[K.val]);if(v!=null)s=(s||0)+v});return s}
function renderGaps(rows){var el=document.getElementById('gaps'),K=META.keyind,D=META.domaine,G=META.gaps;if(gapChart){gapChart.destroy();gapChart=null}
  if(!G||!G.length||!K){el.hidden=true;return}
  var P=K.periods||[],cur=K.last,ref=K.prev,mode=GAP.mode;
  if(K.live&&P.length){// Par défaut : dernière période complète (un mois en cours fausserait la comparaison)
    if(GAP.p==null||P.indexOf(GAP.p)<0)GAP.p=(K.partial===P[P.length-1]&&P.length>=3)?P[P.length-2]:P[P.length-1];
    cur=GAP.p;var i=P.indexOf(cur);ref=mode==='n1'?gYearBefore(cur):(i>0?P[i-1]:null);if(ref!=null&&P.indexOf(ref)<0)ref=null;
    G=K.items.map(function(k){var c=gval(k,rows,cur);if(c==null)return null;var p=ref==null?null:gval(k,rows,ref),pts=!!k.pts;
      if(pts){c=c;p=p}var ok=p!=null&&(pts||p!==0),d=ok?c-p:null;
      return {k:k,indicateur:k.nom,libelle:k.libelle,derniere:c,precedente:p,delta:d,pct:ok?(pts?d:d/p*100):null,pts:pts,bon:k.bon,sens:d==null?null:d>0?'hausse':d<0?'baisse':'stable'}}).filter(Boolean)}
  else if(K.live&&!P.length){G=K.items.map(function(k){var c=gval(k,rows,null);return c==null?null:{indicateur:k.nom,libelle:k.libelle,derniere:c,pct:null,pts:!!k.pts,bon:k.bon}}).filter(Boolean);cur=null;ref=null}
  if(!G.length){el.hidden=true;return}el.hidden=false;
  var t=D?'<span class="gaps-dom">'+esc(D.nom)+'</span>':'',hasN1=P.some(function(k){return P.indexOf(gYearBefore(k))>=0});
  var ctl=K.live&&P.length>1?'<div class="gaps-ctl"><label>Période <select id="gapP">'+P.slice().reverse().map(function(k){return '<option value="'+esc(k)+'"'+(k===cur?' selected':'')+'>'+esc(gkey(k))+(k===K.partial?' (en cours)':'')+'</option>'}).join('')+'</select></label>'
    +'<div class="seg" role="group" aria-label="Comparer avec"><button data-m="prev" aria-pressed="'+(mode==='prev')+'">Période précédente</button>'+(hasN1?'<button data-m="n1" aria-pressed="'+(mode==='n1')+'">Même période N-1</button>':'')+'</div></div>':'';
  var sub=cur==null?'pas de période dans le fichier, pas d\'écart':ref==null?esc(gkey(cur))+' · pas de période de comparaison':esc(gkey(cur))+' vs '+esc(gkey(ref));
  var warn=K.partial&&cur===K.partial?esc(gkey(cur))+' est incomplet : les effectifs sont sous-estimés':'';
  // Vert = évolution souhaitée (ex. baisse des décès), rouge = dégradation
  var tone=function(g){if(g.pct==null)return '';var good=g.pct*(g.bon||1);return g.sens==='stable'?'flat':good>0?'up':'down'};
  // Gris = écart négligeable (< 1 point pour un %, < 1 % pour un nombre) : on n'alarme pas sur du bruit
  var tone2=function(g){if(g.pct==null)return '';if(Math.abs(g.pct)<1)return 'flat';return tone(g)};
  var f1=function(x){return String(Math.round(x*10)/10).replace('.',',')};
  var val=function(x,pts){return x==null?'—':pts?f1(x)+' %':fmt(x)};
  var delta=function(g){if(g.pct==null)return '';var d=g.delta!=null?g.delta:g.precedente!=null?g.derniere-g.precedente:null,a=g.sens==='hausse'?'▲ ':g.sens==='baisse'?'▼ ':'';
    if(d==null)return a+(g.pct>0?'+':g.pct<0?'−':'')+f1(Math.abs(g.pct))+(g.pts?' pt':' %');var sg=d>0?'+':d<0?'−':'';
    return a+(g.pts?sg+f1(Math.abs(d))+' pt'+(Math.abs(d)>=2?'s':''):sg+fmt(Math.abs(d))+' ('+sg+f1(Math.abs(g.pct))+' %)')};
  if(META.composition)return cpIndicateurs(el,{G:G,K:K,D:D,cur:cur,ref:ref,sub:sub,warn:warn,ctl:ctl,val:val,delta:delta});
  var idx=P.indexOf(cur),SP=K.live&&rows&&idx>=0?P.slice(Math.max(0,idx-5),idx+1):[];
  var spark=function(g){if(!g.k||SP.length<3)return '';var v=SP.map(function(p){return gval(g.k,rows,p)});
    var ok=v.filter(function(x){return x!=null});if(ok.length<3)return '';
    var mn=Math.min.apply(null,ok),mx=Math.max.apply(null,ok),W=200,H=40,n=SP.length,pt=[];
    v.forEach(function(x,j){if(x!=null)pt.push([4+j*(W-8)/(n-1),mx===mn?H/2:H-4-(x-mn)/(mx-mn)*(H-10)])});
    var last=pt[pt.length-1],tip=SP.map(function(p,j){return gkey(p)+' : '+val(v[j],g.pts)}).join('\n');
    return '<div class="sv-sp" title="'+esc(tip)+'"><svg viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="none" role="img" aria-label="'+esc('Évolution sur '+n+' périodes : '+tip.replace(/\n/g,', '))+'">'
      +'<polyline points="'+pt.map(function(q){return q[0].toFixed(1)+','+q[1].toFixed(1)}).join(' ')+'" fill="none" stroke-width="2" vector-effect="non-scaling-stroke"/>'
      +'<circle cx="'+last[0].toFixed(1)+'" cy="'+last[1].toFixed(1)+'" r="3.2"/></svg><span>'+esc(gkey(SP[0]))+' → '+esc(gkey(SP[n-1]))+'</span></div>'};
  var bars=function(g){if(g.precedente==null)return '';var top=g.pts?100:Math.max(Math.abs(g.derniere),Math.abs(g.precedente))||1;
    var row=function(lab,x,cl){return '<div class="sv-b"><span class="sv-bl">'+esc(lab)+'</span><span class="sv-bt"><i class="'+cl+'" style="width:'+Math.max(2,Math.min(100,Math.abs(x)/top*100)).toFixed(1)+'%"></i></span><span class="sv-bv">'+val(x,g.pts)+'</span></div>'};
    return row(gkey(ref),g.precedente,'prev')+row(gkey(cur),g.derniere,'cur')};
  var cards=G.map(function(g){var t=tone2(g);
    return '<div class="sv '+t+'"><div class="sv-h" title="'+esc((g.indicateur||'')+(g.libelle?'\n'+g.libelle:''))+'">'+esc(g.indicateur)+'</div>'
      +((g.k&&g.k.etat)||g.etat?'<div class="sv-sub" title="Colonne d\'état actuel : chaque mois compte les patients de ce mois selon leur statut d\'aujourd\'hui">Patients du mois · statut actuel</div>':'')
      +'<div class="sv-v"><b>'+val(g.derniere,g.pts)+'</b>'+(g.pct!=null?'<span class="gap-d '+t+'">'+delta(g)+'</span>':'')+'</div>'
      +bars(g)+spark(g)+'</div>'}).join('');
  var up=G.filter(function(g){return tone2(g)==='up'}).length,dn=G.filter(function(g){return tone2(g)==='down'}).length;
  var resume=G.some(function(g){return g.pct!=null})?'<span class="sv-r">'+(up?'<span class="gap-d up">'+up+' en amélioration</span>':'')+(dn?'<span class="gap-d down">'+dn+' en recul</span>':'')
    +(G.length-up-dn?'<span class="gap-d flat">'+(G.length-up-dn)+' stable'+(G.length-up-dn>1?'s':'')+'</span>':'')+'</span>':'';
  // Indicateurs reliés non suivis ici : on dit pourquoi, au lieu de les faire disparaître
  var suivis={};G.forEach(function(g){suivis[g.indicateur]=1});
  var note='';
  var title='Indicateurs clés de suivi'+(D?' · '+D.nom:'');
  el.innerHTML='<div class="card-h"><div><h2>'+esc(title)+'</h2><small>'+sub+'</small></div>'+ctl+'</div>'+(warn?'<p class="gaps-note">'+warn+'</p>':'')
    +resume+'<div class="sv-grid">'+cards+'</div>'+note;
  var sel=document.getElementById('gapP');if(sel)sel.onchange=function(){GAP.p=sel.value;renderGaps(filtered())};
  [].forEach.call(el.querySelectorAll('.gaps-ctl .seg button'),function(b){b.onclick=function(){GAP.mode=b.dataset.m;renderGaps(filtered())}})}
function renderDict(rows){var el=document.getElementById('dict'),D=META.dictionnaire;if(!D||(!(D.trouves||[]).length&&!(D.absents||[]).length)){el.hidden=true;return}el.hidden=false;
  var F=D.trouves||[],A=D.absents||[],VIS=6;
  function tile(r,i){var isPct=(r.type||'').toLowerCase().indexOf('pourcentage')>=0;
    var val=r.formule&&rows?evalF(rows,r.formule):r.valeur;
    var v=val==null?'—':(isPct?String(Math.round(val*10)/10).replace('.',',')+'<small>%</small>':fmt(val));
    var tip=(r.libelle||'')+(r.detail?'\n'+r.detail:'');
    return '<div class="kpi gap k'+(i%4+1)+'" title="'+esc(tip)+'"><div class="n"><span class="dc">'+esc(r.code||'')+'</span><span class="dl">'+esc(r.libelle||'')+'</span></div>'
      +'<div class="row"><div class="v">'+v+'</div>'+(r.detail&&/^\d/.test(r.detail)?'<div class="s"><span class="gap-p">'+esc(r.detail)+'</span></div>':'')+'</div></div>'}
  var more=F.slice(VIS),li=function(r){return '<li title="'+esc(r.libelle||'')+'"><b>'+esc(r.code)+'</b> '+esc(r.libelle||'')+'</li>'};
  var relies=(META.indicateurs||[]).length;
  if(dictOpen===null)dictOpen=!relies;  // indicateurs reliés en tête : le reste du dictionnaire reste replié
  el.innerHTML='<details class="dict-wrap"'+(dictOpen?' open':'')+'><summary class="card-h"><div><h2>'+(relies?'Autres indicateurs du dictionnaire':'Indicateurs du dictionnaire')+'</h2><small>'
    +(relies?(F.length?F.length+' de plus calculé'+(F.length>1?'s':''):'Aucun autre calculable')+(A.length?' · '+A.length+' absent'+(A.length>1?'s':'')+' de ces données':'')
      :F.length+' calculé'+(F.length>1?'s':'')+' selon votre référentiel · les indicateurs clés du haut sont choisis par Adifa’')+'</small></div>'
    +'<span class="dict-tog" aria-hidden="true"></span></summary><div class="dict-body">'
    +(F.length?'<div class="gaps-grid">'+F.slice(0,VIS).map(tile).join('')+'</div>':relies?'':'<p class="gaps-ct">Aucun indicateur du dictionnaire n\'est calculable de façon sûre avec ces données.</p>')
    +(more.length?'<details class="dict-abs"><summary>'+more.length+' autre'+(more.length>1?'s':'')+' indicateur'+(more.length>1?'s':'')+' calculé'+(more.length>1?'s':'')+'</summary><div class="gaps-grid" style="margin-top:10px">'+more.map(function(r,i){return tile(r,i+VIS)}).join('')+'</div></details>':'')
    +(A.length?'<details class="dict-abs"><summary>'+A.length+' indicateur'+(A.length>1?'s':'')+' du dictionnaire absent'+(A.length>1?'s':'')+' de ces données</summary><ul>'+A.map(li).join('')+'</ul></details>':'')+'</div></details>';
  el.querySelector('.dict-wrap').addEventListener('toggle',function(e){dictOpen=e.target.open})}
function renderIndic(rows){var I=META.indicateurs||[],dims=META.dims||[],r2=document.getElementById('row2'),r3=document.getElementById('row3'),h2='',h3='',todo=[];
  var ev=I.filter(function(k){return k.evolution})[0];
  if(ev&&META.period){var g=groupBy(rows,function(r){return pkey(r.__p)}),ks=Object.keys(g).filter(function(k){return k&&k!=='null'}).sort(),f0=Object.assign({},ev.formule,{stock:false});
    if(ks.length<2&&ks.length)h2+=card('<p class="vide">Une seule période par '+GRAN[gran].toLowerCase()+' avec ces filtres : choisissez un regroupement plus fin.</p>','Évolution — '+ev.nom,'Par '+GRAN[gran].toLowerCase(),seg());
    if(ks.length>=2){var vals=ks.map(function(k){return evalF(g[k],f0)}),u=ev.pct?' %':'';
      h2+=card(canvas('c_ind_t',ev.nom),'Évolution — '+ev.nom,'Par '+GRAN[gran].toLowerCase()+(ev.statut==='propose'?' · formule à valider':''),seg());
      todo.push(function(){var cv=document.getElementById('c_ind_t'),ctx=cv.getContext('2d'),grad=ctx.createLinearGradient(0,0,0,250);grad.addColorStop(0,'rgba(0,145,208,.28)');grad.addColorStop(1,'rgba(16,188,208,0)');
        charts.push(new Chart(cv,{type:'line',data:{labels:ks.map(plabel),datasets:[{data:vals,borderColor:NAVY,backgroundColor:grad,fill:true,tension:.35,borderWidth:2.5,spanGaps:true,pointRadius:ks.length<=15?3:0,pointHoverRadius:5,pointBackgroundColor:NAVY}]},
          options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:false},tooltip:Object.assign({callbacks:{label:function(c){return ev.code+' : '+fmt(c.parsed.y)+u}}},navyTip)},scales:axes}}))})}}
  // Répartitions : chaque indicateur par la dimension principale (puis la suivante), même formule que la tuile
  // Rangée 1 : la courbe et la 1re répartition (ou deux répartitions) ; rangée 2 : les suivantes (6 graphiques au plus)
  var cartes=[];I.forEach(function(k,i){var d=dims[i%Math.max(dims.length,1)]||dims[0];if(!d||cartes.length>=6)return;var e=rankedF(rows,k.formule,d,12);if(e.length<2)return;
    var id='c_ind_'+i,tt=k.nom+' par '+short(d),sub=(k.pct?'Taux':'Valeur')+' par '+short(d)+' · ligne : moyenne'+(k.statut==='propose'?' · formule à valider':'');
    cartes.push(card(canvas(id,tt),tt,sub));todo.push(catBarDraw(id,e,k.pct?' %':''))});
  // Camembert « part de » : seulement pour un NOMBRE (comptage, somme) sur une dimension de 2 à 6 catégories.
  // Jamais pour un taux : des pourcentages par groupe ne s'additionnent pas, leurs « parts » n'auraient pas de sens.
  var kp=I.filter(function(k){return !k.pct&&['comptage','somme'].indexOf((k.formule||{}).mode)>=0})[0];
  if(kp){var nb=function(d){var v={};rows.forEach(function(r){if(!isNull(r[d]))v[String(r[d]).trim()]=1});return Object.keys(v).length};
    var dp=dims.filter(function(d){var n=nb(d);return n>=2&&n<=6}).sort(function(a,b){return nb(a)-nb(b)})[0];
    var ep=dp?rankedF(rows,kp.formule,dp,6).filter(function(x){return x[1]>0}):[];
    if(ep.length>=2){var totp=ep.reduce(function(a,x){return a+x[1]},0)||1,tp='Part de '+kp.nom.split(' · ')[0]+' par '+short(dp);
      cartes.splice(enHaut0(),0,card('<div class="donut">'+canvas('c_ind_pie',tp)+'<ul class="legend">'+ep.map(function(x,j){return '<li><i style="background:'+DONUT[j%DONUT.length]+'"></i><span>'+esc(label(x[0]))+'</span><b>'+Math.round(x[1]/totp*100)+' %</b></li>'}).join('')+'</ul></div>',
        tp,'Répartition du total ('+fmt(totp)+')'+(kp.statut==='propose'?' · formule à valider':'')));
      todo.push(function(){charts.push(new Chart(document.getElementById('c_ind_pie'),{type:'doughnut',data:{labels:ep.map(function(x){return label(x[0])}),datasets:[{data:ep.map(function(x){return x[1]}),backgroundColor:DONUT,borderColor:'#fff',borderWidth:3,hoverOffset:4}]},
        options:{responsive:true,maintainAspectRatio:false,cutout:'68%',plugins:{legend:{display:false},tooltip:Object.assign({callbacks:{label:function(c){return c.label+' : '+fmt(c.parsed)+' ('+Math.round(c.parsed/totp*100)+' %)'}}},navyTip)}}}))})}}
  function enHaut0(){return h2?1:2}
  var enHaut=h2?1:2;h2+=cartes.slice(0,enHaut).join('');h3+=cartes.slice(enHaut,h2?5:6).join('');
  r2.innerHTML=h2;r3.innerHTML=h3;
  // Semaine / Mois / Trimestre : regroupe la courbe autrement
  [].forEach.call(r2.querySelectorAll('.seg button[data-g]'),function(b){b.onclick=function(){gran=b.dataset.g;render()}});
  return todo}
// Classement interactif : un indicateur × une dimension ; survol = fiche (rang, écart), clic = filtre tout le tableau
var RK={k:0,d:0};
function renderRank(){var el=document.getElementById('rank'),I=(META.indicateurs||[]),dims=(META.dims||[]);
  if(!I.length||!dims.length){el.hidden=true;return}
  if(RK.k>=I.length)RK.k=0;if(RK.d>=dims.length)RK.d=0;
  var k=I[RK.k],d=dims[RK.d],filtrable=FCOLS.indexOf(d)>=0;
  // Toutes les lignes filtrées SAUF par cette dimension : le classement garde tous ses groupes, le choisi est surligné
  var base=DATA.filter(function(r){return inPer(r)&&FCOLS.every(function(c){return c===d||state[c]==null||state[c]==='all'||String(r[c]).trim()===state[c]})});
  var e=rankedF(base,k.formule,d,15);el.hidden=false;
  if(e.length<2){var ch=function(list,cur,att){return list.map(function(x,i){return '<button class="rk-c" data-'+att+'="'+i+'" aria-pressed="'+(i===cur)+'">'+esc(att==='k'?x.nom.split(' · ')[0]:short(x))+'</button>'}).join('')};
    el.innerHTML='<div class="card-h"><div><h2>Classement · '+esc(k.nom)+'</h2></div></div><div class="rk-bar"><div class="rk-g">'+ch(I,RK.k,'k')+'</div>'+(dims.length>1?'<div class="rk-g"><span>par</span>'+ch(dims,RK.d,'d')+'</div>':'')+'</div>'
      +'<p class="vide">Un seul groupe par '+esc(short(d).toLowerCase())+' pour cet indicateur : choisissez un autre découpage.</p>';
    [].forEach.call(el.querySelectorAll('.rk-c'),function(b){b.onclick=function(){if(b.dataset.k!=null)RK.k=+b.dataset.k;else RK.d=+b.dataset.d;renderRank()}});return}
  var u=k.pct?' %':'',mx=Math.max.apply(null,e.map(function(x){return x[1]})),mn=Math.min.apply(null,e.map(function(x){return x[1]}));
  var ref=k.pct?evalF(base,k.formule):e.reduce(function(a,x){return a+x[1]},0)/e.length,refl=k.pct?'ensemble':'moyenne par '+short(d).toLowerCase();
  var mix=function(t){var a=[181,212,244],b=[12,68,124];return 'rgb('+a.map(function(v,j){return Math.round(v+(b[j]-v)*t)}).join(',')+')'};
  var tone=function(v){return mx===mn?.6:(v-mn)/(mx-mn)};
  var f1=function(x){return k.pct?String(Math.round(x*10)/10).replace('.',','):fmt(x)};
  var sel=state[d]&&state[d]!=='all'?state[d]:null;
  var chips=function(list,cur,att){return list.map(function(x,i){return '<button class="rk-c" data-'+att+'="'+i+'" aria-pressed="'+(i===cur)+'">'+esc(att==='k'?x.nom.split(' · ')[0]:short(x))+'</button>'}).join('')};
  el.innerHTML='<div class="card-h"><div><h2>Classement · '+esc(k.nom)+'</h2><small>'+(k.pct?'Taux':'Valeur')+' par '+esc(short(d))+' · '+e.length+' groupes'+(filtrable?' · cliquez pour filtrer le tableau':'')+'</small></div></div>'
    +'<div class="rk-bar"><div class="rk-g">'+chips(I,RK.k,'k')+'</div>'+(dims.length>1?'<div class="rk-g"><span>par</span>'+chips(dims,RK.d,'d')+'</div>':'')+'</div>'
    +'<div class="rk-body"><ol class="rk-l">'+e.map(function(x,i){var t=tone(x[1]);return '<li class="rk-i'+(sel===x[0]?' on':'')+'" data-i="'+i+'" tabindex="0" role="button" aria-label="'+esc(label(x[0])+' : '+f1(x[1])+u)+'">'
      +'<span class="rk-n">'+(i+1)+'</span><span class="rk-t">'+esc(label(x[0]))+'<span class="rk-b"><i style="width:'+Math.max(2,mx>0?x[1]/mx*100:0).toFixed(1)+'%;background:'+mix(t)+'"></i></span></span><b>'+f1(x[1])+u+'</b></li>'}).join('')+'</ol>'
    +'<div class="rk-side"><div class="rk-f" id="rkF"></div><div class="rk-lg"><span>'+f1(mn)+u+'</span><i style="background:linear-gradient(90deg,'+mix(0)+','+mix(.5)+','+mix(1)+')"></i><span>'+f1(mx)+u+'</span></div></div></div>';
  var F=el.querySelector('#rkF');
  var fiche=function(i){if(i==null){var s0=sel?e.filter(function(x){return x[0]===sel})[0]:null;if(s0)return fiche(e.indexOf(s0));
      F.innerHTML='<p class="rk-h">Survolez une ligne pour voir sa fiche.</p><p class="rk-h">Référence ('+esc(refl)+') : <b>'+f1(ref)+u+'</b></p>';return}
    var x=e[i],ec=x[1]-ref,pt=k.pct?' pt'+(Math.abs(ec)>=2?'s':''):'';
    F.innerHTML='<div class="rk-fn">'+esc(label(x[0]))+'</div><div class="rk-fv">'+f1(x[1])+u+'</div>'
      +'<div class="rk-fr">Rang <b>'+(i+1)+'</b> sur '+e.length+'</div>'
      +'<div class="rk-fr">'+(ec>=0?'+':'−')+f1(Math.abs(ec))+(k.pct?pt:'')+' vs '+esc(refl)+' ('+f1(ref)+u+')</div>'
      +(filtrable?'<div class="rk-fa">'+(sel===x[0]?'Cliquer : retirer le filtre':'Cliquer : filtrer le tableau sur '+esc(label(x[0])))+'</div>':'')};
  fiche(null);
  [].forEach.call(el.querySelectorAll('.rk-i'),function(li){var i=+li.dataset.i;
    li.onmouseenter=li.onfocus=function(){fiche(i)};li.onmouseleave=li.onblur=function(){fiche(null)};
    var act=function(){if(!filtrable)return;state[d]=sel===e[i][0]?'all':e[i][0];buildFilters();render()};
    li.onclick=act;li.onkeydown=function(ev){if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();act()}}});
  [].forEach.call(el.querySelectorAll('.rk-c'),function(b){b.onclick=function(){if(b.dataset.k!=null)RK.k=+b.dataset.k;else RK.d=+b.dataset.d;renderRank()}})}
__COMPO_JS__
function render(){charts.forEach(function(c){c.destroy()});charts=[];var rows=filtered();
  GOK=gransOK(rows);if(GOK.indexOf(gran)<0)gran=GOK.indexOf('M')>=0?'M':GOK[0];renderKpis(rows);renderGaps(rows);renderDict(rows);renderRank();
  var todo=META.composition?renderCompo(rows):META.indicateurs?renderIndic(rows):renderMain(rows).concat(renderCards(rows));if(window.Chart)todo.forEach(function(f){f()})}
document.getElementById('objective').textContent=window.OBJECTIVE;
try{if(window.self!==window.top)document.body.classList.add('embedded')}catch(e){document.body.classList.add('embedded')}
// Intégré : la page parente ajuste la hauteur du cadre au contenu (pas d'ascenseur interne)
if(document.body.classList.contains('embedded')){var lastH=0,sendH=function(){var h=Math.ceil(document.body.getBoundingClientRect().height);if(Math.abs(h-lastH)>2){lastH=h;parent.postMessage({kldvHeight:h},'*')}};
  if(window.ResizeObserver)new ResizeObserver(sendH).observe(document.body);window.addEventListener('load',sendH);setTimeout(sendH,300)}
// Puce de dates : affiche la période en cours ; un clic ouvre « Début → Fin » (même filtre que dans la barre)
function majPuce(){var r=document.getElementById('range');if(!META.range&&MOISDISPO.length<2)return;r.hidden=false;
  r.querySelector('span').textContent=perActive()?(PER.du&&PER.au?(PER.du===PER.au?moisNom(PER.du):moisNom(PER.du)+' – '+moisNom(PER.au))
    :PER.du?'Depuis '+moisNom(PER.du):'Jusqu’à '+moisNom(PER.au)):(META.range||moisNom(MOISDISPO[0])+' – '+moisNom(MOISDISPO[MOISDISPO.length-1]));
  if(r.classList)r.classList.toggle('on',perActive())}
function periodeChangee(){buildFilters();majPuce();var pp=document.getElementById('perpop');if(!pp.hidden)ouvrirPeriode(true);render()}
function ouvrirPeriode(garder){var pp=document.getElementById('perpop'),r=document.getElementById('range');
  if(!garder&&!pp.hidden){pp.hidden=true;r.setAttribute('aria-expanded','false');return}
  pp.innerHTML='';pp.appendChild(boitePeriode());var a=document.createElement('div');a.className='acts';
  a.innerHTML='<button type="button" data-p="tout">Toute la période</button><button type="button" data-p="fermer">Fermer</button>';pp.appendChild(a);
  a.onclick=function(e){var b=e.target.closest('button');if(!b)return;if(b.dataset.p==='tout'){PER.du=null;PER.au=null;periodeChangee()}
    pp.hidden=true;r.setAttribute('aria-expanded','false')};
  pp.hidden=false;r.setAttribute('aria-expanded','true');if(!garder){var s0=pp.querySelector('select');if(s0)s0.focus()}}
if(MOISDISPO.length>=2)document.getElementById('range').onclick=function(){ouvrirPeriode(false)};
else document.getElementById('range').disabled=true;
if(document.addEventListener)document.addEventListener('keydown',function(e){if(e.key==='Escape'){var pp=document.getElementById('perpop');if(!pp.hidden){pp.hidden=true;document.getElementById('range').setAttribute('aria-expanded','false')}}});
majPuce();
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
    compose = bool((meta or {}).get("composition"))  # hors composition, la page reste exactement celle d'avant
    return (TEMPLATE
            .replace("__COMPO_CSS__", rendu_composition.CSS if compose else "")
            .replace("__COMPO_HTML__", rendu_composition.HTML if compose else "")
            .replace("__COMPO_JS__", rendu_composition.JS if compose else "")
            .replace("__CHARTJS__", chartjs)
            .replace("__DATA__", data_safe)
            .replace("__KPIDEFS__", json.dumps(kpi_defs, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__FILTERCOLS__", json.dumps(filter_cols, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__META__", meta_json)
            .replace("__OBJECTIVE__", obj))


def _long_to_wide(df):
    """Export « long » (DHIS2 : Indicateur + Valeur par ligne) → une colonne par indicateur.

    Sans cela, « somme de Valeur » additionne tous les indicateurs entre eux (dépistés + positifs + décès…).
    Les autres colonnes (période, région, district…) restent des dimensions ; une valeur absente reste vide, jamais 0."""
    import pandas as pd
    ic, vc = _col(df, _IND_COLS), _col(df, _VAL_COLS, numeric=True)
    if ic is None or vc is None or not (2 if not df.attrs.get("agrege") else 1) <= int(df[ic].nunique(dropna=True)) <= 80:
        return df, False
    dims = [c for c in df.columns if c not in (ic, vc)]
    d = df.assign(**{vc: pd.to_numeric(df[vc], errors="coerce"), ic: df[ic].astype(str).str.strip()})
    d[dims] = d[dims].fillna("")
    if not dims:
        wide = d.groupby(ic)[vc].sum(min_count=1).to_frame().T.reset_index(drop=True)
    else:
        wide = d.pivot_table(index=dims, columns=ic, values=vc, aggfunc=lambda x: x.sum(min_count=1)).reset_index()
    wide.columns.name = None
    return wide, True


def generate_dashboard(df, objective: str = "", job_id: str | None = None, llm=None, spec: dict | None = None,
                       dictionnaire: list | None = None, correspondance: dict | None = None,
                       composition: bool = False) -> dict:
    """Génère un tableau de bord (KPI + HTML interactif) depuis un DataFrame.

    Réutilisable par l'orchestrateur HERMES_DA ET par la console KLDataViz (bouton Dashboard).
    Retourne : html (le tableau de bord autonome), path (fichier écrit si job_id), kpis, charts, ia, spec.
    `spec` (facultatif) : indicateurs et graphiques déjà choisis (« spec » d'un appel précédent) → actualisation
    d'une source connectée : mêmes indicateurs sur les nouvelles données, sans appel à l'IA.
    `correspondance` (facultatif) : formules reliant le dictionnaire aux colonnes (proposées par Adifa’, validées
    par l'utilisateur). Présente, elle PILOTE le tableau de bord : tuiles, répartitions, courbes et écarts sont
    calculés par ces formules (hermes_da.correspondance en Python, evalF dans la page), sur les données d'origine.
    `dictionnaire` (facultatif) : indicateurs importés par l'espace (Mode B). La console passe celui de
    l'espace ([] = aucun) ; None = registre PostgreSQL local de HERMES_DA (orchestrateur).
    `composition` : page composée selon les données. Le choix reste dans `spec` : une actualisation garde sa page.
    """
    # Valeurs déjà agrégées : drapeau posé par la source (connecteur DHIS2), ou colonnes exactes du connecteur
    # (si le drapeau s'est perdu en route, ex. copie CSV)
    agrege = bool(df.attrs.get("agrege")) or {"indicateur", "periode", "unite_organisation", "valeur"} <= {
        str(c) for c in df.columns}
    if agrege:
        df.attrs["agrege"] = True
    df_source = df  # avant pivot : le dictionnaire se calcule sur le format d'origine (indicateur + valeur)
    df, pivoted = _long_to_wide(df)
    profile = _profile(df)
    profile_json = json.dumps(profile, ensure_ascii=False, default=str)[:20000]
    if spec is not None:
        spec, ia = {k: v for k, v in spec.items() if k != "_ia"}, bool(spec.get("_ia"))
    else:
        llm_spec = _llm_dashboard(profile_json, llm)
        ia = llm_spec is not None
        spec = llm_spec or _rule_dashboard(profile)

    cols = set(df.columns)
    kpis = []
    import pandas as pd
    p_col, p_ser = _period(df)
    last_p = max((v for v in p_ser.dropna().unique() if isinstance(v, str)), default=None) if p_ser is not None else None
    seen = set()
    hors = None  # réservations annulées / opérations rejetées : hors des totaux
    for c in df.columns:
        if re.search(r"annul|cancel|rejet|refus", _norm(c)):
            low = df[c].dropna().astype(str).str.strip().str.lower()
            yes = [v for v in low.unique() if v in _YES]
            if yes and (~low.isin(_YES)).any():
                hors = {"colonne": str(c), "valeurs": sorted({str(v).strip().lower() for v in df[c].dropna().unique()
                                                              if str(v).strip().lower() in _YES})}
                break
    for k in spec.get("kpis", [])[:8]:
        if last_p and (k.get("agregation") or "").lower() in ("somme", "sum") and k.get("colonne") in cols \
                and _is_stock(_norm(k["colonne"])):
            yearly = p_col in cols and pd.api.types.is_numeric_dtype(df[p_col])
            k = dict(k, agregation="dernier", nom=f"{k.get('nom') or k['colonne']} ({_plabel(last_p, yearly)})")
        col = k.get("colonne")
        if col is not None and col not in cols:
            continue
        key = (col, (k.get("agregation") or "").lower())
        if key in seen:  # même calcul déjà en tuile (l'IA le répète parfois avec une autre dimension)
            continue
        seen.add(key)
        src = df
        if hors and (k.get("agregation") or "").lower() in ("somme", "sum", "dernier") and col is not None:
            src = df[~df[hors["colonne"]].astype(str).str.strip().str.lower().isin(hors["valeurs"])]
            k = dict(k, hors=hors, nom=f"{k.get('nom') or col}, hors « {_joli_min(hors['colonne'])} »")
        v = (_agg(src[p_ser[src.index] == last_p], col, "somme") if (k.get("agregation") == "dernier")
             else _agg(src, col, k.get("agregation", "compte")))
        if v is None:  # incalculable sur ces données (ex. « taux » sur un texte) : pas de tuile « — » ni de courbe vide
            continue
        kpi = {"nom": k.get("nom") or (col or "Total"), "valeur": v, "hors": k.get("hors"),
               "agregation": k.get("agregation"), "colonne": col,
               "dimension": k.get("dimension"), "justification": k.get("justification", "")}
        dim = k.get("dimension")
        if dim and dim in cols and dim != col:
            bd = (_agg_grouped(src[p_ser[src.index] == last_p], col, "somme", dim) if k.get("agregation") == "dernier"
                  else _agg_grouped(src, col, k.get("agregation", "compte"), dim))
            if bd and bd.get("labels"):
                kpi["breakdown"] = bd
                kpi["top"] = (bd["labels"][0], bd["values"][0])
        kpis.append(kpi)

    if not kpis and ia:  # aucune proposition de l'IA n'est calculable : repli par règles
        return generate_dashboard(df, objective, job_id, llm=False, composition=composition or bool(spec.get("composition")))

    charts_count = 0
    for g in spec.get("graphiques", [])[:8]:
        col = g.get("colonne")
        if col is None or col not in cols:
            continue
        if _chart_data(df, col, g.get("type", "bar")).get("labels"):
            charts_count += 1
    charts_count += sum(1 for k in kpis if k.get("breakdown"))

    kpi_defs = [{"nom": k["nom"], "agregation": k["agregation"], "colonne": k["colonne"],
                 "dimension": k.get("dimension"), "hors": k.get("hors")} for k in kpis]
    if len(df) > cube.SEUIL:  # la page ne tient pas tout le fichier : elle garde la valeur juste de chaque tuile
        for d, k in zip(kpi_defs, kpis):
            taux = str(k["agregation"]).lower() in ("taux", "rate", "pct", "proportion") and k["colonne"] in df.columns
            e = cube.echelle_taux(df[k["colonne"]]) if taux else 1
            d["exact"] = _json_safe(k["valeur"] * e)
            if k.get("top"):
                d["tete"] = [str(k["top"][0]), _json_safe(k["top"][1] * e)]
    filter_cols = [str(c) for c in df.columns if 1 < int(df[c].nunique(dropna=True)) <= 30]
    period_col, period_series = _period(df)
    meta = _meta(df, kpi_defs, spec, filter_cols, period_col, period_series)
    if dictionnaire is None:
        try:
            from hermes_da import indicators_catalog as _cat
            dictionnaire = _cat.load_dictionary()
        except Exception:
            dictionnaire = []
    dict_domains = None
    if dictionnaire:
        try:
            from hermes_da import indicators_catalog as _cat
            dict_domains = _cat.to_domains(dictionnaire)
        except Exception:
            dictionnaire = []
    domaine = _detect_domaine(df, dict_domains)
    ic, vc, kis = _keyind(df, domaine)
    keys = _period_key(df, period_col, period_series) if kis else None
    gaps = _gaps(df, keys, ic, vc, kis)
    if not kis and period_series is not None:  # une ligne par patient : comparaison des deux derniers mois
        par_unite = False  # rapport mensuel par centre : chaque (centre, mois) n'apparaît qu'une fois
        if period_col in df.columns:
            for c in df.columns:
                # jamais un identifiant : un registre de visites (un patient par mois) n'est pas un rapport par centre
                if re.search(r"(^|\s)(id|identifiant|matricule|code|n°|no|num|numero|patient)(\s|$)", _norm(c)):
                    continue
                if c != period_col and not pd.api.types.is_numeric_dtype(df[c]) and 2 <= df[c].nunique() < len(df) \
                        and not df.duplicated([c, period_col]).any():
                    par_unite = True
                    break
        kis = _patient_items(df, kpi_defs, domaine, period_col, aggregated=bool(pivoted or agrege or par_unite))
        keys = _period_key(df, period_col, period_series)
        gaps = _patient_gaps(df, keys, kis)
    meta["libelles"] = {str(c): _joli(c) for c in df.columns}  # titres et légendes lisibles dans la page
    if domaine:
        meta["domaine"] = {"cle": domaine["cle"], "nom": domaine["nom"]}
    # Dictionnaire d'indicateurs importé (Mode B) : source de vérité. Indicateurs calculés selon leur
    # type (Nombre/%) et selon le total annuel ; absents signalés ; dans le doute, absent plutôt que faux.
    if dictionnaire:
        try:
            from hermes_da import indicators_catalog as _cat
            dict_inds = dictionnaire
            if dict_inds:
                res = _cat.match_and_compute(df_source, dict_inds)
                llm_fn = _resolve_llm(llm)
                if llm_fn:
                    try:
                        absents = [i for i, r in zip(dict_inds, res) if not r["present"]]
                        res_llm = _cat.match_and_compute_llm(df_source, absents, llm_fn) if absents else None
                        # Le LLM complète seulement ce que le calcul déterministe n'a pas trouvé, sans l'écraser
                        trouves = {r["code"]: r for r in (res_llm or []) if r["present"]}
                        res = [trouves.get(r["code"], r) if not r["present"] else r for r in res]
                    except Exception:
                        pass
                # Variante nationale (« _NAT ») donnant exactement le même calcul qu'un indicateur de base (VL_SUPPRESSION_NAT = TX_PVLS) : un seul affichage
                base_vals = {(r["valeur"], r["detail"]) for r in res
                             if r["present"] and not str(r["code"]).upper().endswith("_NAT")}
                res = [r for r in res if not (r["present"] and str(r["code"]).upper().endswith("_NAT")
                                              and (r["valeur"], r["detail"]) in base_vals)]
                meta["dictionnaire"] = _json_safe({
                    "trouves": [{"code": r["code"], "libelle": r["libelle"], "type": r["type"],
                                 "valeur": r["valeur"], "detail": r["detail"]} for r in res if r["present"]],
                    "absents": [{"code": r["code"], "libelle": r["libelle"]} for r in res if not r["present"]],
                })
        except Exception as exc:  # le tableau de bord reste affiché, mais l'échec laisse une trace
            import sys
            print(f"[AGT-11] dictionnaire d'indicateurs ignoré : {type(exc).__name__} : {exc}", file=sys.stderr, flush=True)
    # ------------------------------------------------------------------ mode « correspondance » : le dictionnaire pilote
    actifs = []
    if correspondance:
        from hermes_da import correspondance as _C
        actifs = [i for i in correspondance.get("indicateurs") or [] if i.get("statut") != "ignore"
                  and i.get("formule") and not _C.valider(df_source, i["formule"])]
    if actifs:
        src = df_source
        pc = correspondance.get("periode") if correspondance.get("periode") in src.columns else None
        if pc is None:  # correspondance sans colonne de période : la même détection que la page classique
            pc = _period(src)[0]
        p_nom, p_ser = _period(src[[pc]]) if pc else (None, None)
        cle = p_ser.map(lambda v: v[:7] if isinstance(v, str) else None) if p_ser is not None else None
        _lic, _lvc = _C.colonnes_longues(src, dictionnaire or [])
        dims = [d for d in correspondance.get("dimensions") or [] if d in src.columns and d != _lic]
        lib = _joli

        def _tit(i):
            court = re.sub(r"^(nombre|%|pourcentage|proportion) d[e']\s*", "", str(i.get("libelle") or ""), flags=re.I)
            court = re.sub(r"\s*\([^)]*\)", "", court)
            court = re.split(r"\s*[:;]|\s+(?:au cours|dans le cadre|selon)\b", court)[0].strip()
            if len(court) > 48:
                court = court[:47].rsplit(" ", 1)[0] + "…"
            return f"{i['code']} · {court[:1].upper() + court[1:]}" if court else i["code"]
        defs, valeurs = [], []
        for i in actifs:
            v = _C.evaluer(src, i["formule"], cle)
            valeurs.append(v)
            defs.append({"nom": _tit(i), "code": i["code"], "libelle": i.get("libelle"), "type": i.get("type"),
                         "agregation": "formule", "colonne": None, "formule": i["formule"],
                         "pct": i["formule"].get("mode") in ("taux", "rapport"), "statut": i.get("statut"),
                         "evolution": bool(i.get("evolution", True)) and cle is not None,
                         "dimension": dims[0] if dims else None,
                         "description": _C.decrire(i["formule"], lib)})
        kpi_defs = defs[:5]
        kpis = [{"nom": d["nom"], "valeur": v, "agregation": "formule", "colonne": None} for d, v in zip(defs[:5], valeurs[:5])]
        if len(src) > cube.SEUIL:  # mêmes tuiles justes que sans dictionnaire
            for d, v in zip(kpi_defs, valeurs):
                d["exact"] = _json_safe(v)
                tete = _C.evaluer_par(src, d["formule"], d["dimension"], cle, 1) if d["dimension"] else []
                if tete:
                    d["tete"] = [str(tete[0][0]), _json_safe(tete[0][1])]
        meta = _meta(src, [], {"graphiques": []}, [], pc, p_ser)
        rang = {d: i for i, d in enumerate(dims)}
        meta["filters"] = sorted(meta.get("filters") or [], key=lambda f: rang.get(f["col"], 99))
        meta.update(measure=None, charts=[], stack=None, indicateurs=defs, dims=dims, libelles={str(c): lib(c) for c in src.columns})
        if domaine:
            meta["domaine"] = {"cle": domaine["cle"], "nom": domaine["nom"]}
        # Section dictionnaire : les indicateurs au-delà des 5 tuiles, et ceux du dictionnaire sans formule
        codes = {d["code"].lower() for d in defs}
        meta["dictionnaire"] = _json_safe({
            "trouves": [{"code": d["code"], "libelle": d["libelle"], "type": d["type"], "valeur": v,
                         "detail": d["description"], "formule": d["formule"], "statut": d["statut"]}
                        for d, v in zip(defs[5:], valeurs[5:])],
            "absents": [{"code": i["code"], "libelle": i["libelle"]} for i in (dictionnaire or [])
                        if i.get("code", "").lower() not in codes]})
        # Écarts : dernière période vs précédente, mêmes formules
        gaps, kis, ic, vc = [], [], None, None
        if cle is not None:
            ps = sorted(k for k in cle.dropna().unique())
            if len(ps) >= 2:
                last, prev = ps[-1], ps[-2]
                for d in defs:
                    # Indicateur d'état actuel (statut du patient) : suivi aussi, par période de la colonne de période,
                    # signalé comme tel dans sa carte (« patients du mois · statut actuel »)
                    f0 = dict(d["formule"], stock=False)
                    cur, pre = _C.evaluer(src[cle == last], f0), _C.evaluer(src[cle == prev], f0)
                    kis.append({"nom": d["nom"], "kind": "formule", "f": f0, "bon": _bon(d["libelle"] or d["nom"]),
                                "libelle": d["description"], "pts": d["pct"], "etat": not d["evolution"]})
                    if cur is None or pre is None or (not d["pct"] and pre == 0):
                        continue
                    dd = cur - pre
                    gaps.append({"indicateur": d["nom"], "libelle": d["description"], "derniere": cur, "precedente": pre,
                                 "etat": not d["evolution"],
                                 "delta": dd, "pct": dd if d["pct"] else dd / pre * 100, "pts": d["pct"],
                                 "bon": _bon(d["libelle"] or d["nom"]), "periode": last, "periode_prec": prev,
                                 "sens": "hausse" if dd > 0 else "baisse" if dd < 0 else "stable"})
        df, period_series, period_col, keys, pivoted = src, p_ser, pc, cle, False
        filter_cols = [f["col"] for f in meta["filters"]]
    data = df.assign(__p=period_series) if period_series is not None else df
    if gaps:
        # Recalculés dans la page selon les filtres (sauf si l'échantillon de 6000 lignes fausserait les sommes)
        meta["gaps"] = _json_safe(gaps)
        periods = sorted(str(k) for k in keys.dropna().unique()) if keys is not None else []
        meta["keyind"] = {"ind": None if ic is None else str(ic), "val": None if vc is None else str(vc), "items": kis,
                          "last": gaps[0]["periode"], "prev": gaps[0]["periode_prec"], "live": len(data) <= cube.SEUIL,
                          "periods": periods, "partial": _partial_month(period_series) if period_series is not None else None,
                          "yearly": bool(period_col in df.columns and pd.api.types.is_numeric_dtype(df[period_col]))}
        if keys is not None:
            data = data.assign(__g=keys)
    meta["agrege"] = bool(pivoted or any(k.get("col") for k in (kis or [])))
    sample = data if len(data) <= cube.SEUIL else data.sample(cube.SEUIL, random_state=0)
    data_json = json.dumps(sample.to_dict(orient="records"), ensure_ascii=False, default=str)
    gros = len(data) > cube.SEUIL
    resume = None
    # Le dictionnaire pilote ses propres calculs : il garde la page classique.
    if (composition or spec.get("composition")) and not meta.get("indicateurs"):
        libelles = spec.get("libelles") or {}
        try:
            from . import composition as compo, fiche as lecture, titres
            # Adifa’ a classé ses KPI : le premier qui a un graphique devient la mesure vedette.
            vedettes = [k.get("colonne") for k in spec.get("kpis") or [] if k.get("agregation") != "compte"] if ia else []
            # Un résumé ne garde pas les valeurs une à une : pas d'histogramme.
            plan = compo.composer(lecture.lire_fiche(df_source), df, vedettes=vedettes, exclus={"histogramme"} if gros else ())
            if gros and plan["cartes"]:
                resume = cube.resumer(data, plan, kpi_defs, [f["col"] for f in meta.get("filters") or []])
                plan = plan if resume else {"cartes": 0}
            if plan["cartes"] and ia and "libelles" not in spec:  # à l'actualisation, les noms enregistrés suffisent
                libelles = titres.demander(plan, _resolve_llm(llm))
            if plan["cartes"]:
                titres.appliquer(plan, libelles)
        except Exception:  # noqa: BLE001 - la page classique reste le repli, jamais une erreur pour l'utilisateur
            plan, resume = {"cartes": 0}, None
        if plan["cartes"]:
            meta["composition"] = plan
            meta["partiel"] = _partial_month(period_series) if period_series is not None else None
            spec = dict(spec, composition=True, libelles=libelles)
    if resume:
        meta["cube"] = True
        meta["filters"] = [f for f in meta.get("filters") or [] if f["col"] not in resume["retires"]]
        if resume["mensuel"]:
            meta["grans"] = [g for g in meta.get("grans") or [] if g not in ("D", "W")] or ["M"]
            meta["gran"] = meta["gran"] if meta["gran"] in meta["grans"] else "M"
        meta["gros"] = {"mode": "resume", "lignes": len(data), "retires": resume["retires"], "mensuel": resume["mensuel"]}
        data_json = json.dumps(resume["cases"].to_dict(orient="records"), ensure_ascii=False, default=str)
    elif gros:
        meta["gros"] = {"mode": "echantillon", "lignes": len(data),
                        "trop_detaille": bool((composition or spec.get("composition")) and not meta.get("indicateurs"))}

    html = _render_html(objective, data_json, kpi_defs, filter_cols, meta)
    path = None
    if job_id:
        config.ensure_dirs()
        path = config.ARTIFACTS_DIR / f"{job_id}__dashboard.html"
        path.write_text(html, encoding="utf-8")

    return {"html": html, "path": str(path) if path else None,
            "kpis": _json_safe(kpis), "charts": charts_count, "ia": ia,
            "filter_cols": filter_cols, "kpi_defs": kpi_defs, "format_long": pivoted,
            "domaine": _json_safe(domaine), "gaps": _json_safe(gaps), "spec": _json_safe(dict(spec, _ia=ia)),
            "dictionnaire": _json_safe(meta.get("dictionnaire")),
            "filtres": _json_safe(meta.get("filters") or [])}  # filtres de la page : la console y cale ses graphiques ajoutés


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
