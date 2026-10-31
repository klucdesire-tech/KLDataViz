# -*- coding: utf-8 -*-
"""Analyse prédictive : régression (linéaire / logistique) et prévision de séries temporelles.

Sans sklearn ni statsmodels (absents du .venv) : moindres carrés et maximum de vraisemblance
avec numpy/scipy. Chaque modèle est validé sur un découpage entraînement/test ; la prévision
est rétro-testée sur les dernières périodes et comparée à deux références naïves.

Corrections issues des relectures (PR 136) :
- dates ISO jamais lues « jour en premier » (2025-03-05 reste le 5 mars) ;
- cible Oui/Non reconnue (OUI/NON, true/false, 1/0) au lieu d'être vidée par to_numeric ;
- nature de la mesure lue par `lire_fiche` (HERMES_DA), pas devinée par mots-clés ;
  nature incertaine = prévision refusée, jamais une somme par défaut ;
- stock = somme des groupes à la dernière date du mois (pas une seule ligne) ;
- prévision : valeur comptée paramétrable, toutes les lignes agrégées avant tout
  échantillonnage, axe calendaire, mois incomplet détecté par les dates au jour
  (jamais par une chute de valeur), horizon borné, rétro-test du même modèle que la
  prévision et comparaison à la moyenne / au dernier mois ;
- saisonnalité conservée seulement si elle améliore le rétro-test ;
- fiabilité resserrée : trois états, positifs en test, aucun vrai positif → jamais
  au-dessus d'« indicatif » ;
- fuite de cible : identifiants écartés, alerte si un prédicteur reproduit la cible ;
- régression logistique standardisée (coefficients affichés à l'échelle d'origine,
  séparation parfaite signalée, convergence vérifiée) ;
- régression linéaire : IC par t de Student, prédicteurs constants ou colinéaires écartés.
"""
from __future__ import annotations

import math
import os
import re
import sys

import numpy as np
import pandas as pd
from scipy import optimize, stats as _st

MAX_PREDICTEURS = 8
MAX_LIGNES = 50_000
MIN_LIGNES = 30
MIN_POSITIFS_TEST = 5          # au moins 5 cas positifs dans l'échantillon de test
R2_UTILISABLE = 0.10           # en dessous : « insuffisant »
AUC_UTILISABLE = 0.65          # en dessous : « insuffisant »
R2_SUSPECT = 0.995             # au-dessus : fuite probable de la cible

YES = {"oui", "yes", "true", "vrai", "o", "y", "positif", "positive", "1", "1.0"}
NO = {"non", "no", "false", "faux", "n", "négatif", "negatif", "0", "0.0"}
IDLIKE = re.compile(r"(^|[\s_])(n°|no|num(ero|éro)?|id|code|matricule|ref|référence|reference|labno|datim)($|[\s_.])", re.I)


def _r(x, n=4):
    try:
        v = float(x)
        return round(v, n) if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _hermes():
    """Fiche et catalogue de HERMES_DA — les mêmes que la page, pas une seconde heuristique."""
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "HERMES_DA")
    if d not in sys.path:
        sys.path.insert(0, d)
    from hermes_da.adapters import catalogue, fiche
    return fiche, catalogue


def _nature_fiche(df, cible):
    """Nature de la mesure lue par `lire_fiche`. Retourne (nature, avertissement).

    nature ∈ volume / moyenne / taux / stock, ou None si la fiche ne la classe pas.
    Dans le doute, on ne choisit pas : la prévision est refusée plus haut.
    """
    try:
        fiche, _ = _hermes()
        f = fiche.lire_fiche(df)
    except Exception as e:                                   # fiche indisponible : on le dit
        return None, f"nature de la mesure indéterminée (fiche indisponible : {type(e).__name__})."
    for nature, cle in (("stock", "stocks"), ("taux", "taux"),
                        ("volume", "volumes"), ("moyenne", "moyennes")):
        if cible in f.get(cle, []):
            return nature, None
    return None, f"nature de « {cible} » incertaine : la fiche ne la classe ni volume, ni moyenne, ni taux, ni stock."


def _agregation(nature):
    """Comment regrouper la mesure — règle unique du catalogue HERMES_DA."""
    try:
        _, catalogue = _hermes()
        return catalogue.agregation(nature)
    except Exception:
        return {"volume": "somme", "moyenne": "moyenne", "taux": "moyenne", "stock": "dernier"}[nature]


def _to_binary(serie: pd.Series) -> pd.Series | None:
    """Convertit une colonne Oui/Non (texte ou 0/1) en 0/1 ; None si ce n'est pas binaire."""
    s = serie.dropna()
    if s.empty:
        return None
    norm = s.astype(str).str.strip().str.lower()   # normaliser AVANT de compter : OUI/Oui/oui = une valeur
    if norm.nunique() == 2 and set(norm.unique()) <= (YES | NO):
        return serie.astype(str).str.strip().str.lower().map(
            lambda v: 1.0 if v in YES else 0.0 if v in NO else np.nan)
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().all() and set(num.unique()) == {0, 1}:
        return pd.to_numeric(serie, errors="coerce")
    return None


def _periodes(serie: pd.Series) -> pd.Series | None:
    """Lecture de période « AAAA-MM » ; None si la colonne n'en est pas une.

    Les dates ISO (AAAA-MM-JJ) ne sont jamais lues jour-en-premier : 2025-03-05 = 5 mars.
    """
    s = serie
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.to_period("M").astype(str)
    txt = s.dropna().astype(str).head(80)
    if txt.empty:
        return None
    if txt.str.match(r"^\d{4}-\d{2}$").mean() >= 0.9:                 # « 2025-04 »
        return s.astype(str).str.slice(0, 7)
    iso = txt.str.match(r"^\d{4}-\d{2}-\d{2}").mean() >= 0.9          # « 2025-03-05 »
    try:
        dt = pd.to_datetime(s, errors="coerce", format="mixed", dayfirst=not iso)
    except Exception:
        return None
    if dt.notna().mean() < 0.9:
        return None
    return dt.dt.to_period("M").astype(str)


def _dates(serie: pd.Series) -> pd.Series | None:
    """Dates au jour (datetime) ; None si la colonne ne contient que des mois « AAAA-MM »."""
    if pd.api.types.is_datetime64_any_dtype(serie):
        return serie
    txt = serie.dropna().astype(str).head(80)
    if txt.empty or txt.str.match(r"^\d{4}-\d{2}$").mean() >= 0.9:
        return None
    iso = txt.str.match(r"^\d{4}-\d{2}-\d{2}").mean() >= 0.9
    try:
        dt = pd.to_datetime(serie, errors="coerce", format="mixed", dayfirst=not iso)
    except Exception:
        return None
    return dt if dt.notna().mean() >= 0.9 else None


def _split(n: int, test_size: float = 0.3) -> tuple:
    idx = np.arange(n)
    rng = np.random.default_rng(0)
    rng.shuffle(idx)
    cut = max(1, int(n * (1 - test_size)))
    return idx[:cut], idx[cut:]


def _design(X: pd.DataFrame) -> np.ndarray:
    return np.column_stack([np.ones(len(X)), X.to_numpy(dtype=float)])


def _predicteurs_sains(df: pd.DataFrame, cible: str, candidats: list) -> tuple:
    """Écarte identifiants, colonnes constantes et prédicteurs quasi identiques à la cible."""
    avertis, gardes = [], []
    y = pd.to_numeric(df[cible], errors="coerce")
    for c in candidats:
        if c == cible or IDLIKE.search(c):
            continue
        v = pd.to_numeric(df[c], errors="coerce")
        if v.notna().sum() < MIN_LIGNES or v.nunique(dropna=True) < 2:
            continue
        try:
            r = float(v.corr(y))
            if math.isfinite(r) and abs(r) >= R2_SUSPECT:
                avertis.append(f"« {c} » reproduit presque parfaitement la cible (r={r:.3f}) : "
                               "écarté (fuite de la cible).")
                continue
        except Exception:
            pass
        gardes.append(c)
    return gardes, avertis


def _etat(r2: float | None, auc: float | None, positifs_test: int | None = None,
          vrais_positifs: int | None = None) -> str:
    """Trois états : insuffisant / indicatif / utilisable (jamais un simple booléen optimiste)."""
    if auc is not None:
        if positifs_test is not None and positifs_test < MIN_POSITIFS_TEST:
            return "insuffisant"
        if vrais_positifs is not None and vrais_positifs == 0:
            return "indicatif"          # aucun vrai positif : jamais « utilisable »
        if auc < AUC_UTILISABLE:
            return "insuffisant"
        return "utilisable" if auc >= 0.75 else "indicatif"
    if r2 is None or r2 < R2_UTILISABLE:
        return "insuffisant"
    return "utilisable" if r2 >= 0.30 else "indicatif"


def regression_lineaire(X: pd.DataFrame, y: pd.Series, noms: list | None = None,
                        test_size: float = 0.3) -> dict:
    noms = list(X.columns) if noms is None else noms
    n = len(y)
    if n < MIN_LIGNES:
        return {"erreur": f"trop peu de lignes ({n} < {MIN_LIGNES}) pour une régression fiable."}
    garder, ecartes = [], []
    for c in noms:
        v = X[c].to_numpy(dtype=float)
        if np.nanstd(v) == 0:
            ecartes.append(str(c))
            continue
        if garder:
            try:
                if abs(float(np.corrcoef(v, X[garder[0]].to_numpy(dtype=float))[0, 1])) > 0.99:
                    ecartes.append(str(c))
                    continue
            except Exception:
                pass
        garder.append(c)
    if not garder:
        return {"erreur": "aucun prédicteur exploitable (colonnes constantes ou colinéaires)."}
    X = X[garder]
    noms = garder
    idx_tr, idx_te = _split(n, test_size)
    A = _design(X)
    yv = y.to_numpy(dtype=float)
    coef, *_ = np.linalg.lstsq(A[idx_tr], yv[idx_tr], rcond=None)
    pred_tr, pred_te = A[idx_tr] @ coef, A[idx_te] @ coef
    rss = float(((yv[idx_tr] - pred_tr) ** 2).sum())
    tss = float(((yv[idx_tr] - yv[idx_tr].mean()) ** 2).sum())
    r2_tr = 1 - rss / tss if tss > 0 else None
    r2_te = None
    if len(idx_te) > 1:
        sse_te = float(((yv[idx_te] - pred_te) ** 2).sum())
        sst_te = float(((yv[idx_te] - yv[idx_te].mean()) ** 2).sum())
        r2_te = 1 - sse_te / sst_te if sst_te > 0 else None
    rmse_te = math.sqrt(float(((yv[idx_te] - pred_te) ** 2).sum()) / len(idx_te)) if len(idx_te) else None
    pvals, ic = [None] * len(coef), [None] * len(coef)
    dof = len(idx_tr) - A.shape[1]
    if dof > 0 and rss > 0:
        try:
            sigma2 = rss / dof
            cov = sigma2 * np.linalg.inv(A[idx_tr].T @ A[idx_tr])
            se = np.sqrt(np.diag(cov))
            tcrit = float(_st.t.ppf(0.975, dof))          # IC à t de Student, pas 1,96
            tstat = coef / np.where(se > 0, se, np.nan)
            pvals = [float(2 * _st.t.sf(abs(t), dof)) if math.isfinite(t) else None for t in tstat]
            ic = [[_r(c - tcrit * s, 3), _r(c + tcrit * s, 3)] for c, s in zip(coef, se)]
        except Exception:
            pass
    etat = _etat(r2_te, None)
    return {
        "modele": "régression linéaire (moindres carrés)",
        "n": int(n), "n_test": int(len(idx_te)), "cible": str(y.name) if y.name else "cible",
        "coefficients": [{"variable": "(constante)" if i == 0 else noms[i - 1], "coef": _r(c, 4),
                          "p": _r(p, 6) if p is not None else None, "ic95": ic[i]}
                         for i, (c, p) in enumerate(zip(coef, pvals))],
        "r2_train": _r(r2_tr, 3), "r2_test": _r(r2_te, 3),
        "rmse_train": _r(math.sqrt(rss / max(len(idx_tr), 1)), 2), "rmse_test": _r(rmse_te, 2),
        "etat": etat, "fiable": etat == "utilisable",
        "predicteurs_ecartes": ecartes,
        "limitations": ["R² test négatif = le modèle prédit moins bien que la moyenne ; ne pas l'utiliser."
                        if (r2_te is not None and r2_te < 0) else
                        "Une corrélation n'est pas une causalité ; un R² faible n'empêche pas une variable d'être utile."],
    }


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def regression_logistique(X: pd.DataFrame, y: pd.Series, noms: list | None = None,
                          test_size: float = 0.3) -> dict:
    noms = list(X.columns) if noms is None else noms
    yv = y.to_numpy(dtype=float)
    if len(np.unique(yv)) != 2:
        return {"erreur": "la cible doit avoir exactement deux modalités (Oui/Non, 0/1…)."}
    n = len(yv)
    if n < MIN_LIGNES:
        return {"erreur": f"trop peu de lignes ({n} < {MIN_LIGNES})."}
    if min(int(yv.sum()), int(n - yv.sum())) < 5:
        return {"erreur": "une des deux modalités a moins de 5 cas : modèle instable."}
    idx_tr, idx_te = _split(n, test_size)
    mu = X.mean()
    sd = X.std(ddof=0).replace(0, 1)
    Xs = (X - mu) / sd
    A = _design(Xs)

    def nll(beta):
        p = _sigmoid(A[idx_tr] @ beta)
        eps = 1e-9
        return -float((yv[idx_tr] * np.log(p + eps) + (1 - yv[idx_tr]) * np.log(1 - p + eps)).sum())

    def grad(beta):
        return A[idx_tr].T @ (_sigmoid(A[idx_tr] @ beta) - yv[idx_tr])

    res = optimize.minimize(nll, np.zeros(A.shape[1]), jac=grad, method="BFGS",
                            options={"maxiter": 800})
    if not res.success:
        return {"erreur": f"le modèle logistique n'a pas convergé ({res.message})."}
    beta_s = res.x
    if np.max(np.abs(beta_s[1:])) > 25:
        return {"erreur": "séparation parfaite : un prédicteur sépare totalement les deux classes ; "
                          "modèle non estimable sans régularisation."}
    beta = np.empty_like(beta_s)
    beta[0] = beta_s[0] - float(np.sum(beta_s[1:] * mu.to_numpy() / sd.to_numpy()))
    beta[1:] = beta_s[1:] / sd.to_numpy()
    p_te = _sigmoid(A[idx_te] @ beta_s)
    pred_te = (p_te >= 0.5).astype(int)
    y_te = yv[idx_te].astype(int)
    acc = float((pred_te == y_te).mean()) if len(y_te) else None
    auc = None
    try:
        pos, neg = p_te[y_te == 1], p_te[y_te == 0]
        if len(pos) and len(neg):
            auc = float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())
    except Exception:
        pass
    positifs = int(y_te.sum())
    tp = int(((pred_te == 1) & (y_te == 1)).sum())
    fp = int(((pred_te == 1) & (y_te == 0)).sum())
    fn = int(((pred_te == 0) & (y_te == 1)).sum())
    tn = int(((pred_te == 0) & (y_te == 0)).sum())
    etat = _etat(None, auc, positifs, tp)
    return {
        "modele": "régression logistique (maximum de vraisemblance, standardisée)",
        "n": int(n), "n_test": int(len(idx_te)), "positifs_test": positifs,
        "vrais_positifs_test": tp,
        "cible": str(y.name) if y.name else "cible",
        "coefficients": [{"variable": "(constante)" if i == 0 else noms[i - 1],
                          "coef": _r(b, 10),
                          "coef_standardise": _r(beta_s[i], 3),
                          "odds_ratio": _r(math.exp(b), 6) if abs(b) < 700 else None,
                          "odds_ratio_par_ecart_type": _r(math.exp(beta_s[i]), 3) if abs(beta_s[i]) < 700 else None}
                         for i, b in enumerate(beta)],
        "auc_test": _r(auc, 3), "exactitude_test": _r(acc, 3),
        "matrice_confusion": {"vrais_positifs": tp, "faux_positifs": fp, "faux_negatifs": fn, "vrais_negatifs": tn},
        "etat": etat, "fiable": etat == "utilisable",
        "limitations": ["AUC 0,5 = hasard ; sous 0,65 le modèle n'apporte rien. "
                        "Seuil de classement à 0,5 : à ajuster selon le coût des faux positifs/négatifs. "
                        + (f"Seulement {positifs} cas positifs en test : estimation fragile."
                           if positifs < MIN_POSITIFS_TEST else "")
                        + ("" if tp > 0 else f" Aucun vrai positif sur {positifs} : le modèle ne reconnaît aucun cas.")],
    }


# ---------------------------------------------------------------- prévision

def _tendance(y, t):
    ok = ~np.isnan(y)
    A = np.column_stack([np.ones(int(ok.sum())), t[ok]])
    coef, *_ = np.linalg.lstsq(A, y[ok], rcond=None)
    return coef


def _saison_residus(y, t, coef):
    """Résidus moyens par mois calendaire (0..11) — la saisonnalité se lit sur les résidus."""
    ok = ~np.isnan(y)
    res = y[ok] - (coef[0] + coef[1] * t[ok])
    s, cnt = np.zeros(12), np.zeros(12)
    for k, r in zip((t[ok] % 12).astype(int), res):
        s[k] += r
        cnt[k] += 1
    return np.where(cnt > 0, s / np.maximum(cnt, 1), 0.0)


def _predire(coef, saison, t):
    base = coef[0] + coef[1] * np.asarray(t, dtype=float)
    if saison is not None:
        base = base + np.array([saison[int(k) % 12] for k in t])
    return base


def prevision(serie: pd.Series, horizon: int = 6, nature: str = "volume",
              mois_ecarte: str | None = None, unite: str | None = None,
              facteur: float = 1.0) -> dict:
    """Prévision par tendance linéaire (+ saisonnalité si elle améliore le rétro-test).

    `serie` : index = période « AAAA-MM », valeurs = mesure agrégée. Axe calendaire réel
    (mois manquants = NaN, pas de compression). Le rétro-test utilise le même modèle que
    la prévision et le compare à deux références naïves (moyenne, dernier mois) : sans
    gain net, la prévision n'est pas dite « utilisable ».
    """
    s = serie.dropna().sort_index()
    if len(s) < 6:
        return {"erreur": f"série trop courte ({len(s)} points) pour une prévision."}
    try:
        idx = pd.PeriodIndex(s.index, freq="M")
        full = pd.period_range(idx.min(), idx.max(), freq="M")
        y = s.reindex(full.astype(str)).astype(float).to_numpy()
    except Exception:
        return {"erreur": "les périodes ne sont pas des mois « AAAA-MM »."}
    n = len(y)
    ok = ~np.isnan(y)
    if n < 6 or ok.sum() < 6:
        return {"erreur": "historique mensuel trop court après alignement calendaire."}
    t = np.arange(n, dtype=float)
    coef = _tendance(y, t)
    saison = _saison_residus(y, t, coef) if ok.sum() >= 24 else None

    # ---- rétro-test : MÊME modèle que la prévision, comparé à deux références naïves
    validation, etat = None, "indicatif"
    h_bt = min(6, n // 4)
    if h_bt >= 2 and ok.sum() >= 8:
        masque = ok.copy()
        masque[np.flatnonzero(ok)[-h_bt:]] = False       # les h derniers observés servent de test
        y_tr, t_tr = y[masque], t[masque]
        y_te, t_te = y[ok][-h_bt:], t[ok][-h_bt:]
        c_tr = _tendance(y_tr, t_tr)
        s_tr = _saison_residus(y_tr, t_tr, c_tr) if int(masque.sum()) >= 24 else None
        err_sans = float(np.mean(np.abs(_predire(c_tr, None, t_te) - y_te)))
        if s_tr is not None:
            err_avec = float(np.mean(np.abs(_predire(c_tr, s_tr, t_te) - y_te)))
            if err_avec < 0.95 * err_sans:                # la saisonnalité ne reste que si elle aide
                err_modele = err_avec
            else:
                saison, err_modele = None, err_sans
        else:
            saison, err_modele = None, err_sans
        ref_moy = float(np.mean(np.abs(np.full(h_bt, float(np.mean(y_tr))) - y_te)))
        ref_der = float(np.mean(np.abs(np.full(h_bt, float(y_tr[-1])) - y_te)))
        ref = min(ref_moy, ref_der)
        validation = {"periodes_testees": int(h_bt),
                      "erreur_modele": _r(err_modele * facteur, 2),
                      "erreur_reference": _r(ref * facteur, 2),
                      "reference": "moyenne" if ref_moy <= ref_der else "dernier mois"}
        if ref > 0:
            etat = ("utilisable" if err_modele < 0.4 * ref          # battre nettement la référence
                    else "indicatif" if err_modele < ref else "insuffisant")

    resid = y[ok] - _predire(coef, saison, t[ok])
    resid_std = float(np.std(resid, ddof=1)) if len(resid) > 2 else float(np.std(resid))
    horizon = max(1, min(int(horizon), n))              # jamais plus loin que l'historique
    futur = np.arange(n, n + horizon, dtype=float)
    base = _predire(coef, saison, futur) * facteur
    marge = 1.28 * resid_std * np.sqrt(1 + np.arange(1, horizon + 1) / max(n, 1)) * facteur
    labels = [str(full[-1] + k) for k in range(1, horizon + 1)]
    return {
        "modele": "tendance linéaire" + (" + saisonnalité mensuelle" if saison is not None else ""),
        "n": int(n), "horizon": int(horizon), "agregation": _agregation(nature), "unite": unite,
        "tendance_pente": _r(coef[1] * facteur, 3), "residu_ecart_type": _r(resid_std * facteur, 2),
        "saisonnalite": saison is not None, "mois_ecarte": mois_ecarte, "validation": validation,
        "etat": etat, "fiable": etat == "utilisable",
        "historique": {"periodes": [str(i) for i in full],
                       "valeurs": [_r(v * facteur, 2) for v in y]},
        "previsions": [{"periode": lab, "valeur": _r(v, 2), "bas": _r(v - m, 2), "haut": _r(v + m, 2)}
                       for lab, v, m in zip(labels, base, marge)],
        "limitations": ["Prévision statistique : elle prolonge la tendance observée, elle n'anticipe "
                        "aucun changement (campagne, épidémie, nouvelle politique). Intervalle à 80 %. "
                        + ("La saisonnalité est conservée parce qu'elle améliore le rétro-test. "
                           if saison is not None else "")
                        + (f"Dernier mois écarté (incomplet) : {mois_ecarte}. " if mois_ecarte else "")
                        + (f"Horizon borné à {horizon} mois (longueur de l'historique)." if horizon < 36 else "")],
    }


def colonnes(df: pd.DataFrame) -> dict:
    """Colonnes proposables à l'interface de prédiction : numériques, binaires, périodes."""
    out = {"numeriques": [], "binaires": [], "periodes": []}
    if df is None or df.empty:
        return out
    for c in df.columns:
        s = df[c]
        try:
            if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
                out["numeriques"].append(str(c))
                continue
            if _to_binary(s) is not None:
                out["binaires"].append(str(c))
            if _periodes(s) is not None:
                out["periodes"].append(str(c))
        except Exception:
            continue
    return out


def analyse(df: pd.DataFrame, cible: str | None = None, predicteurs: list | None = None,
            periode: str | None = None, horizon: int = 6, valeur_comptee: str | None = None) -> dict:
    """Choisit le modèle adapté et renvoie un résultat JSON-compatible."""
    out = {"cible": cible, "predicteurs": predicteurs or [], "resultat": None,
           "erreurs": [], "avertissements": []}
    if df is None or df.empty:
        out["erreurs"].append("aucune donnée.")
        return out
    df = df.replace([np.inf, -np.inf], np.nan)

    # ---------- prévision : agrégation sur TOUTES les lignes, avant tout échantillonnage
    if periode and cible and periode in df.columns and cible in df.columns:
        try:
            per = _periodes(df[periode])
            if per is None:
                out["erreurs"].append(f"« {periode} » n'est pas une colonne de période reconnue.")
                return out
            c = df[cible]
            binaire = _to_binary(c)
            num = pd.to_numeric(c, errors="coerce")
            nature, avert = _nature_fiche(df, cible)
            if avert:
                out["avertissements"].append(avert)
            unite, facteur = None, 1.0
            if binaire is not None:
                if valeur_comptee:
                    vc = str(valeur_comptee).strip().lower()
                    if vc in YES:
                        mesure = binaire
                    elif vc in NO:
                        mesure = 1.0 - binaire
                    else:
                        out["erreurs"].append(f"« {valeur_comptee} » n'est ni Oui ni Non.")
                        return out
                    nature, unite = "volume", f"nombre de {valeur_comptee}"
                else:
                    mesure, nature = binaire, "taux"      # proportion mensuelle de Oui
                    unite, facteur = "part de Oui (%)", 100.0
            else:
                if num.notna().sum() == 0:
                    out["erreurs"].append(
                        f"« {cible} » n'est pas numérique : indiquez valeur_comptee (la modalité à compter) "
                        "ou choisissez une mesure numérique.")
                    return out
                mesure = num
                if nature is None:                       # la fiche ne tranche pas : on le dit, on ne devine pas
                    out["erreurs"].append(
                        f"nature de « {cible} » incertaine : la fiche ne la classe ni volume, ni moyenne, "
                        "ni taux, ni stock ; prévision refusée plutôt que de choisir une agrégation au hasard.")
                    return out
            dates = _dates(df[periode])
            if nature == "stock" and dates is not None:
                # un stock se lit à la dernière date du mois, tous les groupes additionnés
                frame = pd.DataFrame({"p": per, "d": dates, "v": mesure}).dropna()
                maxd = frame.groupby("p")["d"].transform("max")
                agg = frame[frame["d"] == maxd].groupby("p")["v"].sum()
            else:
                frame = pd.DataFrame({"p": per, "v": mesure}).dropna()
                g = frame.groupby("p")["v"]
                agg = {"volume": g.sum(), "moyenne": g.mean(), "taux": g.mean(), "stock": g.sum()}[nature]
            # mois incomplet : uniquement avec des dates au jour ; jamais sur une chute de valeur
            mois_ecarte = None
            if dates is not None and len(agg):
                dernier = agg.index[-1]
                fin = pd.Period(dernier, freq="M").end_time.normalize()
                if pd.Timestamp(dates.max()).normalize() < fin:
                    mois_ecarte = str(dernier)
                    agg = agg.iloc[:-1]
            out["resultat"] = dict(prevision(agg, horizon, nature, mois_ecarte, unite, facteur),
                                   type="prevision", valeur_comptee=valeur_comptee)
        except Exception as e:
            out["erreurs"].append(f"prévision impossible : {e}")
        return out

    if not cible or cible not in df.columns:
        out["erreurs"].append("cible absente : indiquer la colonne à expliquer.")
        return out
    y_raw = df[cible]
    bin_y = _to_binary(y_raw)
    y = bin_y if bin_y is not None else pd.to_numeric(y_raw, errors="coerce")
    preds = [p for p in (predicteurs or []) if p in df.columns and p != cible]
    if not preds:
        preds = [c for c in df.columns if c != cible and pd.api.types.is_numeric_dtype(df[c])]
    preds, avertis = _predicteurs_sains(df, cible, preds[:MAX_PREDICTEURS + 4])
    out["avertissements"] = avertis
    if not preds:
        out["erreurs"].append("aucun prédicteur exploitable après retrait des identifiants et colonnes constantes.")
        return out
    sub = pd.DataFrame({"y": y})
    for p in preds[:MAX_PREDICTEURS]:
        sub[p] = pd.to_numeric(df[p], errors="coerce")
    sub = sub.dropna()
    if len(sub) > MAX_LIGNES:  # l'échantillon ne concerne QUE les régressions, et il est annoncé
        sub = sub.sample(MAX_LIGNES, random_state=0)
        out["avertissements"].append(
            f"Échantillon aléatoire de {MAX_LIGNES:,} lignes pour la régression.".replace(",", " "))
    if len(sub) < MIN_LIGNES:
        out["erreurs"].append(f"trop peu de lignes complètes ({len(sub)} < {MIN_LIGNES}).")
        return out
    X, yv = sub[list(sub.columns[1:])], sub["y"]
    try:
        if bin_y is not None:
            out["resultat"] = dict(regression_logistique(X, yv, list(X.columns)), type="logistique")
        else:
            out["resultat"] = dict(regression_lineaire(X, yv, list(X.columns)), type="lineaire")
    except Exception as e:
        out["erreurs"].append(f"modèle impossible : {e}")
    return out
