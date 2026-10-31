# -*- coding: utf-8 -*-
"""Fiche d'un fichier : ce qu'il contient, décrit sans IA, pour choisir les graphiques et la mise en page.

Réutilise les détections du tableau de bord (périodes, stocks, format long). Rien ici ne calcule un indicateur.
Principe : dans le doute, une colonne n'est pas classée. Mieux vaut un graphique de moins qu'un graphique faux."""
import re

import pandas as pd

from .dashboard import _ID_RE, _MESURE_RE, _NO, _TEMPS_RE, _VOLUME_RE, _YES, _is_stock, _long_to_wide, _norm, _period

LIEU_RE = re.compile(r"(^|\s)(pays|region|district|departement|prefecture|province|commune|ville|localite|quartier|zone|"
                     r"secteur geographique|site|centre|structure|etablissement|formation sanitaire|fosa|agence|magasin|"
                     r"boutique|unite organisation|organisation unit|orgunit|ou)(\s|$)")
OBJECTIF_RE = re.compile(r"(^|\s)(objectif|objectifs|cible|cibles|target|targets|prevision|previsions|prevu|budget)(\s|$)")
TAUX_RE = re.compile(r"(^|\s)(taux|pourcentage|pourcent|pct|ratio|proportion|part|rate)(\s|$)")
MAX_CATEGORIES = 30


def _texte(col):
    return re.sub(r"\s+", " ", _norm(str(col)).replace("_", " ")).strip()


def _est_oui_non(s):
    v = s.dropna()
    if v.empty or v.nunique() > 2:
        return False
    if pd.api.types.is_numeric_dtype(s):
        return set(pd.to_numeric(v).unique()) <= {0, 1}
    return all(_norm(str(x)) in _YES | _NO for x in v.unique())


def _est_identifiant(col, s, lignes):
    if _ID_RE.search(_texte(col)):
        return True
    return not pd.api.types.is_numeric_dtype(s) and lignes > 20 and s.nunique(dropna=True) >= 0.95 * lignes


def _nature_mesure(c, s, periodique):
    """stock, taux, volume (s'additionne) ou moyenne (ne s'additionne pas)."""
    n, v = _texte(c), pd.to_numeric(s, errors="coerce").dropna()
    if "%" in str(c) or TAUX_RE.search(n) or (len(v) and v.between(0, 1).all() and (v % 1 != 0).any()):
        return "taux"
    if _is_stock(n):
        return "stock"
    if re.match(r"(nombre|nb|effectif|total|volume)\b", n) or (_VOLUME_RE.search(n) and not _MESURE_RE.search(n)):
        return "volume"
    entier = len(v) > 0 and (v % 1 == 0).all() and v.min() >= 0
    if periodique and entier and not _MESURE_RE.search(n):  # rapport périodique : des entiers sans unité sont des comptages
        return "volume"
    return "moyenne"


def _fiche_periode(col, serie):
    cles = sorted(k for k in serie.dropna().unique() if isinstance(k, str) and len(k) in (7, 10))
    if len(cles) < 2:
        return None
    mois = sorted({k[:7] for k in cles})
    annees = sorted({k[:4] for k in cles})
    repetes = {m[5:] for m in mois if m[:4] == annees[-1]} & {m[5:] for m in mois if m[:4] == annees[-2]} if len(annees) > 1 else set()
    return {"colonne": col, "debut": cles[0], "fin": cles[-1], "valeurs": len(cles), "mois": len(mois), "annees": len(annees),
            "unite": "jour" if len(cles[0]) == 10 and len(cles) > len(mois) else "annee" if len(mois) == len(annees) else "mois",
            "comparable_n1": bool(repetes), "mois_communs": sorted(repetes)}  # comparer une année partielle à une année entière serait faux


def _hierarchie(df, lieux):
    """Vrai si chaque lieu fin appartient à un seul lieu plus large (région > district > centre)."""
    for large, fin in zip(lieux, lieux[1:]):
        if (df[[large, fin]].dropna().groupby(fin)[large].nunique() > 1).any():
            return False
    return len(lieux) > 1


def lire_fiche(df):
    """Décrit un DataFrame : période, lieux, catégories, mesures, taux, stocks, objectifs, colonnes Oui/Non."""
    source = df
    df, long_format = _long_to_wide(df)
    df = df.loc[:, ~df.columns.duplicated()]
    lignes = int(len(df))
    fiche = {"lignes": lignes, "colonnes": int(df.shape[1]), "format": "long" if long_format else "large",
             "agrege": bool(source.attrs.get("agrege")) or long_format, "periode": None, "lieux": [], "hierarchie": False,
             "categories": [], "volumes": [], "moyennes": [], "taux": [], "stocks": [], "objectifs": [], "oui_non": [],
             "identifiants": [], "ignorees": []}
    if lignes == 0:
        fiche["capacites"] = capacites(fiche)
        return fiche
    col_p, serie = _period(df)
    fiche["periode"] = _fiche_periode(col_p, serie) if col_p else None
    colonne_periode = fiche["periode"]["colonne"] if fiche["periode"] else None
    periodique = colonne_periode is not None
    for c in df.columns:
        if c == colonne_periode or c == col_p:
            continue
        s, n = df[c], _texte(c)
        distinct = int(s.nunique(dropna=True))
        if distinct == 0:
            fiche["ignorees"].append(str(c))
        elif _est_oui_non(s):
            fiche["oui_non"].append(str(c))
        elif _TEMPS_RE.search(n):
            fiche["ignorees"].append(str(c))
        elif _est_identifiant(c, s, lignes):
            fiche["identifiants"].append(str(c))
        elif pd.api.types.is_numeric_dtype(s):
            if OBJECTIF_RE.search(n):
                fiche["objectifs"].append(str(c))
            else:
                nature = _nature_mesure(c, s, periodique)
                fiche[{"stock": "stocks", "taux": "taux", "volume": "volumes", "moyenne": "moyennes"}[nature]].append(str(c))
        elif LIEU_RE.search(n) and 2 <= distinct:
            fiche["lieux"].append(str(c))
        elif 2 <= distinct <= MAX_CATEGORIES:
            fiche["categories"].append(str(c))
        else:
            fiche["ignorees"].append(str(c))
    fiche["lieux"].sort(key=lambda c: df[c].nunique(dropna=True))
    fiche["hierarchie"] = _hierarchie(df, fiche["lieux"])
    fiche["capacites"] = capacites(fiche)
    return fiche


def capacites(f):
    """Ce que le fichier permet de montrer. Le catalogue de graphiques s'appuie sur ces faits, pas sur le sujet du fichier."""
    additifs = f["volumes"]
    mesures = additifs + f["moyennes"] + f["taux"] + f["stocks"]
    per = f["periode"] or {}
    groupes = f["lieux"] + f["categories"]
    return {"evolution": bool(per) and bool(mesures), "comparaison_n1": bool(per.get("comparable_n1")) and bool(mesures),
            "saisonnalite": per.get("mois", 0) >= 12 and bool(groupes) and bool(additifs),
            "classement": bool(groupes) and bool(mesures), "part_du_total": bool(groupes) and bool(additifs),
            "relation": len(mesures) >= 2, "objectif": bool(f["objectifs"]) and bool(mesures),
            "geographie": bool(f["lieux"]), "structure": f["hierarchie"]}
