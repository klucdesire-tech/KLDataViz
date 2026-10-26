# -*- coding: utf-8 -*-
"""Correspondance des variables : relier chaque indicateur du dictionnaire aux colonnes du fichier, par une
FORMULE explicite, proposée (règles, puis IA) et validée par l'utilisateur.

Pourquoi : deviner le sens des colonnes d'après leurs noms ne suffit pas (« statut_actuel = Actif » est TX_CURR,
« tpt_termine = Oui » est TB_PREV…). Une formule validée rend chaque chiffre exact, reproductible et explicable ;
tuiles, répartitions, courbes et écarts du tableau de bord sont tous calculés par CE moteur (et par son jumeau
JavaScript `evalF` dans la page), jamais par deux règles différentes.

Langage de formule (dict) :
    mode          "comptage" | "taux" | "somme" | "rapport" | "moyenne" | "mediane"
    numerateur    condition {"colonne": c, "valeurs": [...]} — lignes comptées (comptage, taux) ou retenues
    denominateur  condition — lignes de référence d'un taux (défaut : numérateur renseigné) ; rapport : lignes D
    colonne       colonne chiffrée (somme, moyenne, médiane, rapport)
    filtre        condition appliquée d'abord (ex. indicateur = « TX_CURR » dans un export long)
    stock         True : valeur de la dernière période seulement (ne s'additionne pas dans le temps)
Une condition compare des textes normalisés (minuscules, espaces retirés) ; une cellule vide ne la vérifie jamais.
"""
from __future__ import annotations

import math
import re

import pandas as pd

MODES = ("comptage", "taux", "somme", "rapport", "moyenne", "mediane")
TAUX = ("pourcentage", "proportion", "taux", "%", "rate", "pct")

# Vocabulaire métier (indicateurs MER et usages courants) : valeurs ou noms de colonnes qui désignent l'indicateur
SYNONYMES = {
    "TX_CURR": ["actif", "actifs", "active", "sous traitement", "en traitement", "sous tarv", "sous arv", "en soins"],
    "TX_ML": ["perdu de vue", "perdus de vue", "pdv", "interruption de traitement", "interruption", "abandon",
              "rupture de suivi", "iit", "sans contact"],
    "TX_RTT": ["retour", "de retour", "reintegre", "reintegration", "retour en soins"],
    "TX_NEW": ["nouveau", "nouveaux", "nouvellement", "nouvel inclus", "nouvelle inclusion", "inclus ce mois"],
    "TX_PVLS": ["supprime", "supprimee", "cv supprimee", "indetectable", "charge virale supprimee"],
    "VL_SUPPRESSION_NAT": ["supprime", "supprimee", "cv supprimee", "indetectable"],
    "TB_PREV": ["tpt termine", "tpt acheve", "tpt complete", "ipt termine", "ipt acheve", "inh termine",
                "traitement preventif termine", "traitement preventif acheve"],  # MER : achevé, jamais « démarré »
    "TB_STAT": ["statut tb", "depistage tb", "tb depiste"],
    "HTS_TST": ["teste", "testes", "depiste", "depistes", "test vih", "resultat recu"],
    "PMTCT_STAT": ["statut vih connu", "statut connu cpn"],
    "PrEP_NEW": ["prep initie", "nouveau prep", "initie prep"],
    "PrEP_CURR": ["sous prep", "prep en cours"],
}
_NEG = ("non ", "pas ", "aucun", "sans ")
_VIDES = {"", "nan", "none", "null", "nat", "na", "n/a"}  # mêmes valeurs « vides » que NULLS dans la page
_OUI = {"oui", "o", "yes", "y", "true", "vrai", "1", "1.0"}
_NON = {"non", "n", "no", "false", "faux", "0", "0.0"}
_ETAT = re.compile(r"statut|actuel|etat|status|current|situation")      # état à une date : pas d'évolution dans le temps
_ATTRIBUT = re.compile(r"naissance|birth|embauche|hire|creation|ouverture|expiration|peremption")
_ACTIVITE = re.compile(r"prelev|visite|consult|depist|initi|inclus|vente|operation|arrivee|livraison|rapport|declar|periode|mois")
_IDLIKE = re.compile(r"(^|\s)(id|identifiant|matricule|code|n°|no|num|numero|labno|dossier|telephone|tel|nom|prenom)(\s|$)")
_DIM_PRIO = ("region", "district", "departement", "commune", "ville", "drena", "site", "centre", "structure",
             "etablissement", "formation sanitaire", "magasin", "agence", "hotel", "cooperative", "direction", "service",
             "sexe", "genre", "tranche", "age", "milieu", "categorie", "type", "classe")


def _norm(t) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9<>]+", " ", t).split())


def _v(x) -> str:
    return str(x).strip().lower()


def est_taux(ind) -> bool:
    return (ind.get("type") or "").strip().lower() in TAUX


def mode_compatible(ind, mode) -> bool:
    """Calcul cohérent avec le type du dictionnaire. Pourcentage : jamais un comptage ou une somme (taux, rapport,
    ou moyenne / médiane d'une colonne qui porte déjà des %) ; Nombre : jamais un taux ou un rapport.
    Type non renseigné : rien à contrôler."""
    if not (ind.get("type") or "").strip():
        return True
    return mode not in (("comptage", "somme") if est_taux(ind) else ("taux", "rapport"))


def _pct(a, b) -> float:
    """Pourcentage au dixième, arrondi comme Math.round dans la page (demi vers le haut, pas « au pair »)."""
    return math.floor(a / b * 1000 + 0.5) / 10


# ---------------------------------------------------------------------------------------------- évaluation
def _masque(df, cond):
    if not cond:
        return pd.Series(True, index=df.index)
    col = cond.get("colonne")
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    vals = {_v(x) for x in cond.get("valeurs") or []} - _VIDES
    t = df[col].astype(str).str.strip().str.lower()
    return df[col].notna() & ~t.isin(_VIDES) & t.isin(vals)


def evaluer(df, f, cle=None):
    """Valeur de la formule sur `df` (None si incalculable). `cle` : série de période alignée sur df (stock)."""
    if not f or f.get("mode") not in MODES:
        return None
    R = df[_masque(df, f.get("filtre"))] if f.get("filtre") else df
    if f.get("stock") and cle is not None:
        k = cle.reindex(R.index)
        if k.notna().any():
            R = R[k == k.dropna().max()]
    m = f["mode"]
    num = f.get("numerateur")
    if m == "comptage":
        return float(_masque(R, num).sum()) if num else float(len(R))
    if m == "taux":
        if not num:
            return None
        den = f.get("denominateur")
        if den:
            base = R[_masque(R, den)]
        elif num.get("colonne") in R.columns:  # « renseignée » : ni vide ni « NA », comme isNull dans la page
            col = R[num["colonne"]]
            base = R[col.notna() & ~col.astype(str).str.strip().str.lower().isin(_VIDES)]
        else:
            base = R.iloc[0:0]
        if not len(base):
            return None
        return _pct(float(_masque(base, num).sum()), len(base))
    col = f.get("colonne")
    if col not in R.columns:
        return None
    if m == "rapport":
        x = pd.to_numeric(R[col], errors="coerce")
        n, d = x[_masque(R, num)].sum(min_count=1), x[_masque(R, f.get("denominateur"))].sum(min_count=1)
        return _pct(float(n), float(d)) if pd.notna(n) and pd.notna(d) and float(d) else None
    x = pd.to_numeric(R[_masque(R, num)][col] if num else R[col], errors="coerce").dropna()
    if not len(x):
        return None
    if m == "somme":
        return float(x.sum())
    if m == "moyenne":
        return float(x.mean())  # non arrondi : la page (evalF) ne l'arrondit pas non plus ; l'affichage s'en charge
    return float(x.median())


def evaluer_par(df, f, dim, cle=None, top=12):
    """[(groupe, valeur)] triés décroissants : la même formule appliquée à chaque valeur de `dim`."""
    if dim not in df.columns:
        return []
    out = []
    for g, part in df.groupby(df[dim].astype(str).str.strip()):
        if g.lower() in _VIDES:  # comme groupBy dans la page
            continue
        v = evaluer(part, f, None if cle is None else cle.reindex(part.index))
        if v is not None:
            out.append((g, v))
    return sorted(out, key=lambda x: -x[1])[:top]


def serie(df, f, cle):
    """[(période, valeur)] dans l'ordre du temps (la formule par période ; un stock vaut sa période)."""
    if cle is None:
        return []
    out = []
    for p in sorted(k for k in cle.dropna().unique()):
        part = df[cle == p]
        g = dict(f, stock=False)  # dans une période donnée, un stock vaut la valeur de cette période
        v = evaluer(part, g)
        if v is not None:
            out.append((str(p), v))
    return out


# ---------------------------------------------------------------------------------------------- contrôle, texte
def valider(df, f) -> list:
    """Erreurs d'une formule pour ce fichier (colonnes absentes, valeurs inexistantes, mode inconnu)."""
    err = []
    if not isinstance(f, dict) or f.get("mode") not in MODES:
        return ["mode de calcul inconnu"]
    for k in ("numerateur", "denominateur", "filtre"):
        c = f.get(k)
        if not c:
            continue
        if c.get("colonne") not in df.columns:
            err.append(f"colonne « {c.get('colonne')} » absente")
            continue
        presentes = set(df[c["colonne"]].dropna().astype(str).str.strip().str.lower()) - _VIDES
        if not ({_v(x) for x in c.get("valeurs") or []} - _VIDES) & presentes:
            err.append(f"aucune des valeurs {c.get('valeurs')} dans « {c['colonne']} »")
    if f["mode"] == "taux" and not f.get("numerateur"):
        err.append("un taux demande un numérateur")
    if f["mode"] == "rapport" and not (f.get("numerateur") and f.get("denominateur")):
        err.append("un rapport demande un numérateur et un dénominateur")
    if f["mode"] in ("somme", "rapport", "moyenne", "mediane"):
        col = f.get("colonne")
        if col not in df.columns:
            err.append(f"colonne chiffrée « {col} » absente")
        elif pd.to_numeric(df[col], errors="coerce").notna().sum() == 0:
            err.append(f"« {col} » n'est pas une colonne chiffrée")
    return err


def _cond_txt(c, lib):
    if not c:
        return ""
    v = c.get("valeurs") or []
    vv = " ou ".join(f"« {x} »" for x in v[:4]) + (" …" if len(v) > 4 else "")
    return f"« {lib(c['colonne'])} » = {vv}"


def decrire(f, lib=lambda c: str(c).replace("_", " ")) -> str:
    """La formule en une phrase (affichée à l'utilisateur pour validation et dans l'icône « i »)."""
    if not f:
        return ""
    m, num, den = f.get("mode"), f.get("numerateur"), f.get("denominateur")
    if m == "comptage":
        t = f"Nombre de lignes où {_cond_txt(num, lib)}" if num else "Nombre de lignes"
    elif m == "taux":
        t = (f"Lignes où {_cond_txt(num, lib)}, rapportées aux lignes où "
             + (_cond_txt(den, lib) if den else f"« {lib(num['colonne'])} » est renseignée") + " (en %)")
    elif m == "rapport":
        t = f"Somme de « {lib(f.get('colonne'))} » où {_cond_txt(num, lib)}, divisée par la somme où {_cond_txt(den, lib)} (en %)"
    else:
        nom = {"somme": "Somme", "moyenne": "Moyenne", "mediane": "Médiane"}.get(m, m)
        t = f"{nom} de « {lib(f.get('colonne'))} »" + (f" où {_cond_txt(num, lib)}" if num else "")
    if f.get("filtre"):
        t += f", pour les lignes où {_cond_txt(f['filtre'], lib)}"
    if f.get("stock"):
        t += " — valeur de la dernière période (stock)"
    return t + "."


# ---------------------------------------------------------------------------------------------- rôles des colonnes
def _date_like(s) -> bool:
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    t = s.dropna().astype(str).head(40)
    if len(t) < 3:
        return False
    # re de Python (et non le moteur PyArrow de pandas 3, pour qui « û » n'est pas une lettre : « Août 2025 »)
    motif = re.compile(r"^\s*(\d{4}-\d{1,2}(-\d{1,2})?|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|[^\W\d_]+\.?\s+\d{4}|"
                       r"\d{4}\s?[tq][1-4]|[tq][1-4]\s?\d{4})", re.I)
    ok = pd.Series([bool(motif.match(str(x))) for x in t])
    return ok.mean() >= 0.8


def roles(df) -> dict:
    """Rôle de chaque colonne : identifiant, période d'activité, dimensions d'analyse (région, district, sexe…)."""
    ids, dates, dims = [], [], []
    n = len(df)
    long_ic, _vc = colonnes_longues(df)  # export long : « Indicateur » porte des NOMS d'indicateurs, pas un axe
    for c in df.columns:
        if c == long_ic:
            continue
        nc, s = _norm(c), df[c]
        if _IDLIKE.search(nc):
            ids.append(c)
            continue
        if not pd.api.types.is_numeric_dtype(s) and _date_like(s):
            # avant le test d'unicité : des dates quotidiennes sont presque toutes distinctes sans être un identifiant
            (ids if _ATTRIBUT.search(nc) else dates).append(c)  # date de naissance : donnée personnelle, jamais un axe
            continue
        if not pd.api.types.is_numeric_dtype(s) and n > 20 and s.nunique() >= 0.9 * n:
            ids.append(c)
            continue
        if not pd.api.types.is_numeric_dtype(s) and 2 <= s.nunique() <= 60:
            if {_v(x) for x in s.dropna().unique()} <= (_OUI | _NON):
                continue  # colonne Oui/Non : un indicateur (taux), pas un axe d'analyse
            dims.append(c)
    actives = [c for c in dates if not _ATTRIBUT.search(_norm(c))]
    actives.sort(key=lambda c: 0 if _ACTIVITE.search(_norm(c)) else 1)
    num_years = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and re.match(r"^(annee|year|an)\b", _norm(c))]

    def prio(c):
        nc = _norm(c)
        return next((i for i, d in enumerate(_DIM_PRIO) if re.search(r"\b" + d + r"\b", nc)), 50)
    dims = [c for c in dims if not _ETAT.search(_norm(c)) or prio(c) < 50]
    dims.sort(key=lambda c: (prio(c), df[c].nunique()))
    return {"periode": (actives or num_years or [None])[0], "dimensions": dims[:4], "identifiants": ids}


# ---------------------------------------------------------------------------------------------- propositions par règles
def _cats(df, ids):
    """Colonnes catégorielles exploitables : 2 à 15 valeurs, pas un identifiant."""
    out = []
    for c in df.columns:
        if c in ids or pd.api.types.is_numeric_dtype(df[c]) and not set(df[c].dropna().unique()) <= {0, 1}:
            continue
        vals = [str(v).strip() for v in df[c].dropna().unique() if str(v).strip().lower() not in ("", "nan", "none", "null")]
        if 2 <= len(vals) <= 15:
            out.append((c, vals))
    return out


def _syn_dans(texte, syns):
    t = " " + _norm(texte) + " "
    return any(" " + _norm(s) + " " in t for s in syns)


def _nie(v) -> bool:
    n = _norm(v) + " "
    return n.startswith(_NEG) or n.strip() in ("non", "no", "n", "0", "faux", "false")


# Noms de colonnes d'un export « long » (une ligne = un indicateur × période × lieu, ex. DHIS2 « dataname / value »)
_LONG_IND = {"indicator", "indicateur", "indicateurs", "dataname", "data name", "dataelement", "data element",
             "dataelementname", "indicatorname", "indicator name", "nom indicateur", "libelle indicateur", "dx"}
_LONG_VAL = {"value", "valeur", "valeurs", "values", "val", "nombre", "effectif", "effectifs", "count", "total",
             "montant", "quantite"}


def colonnes_longues(df, indicateurs=()):
    """(colonne des indicateurs, colonne des valeurs) d'un export long, (None, None) sinon. Codes du dictionnaire
    présents ou non : dans un tel fichier une ligne n'est jamais un patient, on n'y compte donc jamais de lignes."""
    from hermes_da import indicators_catalog as cat
    codes = {_norm(i["code"]) for i in indicateurs if i.get("code")}
    if any(_norm(c) in codes for c in df.columns):
        return None, None  # une colonne porte un code du dictionnaire : format large, pas long
    _l, ic, vc = cat._as_long(df, list(indicateurs))
    if ic in df.columns and vc in df.columns:
        return ic, vc
    ic = next((c for c in df.columns if _norm(c) in _LONG_IND and not pd.api.types.is_numeric_dtype(df[c])), None)
    vc = next((c for c in df.columns if _norm(c) in _LONG_VAL
               and pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.5), None)
    return (ic, vc) if ic is not None and vc is not None else (None, None)


def proposer_regles(df, indicateurs, r=None) -> list:
    """Formules proposées par règles (déterministes) : formats long/large (codes explicites) puis listes
    d'observations (vocabulaire métier, colonnes Oui/Non nommées d'après l'indicateur)."""
    from hermes_da import indicators_catalog as cat
    r = r or roles(df)
    out = []
    long_df, ic, vc = cat._as_long(df, indicateurs)
    lic, lvc = colonnes_longues(df, indicateurs)
    long_natif = lic is not None
    if long_natif:
        ic, vc = lic, lvc
    cats = [(c, v) for c, v in _cats(df, r["identifiants"]) if c != ic]
    for ind in indicateurs:
        code, cnorm, rate = ind["code"], _norm(ind["code"]), est_taux(ind)
        base = {"code": code, "libelle": ind.get("libelle") or code, "type": ind.get("type") or "", "source": "regles"}
        # 1) export long : une ligne par indicateur (DHIS2) — filtre sur la colonne indicateur
        if long_natif:
            parts = {}
            for lab in df[ic].dropna().astype(str).unique():
                p = cat._part(_norm(lab), cnorm)
                if p is not None:
                    parts.setdefault(p, []).append(lab)
            if parts:
                snap = bool(cat._is_snapshot(ind))  # valeur de fin de période (TX_PVLS, TX_CURR…) : dernière période
                if rate and parts.get("n") and parts.get("d"):
                    f = {"mode": "rapport", "colonne": vc, "numerateur": {"colonne": ic, "valeurs": parts["n"]},
                         "denominateur": {"colonne": ic, "valeurs": parts["d"]}, "stock": snap}
                elif rate:
                    f = {"mode": "moyenne", "colonne": vc, "filtre": {"colonne": ic, "valeurs": parts.get("") or parts.get("n")},
                         "stock": snap}
                else:
                    f = {"mode": "somme", "colonne": vc, "filtre": {"colonne": ic, "valeurs": (parts.get("") or []) + (parts.get("n") or [])},
                         "stock": bool(cat._is_snapshot(ind))}
                moy = f["mode"] == "moyenne"
                out.append(dict(base, formule=f, evolution=True, confiance=0.7 if moy else 0.95,
                                explication="code de l'indicateur présent dans la colonne des indicateurs"
                                + (" ; moyenne simple de pourcentages, non pondérée : à vérifier" if moy else "")))
            # Export long sans le code : les libellés y sont des NOMS d'indicateurs, pas des catégories à compter.
            # La correspondance se fait sur la colonne « valeur » (somme / rapport) : laissée à l'IA et à l'utilisateur.
            continue
        # 2) format large : une colonne chiffrée porte le code
        col = next((c for c in df.columns if _norm(c) == cnorm and pd.api.types.is_numeric_dtype(df[c])), None)
        if col is not None:
            f = {"mode": "moyenne" if rate else "somme", "colonne": col, "stock": bool(cat._is_snapshot(ind))}
            out.append(dict(base, formule=f, evolution=True, confiance=0.7 if rate else 0.95,
                            explication="colonne nommée d'après le code"
                            + (" ; moyenne simple de pourcentages, non pondérée : à vérifier" if rate else "")))
            continue
        # 3) liste d'observations : une valeur de catégorie désigne l'indicateur (vocabulaire métier)
        syns = SYNONYMES.get(code.upper(), [])
        trouve = None
        if syns:
            for c, vals in cats:
                pos = [v for v in vals if not _nie(v) and _syn_dans(v, syns)]
                if pos:
                    trouve = (c, pos, "valeur « " + pos[0] + " »")
                    break
            if trouve is None:  # colonne Oui/Non nommée d'après l'indicateur (« tpt_demarre », « cv_supprimee »)
                for c, vals in cats:
                    lv = {_v(v) for v in vals}
                    if lv <= (_OUI | _NON) and lv & _OUI and _syn_dans(c, syns):
                        trouve = (c, [v for v in vals if _v(v) in _OUI], "colonne Oui/Non « " + str(c) + " »")
                        break
        if trouve:
            c, pos, pourquoi = trouve
            num = {"colonne": c, "valeurs": pos}
            f = {"mode": "taux", "numerateur": num} if rate else {"mode": "comptage", "numerateur": num}
            out.append(dict(base, formule=f, evolution=not _ETAT.search(_norm(c)), confiance=0.75,
                            explication=f"{pourquoi} reconnue pour {code}"))
    # Variante nationale (« _NAT ») calculée par la même formule qu'un indicateur déjà proposé : pas de doublon
    vues = {repr(sorted(p["formule"].items(), key=str)) for p in out if not p["code"].upper().endswith("_NAT")}
    return [p for p in out if not (p["code"].upper().endswith("_NAT") and repr(sorted(p["formule"].items(), key=str)) in vues)]


# ---------------------------------------------------------------------------------------------- pour l'IA
def resume_colonnes(df, r=None) -> list:
    """Ce que l'IA peut voir : nom, type, rôle et ÉTIQUETTES de catégories répétées. Jamais une valeur individuelle."""
    r = r or roles(df)
    out = []
    for c in df.columns:
        e = {"nom": str(c), "type": "nombre" if pd.api.types.is_numeric_dtype(df[c]) else "texte"}
        if c in r["identifiants"]:
            e["role"] = "identifiant"
        elif c == r["periode"]:
            e["role"] = "periode"
        elif c in r["dimensions"]:
            e["role"] = "dimension"
        if c not in r["identifiants"] and not pd.api.types.is_numeric_dtype(df[c]):
            vc = df[c].dropna().astype(str).str.strip().value_counts()
            if 2 <= len(vc) <= 15 and int(vc.min()) >= 2:
                e["categories"] = [str(x) for x in vc.index]
        out.append(e)
    return out


def cle_periode(df, col):
    """Série « AAAA-MM » (ou « AAAA ») de la colonne de période, lue comme le fait le tableau de bord (ISO tel quel)."""
    if not col or col not in df.columns:
        return None
    from hermes_da.adapters.dashboard import _period
    nom, ser = _period(df[[col]])
    if ser is None:
        return None
    return ser.map(lambda v: v[:7] if isinstance(v, str) else None)
