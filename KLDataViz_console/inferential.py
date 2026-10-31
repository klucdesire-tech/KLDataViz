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
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats as _st

MAX_LIGNES = 20_000
MAX_MODALITES = 15
MAX_COLONNES = 12
MAX_TESTS = 60
ALPHA = 0.05


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


def analyse(df: pd.DataFrame, alpha: float = ALPHA) -> dict:
    """Tests inférentiels sur un DataFrame. Retourne un dict JSON-compatible."""
    out = {"alpha": alpha, "correlations": [], "associations": [], "comparaisons": [],
           "resume": "", "limitations": []}
    if df is None or df.empty:
        out["resume"] = "Aucune donnée à tester."
        return out
    if len(df) > MAX_LIGNES:
        df = df.sample(MAX_LIGNES, random_state=0)
        out["limitations"].append(f"Échantillon aléatoire de {MAX_LIGNES:,} lignes.".replace(",", " "))
    n_rows = int(len(df))
    numeric = df.select_dtypes(include=[np.number]).dropna(axis=1, how="all")
    cat = df.select_dtypes(exclude=[np.number])
    tests = 0

    # --- Numérique × numérique : Pearson + p-value + IC 95 % (transformation de Fisher)
    num_cols = [c for c in numeric.columns if numeric[c].nunique(dropna=True) > 2][:MAX_COLONNES]
    for i, a in enumerate(num_cols):
        for b in num_cols[:i]:
            if tests >= MAX_TESTS:
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
                    "n": n, "ic95": ic, "significatif": bool(p < alpha),
                    "force": ("forte" if abs(r) >= 0.7 else "modérée" if abs(r) >= 0.4 else
                              "faible" if abs(r) >= 0.2 else "négligeable"),
                })
                tests += 1
            except Exception:
                continue
    out["correlations"].sort(key=lambda x: (not x["significatif"], -abs(x["r"] or 0)))

    # --- Catégoriel × catégoriel : chi² + p-value + V de Cramér
    nun = cat.nunique(dropna=True)
    cat_ok = [c for c in cat.columns if 2 <= nun[c] <= MAX_MODALITES][:MAX_COLONNES]
    for i, a in enumerate(cat_ok):
        for b in cat_ok[:i]:
            if tests >= MAX_TESTS:
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
                    "attendus_faibles": attendus_faibles,
                    "force": ("forte" if v >= 0.5 else "modérée" if v >= 0.3 else
                              "faible" if v >= 0.1 else "négligeable"),
                })
                tests += 1
            except Exception:
                continue
    out["associations"].sort(key=lambda x: (not x["significatif"], -x["v_cramer"]))

    # --- Numérique × catégoriel : t de Student (2 groupes) ou ANOVA (> 2)
    cat_ok2 = [c for c in cat.columns if 2 <= nun[c] <= MAX_MODALITES][:6]
    for nc in num_cols[:6]:
        for cc in cat_ok2:
            if tests >= MAX_TESTS:
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
                    "n": int(len(sub)),
                    "groupes": [{"label": nom, "n": int(len(g)), "moyenne": _r(float(g.mean()), 2),
                                 "ic95": _ic_moyenne(g)} for nom, g in zip(noms, groupes)],
                })
                tests += 1
            except Exception:
                continue
    out["comparaisons"].sort(key=lambda x: (not x["significatif"], -(abs(x["effet"]) if x["effet"] is not None else 0)))

    # --- correction pour tests multiples (Benjamini-Hochberg, taux de fausses découvertes)
    tous = out["correlations"] + out["associations"] + out["comparaisons"]
    ajustees = _bh([x.get("p") for x in tous])
    for x, pa in zip(tous, ajustees):
        x["p_ajuste"] = _r(pa, 6) if pa is not None else None
        x["significatif_brut"] = bool(x["significatif"])          # p brute conservée
        x["significatif"] = bool(pa is not None and pa < alpha)   # décision sur la p ajustée
    n_sig = sum(1 for x in tous if x["significatif"])
    n_brut = sum(1 for x in tous if x["significatif_brut"])
    out["resume"] = (f"{n_sig} résultat(s) significatif(s) après correction pour tests multiples "
                     f"(Benjamini-Hochberg) sur {tests} test(s), contre {n_brut} sans correction "
                     f"({n_rows:,} lignes).".replace(",", " "))
    out["limitations"].append(
        "Tests d'association et de comparaison : ils indiquent une liaison statistique, jamais une causalité. "
        "Les p-values sont corrigées par Benjamini-Hochberg (taux de fausses découvertes) ; "
        "un résultat significatif après correction reste à confirmer sur d'autres données."
    )
    return out
