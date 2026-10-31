# -*- coding: utf-8 -*-
"""Tests de KLDataViz_console/predictive.py — données fictives, valeurs connues.

Chaque test vise un point de la relecture de la PR 136 (numéro rappelé en docstring).
Aucun accès réseau, aucune donnée réelle.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import predictive as P


# ---------- point 1 : dates ISO ----------
def test_dates_iso_ne_sont_pas_inversees():
    """2025-03-05 est le 5 mars, pas le 3 mai ; 2025-12-01 est décembre, pas janvier."""
    p = P._periodes(pd.Series(["2025-03-05", "2025-12-01", "2025-01-10"]))
    assert p.tolist() == ["2025-03", "2025-12", "2025-01"]


# ---------- point 2 : cible Oui/Non ----------
def test_cible_oui_non_reconnue():
    b = P._to_binary(pd.Series(["OUI", "NON", "OUI", "non"]))
    assert b is not None
    assert b.tolist() == [1.0, 0.0, 1.0, 0.0]
    assert P._to_binary(pd.Series(["A", "B", "C"])) is None


# ---------- point 3 : cible non numérique ----------
def test_cible_texte_sans_valeur_comptee_est_refusee():
    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=10), "ville": ["A", "B"] * 5})
    r = P.analyse(df, cible="ville", periode="date")
    assert r["resultat"] is None
    assert any("valeur_comptee" in e for e in r["erreurs"])


def test_valeur_comptee_compte_la_bonne_modalite():
    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-01", periods=300, freq="D")
    df = pd.DataFrame({"date": dates, "conforme": rng.choice(["OUI", "NON"], 300, p=[0.9, 0.1])})
    # proportion de NON : moyenne mensuelle autour de 10 %
    r = P.analyse(df, cible="conforme", periode="date", valeur_comptee="NON", horizon=3)
    assert r["resultat"]["agregation"] == "somme"
    assert 1 <= np.mean(r["resultat"]["historique"]["valeurs"]) <= 10


# ---------- point 4 : agrégation selon la nature ----------
def test_mesure_de_taux_agregee_en_moyenne():
    dates = pd.date_range("2024-01-01", periods=240, freq="D")
    df = pd.DataFrame({"date": dates, "taux_humidite_pct": np.full(240, 7.5)})
    r = P.analyse(df, cible="taux_humidite_pct", periode="date", horizon=3)
    res = r["resultat"]
    assert res["agregation"] == "moyenne"
    assert all(abs(v - 7.5) < 1e-9 for v in res["historique"]["valeurs"])


# ---------- point 5 : prévision sur toutes les lignes ----------
def test_prevision_agrege_toutes_les_lignes_avant_echantillon():
    """60 000 lignes (au-delà de MAX_LIGNES=50 000), 8 mois de 7 500 : chaque mois vaut 7 500."""
    dates = [f"2025-{m:02d}-15" for m in range(1, 9) for _ in range(7500)]
    df = pd.DataFrame({"date": dates, "ventes": np.ones(60000)})
    r = P.analyse(df, cible="ventes", periode="date", horizon=3)
    vals = r["resultat"]["historique"]["valeurs"]
    assert len(vals) == 8
    assert all(abs(v - 7500) < 1 for v in vals)


# ---------- point 7 : fiabilité ----------
def test_classe_rare_nest_pas_declaree_fiable():
    rng = np.random.default_rng(0)
    n = 500
    df = pd.DataFrame({"x": rng.normal(size=n), "y": (rng.random(n) < 0.04).astype(int)})
    r = P.analyse(df, cible="y")
    res = r["resultat"]
    assert res["etat"] in ("insuffisant", "indicatif")
    assert res["fiable"] is False


def test_modele_utile_est_declare_utilisable():
    rng = np.random.default_rng(1)
    n = 600
    x = rng.normal(size=n)
    y = (x + rng.normal(0, 1.2, n) > 0).astype(int)
    r = P.analyse(pd.DataFrame({"x": x, "y": y}), cible="y")
    assert r["resultat"]["etat"] == "utilisable"


# ---------- point 8 : fuite de la cible / identifiants ----------
def test_predicteur_qui_reproduit_la_cible_est_ecarte():
    rng = np.random.default_rng(2)
    base = rng.uniform(100, 1000, 200)
    df = pd.DataFrame({"ventes": base, "ventes_ttc": base * 1.18, "id_lot": np.arange(200),
                       "autre": rng.normal(size=200)})
    r = P.analyse(df, cible="ventes")
    assert any("ventes_ttc" in a for a in r["avertissements"])
    variables = [c["variable"] for c in r["resultat"]["coefficients"]]
    assert "id_lot" not in variables and "ventes_ttc" not in variables


# ---------- point 9 : horizon borné + validation ----------
def test_horizon_borne_par_lhistorique_et_retro_test():
    dates = pd.date_range("2024-01-01", periods=240, freq="D")
    df = pd.DataFrame({"date": dates, "ventes": (np.arange(240) % 40 + 20).astype(float)})
    r = P.analyse(df, cible="ventes", periode="date", horizon=36)
    res = r["resultat"]
    assert res["horizon"] <= res["n"]
    assert res["validation"] is not None
    assert res["etat"] in ("insuffisant", "indicatif", "utilisable")


# ---------- point 10 : mois incomplet ----------
def test_dernier_mois_incomplet_est_ecarte():
    idx = [f"2024-{m:02d}" for m in range(1, 10)]
    vals = [100] * 8 + [10]          # septembre s'effondre : mois en cours, incomplet
    r = P.prevision(pd.Series(vals, index=idx), horizon=2)
    assert r["mois_ecarte"] == "2024-09"
    assert "2024-09" not in r["historique"]["periodes"]


# ---------- point 11 : axe calendaire ----------
def test_mois_manquant_reste_dans_laxe():
    serie = pd.Series([10, 12, 13, 12, 14, 13],
                      index=["2024-01", "2024-02", "2024-04", "2024-05", "2024-06", "2024-07"])
    r = P.prevision(serie, horizon=2)
    assert "2024-03" in r["historique"]["periodes"]
    i = r["historique"]["periodes"].index("2024-03")
    assert r["historique"]["valeurs"][i] is None


# ---------- point 13 : logistique standardisée ----------
def test_logistique_montant_grand_garde_un_coefficient_lisible():
    rng = np.random.default_rng(3)
    n = 400
    montant = rng.uniform(4_000_000, 6_000_000, n)
    y = (montant + rng.normal(0, 900_000, n) > 5_000_000).astype(int)
    r = P.analyse(pd.DataFrame({"montant": montant, "y": y}), cible="y")
    res = r["resultat"]
    coef = [c for c in res["coefficients"] if c["variable"] == "montant"][0]["coef"]
    assert coef is not None and abs(coef) > 0


# ---------- point 14 : linéaire ----------
def test_lineaire_ic95_et_predicteur_constant_ecarte():
    rng = np.random.default_rng(4)
    n = 120
    x = rng.normal(size=n)
    df = pd.DataFrame({"x": x, "const": np.ones(n), "y": 2 * x + rng.normal(0, 0.5, n)})
    r = P.analyse(df, cible="y", predicteurs=["x", "const"])
    res = r["resultat"]
    assert "const" not in [c["variable"] for c in res["coefficients"]]   # constante : aucun coefficient arbitraire
    assert all("ic95" in c for c in res["coefficients"])
    c = [c for c in res["coefficients"] if c["variable"] == "x"][0]
    assert c["ic95"] and c["ic95"][0] < c["coef"] < c["ic95"][1]      # IC centré sur l'estimation
    assert abs(c["coef"] - 2) < 0.3                                   # proche de la vraie pente


# ---------- prévision d'un Oui/Non : proportion, pas somme de 1/0 ----------
def test_prevision_oui_non_sans_valeur_comptee_donne_une_proportion():
    rng = np.random.default_rng(5)
    dates = pd.date_range("2024-01-01", periods=300, freq="D")
    df = pd.DataFrame({"date": dates, "conforme": rng.choice(["OUI", "NON"], 300, p=[0.8, 0.2])})
    r = P.analyse(df, cible="conforme", periode="date", horizon=3)
    res = r["resultat"]
    assert res["agregation"] == "moyenne"
    assert all(0 <= v <= 1 for v in res["historique"]["valeurs"])
