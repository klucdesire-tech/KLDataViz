# -*- coding: utf-8 -*-
"""Recettes de transformation : la remise en forme d'un fichier en étapes simples, visibles et rejouées.

Comme les « étapes appliquées » de Power Query : chaque étape est une opération déterministe (aucune IA dans
l'exécution), décrite en une phrase, avec le nombre de lignes avant et après. Une recette est mémorisée pour une
forme de fichier (ses colonnes) : le fichier du mois suivant, une source connectée ou un rapport planifié la rejouent
à la lecture. Si une étape ne s'applique plus (colonne disparue), la recette s'arrête et le dit : jamais un résultat
à moitié transformé présenté comme juste.

Opérations : recopier_bas, depivoter, retirer_lignes, garder_lignes, fractionner, renommer, retirer_colonnes,
remplacer, convertir, regrouper, decomposer, extraire, nettoyer_texte. `proposer` en suggère une recette par règles, à
partir des défauts typiques des classeurs Excel (cellules fusionnées, mois en colonnes, sous-totaux, « District -
Centre » dans une seule colonne) et des exports de plateformes (DHIS2 : indicateur, période, sexe et âge collés dans
les titres ou les catégories ; niveaux orgunitlevel ; colonne recopiée)."""
import datetime as dt
import hashlib
import json
import os
import re
import threading
import unicodedata

import numpy as np
import pandas as pd

import dimensions

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


# ---------------------------------------------------------------- informations collées (période, sexe, âge)
NON_VENTILE = "Non ventilé"


def _nombres(s):
    """Valeurs d'une colonne de chiffres ; une case vide ou du texte devient NaN (jamais 0)."""
    return pd.to_numeric(s, errors="coerce")


def _nom_libre(df, nom):
    return nom if nom not in df.columns else f"{nom} (extrait)"


def _niveaux(entrees):
    """Les entrées (titre ou valeur → (reste, dimensions)) d'un même indicateur, regroupées par jeu de dimensions.

    Rend (garder, agregats, variantes) :
    - un jeu qui contient tous les autres (le plus détaillé) : on le garde, les autres sont des sous-totaux recalculables ;
    - sinon (« par sexe » et « par âge » non croisés) : chaque jeu devient une variante de l'indicateur, jamais additionnée
      aux autres."""
    jeux = {}
    for cle, (reste, dims) in entrees.items():
        jeux.setdefault(frozenset(dims), []).append(cle)
    if len(jeux) == 1:
        return list(entrees), [], {}
    plus_grand = max(jeux, key=len)
    if all(j <= plus_grand for j in jeux):
        return jeux[plus_grand], [c for j, cs in jeux.items() if j != plus_grand for c in cs], {}
    noms = {"sexe": "sexe", "tranche_age": "âge", "periode": "période"}
    variantes = {}
    for j, cs in jeux.items():
        quoi = " et ".join(noms[d] for d in dimensions.DIMENSIONS if d in j and d != "periode")
        for c in cs:
            variantes[c] = f"par {quoi}" if quoi else "ensemble"
    return list(entrees), [], variantes


def _ecarts_sous_totaux(df, ident, details, agregats, parts):
    """Sous-totaux écartés comparés à la somme de leurs détails, ligne par ligne : nombre de cases en écart."""
    ecarts = 0
    for a in agregats:
        dims_a = parts[a][1]
        enfants = [d for d in details if parts[d][0] == parts[a][0]
                   and all(parts[d][1].get(k) == v for k, v in dims_a.items())]
        if not enfants:
            continue
        somme = _nombres(df[enfants].stack()).groupby(level=0).sum(min_count=1).reindex(df.index)
        agg = _nombres(df[a])
        diff = (agg - somme).abs() > 1e-9
        ecarts += int((diff & agg.notna() & somme.notna()).sum())
    return ecarts


def plan_decomposer(df, e):
    """Ce que fait l'étape « decomposer » sur ce tableau, sans l'appliquer (partagé avec l'export Power Query) :
    (colonnes gardées, sous-totaux écartés, correspondance titre → {colonne: valeur}, titres découpés, nom de la
    colonne des chiffres). Un seul indicateur : la colonne des chiffres porte son nom (« Dépistés par district »,
    pas « Valeur par district ») et il n'y a pas de colonne Indicateur."""
    cols = _colonnes(df, e["colonnes"], e)
    val = e.get("valeur") or "Valeur"
    parts = {c: dimensions.decouper(c) for c in cols}
    if not any(d for _, d in parts.values()):
        raise Erreur(f"Étape « {libelle(e)} » : aucun titre ne contient de période, de sexe ou de tranche d'âge.")
    garder, ecartees, variantes = [], [], {}
    par_indicateur = {}
    for c, p in parts.items():
        par_indicateur.setdefault(p[0], {})[c] = p
    for entrees in par_indicateur.values():
        g, a, v = _niveaux(entrees)
        garder += g
        ecartees += a
        variantes.update(v)
    garder = [c for c in cols if c in garder]
    presentes = [d for d in dimensions.DIMENSIONS if any(d in parts[c][1] for c in garder)]
    noms = {d: _nom_libre(df, dimensions.NOMS[d]) for d in presentes}
    nom_ind = _nom_libre(df, dimensions.NOMS["indicateur"])
    avec_ind = any(parts[c][0] for c in garder) or variantes
    carte = {}
    for c in garder:
        reste, dims = parts[c]
        ind = reste or e.get("indicateur") or val
        if c in variantes:
            ind = f"{ind} ({variantes[c]})"
        carte[c] = {**({nom_ind: ind} if avec_ind else {}), **{noms[d]: dims.get(d, NON_VENTILE) for d in presentes}}
    indicateurs = {v[nom_ind] for v in carte.values()} if avec_ind else set()
    if len(indicateurs) == 1 and not variantes:
        val = _nom_libre(df.drop(columns=cols), next(iter(indicateurs)))
        for v in carte.values():
            del v[nom_ind]
    return garder, ecartees, carte, parts, val


def _decomposer(df, e):
    """Colonnes dont le titre colle indicateur, période, sexe, âge → une ligne par combinaison, une colonne par
    information. Le total des valeurs gardées est vérifié avant et après."""
    garder, ecartees, correspondance, parts, val = plan_decomposer(df, e)
    ident = [c for c in df.columns if c not in e["colonnes"]]
    carte = pd.DataFrame([{"_col": c, **v} for c, v in correspondance.items()])
    # nom provisoire : le nom de l'indicateur peut être le titre d'une colonne dépliée (« Dépistés », son total)
    long = df[ident + garder].melt(id_vars=ident, value_vars=garder, var_name="_col", value_name="_valeur_")
    long = long.merge(carte, on="_col", how="left").drop(columns="_col").rename(columns={"_valeur_": val})
    if not e.get("garder_vides", False):
        long = long[long[val].notna() & (_txt(long[val]).str.strip() != "")]
    sorties = [c for c in carte.columns if c != "_col"]
    long = long[ident + sorties + [val]].reset_index(drop=True)
    avant, apres = _nombres(df[garder].stack()).sum(), _nombres(long[val]).sum()
    if abs(avant - apres) > 1e-6 * max(1.0, abs(avant)):
        raise Erreur(f"Étape « {libelle(e)} » : le total change ({avant:g} avant, {apres:g} après). Étape refusée.")
    if ecartees:
        e["_sous_totaux"] = len(ecartees)
        e["_ecarts"] = _ecarts_sous_totaux(df, ident, garder, ecartees, parts)
    return long, len(garder)


def _extraire(df, e):
    """Une colonne de texte qui colle plusieurs informations (« VIH - Dépistés Masculin, 15-24 ans ») → une colonne
    par information, à la place de l'ancienne."""
    import dimensions
    col = _colonnes(df, [e["colonne"]], e)[0]
    vals = _txt(df[col]).str.strip()
    # « default » : la catégorie DHIS2 d'un élément sans ventilation, ni indicateur ni sous-total.
    parts = {v: ("", {}) if v.lower() == "default" else dimensions.decouper(v) for v in vals.unique() if v}
    if not any(d for _, d in parts.values()):
        raise Erreur(f"Étape « {libelle(e)} » : aucune période, aucun sexe ni aucune tranche d'âge dans « {col} ».")
    par_indicateur = {}
    for v, p in parts.items():
        if v.lower() != "default":
            par_indicateur.setdefault(p[0], {})[v] = p
    variantes = {}
    for reste, entrees in par_indicateur.items():
        g, a, v = _niveaux(entrees)
        if a:
            raise Erreur(f"Étape « {libelle(e)} » : « {col} » mêle des détails et des sous-totaux "
                         f"(ex. « {a[0]} »). Retirez d'abord ces lignes de sous-total.")
        if v and not reste:
            raise Erreur(f"Étape « {libelle(e)} » : « {col} » mêle des ventilations différentes sans indicateur.")
        variantes.update(v)
    presentes = [d for d in dimensions.DIMENSIONS if any(d in p[1] for p in parts.values())]
    avec_ind = any(p[0] for p in parts.values())
    out = df.copy()
    pos = list(out.columns).index(col)
    nouvelles = []
    if avec_ind:
        nom = _nom_libre(df.drop(columns=[col]), e.get("nom") or dimensions.NOMS["indicateur"])
        ind = vals.map(lambda v: (parts[v][0] + (f" ({variantes[v]})" if v in variantes else "")) if parts.get(v, ("",))[0]
                       else np.nan)
        nouvelles.append((nom, ind))
    for d in presentes:
        nom = _nom_libre(df.drop(columns=[col]), dimensions.NOMS[d])
        nouvelles.append((nom, vals.map(lambda v, d=d: parts[v][1].get(d, NON_VENTILE) if v else np.nan)))
    out = out.drop(columns=[col])
    for i, (nom, serie) in enumerate(nouvelles):
        out.insert(pos + i, nom, serie)
    return out, int((vals != "").sum())


def _nettoyer_texte(df, e):
    """Espaces en trop dans les libellés (« ␣CS Alpha », « Nord␣␣Est ») : sans cela, deux écritures du même
    lieu comptent comme deux lieux."""
    out, touches = df.copy(), 0
    for c in df.columns:
        s = df[c]
        if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
            continue
        propre = s.map(lambda v: " ".join(v.split()) if isinstance(v, str) else v)
        change = s.notna() & (_txt(s) != _txt(propre))
        if change.any():
            out[c] = propre
            touches += int(change.sum())
    return out, touches


OPERATIONS = {"recopier_bas": _recopier_bas, "depivoter": _depivoter, "retirer_lignes": _retirer_lignes,
              "garder_lignes": _garder_lignes, "fractionner": _fractionner, "renommer": _renommer,
              "retirer_colonnes": _retirer_colonnes, "remplacer": _remplacer, "convertir": _convertir,
              "regrouper": _regrouper, "decomposer": _decomposer, "extraire": _extraire,
              "nettoyer_texte": _nettoyer_texte}


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
    if op == "decomposer":
        n = len(e.get("colonnes") or [])
        return f"Séparer les informations collées dans les titres de {n} colonne(s) (indicateur, période, sexe, âge)"
    if op == "extraire":
        return f"Séparer les informations collées dans « {e.get('colonne')} » (indicateur, période, sexe, âge)"
    if op == "nettoyer_texte":
        return "Retirer les espaces en trop dans les libellés"
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
        if e.get("_sous_totaux"):
            entree["info"] = (f"{e['_sous_totaux']} colonne(s) de sous-total écartée(s) : recalculables à partir du détail, "
                              "elles feraient compter deux fois")
            if e.get("_ecarts"):
                entree["avertissement"] = (f"{e['_ecarts']} sous-total(aux) du fichier différent(s) de la somme de leur "
                                           "détail : à vérifier à la source")
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
    etapes = _propose_nettoyage(df) + _propose_doublons(df)
    retirees = set(etapes[-1]["colonnes"]) if etapes and etapes[-1]["op"] == "retirer_colonnes" else set()
    extraites = _propose_extraire(df.drop(columns=list(retirees)))
    decomposition = _propose_decomposer(df)
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
        if str(c) in {x["colonne"] for x in extraites} or str(c) in retirees:
            continue
        # « District - Centre » : la partie gauche varie (sinon « VIH - Dépistés » se couperait en un « VIH » constant)
        if len(s) >= 3 and s.str.count(" - ").eq(1).mean() >= 0.9 and s.str.split(" - ").str[0].nunique() >= 2:
            noms = [p.strip() for p in str(c).split(" - ")] if str(c).count(" - ") == 1 else [f"{c}_1", f"{c}_2"]
            etapes.append({"op": "fractionner", "colonne": str(c), "separateur": " - ", "noms": noms})
    # 4. Périodes en colonnes (janv., févr. … ou 2023, 2024 …) : une colonne Période, une colonne Valeur
    etapes += extraites
    periodes = [c for c in df.columns if _entete_periode(c)]
    if decomposition:
        etapes += decomposition
    elif len(periodes) >= 3:
        etapes.append({"op": "depivoter", "colonnes": [str(c) for c in periodes], "nom": "periode", "valeur": "valeur"})
    renommage = _propose_niveaux_dhis2([c for c in df.columns if str(c) not in retirees])
    return etapes + renommage


# ---------------------------------------------------------------- propositions : informations collées, sources connues
_NIVEAU_DHIS2 = re.compile(r"^orgunitlevel(\d+)$", re.I)


def _chiffres(s):
    """Colonne de chiffres (ou vide) : ce que l'on peut déplier en lignes."""
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return True
    t = _txt(s).str.strip()
    t = t[t != ""]
    return t.empty or pd.to_numeric(t.str.replace(",", ".", regex=False), errors="coerce").notna().mean() >= 0.95


def _propose_nettoyage(df):
    for c in df.columns:
        s = df[c]
        if s.dtype == object or pd.api.types.is_string_dtype(s):
            t = s.dropna()
            t = t[t.map(lambda v: isinstance(v, str))]
            if (t != t.map(lambda v: " ".join(v.split()))).any():
                return [{"op": "nettoyer_texte"}]
    return []


def _propose_doublons(df):
    """Colonne de libellés recopiée à l'identique d'une autre (DHIS2 : organisationunitname = dernier orgunitlevel).
    Jamais des colonnes de chiffres : deux mesures peuvent avoir les mêmes valeurs sans être la même mesure."""
    doublons = []
    cols = [c for c in df.columns if df[c].dtype == object or pd.api.types.is_string_dtype(df[c])]
    for i, a in enumerate(cols):
        if a in doublons or df[a].notna().sum() < 2:
            continue
        sa = _txt(df[a]).str.strip()
        for b in cols[i + 1:]:
            if b not in doublons and sa.equals(_txt(df[b]).str.strip()):
                doublons.append(b)
    return [{"op": "retirer_colonnes", "colonnes": [str(c) for c in doublons]}] if doublons else []


def _propose_niveaux_dhis2(colonnes):
    """orgunitlevel1…n d'un export DHIS2 : le dernier niveau est l'établissement, puis le district, puis la région
    (pyramide sanitaire). Le nom reste modifiable : c'est une étape « Renommer » comme une autre."""
    niveaux = sorted((int(m.group(1)), c) for c in colonnes if (m := _NIVEAU_DHIS2.match(str(c))))
    if not niveaux:
        return []
    noms = ["Établissement", "District", "Région"]
    pris = {str(c) for c in colonnes}
    carte = {}
    for rang, (_, c) in enumerate(reversed(niveaux)):
        nom = noms[rang] if rang < len(noms) else f"Niveau {niveaux[len(niveaux) - 1 - rang][0]}"
        if nom not in pris:
            carte[str(c)] = nom
    return [{"op": "renommer", "colonnes": carte}] if carte else []


def _propose_decomposer(df):
    """Titres de colonnes qui collent plusieurs informations (indicateur, période, sexe, âge) sur des colonnes de
    chiffres. Des titres qui ne sont qu'une période (« Janv. », « 2024 ») relèvent de « dépivoter »."""
    import dimensions
    cand = []
    for c in df.columns:
        reste, dims = dimensions.decouper(c)
        if dims and _chiffres(df[c]) and not _entete_periode(c):
            cand.append((str(c), reste, dims))
    if len(cand) < 2:
        return []
    infos = {d for _, _, dims in cand for d in dims} | ({"indicateur"} if any(r for _, r, _ in cand) else set())
    if len(infos) < 2:
        return []
    # Le total d'un indicateur, sans ventilation (« Dépistés » à côté de « Dépistés Masculin 15-24 ans ») : laissé à
    # part, il serait répété sur chaque ligne et compterait plusieurs fois. Rattaché, il devient un sous-total écarté.
    indicateurs = {_norme(r) for _, r, _ in cand if r}
    pris = {c for c, _, _ in cand}
    totaux = [str(c) for c in df.columns if str(c) not in pris and _norme(c) in indicateurs and _chiffres(df[c])]
    return [{"op": "decomposer", "colonnes": [str(c) for c in df.columns if str(c) in pris or str(c) in totaux],
             "valeur": "Valeur"}]


def _propose_extraire(df):
    """Colonne de texte qui colle au moins deux informations (« VIH - Dépistés Masculin, 15-24 ans »,
    « Masculin, 15-24 ans »). Une colonne qui n'en porte qu'une (« Janvier 2026 », « Masculin ») est déjà propre."""
    import dimensions
    etapes = []
    for c in df.columns:
        s = df[c]
        if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
            continue
        vals = [v for v in _txt(s).str.strip().unique() if v and v.lower() != "default"]
        if len(vals) < 2:
            continue
        parts = [dimensions.decouper(v) for v in vals]
        reconnus = [p for p in parts if p[1]]
        if len(reconnus) < 0.8 * len(parts):
            continue
        infos = {d for _, dims in reconnus for d in dims} | ({"indicateur"} if any(r for r, _ in reconnus) else set())
        if len(infos) >= 2:
            etape = {"op": "extraire", "colonne": str(c)}
            try:
                _extraire(df, dict(etape))                    # proposée seulement si elle s'applique telle quelle
            except Erreur:
                continue
            etapes.append(etape)
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
