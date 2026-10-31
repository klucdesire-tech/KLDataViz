# -*- coding: utf-8 -*-
"""Analyse prédictive : régression (linéaire / logistique) et prévision de séries temporelles.

Sans sklearn ni statsmodels (absents du .venv) : moindres carrés et maximum de vraisemblance
avec numpy/scipy. Chaque modèle est validé sur un découpage entraînement/test :
- régression linéaire    : coefficients, R², RMSE (train et test), p-values, IC 95 %
- régression logistique  : coefficients, odds ratios, AUC, exactitude, matrice de confusion
- prévision              : tendance (+ saisonnalité mensuelle), prévisions et intervalle 80 %

Règles de prudence :
- jamais de prédiction sans variable cible explicite ;
- aucune extrapolation au-delà de l'horizon demandé ;
- un modèle n'est retenu que si le test tient (R²/AUC supérieurs à un minimum) ; sinon, on le dit.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import optimize, stats as _st

MAX_PREDICTEURS = 8
MAX_LIGNES = 50_000
MIN_LIGNES = 30
R2_MIN = 0.05
AUC_MIN = 0.60


def _r(x, n=4):
    try:
        v = float(x)
        return round(v, n) if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _split(n: int, test_size: float = 0.3) -> tuple:
    """Indices entraînement/test, mélange déterministe (reproductible)."""
    idx = np.arange(n)
    rng = np.random.default_rng(0)
    rng.shuffle(idx)
    cut = max(1, int(n * (1 - test_size)))
    return idx[:cut], idx[cut:]


def _design(X: pd.DataFrame) -> np.ndarray:
    """Matrice de conception avec constante (intercept)."""
    return np.column_stack([np.ones(len(X)), X.to_numpy(dtype=float)])


def _standardiser(X: pd.DataFrame) -> tuple:
    """Centre-réduit chaque prédicteur (les coefficients restent comparables)."""
    mu = X.mean()
    sd = X.std(ddof=0).replace(0, 1)
    return (X - mu) / sd, mu, sd


def regression_lineaire(X: pd.DataFrame, y: pd.Series, noms: list | None = None,
                        test_size: float = 0.3) -> dict:
    """Moindres carrés ordinaires avec validation entraînement/test."""
    noms = list(X.columns) if noms is None else noms
    n = len(y)
    if n < MIN_LIGNES:
        return {"erreur": f"trop peu de lignes ({n} < {MIN_LIGNES}) pour une régression fiable."}
    idx_tr, idx_te = _split(n, test_size)
    A = _design(X)
    yv = y.to_numpy(dtype=float)
    coef, *_ = np.linalg.lstsq(A[idx_tr], yv[idx_tr], rcond=None)
    pred_tr = A[idx_tr] @ coef
    pred_te = A[idx_te] @ coef
    rss = float(((yv[idx_tr] - pred_tr) ** 2).sum())
    tss = float(((yv[idx_tr] - yv[idx_tr].mean()) ** 2).sum())
    r2_tr = 1 - rss / tss if tss > 0 else None
    r2_te = None
    if len(idx_te) > 1:
        sse_te = float(((yv[idx_te] - pred_te) ** 2).sum())
        sst_te = float(((yv[idx_te] - yv[idx_te].mean()) ** 2).sum())
        r2_te = 1 - sse_te / sst_te if sst_te > 0 else None
    rmse_tr = math.sqrt(rss / len(idx_tr)) if len(idx_tr) else None
    rmse_te = math.sqrt(float(((yv[idx_te] - pred_te) ** 2).sum()) / len(idx_te)) if len(idx_te) else None
    # p-values des coefficients (approximation normale de Student)
    pvals, ic = [None] * len(coef), [None] * len(coef)
    dof = len(idx_tr) - A.shape[1]
    if dof > 0 and rss > 0:
        try:
            sigma2 = rss / dof
            cov = sigma2 * np.linalg.inv(A[idx_tr].T @ A[idx_tr])
            se = np.sqrt(np.diag(cov))
            tstat = coef / np.where(se > 0, se, np.nan)
            pvals = [float(2 * _st.t.sf(abs(t), dof)) if math.isfinite(t) else None for t in tstat]
            ic = [[_r(c - 1.96 * s, 3), _r(c + 1.96 * s, 3)] for c, s in zip(coef, se)]
        except Exception:
            pass
    return {
        "modele": "régression linéaire (moindres carrés)",
        "n": int(n), "n_test": int(len(idx_te)), "cible": str(y.name) if y.name else "cible",
        "coefficients": [{"variable": "(constante)" if i == 0 else noms[i - 1], "coef": _r(c, 4),
                          "p": _r(p, 6) if p is not None else None, "ic95": ic[i]}
                         for i, (c, p) in enumerate(zip(coef, pvals))],
        "r2_train": _r(r2_tr, 3), "r2_test": _r(r2_te, 3),
        "rmse_train": _r(rmse_tr, 2), "rmse_test": _r(rmse_te, 2),
        "fiable": bool(r2_te is not None and r2_te >= R2_MIN),
        "limitations": ["R² négatif en test = le modèle prédit moins bien que la moyenne ; ne pas l'utiliser."
                        if (r2_te is not None and r2_te < 0) else
                        "Un R² faible n'empêche pas une variable d'être utile ; une corrélation n'est pas une causalité."],
    }


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def regression_logistique(X: pd.DataFrame, y: pd.Series, noms: list | None = None,
                          test_size: float = 0.3) -> dict:
    """Régression logistique par maximum de vraisemblance (BFGS), avec AUC et matrice de confusion."""
    noms = list(X.columns) if noms is None else noms
    yv = y.to_numpy(dtype=float)
    if len(np.unique(yv)) != 2:
        return {"erreur": "la cible doit avoir exactement deux modalités (Oui/Non, 0/1…)."}
    n = len(yv)
    if n < MIN_LIGNES:
        return {"erreur": f"trop peu de lignes ({n} < {MIN_LIGNES}) pour une régression fiable."}
    if min(int(yv.sum()), int(n - yv.sum())) < 5:
        return {"erreur": "une des deux modalités a moins de 5 cas : modèle instable."}
    idx_tr, idx_te = _split(n, test_size)
    A = _design(X)

    def nll(beta):
        z = A[idx_tr] @ beta
        p = _sigmoid(z)
        eps = 1e-9
        return -float((yv[idx_tr] * np.log(p + eps) + (1 - yv[idx_tr]) * np.log(1 - p + eps)).sum())

    def grad(beta):
        z = A[idx_tr] @ beta
        return A[idx_tr].T @ (_sigmoid(z) - yv[idx_tr])

    res = optimize.minimize(nll, np.zeros(A.shape[1]), jac=grad, method="BFGS",
                            options={"maxiter": 500})
    beta = res.x
    p_te = _sigmoid(A[idx_te] @ beta)
    pred_te = (p_te >= 0.5).astype(int)
    y_te = yv[idx_te].astype(int)
    acc = float((pred_te == y_te).mean()) if len(y_te) else None
    # AUC (probabilité qu'un cas positif soit mieux classé qu'un négatif)
    auc = None
    try:
        pos, neg = p_te[y_te == 1], p_te[y_te == 0]
        if len(pos) and len(neg):
            auc = float((pos[:, None] > neg[None, :]).mean()
                        + 0.5 * (pos[:, None] == neg[None, :]).mean())
    except Exception:
        pass
    tp = int(((pred_te == 1) & (y_te == 1)).sum())
    fp = int(((pred_te == 1) & (y_te == 0)).sum())
    fn = int(((pred_te == 0) & (y_te == 1)).sum())
    tn = int(((pred_te == 0) & (y_te == 0)).sum())
    return {
        "modele": "régression logistique (maximum de vraisemblance)",
        "n": int(n), "n_test": int(len(idx_te)), "cible": str(y.name) if y.name else "cible",
        "coefficients": [{"variable": "(constante)" if i == 0 else noms[i - 1],
                          "coef": _r(b, 4), "odds_ratio": _r(math.exp(b), 3) if abs(b) < 700 else None}
                         for i, b in enumerate(beta)],
        "auc_test": _r(auc, 3), "exactitude_test": _r(acc, 3),
        "matrice_confusion": {"vrais_positifs": tp, "faux_positifs": fp,
                              "faux_negatifs": fn, "vrais_negatifs": tn},
        "fiable": bool(auc is not None and auc >= AUC_MIN),
        "limitations": ["AUC 0,5 = hasard ; en dessous de 0,6 le modèle n'apporte rien. "
                        "Classement à 0,5 : ajuster le seuil selon le coût des faux positifs/négatifs."],
    }


def prevision(serie: pd.Series, horizon: int = 6, saison: int = 12) -> dict:
    """Prévision par tendance linéaire (+ saisonnalité mensuelle si au moins 2 cycles).

    `serie` : index = période « AAAA-MM » triée, valeurs = mesure agrégée.
    """
    s = serie.dropna().sort_index()
    if len(s) < max(6, saison // 2):
        return {"erreur": f"série trop courte ({len(s)} points) pour une prévision."}
    n = len(s)
    t = np.arange(n, dtype=float)
    y = s.to_numpy(dtype=float)
    A = np.column_stack([np.ones(n), t])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    tendance = A @ coef
    resid = y - tendance
    resid_std = float(resid.std(ddof=1)) if n > 2 else float(resid.std())
    # Saisonnalité : moyenne des résidus par position dans le cycle (si assez de cycles complets)
    saisonnalite = np.zeros(saison)
    has_saison = n >= saison * 2
    if has_saison:
        for i in range(n):
            saisonnalite[i % saison] += resid[i]
        saisonnalite = saisonnalite / np.array([((n - i - 1) // saison) + 1 for i in range(saison)])
    futur = np.arange(n, n + horizon, dtype=float)
    A_f = np.column_stack([np.ones(horizon), futur])
    base = A_f @ coef
    if has_saison:
        base = base + np.array([saisonnalite[(n + k) % saison] for k in range(horizon)])
    # Intervalle de prédiction à 80 % (approx. normale)
    marge = 1.28 * resid_std * np.sqrt(1 + np.arange(1, horizon + 1) / max(n, 1))
    # Étiquettes des périodes futures (AAAA-MM)
    labels = []
    try:
        last = pd.Period(str(s.index[-1]), freq="M")
        labels = [str(last + k) for k in range(1, horizon + 1)]
    except Exception:
        labels = [f"T+{k}" for k in range(1, horizon + 1)]
    return {
        "modele": "tendance linéaire" + (" + saisonnalité mensuelle" if has_saison else ""),
        "n": int(n), "horizon": int(horizon),
        "tendance_pente": _r(coef[1], 3), "residu_ecart_type": _r(resid_std, 2),
        "saisonnalite": bool(has_saison),
        "historique": {"periodes": [str(i) for i in s.index], "valeurs": [_r(v, 2) for v in y]},
        "previsions": [{"periode": lab, "valeur": _r(v, 2), "bas": _r(v - m, 2), "haut": _r(v + m, 2)}
                       for lab, v, m in zip(labels, base, marge)],
        "limitations": ["Prévision statistique : elle prolonge la tendance observée, elle n'anticipe "
                        "aucun changement (campagne, épidémie, nouvelle politique). Intervalle à 80 %."],
    }


def analyse(df: pd.DataFrame, cible: str | None = None, predicteurs: list | None = None,
            periode: str | None = None, horizon: int = 6) -> dict:
    """Choisit le modèle adapté et renvoie un résultat JSON-compatible.

    - `cible` numérique → régression linéaire ; `cible` à 2 modalités → régression logistique.
    - `periode` (colonne de dates) + cible numérique → prévision de la série agrégée.
    """
    out = {"cible": cible, "predicteurs": predicteurs or [], "resultat": None, "erreurs": []}
    if df is None or df.empty:
        out["erreurs"].append("aucune donnée.")
        return out
    if len(df) > MAX_LIGNES:
        df = df.sample(MAX_LIGNES, random_state=0)
    df = df.replace([np.inf, -np.inf], np.nan)
    if periode and cible and periode in df.columns:
        try:
            per = pd.to_datetime(df[periode], errors="coerce", format="mixed", dayfirst=True)
            s = df.assign(_p=per.dt.to_period("M").astype(str)).dropna(subset=["_p"])
            s = s[s["_p"] != "NaT"]
            agg = pd.to_numeric(s[cible], errors="coerce").groupby(s["_p"]).sum()
            out["resultat"] = dict(prevision(agg, horizon), type="prevision")
            out["resultat"]["agregation"] = "somme mensuelle"
        except Exception as e:
            out["erreurs"].append(f"prévision impossible : {e}")
        return out
    if not cible or cible not in df.columns:
        out["erreurs"].append("cible absente : indiquer la colonne à expliquer.")
        return out
    y = df[cible]
    preds = [p for p in (predicteurs or []) if p in df.columns and p != cible]
    if not preds:
        preds = [c for c in df.columns if c != cible and pd.api.types.is_numeric_dtype(df[c])]
        preds = preds[:MAX_PREDICTEURS]
    if not preds:
        out["erreurs"].append("aucun prédicteur numérique disponible.")
        return out
    sub = pd.DataFrame({"y": pd.to_numeric(y, errors="coerce")})
    for p in preds[:MAX_PREDICTEURS]:
        sub[p] = pd.to_numeric(df[p], errors="coerce")
    sub = sub.dropna()
    if len(sub) < MIN_LIGNES:
        out["erreurs"].append(f"trop peu de lignes complètes ({len(sub)} < {MIN_LIGNES}).")
        return out
    X, yv = sub[list(sub.columns[1:])], sub["y"]
    uniques = np.unique(yv.dropna())
    try:
        if len(uniques) == 2:
            out["resultat"] = dict(regression_logistique(X, yv, list(X.columns)), type="logistique")
        else:
            out["resultat"] = dict(regression_lineaire(X, yv, list(X.columns)), type="lineaire")
    except Exception as e:
        out["erreurs"].append(f"modèle impossible : {e}")
    return out
