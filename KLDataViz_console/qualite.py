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
        d = pd.to_datetime(s.astype(str).str.strip().where(s.notna()), errors="coerce", format="mixed", dayfirst=not iso)
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
    cellules = n * max(1, df.shape[1])
    vides_tot = 0
    for c in df.columns:
        s = df[c]
        vide, att = _vide(s), _attente(s)
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


def _validite(df, nums, per_col, per, aujourd_hui):
    out, n = [], len(df)
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


def _plausibilite(anomalies, n):
    out = []
    for a in anomalies or []:
        out.append(_constat("attention", f"« {a['column']} » : {a['count']} valeur(s) extrême(s)",
                            f"{a['detail']}. Erreur de saisie ou vrai pic : à vérifier à la source avant de conclure.",
                            a["count"], max(n, 1), a["column"], a.get("lignes"), "extremes"))
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
    par_dim = {"completude": comp, "unicite": _unicite(df, nums, dims, per_col, per),
               "validite": _validite(df, nums, per_col, per, aujourd_hui),
               "coherence": _coherence(df, dims, controles, nums, per),
               "plausibilite": _plausibilite(anomalies, n), "actualite": actu}
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
