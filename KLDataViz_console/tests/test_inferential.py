# -*- coding: utf-8 -*-
"""Tests de KLDataViz_console/inferential.py — données fictives, valeurs connues.

Chaque test vise un point de la relecture de la PR 136.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inferential as I


# ---------- point 6 : correction pour tests multiples ----------
def test_bh_p_ajustee_toujours_superieure_ou_egale():
    pvals = [0.001, 0.02, 0.04, 0.5]
    ajustees = I._bh(pvals)
    assert all(a >= p for a, p in zip(ajustees, pvals))
    assert ajustees == sorted(ajustees)


def test_bh_garde_les_none():
    assert I._bh([None, 0.01]) == [None, 0.01]


def test_bruit_pur_ne_produit_pas_de_significatif_apres_correction():
    """Sur 8 colonnes indépendantes (28 corrélations), la correction doit retirer les faux positifs."""
    rng = np.random.default_rng(0)
    df = pd.DataFrame({f"x{i}": rng.normal(size=400) for i in range(8)})
    r = I.analyse(df)
    assert all(x["p_ajuste"] is not None for x in r["correlations"])
    assert sum(1 for x in r["correlations"] if x["significatif"]) == 0
    assert "Benjamini" in r["resume"]


def test_significatif_suit_la_p_ajustee():
    rng = np.random.default_rng(1)
    # une vraie liaison forte + du bruit : la liaison doit rester significative
    df = pd.DataFrame({"x": rng.normal(size=300)})
    df["y"] = 3 * df["x"] + rng.normal(0, 0.4, 300)
    r = I.analyse(df)
    forte = [x for x in r["correlations"] if {x["a"], x["b"]} == {"x", "y"}]
    assert forte and forte[0]["significatif"] is True
    assert forte[0]["p_ajuste"] < 0.05


# ---------- point 12 : chi² ----------
def test_tableau_2x2_utilise_fisher():
    rng = np.random.default_rng(2)
    df = pd.DataFrame({"a": rng.choice(["A", "B"], 200), "b": rng.choice(["OUI", "NON"], 200)})
    r = I.analyse(df)
    assert any("Fisher" in x["test"] for x in r["associations"])


def test_effectifs_attendus_faibles_signales():
    # (9,0) / (0,1) : effectifs attendus 0,9 et 0,1 → sous le seuil de validité du chi²
    df = pd.DataFrame({"a": ["A"] * 9 + ["B"], "b": ["X"] * 5 + ["Y"] * 4 + ["X"]})
    r = I.analyse(df)
    assert r["associations"] and r["associations"][0]["attendus_faibles"] is True


def test_tableau_equilibre_sans_alerte_dattendus():
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"a": rng.choice(["A", "B"], 300), "b": rng.choice(["X", "Y", "Z"], 300)})
    r = I.analyse(df)
    assert r["associations"] and r["associations"][0]["attendus_faibles"] is False
    assert r["associations"][0]["test"] == "chi² d'indépendance"


# ---------- structure générale ----------
def test_resultat_json_compatible_et_borne():
    rng = np.random.default_rng(4)
    df = pd.DataFrame({"n": rng.normal(size=100), "c": rng.choice(["A", "B", "C"], 100)})
    r = I.analyse(df)
    for cle in ("correlations", "associations", "comparaisons"):
        assert isinstance(r[cle], list)
    # aucune valeur NaN/inf ne doit sortir
    import math
    for x in r["comparaisons"]:
        assert x["p"] is None or math.isfinite(x["p"])
