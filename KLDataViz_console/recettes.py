# -*- coding: utf-8 -*-
"""Recettes de transformation : la remise en forme d'un fichier en étapes simples, visibles et rejouées.

Comme les « étapes appliquées » de Power Query : chaque étape est une opération déterministe (aucune IA dans
l'exécution), décrite en une phrase, avec le nombre de lignes avant et après. Une recette est mémorisée pour une
forme de fichier (ses colonnes) : le fichier du mois suivant, une source connectée ou un rapport planifié la rejouent
à la lecture. Si une étape ne s'applique plus (colonne disparue), la recette s'arrête et le dit : jamais un résultat
à moitié transformé présenté comme juste.

Opérations : recopier_bas, depivoter, retirer_lignes, garder_lignes, fractionner, renommer, retirer_colonnes,
remplacer, convertir, regrouper. `proposer` en suggère une recette par règles, à partir des défauts typiques des
classeurs Excel (cellules fusionnées, mois en colonnes, sous-totaux, « District - Centre » dans une seule colonne)."""
import datetime as dt
import hashlib
import json
import os
import re
import threading
import unicodedata

import numpy as np
import pandas as pd

VERSION = 1
MAX_ETAPES = 40
_verrou = threading.Lock()
TOTAL = re.compile(r"^\s*(sous[-\s]?|grand\s+)?tot(al(e|s)?|aux)(\s+g[ée]n[ée]ral)?\b|^\s*ensemble\s*$", re.I)
MOIS = ["janv", "fev", "mars", "avr", "mai", "juin", "juil", "aout", "sept", "oct", "nov", "dec",
        "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep"]
_MOIS_RE = re.compile(r"^(" + "|".join(sorted(MOIS, key=len, reverse=True)) + r")[a-z]*\.?(\s*[-/ ]?\s*(\d{2}|\d{4}))?$")
AGG = {"somme": "sum", "moyenne": "mean", "compte": "count", "min": "min", "max": "max", "mediane": "median"}


class Erreur(ValueError):
    """Étape impossible sur ce fichier : le message dit laquelle et pourquoi."""


def _norme(t):
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().lower().strip()
    return re.sub(r"\s+", " ", t)


def _txt(x):
    """Texte sûr d'une colonne ou d'un tableau : une case vide devient « », jamais NaN (pandas 3 garde NaN dans une
    colonne de texte, même après astype(str))."""
    return x.astype(object).where(x.notna(), "").astype(str)


def _colonnes(df, noms, etape):
    absentes = [c for c in noms if c not in df.columns]
    if absentes:
        raise Erreur(f"Étape « {libelle(etape)} » : colonne(s) absente(s) de ce fichier : {', '.join(map(str, absentes))}.")
    return list(noms)


# ---------------------------------------------------------------- opérations
def _recopier_bas(df, e):
    cols = _colonnes(df, e["colonnes"], e)
    vide = df[cols].isna() | _txt(df[cols]).apply(lambda s: s.str.strip() == "")
    out = df.copy()
    out[cols] = df[cols].mask(vide).ffill()
    return out, int((vide & out[cols].notna()).sum().sum())


def _depivoter(df, e):
    if e.get("colonnes"):
        valeurs = _colonnes(df, e["colonnes"], e)
    else:
        rx = re.compile(e["motif"], re.I)
        valeurs = [c for c in df.columns if rx.search(str(c))]
        if not valeurs:
            raise Erreur(f"Étape « {libelle(e)} » : aucune colonne ne correspond.")
    ident = [c for c in df.columns if c not in valeurs]
    nom, val = e.get("nom") or "attribut", e.get("valeur") or "valeur"
    long = df.melt(id_vars=ident, value_vars=valeurs, var_name=nom, value_name=val)
    if not e.get("garder_vides", False):
        long = long[long[val].notna() & (_txt(long[val]).str.strip() != "")]
    return long.reset_index(drop=True), len(valeurs)


def _masque(df, e):
    if e.get("vides"):
        return df.isna().all(axis=1) | _txt(df).apply(lambda s: s.str.strip().isin(["", "nan", "None"])).all(axis=1)
    rx = re.compile(e.get("motif") or "", re.I) if e.get("motif") else None
    cols = _colonnes(df, [e["colonne"]], e) if e.get("colonne") else [c for c in df.columns if df[c].dtype == object
                                                                         or pd.api.types.is_string_dtype(df[c])]
    if not cols:
        return pd.Series(False, index=df.index)
    if e.get("valeurs") is not None:
        cibles = {_norme(v) for v in e["valeurs"]}
        return _txt(df[cols]).apply(lambda s: s.map(_norme).isin(cibles)).any(axis=1)
    if rx is None:
        raise Erreur(f"Étape « {libelle(e)} » : ni motif ni valeurs.")
    return _txt(df[cols]).apply(lambda s: s.map(lambda v: bool(rx.search(v)))).any(axis=1)


def _retirer_lignes(df, e):
    m = _masque(df, e)
    return df[~m].reset_index(drop=True), int(m.sum())


def _garder_lignes(df, e):
    m = _masque(df, e)
    return df[m].reset_index(drop=True), int((~m).sum())


def _fractionner(df, e):
    col = _colonnes(df, [e["colonne"]], e)[0]
    noms = e.get("noms") or [f"{col}_1", f"{col}_2"]
    parts = _txt(df[col]).where(df[col].notna()).str.split(e.get("separateur", " - "), n=len(noms) - 1, regex=False,
                                                                  expand=True)
    out = df.copy()
    pos = list(out.columns).index(col)
    for i, nom in enumerate(noms):
        serie = parts[i].str.strip() if i in parts.columns else pd.Series(np.nan, index=df.index)
        out.insert(pos + 1 + i, nom, serie)
    if not e.get("garder", False):
        out = out.drop(columns=[col])
    return out, int(df[col].notna().sum())


def _renommer(df, e):
    _colonnes(df, list(e["colonnes"]), e)
    return df.rename(columns=e["colonnes"]), len(e["colonnes"])


def _retirer_colonnes(df, e):
    cols = _colonnes(df, e["colonnes"], e)
    return df.drop(columns=cols), len(cols)


def _remplacer(df, e):
    col = _colonnes(df, [e["colonne"]], e)[0]
    rempl = {str(k): v for k, v in e["remplacements"].items()}
    avant = _txt(df[col]).str.strip()
    out = df.copy()
    touche = avant.isin(rempl)
    out.loc[touche, col] = avant[touche].map(rempl)
    return out, int(touche.sum())


def _convertir(df, e):
    col = _colonnes(df, [e["colonne"]], e)[0]
    out = df.copy()
    genre = e["type"]
    if genre == "nombre":
        import lecture
        out[col] = df[col].map(lambda v: v if isinstance(v, (int, float, np.number)) and not isinstance(v, bool)
                               else lecture._nombre(v) if pd.notna(v) else np.nan).astype(float)
    elif genre == "date":
        import modeling
        out[col] = modeling.to_dates(df[col].astype(str).where(df[col].notna(), ""))
    elif genre == "texte":
        out[col] = df[col].map(lambda v: v if pd.isna(v) else str(v).strip())
    else:
        raise Erreur(f"Type inconnu : {genre}.")
    perdus = int((df[col].notna() & out[col].isna()).sum())
    if perdus:
        e["_perdus"] = perdus
    return out, int(df[col].notna().sum())


def _regrouper(df, e):
    par = _colonnes(df, e["par"], e)
    mesures = e.get("mesures") or {}
    _colonnes(df, list(mesures), e)
    if not mesures:
        out = df.groupby(par, dropna=False).size().reset_index(name="nombre")
    else:
        out = df.groupby(par, dropna=False).agg({c: AGG.get(a, a) for c, a in mesures.items()}).reset_index()
    return out, len(df)


OPERATIONS = {"recopier_bas": _recopier_bas, "depivoter": _depivoter, "retirer_lignes": _retirer_lignes,
              "garder_lignes": _garder_lignes, "fractionner": _fractionner, "renommer": _renommer,
              "retirer_colonnes": _retirer_colonnes, "remplacer": _remplacer, "convertir": _convertir,
              "regrouper": _regrouper}


def libelle(e):
    """L'étape en une phrase, comme dans la liste des étapes appliquées."""
    op = e.get("op")
    liste = lambda xs: ", ".join(f"« {x} »" for x in xs)  # noqa: E731
    if op == "recopier_bas":
        return f"Recopier vers le bas {liste(e.get('colonnes', []))} (cellules fusionnées)"
    if op == "depivoter":
        quoi = liste(e["colonnes"][:3]) + ("…" if len(e["colonnes"]) > 3 else "") if e.get("colonnes") else f"les colonnes « {e.get('motif')} »"
        return f"Dépivoter {quoi} en « {e.get('nom') or 'attribut'} » / « {e.get('valeur') or 'valeur'} »"
    if op in ("retirer_lignes", "garder_lignes"):
        verbe = "Retirer" if op == "retirer_lignes" else "Garder"
        if e.get("vides"):
            return f"{verbe} les lignes vides"
        if e.get("motif") == TOTAL.pattern:
            return f"{verbe} les lignes de total et de sous-total"
        cond = (f"valant {liste(e['valeurs'])}" if e.get("valeurs") is not None else f"contenant « {e.get('motif')} »")
        return f"{verbe} les lignes {cond}" + (f" dans « {e['colonne']} »" if e.get("colonne") else "")
    if op == "fractionner":
        return f"Fractionner « {e.get('colonne')} » au séparateur « {e.get('separateur', ' - ')} » en {liste(e.get('noms') or [])}"
    if op == "renommer":
        return "Renommer " + ", ".join(f"« {a} » en « {b} »" for a, b in (e.get("colonnes") or {}).items())
    if op == "retirer_colonnes":
        return f"Retirer les colonnes {liste(e.get('colonnes', []))}"
    if op == "remplacer":
        return f"Remplacer dans « {e.get('colonne')} » " + ", ".join(f"« {a} » par « {b} »" for a, b in (e.get("remplacements") or {}).items())
    if op == "convertir":
        return f"Convertir « {e.get('colonne')} » en {e.get('type')}"
    if op == "regrouper":
        return f"Regrouper par {liste(e.get('par', []))}" + (
            " : " + ", ".join(f"{a} de « {c} »" for c, a in (e.get("mesures") or {}).items()) if e.get("mesures") else "")
    return str(op)


def appliquer(df, etapes):
    """Rejoue les étapes dans l'ordre. Rend le tableau final et le journal (une entrée par étape : phrase, lignes et
    colonnes avant/après, éléments touchés). Une étape impossible lève Erreur : on ne garde pas un résultat partiel."""
    if len(etapes) > MAX_ETAPES:
        raise Erreur(f"{MAX_ETAPES} étapes au plus.")
    journal = []
    for i, e in enumerate(etapes, 1):
        e = dict(e)
        op = OPERATIONS.get(e.get("op"))
        if op is None:
            raise Erreur(f"Étape {i} : opération inconnue « {e.get('op')} ».")
        avant = df.shape
        try:
            df, touches = op(df, e)
        except Erreur:
            raise
        except Exception as exc:  # noqa: BLE001 - une étape mal paramétrée s'explique, sans trace technique
            raise Erreur(f"Étape {i} « {libelle(e)} » impossible : {type(exc).__name__}.") from None
        entree = {"etape": i, "op": e["op"], "libelle": libelle(e), "lignes_avant": int(avant[0]),
                  "lignes_apres": int(df.shape[0]), "colonnes_apres": [str(c) for c in df.columns], "touches": touches}
        if e.get("_perdus"):
            entree["avertissement"] = f"{e['_perdus']} valeur(s) illisible(s) devenue(s) vide(s)"
        journal.append(entree)
    return df, journal


# ---------------------------------------------------------------- proposition par règles
def _entete_periode(nom):
    t = _norme(nom).replace(".", "")
    return bool(_MOIS_RE.match(t) or re.fullmatch(r"(19|20)\d{2}([-/](0?[1-9]|1[0-2]))?", t)
                or re.fullmatch(r"(t|q|s)[1-4]\s*[-/ ]?\s*((19|20)\d{2})?", t)
                or re.fullmatch(r"((19|20)\d{2})\s*[-/ ]?\s*(t|q|s)[1-4]", t))


def proposer(df):
    """Recette suggérée pour les défauts typiques d'un classeur Excel. Rien n'est appliqué : c'est une proposition."""
    etapes = []
    textes = [c for c in df.columns if df[c].dtype == object or pd.api.types.is_string_dtype(df[c])]
    # 1. Cellules fusionnées : une colonne de libellés vide sous chaque libellé, à gauche des chiffres
    fusionnees = []
    for c in textes[:4]:
        s = df[c]
        vide = s.isna() | (_txt(s).str.strip() == "")
        courts = _txt(s[~vide]).str.len().mean() <= 40          # des libellés, pas des commentaires
        # une colonne fusionnée est vide sur une bonne part de ses lignes (une case vide isolée n'en fait pas une)
        if 0.2 <= vide.mean() <= 0.9 and not vide.iloc[0] and courts:
            autres = df.drop(columns=[c])
            if autres[vide].notna().any(axis=1).mean() > 0.8:      # les lignes vides ici sont remplies ailleurs
                fusionnees.append(c)
    if fusionnees:
        etapes.append({"op": "recopier_bas", "colonnes": fusionnees})
    # 2. Sous-totaux et totaux mêlés aux détails
    if textes and _txt(df[textes]).apply(lambda s: s.str.match(TOTAL)).any(axis=1).any():
        etapes.append({"op": "retirer_lignes", "motif": TOTAL.pattern})
    # 3. Fractionner « District - Centre »
    for c in textes:
        s = _txt(df[c].dropna())
        if len(s) >= 3 and s.str.count(" - ").eq(1).mean() >= 0.9:
            noms = [p.strip() for p in str(c).split(" - ")] if str(c).count(" - ") == 1 else [f"{c}_1", f"{c}_2"]
            etapes.append({"op": "fractionner", "colonne": str(c), "separateur": " - ", "noms": noms})
    # 4. Périodes en colonnes (janv., févr. … ou 2023, 2024 …) : une colonne Période, une colonne Valeur
    periodes = [c for c in df.columns if _entete_periode(c)]
    if len(periodes) >= 3:
        etapes.append({"op": "depivoter", "colonnes": [str(c) for c in periodes], "nom": "periode", "valeur": "valeur"})
    return etapes


# ---------------------------------------------------------------- mémoire des recettes
def signature(df):
    """Forme d'un fichier : ses colonnes (noms normalisés, dans l'ordre). Deux mois d'un même rapport ont la même."""
    # empreinte de forme, pas de sécurité : usedforsecurity=False (Bandit B324), les signatures restent les mêmes
    return hashlib.sha1("|".join(_norme(c) for c in df.columns).encode(), usedforsecurity=False).hexdigest()[:16]


def _chemin(dossier):
    os.makedirs(dossier, exist_ok=True)
    return os.path.join(dossier, "recettes.json")


def _lire(dossier):
    try:
        with open(_chemin(dossier), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def enregistrer(dossier, df, etapes, nom=""):
    """Mémorise la recette pour cette forme de fichier. Elle est d'abord essayée : une recette qui échoue n'est pas
    enregistrée."""
    appliquer(df, etapes)
    propre = [{k: v for k, v in e.items() if not k.startswith("_")} for e in etapes]
    with _verrou:
        tout = _lire(dossier)
        tout[signature(df)] = {"version": VERSION, "etapes": propre, "fichier": str(nom)[:200],
                               "colonnes": [str(c) for c in df.columns][:200],
                               "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        with open(_chemin(dossier), "w", encoding="utf-8") as f:
            json.dump(tout, f, ensure_ascii=False, indent=1)
    return tout[signature(df)]


def supprimer(dossier, df):
    with _verrou:
        tout = _lire(dossier)
        retiree = tout.pop(signature(df), None)
        with open(_chemin(dossier), "w", encoding="utf-8") as f:
            json.dump(tout, f, ensure_ascii=False, indent=1)
    return retiree is not None


def trouver(dossier, df):
    return _lire(dossier).get(signature(df))


def rejouer(dossier, df):
    """À la lecture d'un fichier : applique la recette mémorisée pour sa forme, s'il y en a une. Si elle échoue, le
    fichier est rendu tel quel avec l'erreur dans `attrs["recette_erreur"]` (le bilan qualité la signale)."""
    r = trouver(dossier, df) if dossier else None
    if not r:
        return df
    attrs = dict(df.attrs)
    try:
        out, journal = appliquer(df, r["etapes"])
    except Erreur as exc:
        df.attrs["recette_erreur"] = str(exc)
        return df
    out.attrs = dict(attrs, recette={"etapes": journal, "fichier_modele": r.get("fichier"), "date": r.get("date")})
    return out
