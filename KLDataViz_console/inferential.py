# -*- coding: utf-8 -*-
"""Analyse inférentielle : tests formels avec p-value, intervalles de confiance et taille d'effet.

Complète `modeling.bivariate_stats` (descriptif) : ici, on teste si les écarts observés sont
statistiquement significatifs, et de quelle ampleur.

- Numérique × numérique  : corrélation de Pearson, p-value, IC 95 %, n
- Catégoriel × catégoriel : chi² d'indépendance, p-value, degrés de liberté, V de Cramér, n
- Numérique × catégoriel  : test t (2 groupes) ou ANOVA (> 2), p-value, taille d'effet
                           (Cohen's d ou êta²), moyennes et IC 95 % par groupe

Bornes identiques à bivariate_stats : échantillon ≤ MAX_LIGNES, catégories 2..MAX_MODALITES,
nombre de tests plafonné. Chaque test est entouré d'un try/except : un test impossible est
omis, jamais deviné.

La fiche de la page choisit les colonnes : une année, une date ou un identifiant ne se teste pas.
Un lien qui tient à la construction du fichier (chaque hôtel dans une seule ville, marge et chiffre
d'affaires) est mis à part : il ne compte ni dans les résultats ni dans la correction.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as _st

MAX_LIGNES = 20_000
MAX_MODALITES = 15
MAX_COLONNES = 12
MAX_TESTS = 60
ALPHA = 0.05
LIEN_STRUCTURE = 0.999      # |r|, V ou êta² au-delà : une colonne découle de l'autre


def _r(x, n=4):
    """Arrondi sûr : NaN/inf → None (JSON valide)."""
    try:
        v = float(x)
        return round(v, n) if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _ic_moyenne(s: pd.Series) -> list | None:
    """IC 95 % de la moyenne (t de Student), None si trop peu de valeurs."""
    v = pd.to_numeric(s, errors="coerce").dropna()
    n = len(v)
    if n < 3:
        return None
    m, se = float(v.mean()), float(v.std(ddof=1)) / math.sqrt(n)
    if not math.isfinite(se) or se == 0:
        return None
    lo, hi = _st.t.interval(0.95, n - 1, loc=m, scale=se)
    return [_r(lo, 2), _r(hi, 2)]


def _cohen_d(a: pd.Series, b: pd.Series) -> float | None:
    """Taille d'effet pour deux groupes (d de Cohen, écart-type poolé)."""
    a, b = pd.to_numeric(a, errors="coerce").dropna(), pd.to_numeric(b, errors="coerce").dropna()
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    va, vb = float(a.var(ddof=1)), float(b.var(ddof=1))
    sp = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    if not math.isfinite(sp) or sp == 0:
        return None
    return _r((float(a.mean()) - float(b.mean())) / sp, 3)


def _eta2(groups: list[pd.Series]) -> float | None:
    """êta² (part de variance expliquée) pour l'ANOVA."""
    try:
        grand = pd.concat(groups)
        m = float(grand.mean())
        ss_tot = float(((grand - m) ** 2).sum())
        ss_bet = sum(len(g) * (float(g.mean()) - m) ** 2 for g in groups)
        return _r(ss_bet / ss_tot, 3) if ss_tot > 0 else None
    except Exception:
        return None


def _bh(pvals: list) -> list:
    """p-values ajustées par Benjamini-Hochberg (contrôle du taux de fausses découvertes).

    Renvoie une liste de p ajustées dans le même ordre que l'entrée. None reste None et
    ne compte pas dans le nombre de tests.
    """
    valides = [(i, p) for i, p in enumerate(pvals) if p is not None]
    m = len(valides)
    if m == 0:
        return [None] * len(pvals)
    ordre = sorted(valides, key=lambda x: x[1])
    ajustees = [None] * len(pvals)
    precedent = 1.0
    for rang in range(m - 1, -1, -1):
        i, p = ordre[rang]
        precedent = min(precedent, float(p) * m / (rang + 1))
        ajustees[i] = precedent
    return ajustees


def _force_effet(effet, nom) -> str | None:
    """Ampleur d'un écart entre groupes, seuils usuels de Cohen."""
    if effet is None:
        return None
    fort, moyen, faible = (0.8, 0.5, 0.2) if nom == "d de Cohen" else (0.14, 0.06, 0.01)
    v = abs(effet)
    return "forte" if v >= fort else "modérée" if v >= moyen else "faible" if v >= faible else "négligeable"


def _cadre(df: pd.DataFrame) -> tuple:
    """Lecture de la fiche de la page : colonnes à ne pas tester, et lignes qui ne sont pas des observations séparées.

    Une ligne par combinaison de période, lieu et catégorie : chaque ligne résume un groupe, ou le même lieu revient
    à chaque période. Les tests supposent des lignes indépendantes, leurs p sont alors trop favorables.
    """
    try:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "HERMES_DA")
        if d not in sys.path:
            sys.path.insert(0, d)
        from hermes_da.adapters.fiche import lire_fiche
        f = lire_fiche(df)
    except Exception:
        return set(), False
    periode = (f["periode"] or {}).get("colonne")
    exclues = set(f["identifiants"]) | set(f["ignorees"]) | ({str(periode)} if periode else set())
    cles = [c for c in df.columns if str(c) == str(periode) or str(c) in f["lieux"] or str(c) in f["categories"]]
    return exclues, bool(f["agrege"]) or bool(cles and not df.duplicated(subset=cles).any())


def analyse(df: pd.DataFrame, alpha: float = ALPHA) -> dict:
    """Tests inférentiels sur un DataFrame. Retourne un dict JSON-compatible."""
    out = {"alpha": alpha, "correlations": [], "associations": [], "comparaisons": [],
           "resume": "", "limitations": [], "n_tests": 0, "n_significatifs": 0, "n_structure": 0,
           "lignes_dependantes": False}
    if df is None or df.empty:
        out["resume"] = "Aucune donnée à tester."
        return out
    if len(df) > MAX_LIGNES:
        df = df.sample(MAX_LIGNES, random_state=0)
        out["limitations"].append(f"Échantillon aléatoire de {MAX_LIGNES:,} lignes.".replace(",", " "))
    n_rows = int(len(df))
    exclues, out["lignes_dependantes"] = _cadre(df)
    numeric = df.select_dtypes(include=[np.number]).dropna(axis=1, how="all")
    numeric = numeric[[c for c in numeric.columns if str(c) not in exclues]]
    cat = df.select_dtypes(exclude=[np.number])
    cat = cat[[c for c in cat.columns if str(c) not in exclues]]
    tests = 0
    tous_num = [c for c in numeric.columns if numeric[c].nunique(dropna=True) > 2]
    nun = cat.nunique(dropna=True)
    tous_cat = [c for c in cat.columns if 2 <= nun[c] <= MAX_MODALITES]
    cat_ok = tous_cat[:MAX_COLONNES]
    # chaque famille garde sa part : douze mesures ne doivent pas consommer tous les tests
    plafond_corr = MAX_TESTS // 3 if cat_ok else MAX_TESTS
    plafond_assoc = 2 * MAX_TESTS // 3 if tous_num else MAX_TESTS
    coupe = len(tous_num) > MAX_COLONNES or len(tous_cat) > MAX_COLONNES

    # --- Numérique × numérique : Pearson + p-value + IC 95 % (transformation de Fisher)
    num_cols = tous_num[:MAX_COLONNES]
    for i, a in enumerate(num_cols):
        for b in num_cols[:i]:
            if tests >= plafond_corr:
                coupe = True
                break
            try:
                sub = df[[a, b]].apply(pd.to_numeric, errors="coerce").dropna()
                n = len(sub)
                if n < 5:
                    continue
                r, p = _st.pearsonr(sub[a], sub[b])
                r, p = float(r), float(p)
                ic = None
                if abs(r) < 1 and n > 3:
                    z, se = math.atanh(r), 1 / math.sqrt(n - 3)
                    ic = [_r(math.tanh(z - 1.96 * se), 3), _r(math.tanh(z + 1.96 * se), 3)]
                out["correlations"].append({
                    "a": str(a), "b": str(b), "r": _r(r, 3), "p": _r(p, 6),
                    "n": n, "ic95": ic, "significatif": bool(p < alpha), "structurel": abs(r) >= LIEN_STRUCTURE,
                    "force": ("forte" if abs(r) >= 0.7 else "modérée" if abs(r) >= 0.4 else
                              "faible" if abs(r) >= 0.2 else "négligeable"),
                })
                tests += 1
            except Exception:
                continue

    # --- Catégoriel × catégoriel : chi² + p-value + V de Cramér
    for i, a in enumerate(cat_ok):
        for b in cat_ok[:i]:
            if tests >= plafond_assoc:
                coupe = True
                break
            try:
                ct = pd.crosstab(cat[a], cat[b])
                if ct.shape[0] < 2 or ct.shape[1] < 2:
                    continue
                chi2, p, dl, exp = _st.chi2_contingency(ct.values)
                n = int(ct.values.sum())
                k = min(ct.shape) - 1
                v = math.sqrt(chi2 / (n * k)) if n and k else 0.0
                attendus_faibles = bool((exp < 5).any())
                test = "chi² d'indépendance"
                if ct.shape == (2, 2):      # 2×2 : test exact de Fisher (valide même à petits effectifs)
                    try:
                        _, p_fisher = _st.fisher_exact(ct.values)
                        p, test = float(p_fisher), "test exact de Fisher (2×2)"
                    except Exception:
                        pass
                out["associations"].append({
                    "a": str(a), "b": str(b), "chi2": _r(chi2, 2), "p": _r(p, 6), "dl": int(dl),
                    "v_cramer": _r(v, 3), "n": n, "significatif": bool(p < alpha), "test": test,
                    "attendus_faibles": attendus_faibles, "structurel": v >= LIEN_STRUCTURE,
                    "force": ("forte" if v >= 0.5 else "modérée" if v >= 0.3 else
                              "faible" if v >= 0.1 else "négligeable"),
                })
                tests += 1
            except Exception:
                continue

    # --- Numérique × catégoriel : t de Student (2 groupes) ou ANOVA (> 2)
    cat_ok2 = tous_cat[:6]
    coupe = coupe or (len(num_cols) > 6 and bool(cat_ok2)) or len(tous_cat) > 6
    for nc in num_cols[:6]:
        for cc in cat_ok2:
            if tests >= MAX_TESTS:
                coupe = True
                break
            try:
                sub = pd.DataFrame({"y": pd.to_numeric(df[nc], errors="coerce"), "g": df[cc]}).dropna()
                if len(sub) < 6:
                    continue
                noms, groupes = [], []
                for g, part in sub.groupby("g", observed=True)["y"]:
                    if len(part) >= 2:
                        noms.append(str(g))
                        groupes.append(part)
                if len(groupes) < 2:
                    continue
                if len(groupes) == 2:
                    t, p = _st.ttest_ind(groupes[0], groupes[1], equal_var=False)
                    test, effet, effet_nom = "t de Student (Welch)", _cohen_d(groupes[0], groupes[1]), "d de Cohen"
                else:
                    f, p = _st.f_oneway(*groupes)
                    test, effet, effet_nom = "ANOVA à un facteur", _eta2(groupes), "êta²"
                out["comparaisons"].append({
                    "num": str(nc), "cat": str(cc), "test": test, "p": _r(float(p), 6),
                    "significatif": bool(float(p) < alpha), "effet": effet, "effet_nom": effet_nom,
                    "force": _force_effet(effet, effet_nom), "n": int(len(sub)),
                    "structurel": effet_nom == "êta²" and (effet or 0) >= LIEN_STRUCTURE,
                    "groupes": [{"label": nom, "n": int(len(g)), "moyenne": _r(float(g.mean()), 2),
                                 "ic95": _ic_moyenne(g)} for nom, g in zip(noms, groupes)],
                })
                tests += 1
            except Exception:
                continue

    # --- correction pour tests multiples (Benjamini-Hochberg, taux de fausses découvertes)
    tous = [x for x in out["correlations"] + out["associations"] + out["comparaisons"] if not x["structurel"]]
    ajustees = _bh([x.get("p") for x in tous])
    for x, pa in zip(tous, ajustees):
        x["p_ajuste"] = _r(pa, 6) if pa is not None else None
        x["significatif_brut"] = bool(x["significatif"])          # p brute conservée
        x["significatif"] = bool(pa is not None and pa < alpha)   # décision sur la p ajustée
    structure = [x for x in out["correlations"] + out["associations"] + out["comparaisons"] if x["structurel"]]
    for x in structure:
        x["p_ajuste"], x["significatif_brut"], x["significatif"] = x["p"], bool(x["significatif"]), False
    for cle in ("correlations", "associations", "comparaisons"):
        out[cle].sort(key=lambda x: (x["structurel"], not x["significatif"], x["p_ajuste"] if x["p_ajuste"] is not None else 1))
    n_sig = sum(1 for x in tous if x["significatif"])
    n_brut = sum(1 for x in tous if x["significatif_brut"])
    out["n_tests"], out["n_significatifs"], out["n_structure"] = len(tous), n_sig, len(structure)
    out["resume"] = (f"{n_sig} résultat(s) significatif(s) après correction pour tests multiples "
                     f"(Benjamini-Hochberg) sur {len(tous)} test(s), contre {n_brut} sans correction "
                     f"({n_rows:,} lignes).".replace(",", " "))
    if out["lignes_dependantes"]:
        out["limitations"].append(
            "Ce fichier semble compter une ligne par groupe ou par période : ses lignes ne sont pas des observations "
            "indépendantes, et les résultats sont à vérifier sur les données détaillées.")
    if coupe:
        out["limitations"].append("Trop de colonnes pour tout tester : seules les premières de chaque type l'ont été.")
    out["limitations"].append(
        "Tests d'association et de comparaison : ils indiquent une liaison statistique, jamais une causalité. "
        "Les p-values sont corrigées par Benjamini-Hochberg (taux de fausses découvertes) ; "
        "un résultat significatif après correction reste à confirmer sur d'autres données."
    )
    return out
