# -*- coding: utf-8 -*-
"""Tests de KLDataViz_console/predictive.py — données fictives, valeurs connues.

Reprend exactement les colonnes des relectures (humidite, note_qualite, total_ventes,
population, TX_NEW) et chaque point corrigé de la PR 136.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import predictive as P


def _mois_3_districts(**vals):
    """12 mois, 3 districts par mois (A, B, C) — le cas des relectures."""
    mois = pd.date_range("2024-01-01", periods=12, freq="MS")
    base = {"humidite": 7.5, "note_qualite": 14, "total_ventes": 100,
            "population": 1000, "TX_NEW": 10}
    base.update(vals)
    return pd.DataFrame({"date": [m for m in mois for _ in range(3)],
                         "district": ["A", "B", "C"] * 12, **base})


# ---------- point 1 : dates ISO ----------
def test_dates_iso_ne_sont_pas_inversees():
    p = P._periodes(pd.Series(["2025-03-05", "2025-12-01", "2025-01-10"]))
    assert p.tolist() == ["2025-03", "2025-12", "2025-01"]


# ---------- point 2 : cible Oui/Non ----------
def test_cible_oui_non_reconnue():
    b = P._to_binary(pd.Series(["OUI", "NON", "OUI", "non"]))
    assert b is not None and b.tolist() == [1.0, 0.0, 1.0, 0.0]
    assert P._to_binary(pd.Series(["A", "B", "C"])) is None


# ---------- point 3 : cible non numérique ----------
def test_cible_texte_sans_valeur_comptee_est_refusee():
    df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=10), "ville": ["A", "B"] * 5})
    r = P.analyse(df, cible="ville", periode="date")
    assert r["resultat"] is None and any("valeur_comptee" in e for e in r["erreurs"])


def test_valeur_comptee_compte_la_bonne_modalite():
    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-01", periods=300, freq="D")
    df = pd.DataFrame({"date": dates, "conforme": rng.choice(["OUI", "NON"], 300, p=[0.9, 0.1])})
    r = P.analyse(df, cible="conforme", periode="date", valeur_comptee="NON", horizon=3)
    res = r["resultat"]
    assert res["unite"] == "nombre de NON"
    assert 1 <= np.mean([v for v in res["historique"]["valeurs"] if v is not None]) <= 10


# ---------- point A : nature lue par la fiche, pas par mots-clés ----------
def test_nature_humidite_est_une_moyenne():
    r = P.analyse(_mois_3_districts(), cible="humidite", periode="date")
    assert r["resultat"]["agregation"] == "moyenne"
    assert r["resultat"]["historique"]["valeurs"][0] == 7.5


def test_nature_note_qualite_est_une_moyenne():
    r = P.analyse(_mois_3_districts(), cible="note_qualite", periode="date")
    assert r["resultat"]["agregation"] == "moyenne"
    assert r["resultat"]["historique"]["valeurs"][0] == 14.0


def test_nature_total_ventes_est_une_somme():
    r = P.analyse(_mois_3_districts(), cible="total_ventes", periode="date")
    assert r["resultat"]["agregation"] == "somme"
    assert r["resultat"]["historique"]["valeurs"][0] == 300.0


def test_nature_population_est_un_stock_somme_des_groupes():
    r = P.analyse(_mois_3_districts(), cible="population", periode="date")
    assert r["resultat"]["agregation"] == "dernier"
    assert r["resultat"]["historique"]["valeurs"][0] == 3000.0     # 3 districts à 1 000


def test_nature_tx_new_est_un_volume():
    r = P.analyse(_mois_3_districts(), cible="TX_NEW", periode="date")
    assert r["resultat"]["agregation"] == "somme"
    assert r["resultat"]["historique"]["valeurs"][0] == 30.0


# ---------- point 5 : prévision sur toutes les lignes ----------
def test_prevision_agrege_toutes_les_lignes_avant_echantillon():
    """60 000 lignes (au-delà de MAX_LIGNES=50 000), 8 mois de 7 500, moyenne 5,5 par ligne."""
    fins = pd.date_range("2025-01-31", periods=8, freq="ME")     # fins de mois : aucun mois incomplet
    dates = [d for d in fins for _ in range(7500)]
    df = pd.DataFrame({"date": dates, "ventes": (np.arange(60000) % 10 + 1).astype(float)})
    r = P.analyse(df, cible="ventes", periode="date", horizon=3)
    vals = r["resultat"]["historique"]["valeurs"]
    assert len(vals) == 8 and all(abs(v - 41250) < 1 for v in vals)   # 7 500 × 5,5


# ---------- point 7 / G : fiabilité ----------
def test_classe_rare_nest_pas_declaree_fiable():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"x": rng.normal(size=500), "y": (rng.random(500) < 0.04).astype(int)})
    res = P.analyse(df, cible="y")["resultat"]
    assert res["etat"] in ("insuffisant", "indicatif") and res["fiable"] is False


def test_aucun_vrai_positif_bloque_utilisable():
    assert P._etat(None, 0.95, 20, 0) == "indicatif"


def test_modele_utile_est_declare_utilisable():
    rng = np.random.default_rng(1)
    x = rng.normal(size=600)
    y = (x + rng.normal(0, 1.2, 600) > 0).astype(int)
    assert P.analyse(pd.DataFrame({"x": x, "y": y}), cible="y")["resultat"]["etat"] == "utilisable"


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
    dates = pd.date_range("2024-01-01", periods=365, freq="D")
    df = pd.DataFrame({"date": dates, "ventes": (np.arange(365) % 40 + 20).astype(float)})
    res = P.analyse(df, cible="ventes", periode="date", horizon=36)["resultat"]
    assert res["horizon"] <= res["n"]
    assert res["validation"] is not None and "erreur_modele" in res["validation"]


# ---------- point C : mois incomplet ----------
def test_vraie_chute_sur_mois_complet_conservee():
    d = pd.date_range("2024-01-01", "2024-06-30", freq="D")
    v = np.where(d.month < 6, 100, 50)          # juin complet, en baisse de 50 %
    res = P.analyse(pd.DataFrame({"date": d, "ventes": v}), cible="ventes", periode="date")["resultat"]
    assert res["mois_ecarte"] is None
    assert res["historique"]["valeurs"][-1] == 1500.0    # 30 jours × 50, juin conservé


def test_fichier_arrete_au_20_ecarte_le_mois():
    d = pd.date_range("2023-01-01", "2024-06-20", freq="D")
    v = np.where((d.year == 2024) & (d.month == 6), 50, 100)
    res = P.analyse(pd.DataFrame({"date": d, "ventes": v}), cible="ventes", periode="date")["resultat"]
    assert res["mois_ecarte"] == "2024-06"


def test_mois_sans_jour_ne_decarte_rien():
    idx = [f"2024-{m:02d}" for m in range(1, 9)]
    r = P.prevision(pd.Series([100] * 7 + [50], index=idx))
    assert r["mois_ecarte"] is None


def test_fichier_date_du_1er_garde_le_dernier_mois():
    dates = pd.date_range("2024-01-01", periods=8, freq="MS")   # une date par mois, le 1er
    res = P.analyse(pd.DataFrame({"date": dates, "ventes": np.arange(8) * 10 + 100}),
                    cible="ventes", periode="date")["resultat"]
    assert res["mois_ecarte"] is None and res["n"] == 8


def test_fichier_date_du_15_garde_le_dernier_mois():
    dates = pd.date_range("2024-01-15", periods=8, freq="MS")   # une date par mois, le 15
    res = P.analyse(pd.DataFrame({"date": dates, "ventes": np.arange(8) * 10 + 100}),
                    cible="ventes", periode="date")["resultat"]
    assert res["mois_ecarte"] is None and res["n"] == 8


def test_fichier_arrete_le_28_garde_juin():
    d = pd.date_range("2024-01-01", "2024-06-28", freq="D")     # 28 > 25 : mois jugé complet
    res = P.analyse(pd.DataFrame({"date": d, "ventes": np.full(len(d), 100.0)}),
                    cible="ventes", periode="date")["resultat"]
    assert res["mois_ecarte"] is None and res["n"] == 6


def test_stock_derniere_date_par_district():
    mois = pd.date_range("2024-01-01", periods=8, freq="MS")
    lignes = []
    for m in mois:
        lignes += [{"date": m.replace(day=15), "district": "B", "population": 1000},
                   {"date": m.replace(day=28), "district": "A", "population": 1000}]
    res = P.analyse(pd.DataFrame(lignes), cible="population", periode="date")["resultat"]
    assert res["historique"]["valeurs"][0] == 2000.0     # A au 28 + B au 15, pas seulement A


# ---------- point 11 : axe calendaire ----------
def test_mois_manquant_reste_dans_laxe():
    serie = pd.Series([10, 12, 13, 12, 14, 13],
                      index=["2024-01", "2024-02", "2024-04", "2024-05", "2024-06", "2024-07"])
    r = P.prevision(serie, horizon=2)
    assert "2024-03" in r["historique"]["periodes"]
    assert r["historique"]["valeurs"][r["historique"]["periodes"].index("2024-03")] is None


# ---------- point D : la prévision doit battre une référence ----------
def test_bruit_pur_nest_jamais_utilisable():
    rng = np.random.default_rng(0)
    dates = pd.date_range("2021-01-01", periods=365 * 4, freq="D")
    for _ in range(10):
        df = pd.DataFrame({"date": dates, "v": rng.normal(100, 10, len(dates))})
        res = P.analyse(df, cible="v", periode="date")["resultat"]
        assert res["etat"] != "utilisable"


def test_tendance_nette_est_utilisable():
    rng = np.random.default_rng(3)
    dates = pd.date_range("2021-01-01", periods=365 * 4, freq="D")
    t = np.arange(len(dates)) / 365.0
    df = pd.DataFrame({"date": dates, "v": 100 + 8 * t + rng.normal(0, 3, len(dates))})
    assert P.analyse(df, cible="v", periode="date")["resultat"]["etat"] == "utilisable"


# ---------- point 13 : logistique standardisée ----------
def test_logistique_montant_grand_garde_un_coefficient_lisible():
    rng = np.random.default_rng(4)
    montant = rng.uniform(4_000_000, 6_000_000, 400)
    y = (montant + rng.normal(0, 900_000, 400) > 5_000_000).astype(int)
    res = P.analyse(pd.DataFrame({"montant": montant, "y": y}), cible="y")["resultat"]
    coef = [c for c in res["coefficients"] if c["variable"] == "montant"][0]
    assert coef["coef"] and abs(coef["coef"]) > 0


# ---------- point 14 : linéaire ----------
def test_lineaire_ic95_et_predicteur_constant_ecarte():
    rng = np.random.default_rng(5)
    x = rng.normal(size=120)
    df = pd.DataFrame({"x": x, "const": np.ones(120), "y": 2 * x + rng.normal(0, 0.5, 120)})
    res = P.analyse(df, cible="y", predicteurs=["x", "const"])["resultat"]
    assert "const" not in [c["variable"] for c in res["coefficients"]]
    assert all("ic95" in c for c in res["coefficients"])
    c = [c for c in res["coefficients"] if c["variable"] == "x"][0]
    assert c["ic95"][0] < c["coef"] < c["ic95"][1] and abs(c["coef"] - 2) < 0.3


# ---------- point F : unité d'une proportion ----------
def test_prevision_oui_non_sans_valeur_comptee_donne_une_proportion_en_pourcent():
    rng = np.random.default_rng(6)
    dates = pd.date_range("2024-01-01", periods=300, freq="D")
    df = pd.DataFrame({"date": dates, "conforme": rng.choice(["OUI", "NON"], 300, p=[0.8, 0.2])})
    res = P.analyse(df, cible="conforme", periode="date")["resultat"]
    assert res["agregation"] == "moyenne" and res["unite"] == "part de Oui (%)"
    assert all(0 <= v <= 100 for v in res["historique"]["valeurs"] if v is not None)
