# -*- coding: utf-8 -*-
"""Bilan qualité d'un fichier, dimension par dimension, avant toute analyse.

Référentiels : ISO/IEC 25012 (modèle de qualité des données) et le guide de l'OMS pour la revue de la qualité des
données de santé (complétude et promptitude, cohérence interne). Six dimensions :
- Complétude : cases vides, valeurs d'attente (« -999 », « NA », « ? »), colonnes et lignes vides ;
- Unicité : lignes en double, même site et même période saisis deux fois ;
- Validité : nombres écrits en texte, texte dans une colonne de nombres, comptages négatifs, taux hors bornes,
  âges et dates impossibles, oui/non mal saisis ;
- Cohérence : lignes de total mêlées aux détails, totaux différents de leurs désagrégations, libellés écrits de
  plusieurs façons ;
- Plausibilité : valeurs extrêmes (ISO/IEC 25012 parle d'exactitude, que seul un retour à la source peut prouver) ;
- Actualité : ancienneté des dernières données, mois sans aucune donnée, déclarations manquantes site par site.

Chaque constat dit ce qui ne va pas, combien de lignes sont touchées, lesquelles (numéro de ligne du fichier, en-tête
compris), et pourquoi cela compte pour les chiffres. Le calcul est déterministe et sans IA.

Note d'une dimension : 100 moins des pénalités proportionnelles à la part touchée, plafonnées par gravité ; une
dimension qui porte un constat grave ne dépasse pas 69. Note globale : moyenne des dimensions mesurées, plafonnée
à 69 si l'une d'elles est grave. Ce n'est pas un label : c'est un résumé, le détail compte."""
import datetime as dt
import re

import numpy as np
import pandas as pd

import lecture

VERSION = 1
NORMES = "ISO/IEC 25012 et guide OMS de revue de la qualité des données"
MAX_LIGNES = 20                      # numéros de ligne cités par constat
VIDES = {"", "nan", "none", "null"}
ATTENTES = {"-", "--", "—", "n/a", "na", "nd", "n.d.", "?", ".", "néant", "neant", "#n/a", "#ref!", "#div/0!", "#value!",
            "non renseigné", "non renseigne", "inconnu", "x"}
ATTENTES_NUM = {-999, -9999, -99, 9999, 99999, 999999}
OUI = {"oui", "non", "yes", "no", "o", "n", "vrai", "faux", "true", "false", "1", "0"}
TOTAUX = re.compile(r"^(grand |sous[- ]?)?total(e?s?| général| general)?$|^ensemble$|^tous$|^toutes$|^global$", re.I)
COMPTAGE = re.compile(r"(^|[_\s])(nombre|nb|effectif|cas|consultations?|visites?|naissances?|d[ée]c[eè]s|patients?|"
                      r"d[ée]pist[ée]s?|vaccin[ée]s?|accouchements?|[ée]l[eè]ves|quantit[ée]s?|stocks?|population|"
                      r"file_active|tx_\w+|hts_\w+|cpn\d?|tdr\w*|inscrits?|admis|clients?)($|[_\s])", re.I)
TAUX = re.compile(r"(^|[_\s])(taux|pourcentage|pct|ratio_pct|part)($|[_\s])|%", re.I)
AGE = re.compile(r"(^|[_\s])([âa]ge|age_ans)($|[_\s])", re.I)
PERIODE = re.compile(r"date|p[ée]riode|mois|ann[ée]e|month|year|trimestre|semaine|jour", re.I)
LIEU = re.compile(r"site|centre|district|r[ée]gion|zone|commune|ville|unite_organisation|unit[ée]|structure|"
                  r"[ée]tablissement|agence|[ée]cole|pays|d[ée]partement|localit[ée]|village|quartier|boutique|magasin|"
                  r"province|formation_sanitaire|fosa|h[ôo]pital|csps?|csu|antenne|bureau|entrep[ôo]t|d[ée]p[ôo]t", re.I)
# Part des clés (site + période…) qui se répètent : au-delà, c'est un registre (une ligne par patient, par vente), où
# plusieurs lignes par clé sont la norme. Mesurée sur les clés et non sur les lignes, elle ne dépend pas de la taille
# du fichier : une saisie en double dans un rapport de 12 lignes est signalée comme dans un de 50 000.
SEUIL_DOUBLE = 0.10
IDENT = re.compile(r"(^|[_\s])(id|code|n°|no|num(ero|éro)?|matricule|ref|identifiant)($|[_\s])", re.I)
NOMBRE_TEXTE = re.compile(r"^[+-]?\d{1,3}([   .]\d{3})+(,\d+)?$|^[+-]?\d+,\d+$")
GRAVITES = {"grave": (3.0, 60), "attention": (1.5, 30), "info": (0.5, 10)}
DIMENSIONS = [("completude", "Complétude"), ("unicite", "Unicité"), ("validite", "Validité"), ("coherence", "Cohérence"),
              ("plausibilite", "Plausibilité"), ("actualite", "Actualité")]


# ---------------------------------------------------------------- outils
def _lignes(masque):
    """Numéros de ligne du fichier (en-tête = ligne 1) des lignes touchées, les premiers seulement."""
    pos = np.flatnonzero(np.asarray(masque))
    return [int(p) + 2 for p in pos[:MAX_LIGNES]]


def _texte(s):
    return s.astype(str).str.strip().str.lower()


def _vide(s):
    return s.isna() | _texte(s).isin(VIDES)


def _attente(s):
    """Valeurs qui veulent dire « pas de donnée » : « NA », « ? », « -999 »…"""
    t = _texte(s)
    m = t.isin(ATTENTES)
    v = pd.to_numeric(s, errors="coerce")
    if v.notna().sum() >= 5:
        autres = v[~v.isin(ATTENTES_NUM)].dropna()
        if len(autres):  # -999 n'est une valeur d'attente que si les autres valeurs n'en sont pas proches
            m |= v.isin(ATTENTES_NUM) & ((v < autres.min() - 1) | (v > 10 * max(abs(autres.max()), 1)))
    return m & s.notna()


def _numerique(s):
    """Colonne de nombres : type numérique, ou au moins 80 % de valeurs remplies lisibles comme des nombres."""
    if pd.api.types.is_bool_dtype(s):
        return False
    if pd.api.types.is_numeric_dtype(s):
        return True
    rempli = s[~_vide(s) & ~_attente(s)]
    return len(rempli) >= 3 and pd.to_numeric(rempli, errors="coerce").notna().mean() >= 0.8


def _periode(df):
    """Colonne de période et ses dates (une par ligne, NaT si illisible)."""
    for c in df.columns:
        if not PERIODE.search(str(c)):
            continue
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            return str(c), s
        t = s.dropna().astype(str).str.strip()
        if len(t) < 2:
            continue
        if pd.api.types.is_numeric_dtype(s):
            v = pd.to_numeric(s, errors="coerce")
            if v.dropna().between(1900, 2100).all() and (v.dropna() % 1 == 0).all():
                return str(c), pd.to_datetime(v.astype("Int64").astype(str) + "-01-01", errors="coerce")
            continue
        iso = t.str.match(r"^\d{4}-\d{2}(-\d{2})?").mean() >= 0.9
        jour = not iso and lecture.ordre_dates(t) != "mois"  # « 04/23/2025 » prouve le mois en premier
        d = pd.to_datetime(s.astype(str).str.strip().where(s.notna()), errors="coerce", format="mixed", dayfirst=jour)
        if d.notna().sum() >= 0.8 * s.notna().sum():
            return str(c), d
    return None, None


def _constat(gravite, titre, detail, n=0, total=1, colonne=None, lignes=None, cle=""):
    return {"gravite": gravite, "titre": titre, "detail": detail, "n": int(n), "part": round(n / total, 4) if total else 0,
            "colonne": None if colonne is None else str(colonne), "lignes": lignes or [], "cle": cle}


def _note(constats):
    pen = 0.0
    for c in constats:
        mult, plafond = GRAVITES[c["gravite"]]
        pen += min(plafond, max(c["part"] * 100 * mult, 1 if c["gravite"] != "info" else 0))
    note = max(0, round(100 - pen))
    return min(note, 69) if any(c["gravite"] == "grave" for c in constats) else note


# ---------------------------------------------------------------- dimensions
def _completude(df, nums):
    out, n = [], len(df)
    # Valeurs écartées à la lecture (« trois cents » dans une colonne de nombres) : déjà signalées en validité, elles
    # ne sont pas comptées une seconde fois comme cases vides
    ecartees = {conv["colonne"]: conv for conv in df.attrs.get("lecture") or []}
    cellules = n * max(1, df.shape[1])
    vides_tot = 0
    for c in df.columns:
        s = df[c]
        vide, att = _vide(s), _attente(s)
        if str(c) in ecartees:
            conv = ecartees[str(c)]
            lignes_ec = set(conv.get("positions_non_lues") or
                            [ligne - 2 for ligne, _ in conv["exemples_ecartes"] + conv.get("exemples_attentes", [])])
            vide = vide & ~pd.Series(range(n), index=df.index).isin(lignes_ec)
            if conv.get("attentes"):
                ex = ", ".join(sorted({v for _, v in conv["exemples_attentes"]})[:4])
                out.append(_constat("attention", f"« {c} » : {conv['attentes']} valeur(s) qui veulent dire « pas de donnée »",
                                    f"Valeurs : {ex}. Elles sont traitées comme des cases vides, jamais comptées comme des "
                                    "nombres.", conv["attentes"], n, c, [l for l, _ in conv["exemples_attentes"]], "attente"))
        if vide.all():
            out.append(_constat("attention", f"Colonne « {c} » entièrement vide", "Elle ne sera pas analysée.",
                                n, n, c, cle="colonne_vide"))
            vides_tot += n
            continue
        nv, na = int(vide.sum()), int(att.sum())
        vides_tot += nv + na
        if na:
            ex = ", ".join(sorted({str(v) for v in s[att].astype(str).str.strip()})[:4])
            out.append(_constat("attention", f"« {c} » : {na} valeur(s) qui veulent dire « pas de donnée »",
                                f"Valeurs : {ex}. Elles sont traitées comme des cases vides, jamais comptées comme des nombres.",
                                na, n, c, _lignes(att), "attente"))
        if nv:
            part = nv / n
            g = "grave" if part > .5 and c in nums else "attention" if part > .1 else "info"
            pourquoi = (" Les totaux et moyennes de cette colonne ne portent que sur les cases remplies." if c in nums
                        else "")
            out.append(_constat(g, f"« {c} » : {nv} case(s) vide(s) ({part:.0%})", "Non remplies dans le fichier." + pourquoi,
                                nv, n, c, _lignes(vide), "vides"))
    lignes_vides = df.apply(_vide).all(axis=1) if n else pd.Series([], dtype=bool)
    if lignes_vides.any():
        out.append(_constat("info", f"{int(lignes_vides.sum())} ligne(s) entièrement vide(s)", "Ignorées dans l'analyse.",
                            int(lignes_vides.sum()), n, lignes=_lignes(lignes_vides), cle="ligne_vide"))
    taux = 100 * (1 - vides_tot / cellules) if cellules else 100
    return out, round(taux, 2)


def _unicite(df, nums, dims, per_col, per=None):
    out, n = [], len(df)
    if not n:
        return out
    dup = df.duplicated(keep="first")
    if dup.any():
        out.append(_constat("grave", f"{int(dup.sum())} ligne(s) en double", "Lignes identiques à une ligne précédente : "
                            "gardées, elles compteraient deux fois dans les totaux.", int(dup.sum()), n,
                            lignes=_lignes(dup), cle="doublons"))
    cle = ([per_col] if per_col else []) + dims
    # Fichier de rapport : daté au mois ou à l'année (un seul jour par mois). Un registre est daté au jour, avec des
    # jours variables : plusieurs lignes par lieu et par date y sont la norme (une par patient, par vente).
    rapport = per is not None and per.notna().any() and per.dropna().dt.day.nunique() <= 1
    if per_col and dims and nums and rapport:
        # même site, même période (et mêmes autres libellés) saisis deux fois avec des valeurs différentes
        base = df[~dup]
        k = base.duplicated(subset=cle, keep=False) & ~base.duplicated(keep=False)
        repetees = base[k].groupby(cle, dropna=False).ngroups if k.any() else 0
        if k.any() and repetees <= max(1, SEUIL_DOUBLE * base.groupby(cle, dropna=False).ngroups):
            ex = base[k].iloc[0]
            lib = ", ".join(f"{c} = {ex[c]}" for c in cle[:3])
            out.append(_constat("grave", f"{int(k.sum())} ligne(s) déclarent deux fois la même chose avec des valeurs différentes",
                                f"Même {' + '.join(map(str, cle[:3]))} (ex. {lib}) avec d'autres chiffres : laquelle est juste ? "
                                "Les totaux additionnent les deux.", int(k.sum()), n, lignes=_lignes(df.index.isin(base[k].index)),
                                cle="cle_double"))
    return out


def _fr(x):
    """1234.5 → « 1 234,5 » (espace fine, virgule décimale)."""
    return f"{x:,.10g}".replace(",", "\u202f").replace(".", ",")


def _validite(df, nums, per_col, per, aujourd_hui):
    out, n = [], len(df)
    for conv in df.attrs.get("lecture") or []:  # nombres écrits en texte, convertis à la lecture (lecture.py)
        c = conv["colonne"]
        avant, apres = conv["exemple"]
        out.append(_constat("info", f"« {c} » : {conv['convertis']} nombre(s) écrit(s) en texte, lu(s) comme des nombres",
                            f"Ex. « {avant} » lu {_fr(apres)}. Ils comptent dans les totaux et les graphiques.",
                            conv["convertis"], n, c, cle="nombre_converti"))
        if conv["ecartees"]:
            ex = conv["exemples_ecartes"]
            out.append(_constat("grave", f"« {c} » : {conv['ecartees']} valeur(s) qui ne sont pas des nombres",
                                f"Ex. « {ex[0][1]} ». Dans une colonne de nombres, elles sont écartées des calculs.",
                                conv["ecartees"], n, c, [ligne for ligne, _ in ex], "texte_dans_nombres"))
    for c in df.columns:
        s = df[c]
        if c == per_col:
            continue
        rempli = s[~_vide(s) & ~_attente(s)]
        if not len(rempli):
            continue
        if not pd.api.types.is_numeric_dtype(s):
            t = rempli.astype(str).str.strip()
            fr = t.str.match(NOMBRE_TEXTE)
            if fr.mean() >= .8:
                out.append(_constat("grave", f"« {c} » : nombres écrits en texte ({t[fr].iloc[0]})",
                                    "Avec des espaces ou une virgule, ils ne sont pas lus comme des nombres : ils manquent aux "
                                    "totaux, moyennes et graphiques.", int(fr.sum()), n, c,
                                    _lignes(s.index.isin(t[fr].index)), "nombre_texte"))
                continue
            v = pd.to_numeric(t, errors="coerce")
            if c in nums and v.isna().any():
                bad = t[v.isna()]
                out.append(_constat("grave", f"« {c} » : {len(bad)} valeur(s) qui ne sont pas des nombres",
                                    f"Ex. « {bad.iloc[0]} ». Dans une colonne de nombres, elles sont écartées des calculs.",
                                    len(bad), n, c, _lignes(s.index.isin(bad.index)), "texte_dans_nombres"))
            low = t.str.lower()
            if low.isin(OUI).mean() >= .8 and not low.isin(OUI).all() and low.nunique() > 2:
                bad = t[~low.isin(OUI)]
                out.append(_constat("attention", f"« {c} » : {len(bad)} réponse(s) hors oui/non",
                                    f"Ex. « {bad.iloc[0]} ». Elles sont écartées des taux.", len(bad), n, c,
                                    _lignes(s.index.isin(bad.index)), "oui_non"))
        if c not in nums:
            continue
        v = pd.to_numeric(s, errors="coerce").where(~_attente(s))
        if COMPTAGE.search(str(c)) and (v < 0).any():
            m = v < 0
            out.append(_constat("grave", f"« {c} » : {int(m.sum())} comptage(s) négatif(s)",
                                "Un nombre de cas, de patients ou un stock ne peut pas être négatif.", int(m.sum()), n, c,
                                _lignes(m), "negatif"))
        if TAUX.search(str(c)) and v.notna().any():
            echelle = 1 if v.dropna().quantile(.9) <= 1 else 100
            m = (v < 0) | (v > echelle)
            if m.any():
                out.append(_constat("attention", f"« {c} » : {int(m.sum())} taux hors de 0 à {echelle}{' %' if echelle == 100 else ''}",
                                    "Un taux au-delà de 100 % vient souvent d'un dénominateur sous-estimé : à vérifier.",
                                    int(m.sum()), n, c, _lignes(m), "taux_hors_bornes"))
        if AGE.search(str(c)):
            m = (v < 0) | (v > 120)
            if m.any():
                out.append(_constat("grave", f"« {c} » : {int(m.sum())} âge(s) impossible(s)", "Âge négatif ou supérieur à 120 ans.",
                                    int(m.sum()), n, c, _lignes(m), "age"))
    if per_col is not None:
        brut = df[per_col]
        if not pd.api.types.is_datetime64_any_dtype(brut):
            ordre = lecture.ordre_dates(brut)
            if ordre == "melange":
                out.append(_constat("grave", f"« {per_col} » : dates écrites dans deux ordres",
                                    "Certaines sont jour/mois (ex. 23/04/2025), d'autres mois/jour (ex. 04/23/2025) : une "
                                    "date comme 03/04/2025 ne peut pas être lue sûrement. À uniformiser à la source.",
                                    int(brut.notna().sum()), n, per_col, cle="dates_deux_ordres"))
            elif ordre == "mois":
                out.append(_constat("info", f"« {per_col} » : dates lues mois en premier",
                                    "Le fichier écrit les dates à l'anglaise (ex. 04/23/2025 = 23 avril 2025) : elles sont "
                                    "lues ainsi.", int(brut.notna().sum()), n, per_col, cle="dates_mois_premier"))
        illisible = per.isna() & ~_vide(brut)
        if illisible.any():
            out.append(_constat("attention", f"« {per_col} » : {int(illisible.sum())} date(s) illisible(s)",
                                f"Ex. « {brut[illisible].iloc[0]} ». Ces lignes ne sont rattachées à aucune période.",
                                int(illisible.sum()), n, per_col, _lignes(illisible), "date_illisible"))
        futur = per > pd.Timestamp(aujourd_hui) + pd.Timedelta(days=31)
        if futur.any():
            out.append(_constat("attention", f"« {per_col} » : {int(futur.sum())} date(s) dans le futur",
                                f"Ex. {per[futur].iloc[0]:%d/%m/%Y}. Erreur de saisie probable (année ou inversion jour/mois).",
                                int(futur.sum()), n, per_col, _lignes(futur), "date_future"))
        ancien = per < pd.Timestamp("1900-01-01")
        if ancien.any():
            out.append(_constat("grave", f"« {per_col} » : {int(ancien.sum())} date(s) avant 1900", "Date impossible pour ce suivi.",
                                int(ancien.sum()), n, per_col, _lignes(ancien), "date_ancienne"))
    return out


def _total_juste(df, m, nums, per):
    """La ligne de total vaut-elle la somme des lignes de détail (même période) ? Premier écart trouvé, ou None."""
    for col in nums:
        v = pd.to_numeric(df[col], errors="coerce")
        cles = per.dt.to_period("M") if per is not None else pd.Series(0, index=df.index)
        for p in cles[m].dropna().unique():
            tot = v[m & (cles == p)]
            if len(tot) != 1 or pd.isna(tot.iloc[0]):
                continue  # plusieurs niveaux de total (sous-totaux, total général) : pas de comparaison simple
            somme = v[~m & (cles == p)].sum()
            if abs(tot.iloc[0] - somme) > max(1, .005 * abs(somme)):
                return col, p, float(tot.iloc[0]), float(somme)
    return None


def _coherence(df, dims, controles, nums=(), per=None):
    out, n = [], len(df)
    if df.attrs.get("recette_erreur"):  # la remise en forme mémorisée ne s'applique plus : fichier lu tel quel
        out.append(_constat("grave", "La recette de transformation ne s'applique plus à ce fichier",
                            f"{df.attrs['recette_erreur']} Le fichier est analysé tel quel : vérifiez sa forme ou mettez la "
                            "recette à jour.", n, max(n, 1), cle="recette_erreur"))
    if df.attrs.get("recette"):
        r = df.attrs["recette"]
        out.append(_constat("info", f"Recette de transformation appliquée ({len(r['etapes'])} étape(s))",
                            " ; ".join(e["libelle"] for e in r["etapes"][:5]) + ".", 0, max(n, 1), cle="recette"))
    for c in dims:
        t = df[c].astype(str).str.strip()
        m = t.str.match(TOTAUX) & df[c].notna()
        if m.any():
            ecart = _total_juste(df, m, nums, per)
            if ecart:
                col, p, tot, somme = ecart
                quand = f" ({p.strftime('%m/%Y')})" if hasattr(p, "strftime") else ""
                out.append(_constat("grave", f"« {c} » : la ligne « {t[m].iloc[0]} » ne vaut pas la somme des lignes",
                                    f"{col}{quand} : {tot:,.0f} sur la ligne de total, {somme:,.0f} en additionnant les lignes. "
                                    "Une saisie est fausse, ou des lignes manquent.".replace(",", "\u202f"),
                                    int(m.sum()), n, c, _lignes(m), "total_faux"))
            out.append(_constat("attention", f"« {c} » : {int(m.sum())} ligne(s) de total mêlée(s) aux détails",
                                f"Valeur « {t[m].iloc[0]} ». Écartée(s) des calculs pour ne pas compter deux fois : vérifiez "
                                "qu'aucune vraie catégorie ne porte ce nom.", int(m.sum()), n, c, _lignes(m), "ligne_total"))
        propre = df[c].map(lambda v: " ".join(v.split()) if isinstance(v, str) else v)
        cles = propre.map(lambda v: v.casefold() if isinstance(v, str) else v)
        formes = pd.DataFrame({"k": cles, "v": df[c]}).dropna().groupby("k")["v"].nunique()
        multiples = formes[formes > 1]
        if len(multiples):
            k = multiples.index[0]
            variantes = sorted({repr(v) for v in df[c][cles == k].dropna().unique()})[:3]
            touches = cles.isin(multiples.index)
            out.append(_constat("info", f"« {c} » : {len(multiples)} libellé(s) écrit(s) de plusieurs façons",
                                f"Ex. {', '.join(variantes)}. Regroupés automatiquement (espaces en trop, majuscules).",
                                int(touches.sum()), n, c, cle="libelles"))
    if controles:
        c0 = controles[0]
        out.append(_constat("grave", f"{len(controles)} total(aux) différent(s) de la somme de leurs désagrégations",
                            f"Ex. {c0.get('indicateur', '')}, {c0.get('site', '')} : {c0.get('controle', '')}, attendu "
                            f"{c0.get('attendu', '')}, trouvé {c0.get('trouve', '')}.", len(controles), max(n, 1),
                            cle="desagregation"))
    return out


def _plausibilite(anomalies, n, deja=()):
    """Valeurs extrêmes de tout le fichier (IQR). Une colonne déjà lue site par site (`deja`) n'y passe plus : comparé à
    tous les sites ensemble, un grand hôpital paraît « extrême » à chaque mois, sans aucune erreur."""
    out = []
    for a in anomalies or []:
        if a["column"] in deja:
            continue
        out.append(_constat("attention", f"« {a['column']} » : {a['count']} valeur(s) extrême(s)",
                            f"{a['detail']}. Erreur de saisie ou vrai pic : à vérifier à la source avant de conclure.",
                            a["count"], max(n, 1), a["column"], a.get("lignes"), "extremes"))
    return out


# ---------------------------------------------------------------- cohérence interne (guide OMS)
ECARTS_TYPES = 3           # guide OMS : valeur extrême au-delà de 3 écarts-types de la moyenne du site
ECART_MIN = 0.5            # et au moins 50 % d'écart à cette moyenne : un petit écart n'est pas une anomalie
POINTS_MIN = 8             # mois nécessaires pour connaître l'habitude d'un site


def _n(x):
    return f"{x:,.0f}".replace(",", "\u202f")


def _serie_extremes(v):
    """Positions des valeurs d'une série (un site, une mesure) hors de son habitude (guide OMS) : à plus de 3 écarts-types
    de la moyenne des AUTRES mois du site (la valeur testée n'élargit pas elle-même la plage), à au moins 50 % et 5 unités
    de cette moyenne. Une saison qui revient (paludisme en saison des pluies) reste dans la plage des autres mois."""
    x = v.dropna().astype(float)
    if len(x) < POINTS_MIN:
        return x.index[:0], None
    tot, tot2, k = x.sum(), (x * x).sum(), len(x) - 1
    moy = (tot - x) / k                                            # moyenne des autres mois
    var = ((tot2 - x * x) - k * moy * moy) / max(k - 1, 1)         # leur variance
    et = var.clip(lower=0) ** .5
    if (x >= 0).all() and (x % 1 == 0).all():
        # comptages (cas, consultations) : d'un mois à l'autre, ils varient au moins comme le hasard (loi de Poisson,
        # écart-type = racine de la moyenne). Sans ce plancher, un petit centre serait « extrême » au moindre écart.
        et = et.where(et >= moy.clip(lower=0) ** .5, moy.clip(lower=0) ** .5)
    ecart = (x - moy).abs()
    hors = (ecart > ECART_MIN * moy.abs().clip(lower=1)) & (ecart >= 5) & \
        ((et > 0) & (ecart > ECARTS_TYPES * et) | (et == 0) & (ecart > moy.abs().clip(lower=1)))
    q1, q3 = x.quantile(.25), x.quantile(.75)
    return x.index[hors.values], (float(q1), float(q3), float(x.median()))


def _extremes_temps(df, lieu, mois, nums, dims, index_complet):
    """Valeurs hors de l'habitude de leur site (guide OMS, cohérence interne) : chaque site et chaque mesure ont leur
    propre série dans le temps. Rend les constats et les colonnes lues ainsi."""
    out, lues, n = [], set(), len(df)
    # Une série = un site, plus les colonnes qui distinguent ses lignes d'un même mois (« indicateur » d'un export DHIS2).
    # Une colonne qui change d'un mois à l'autre (rupture Oui/Non) ou qui suit le site (district) ne découpe rien.
    groupe = [df[lieu].astype(str), mois.astype(str)]
    cle = [lieu] + [d for d in dims if d != lieu and df[d].nunique() <= 60
                    and not _texte(df[d].dropna()).isin(OUI).all()
                    and (df.groupby(groupe, dropna=True)[d].nunique() > 1).any()]
    for c in nums:
        if IDENT.search(str(c)) or PERIODE.search(str(c)):
            continue
        v = pd.to_numeric(df[c], errors="coerce").where(~_attente(df[c]))
        if v.nunique() <= 2:
            continue
        lues.add(str(c))
        trouves, ex = [], None
        for k, g in v.groupby([df[x].astype(str) for x in cle] + [mois], sort=False).sum(min_count=1).groupby(level=list(range(len(cle)))):
            serie = g.droplevel(list(range(len(cle))))
            hors, hab = _serie_extremes(serie)
            for m in hors:
                trouves.append((k if isinstance(k, tuple) else (k,), m, float(serie[m]), hab))
        if not trouves:
            continue
        masque = pd.Series(False, index=df.index)
        for k, m, _, _ in trouves:
            sel = (mois == m)
            for col, val in zip(cle, k):
                sel &= df[col].astype(str) == val
            masque |= sel
        k, m, val, (q1, q3, med) = max(trouves, key=lambda t: abs(t[2] - t[3][2]) / max(abs(t[3][2]), 1))
        fois = val / med if med else None
        indice = (" (un zéro de trop ?)" if fois and 8 <= fois <= 12 else "")
        out.append(_constat("attention", f"« {c} » : {len(trouves)} valeur(s) hors de l'habitude de leur {lieu}",
                            f"Ex. {', '.join(k)}, {m.strftime('%m/%Y')} : {_n(val)} alors que la valeur habituelle est "
                            f"de {_n(q1)} à {_n(q3)}{indice}. Erreur de saisie ou vrai pic : à vérifier à la source avant "
                            "de conclure.", len(trouves), n, c,
                            _lignes(index_complet.isin(masque[masque].index)), "extremes_site"))
    return out, lues


def _norme(c):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


# (nom, petit, grand, portée, gravité, pourquoi). Portée « ligne » : règle de définition, vraie sur chaque ligne.
# Portée « site » : règle de cohorte (une femme fait sa CPN1 un mois, sa CPN4 plus tard) : vraie sur le total de la
# période d'un site, pas forcément d'un mois à l'autre.
REGLES = [
    ("positifs ≤ testés", r"^(hts_tst_pos|positifs?|tests?_positifs?|depistes?_positifs?|cas_positifs?)$",
     r"^(hts_tst|depistes?|testes?|tests?|tests?_realises?|personnes?_testees?)$", "ligne", "grave",
     "on ne trouve pas plus de positifs que de personnes testées"),
    ("TDR positifs ≤ TDR réalisés", r"^tdr_positifs?$", r"^tdr(_realises?)?$", "ligne", "grave",
     "un test ne peut être positif que s'il a été réalisé"),
    ("accouchements assistés ≤ accouchements", r"^accouchements?_assistes?$", r"^accouchements?(_total)?$", "ligne", "grave",
     "les accouchements assistés font partie de tous les accouchements"),
    ("marge ≤ chiffre d'affaires", r"^(marge|marge_brute|benefice)$", r"^(chiffre_(d_)?affaires?|ca|recettes?|ventes?)$", "ligne",
     "grave", "une marge ne dépasse pas le chiffre d'affaires dont elle est tirée"),
    ("cas confirmés ≤ tests réalisés", r"^cas_\w*confirmes?$", r"^(tdr_realises?|tests?_realises?)$", "ligne", "attention",
     "sauf si d'autres examens (goutte épaisse) ont confirmé des cas"),
    ("CPN4 ≤ CPN1", r"^cpn_?4$", r"^cpn_?1$", "site", "attention", "une femme ne fait sa 4e consultation qu'après la 1re"),
    ("CPN3 ≤ CPN1", r"^cpn_?3$", r"^cpn_?1$", "site", "attention", "une femme ne fait sa 3e consultation qu'après la 1re"),
    ("CPN2 ≤ CPN1", r"^cpn_?2$", r"^cpn_?1$", "site", "attention", "une femme ne fait sa 2e consultation qu'après la 1re"),
    ("Penta3 ≤ Penta1", r"^(enfants_vaccines_)?penta_?3$", r"^(enfants_vaccines_)?penta_?1$", "site", "attention",
     "un enfant ne reçoit la 3e dose qu'après la 1re"),
    ("VPO3 ≤ VPO1", r"^(enfants_vaccines_)?vpo_?3$", r"^(enfants_vaccines_)?vpo_?1$", "site", "attention",
     "un enfant ne reçoit la 3e dose qu'après la 1re"),
    ("mis sous traitement ≤ positifs", r"^tx_new$", r"^hts_tst_pos$", "site", "attention",
     "au-delà, des patients dépistés ailleurs ont été mis sous traitement ici : à vérifier"),
]


def _regles(df, nums, lieu, index_complet):
    """Règles entre indicateurs liés, reconnues aux noms de colonnes. Une règle ne s'applique que si chaque côté
    désigne une seule colonne (sinon on ne sait pas laquelle comparer)."""
    out, n = [], len(df)
    noms = {_norme(c): c for c in nums}
    for nom, petit, grand, portee, gravite, pourquoi in REGLES:
        a = [c for k, c in noms.items() if re.match(petit, k)]
        b = [c for k, c in noms.items() if re.match(grand, k)]
        if len(a) != 1 or len(b) != 1 or a[0] == b[0]:
            continue
        a, b = a[0], b[0]
        va, vb = pd.to_numeric(df[a], errors="coerce"), pd.to_numeric(df[b], errors="coerce")
        if portee == "ligne":
            m = (va > vb) & va.notna() & vb.notna()
            if m.any():
                i = m.idxmax()
                out.append(_constat(gravite, f"{int(m.sum())} ligne(s) ne respectent pas « {nom} »",
                    f"Ex. « {a} » ({_n(va[i])}) plus grand que « {b} » ({_n(vb[i])}) : {pourquoi}.",
                    int(m.sum()), n, a, _lignes(index_complet.isin(m[m].index)), "regle"))
        else:
            if lieu is not None:
                sa = va.groupby(df[lieu].astype(str)).sum(min_count=1)
                sb = vb.groupby(df[lieu].astype(str)).sum(min_count=1)
            else:
                sa, sb = pd.Series({"Ensemble": va.sum()}), pd.Series({"Ensemble": vb.sum()})
            hors = (sa > sb) & sa.notna() & sb.notna()
            if hors.any():
                ex = hors[hors].index[0]
                touches = df[lieu].astype(str).isin(hors[hors].index) if lieu is not None else pd.Series(True, index=df.index)
                qui = f"{int(hors.sum())} {lieu}" if lieu is not None else "le fichier"
                out.append(_constat(gravite, f"{nom} non respecté sur la période pour {qui}",
                                    f"Ex. {ex} : {a} = {_n(sa[ex])}, {b} = {_n(sb[ex])} sur toute la période ; "
                                    f"{pourquoi}.", int(touches.sum()), n, a, cle="regle"))
    return out


def _actualite(df, per_col, per, dims, aujourd_hui):
    out = []
    if per_col is None or per.notna().sum() < 2:
        return out, None
    # dates impossibles (futur, avant 1900) : déjà signalées en validité, elles fausseraient la dernière période
    per = per.where((per <= pd.Timestamp(aujourd_hui) + pd.Timedelta(days=31)) & (per >= pd.Timestamp("1900-01-01")))
    if per.notna().sum() < 2:
        return out, None
    mois = per.dt.to_period("M")
    dernier = mois.max()
    age = (pd.Timestamp(aujourd_hui).to_period("M") - dernier).n
    if age > 3:
        # jamais « grave » : des données anciennes restent justes ; « grave » est réservé à ce qui rend un chiffre faux
        out.append(_constat("attention", f"Dernières données : {dernier.strftime('%m/%Y')}",
                            f"Il y a {age} mois. Les constats décrivent une situation passée.", 1, 1, per_col, cle="anciennete"))
    tous = pd.period_range(mois.min(), dernier, freq="M")
    presents = set(mois.dropna())
    if per.dt.day.nunique() > 1 or len(presents) > 1:
        trous = [p for p in tous if p not in presents]
        annuel = len(presents) <= len({p.year for p in presents})
        if trous and not annuel:
            out.append(_constat("attention", f"{len(trous)} mois sans aucune donnée",
                                f"Ex. {', '.join(p.strftime('%m/%Y') for p in trous[:4])}. Une courbe ou une comparaison les "
                                "traverse sans les voir.", len(trous), len(tous), per_col, cle="mois_absents"))
    suivi = None
    lieu = next((c for c in dims if LIEU.search(str(c)) and 2 <= df[c].nunique() <= 500), None)
    if lieu is not None:  # une ligne « Total » n'est pas un site qui déclare
        reel = ~df[lieu].astype(str).str.strip().str.match(TOTAUX)
        df, mois = df[reel], mois[reel]
    lignes_par = pd.DataFrame({"u": df[lieu].astype(str), "m": mois}).dropna().value_counts() if lieu is not None else None
    # Fichier de rapport : chaque lieu déclare chaque mois le même nombre de lignes (une, ou une par indicateur).
    # Dans un registre, un mois sans ligne pour un lieu veut dire « aucun cas », pas « rapport manquant ».
    rapport = lignes_par is not None and len(lignes_par) and lignes_par.std(ddof=0) <= .2 * lignes_par.mean()
    if rapport and len(presents) >= 2:
        suivi = matrice_declarations(df[lieu].astype(str), mois, lieu)
        recu, attendu, taux, unites = suivi["recues"], suivi["attendues"], suivi["taux"], suivi["unites"]
        if recu < attendu:
            retard = [u for u in suivi["par_unite"] if u["manquants"]]
            ex = ", ".join(f"{u['unite']} ({len(u['manquants'])} mois)" for u in retard[:3])
            # un manque au dernier mois fausse la tendance la plus récente, celle qu'on lit en premier
            dernier_incomplet = bool(suivi["dernier"]["manquants"])
            g = "grave" if taux < 80 else "attention" if taux < 95 or dernier_incomplet else "info"
            manquants = suivi["dernier"]["manquants"]
            fin = (f" Au dernier mois ({suivi['dernier']['mois']}), pas encore de déclaration pour : "
                   f"{', '.join(manquants[:5])}{' et ' + str(len(manquants) - 5) + ' autre(s)' if len(manquants) > 5 else ''}."
                   if dernier_incomplet else "")
            out.append(_constat(g, f"Déclarations reçues : {taux:.0f} % ({recu} sur {attendu})",
                                f"{len(retard)} {lieu} sur {unites} n'ont pas déclaré tous les mois (ex. {ex}).{fin} Une baisse "
                                "peut venir d'un manque de déclaration plutôt que d'une vraie baisse.", attendu - recu, attendu,
                                lieu, cle="declarations"))
    return out, suivi


MOIS_MATRICE = 24   # mois montrés dans la matrice (les plus récents) ; les taux portent sur toute la période
UNITES_MATRICE = 300


def matrice_declarations(unites, mois, colonne):
    """Complétude des rapports selon le guide de l'OMS : pour chaque site et chaque mois, rapport reçu ou manquant.
    Un site est attendu à partir de son premier rapport : un centre ouvert en cours d'année n'est pas en retard
    avant son ouverture. Les sites attendus sont ceux du fichier : sans liste officielle des sites, un site qui n'a
    jamais déclaré ne peut pas être vu (limite dite à l'écran).

    Rend les taux global, par mois et par site, et une ligne par site : « 1 » reçu, « 0 » manquant, « - » pas
    encore attendu, sur les MOIS_MATRICE derniers mois."""
    paires = pd.DataFrame({"u": unites, "m": mois}).dropna().drop_duplicates()
    axe = sorted(paires["m"].unique())
    premier = paires.groupby("u")["m"].min()
    recus = set(map(tuple, paires[["u", "m"]].itertuples(index=False)))
    par_unite = []
    for u in premier.index:
        attendus = [m for m in axe if m >= premier[u]]
        manquants = [m for m in attendus if (u, m) not in recus]
        cases = "".join("-" if m < premier[u] else "1" if (u, m) in recus else "0" for m in axe[-MOIS_MATRICE:])
        par_unite.append({"unite": str(u), "recues": len(attendus) - len(manquants), "attendues": len(attendus),
                          "taux": round(100 * (len(attendus) - len(manquants)) / len(attendus), 1),
                          "manquants": [m.strftime("%m/%Y") for m in manquants], "cases": cases})
    par_unite.sort(key=lambda x: (x["taux"], x["unite"]))
    par_mois = []
    for m in axe:
        attendus = [u for u in premier.index if premier[u] <= m]
        n_recus = sum(1 for u in attendus if (u, m) in recus)
        par_mois.append({"mois": m.strftime("%m/%Y"), "recues": n_recus, "attendues": len(attendus),
                         "taux": round(100 * n_recus / len(attendus), 1) if attendus else 100.0})
    recu = sum(x["recues"] for x in par_unite)
    attendu = sum(x["attendues"] for x in par_unite)
    der = axe[-1]
    return {"colonne": str(colonne), "unites": int(len(premier)), "periodes": len(axe), "recues": int(recu),
            "attendues": int(attendu), "taux": round(100 * recu / attendu, 1) if attendu else 100.0,
            "mois": [m.strftime("%m/%Y") for m in axe[-MOIS_MATRICE:]], "par_mois": par_mois,
            "par_unite": par_unite[:UNITES_MATRICE], "unites_cachees": max(0, len(par_unite) - UNITES_MATRICE),
            "dernier": {"mois": der.strftime("%m/%Y"),
                        "manquants": sorted(str(u) for u in premier.index if premier[u] <= der and (u, der) not in recus)},
            "methode": "Rapports reçus sur rapports attendus (guide OMS). Un site est attendu chaque mois à partir de son "
                       "premier rapport ; les sites sont ceux du fichier."}


# ---------------------------------------------------------------- point d'entrée
def bilan(df, anomalies=None, controles=None, aujourd_hui=None):
    """Bilan qualité du fichier : note globale, note et constats par dimension, méthode."""
    aujourd_hui = aujourd_hui or dt.date.today()
    n = len(df)
    nums = [c for c in df.columns if _numerique(df[c])]
    per_col, per = _periode(df)
    ids = [c for c in df.columns if c not in nums and c != per_col and IDENT.search(str(c)) and df[c].nunique() > .9 * max(n, 1)]
    dims = [c for c in df.columns if c not in nums and c != per_col and c not in ids
            and 1 < df[c].nunique(dropna=True) <= max(60, int(.5 * n))]
    comp, taux_rempli = _completude(df, nums)
    actu, suivi = _actualite(df, per_col, per, dims, aujourd_hui)
    lieu = suivi["colonne"] if suivi else None
    temps, lues = [], set()
    if lieu is not None:  # fichier de rapport : chaque site a sa propre habitude dans le temps
        mois_ok = per.where((per <= pd.Timestamp(aujourd_hui) + pd.Timedelta(days=31)) & (per >= pd.Timestamp("1900-01-01")))
        reel = ~df[lieu].astype(str).str.strip().str.match(TOTAUX)
        temps, lues = _extremes_temps(df[reel], lieu, mois_ok[reel].dt.to_period("M"), nums, dims, df.index)
    regles = _regles(df[~df[lieu].astype(str).str.strip().str.match(TOTAUX)] if lieu is not None else df, nums, lieu,
                     df.index)
    par_dim = {"completude": comp, "unicite": _unicite(df, nums, dims, per_col, per),
               "validite": _validite(df, nums, per_col, per, aujourd_hui),
               "coherence": _coherence(df, dims, controles, nums, per) + regles,
               "plausibilite": temps + _plausibilite(anomalies, n, lues), "actualite": actu}
    ordre = {"grave": 0, "attention": 1, "info": 2}
    dimensions = []
    for cle, nom in DIMENSIONS:
        cs = sorted(par_dim[cle], key=lambda c: (ordre[c["gravite"]], -c["part"]))
        mesuree = not (cle == "actualite" and per_col is None)
        note = _note(cs) if mesuree else None
        dimensions.append({"cle": cle, "nom": nom, "note": note, "mesuree": mesuree, "constats": cs,
                           "statut": None if note is None else "grave" if any(c["gravite"] == "grave" for c in cs)
                           else "attention" if any(c["gravite"] == "attention" for c in cs) else "bon"})
    notes = [d["note"] for d in dimensions if d["note"] is not None]
    globale = round(sum(notes) / len(notes)) if notes else 100
    graves = sum(1 for d in dimensions for c in d["constats"] if c["gravite"] == "grave")
    attentions = sum(1 for d in dimensions for c in d["constats"] if c["gravite"] == "attention")
    if graves:
        globale = min(globale, 69)
    resume = ("Aucun problème relevé." if not graves and not attentions else
              f"{graves} problème(s) grave(s) et {attentions} point(s) d'attention : "
              + ("les chiffres concernés peuvent être faux tant qu'ils ne sont pas corrigés." if graves
                 else "à vérifier avant de conclure."))
    return {"version": VERSION, "normes": NORMES, "note": globale, "graves": graves, "attentions": attentions,
            "resume": resume, "lignes": int(n), "remplissage_pct": taux_rempli, "periode": per_col,
            "declarations": suivi, "dimensions": dimensions,
            "methode": "Note d'une dimension : 100 moins des pénalités proportionnelles à la part des lignes touchées "
                       "(grave ×3, plafond 60 ; attention ×1,5, plafond 30 ; information ×0,5, plafond 10) ; 69 au plus "
                       "avec un constat grave. Note globale : moyenne des dimensions mesurées, 69 au plus avec un constat grave."}


def principaux(b, n=3):
    """Les constats les plus importants, pour un bandeau : graves d'abord."""
    tous = [dict(c, dimension=d["nom"]) for d in b.get("dimensions", []) for c in d["constats"] if c["gravite"] != "info"]
    tous.sort(key=lambda c: (0 if c["gravite"] == "grave" else 1, -c["part"]))
    return tous[:n]
