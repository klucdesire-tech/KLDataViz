# -*- coding: utf-8 -*-
"""Tests de KLDataViz_console/inferential.py — données fictives, valeurs connues.

Chaque test vise un point de la relecture de la PR 136.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

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


def test_annee_date_et_identifiant_ne_sont_pas_testes():
    rng = np.random.default_rng(3)
    n = 120
    df = pd.DataFrame({"annee": rng.integers(2015, 2025, n), "code_site": rng.integers(1000, 9999, n),
                       "date": pd.date_range("2024-01-01", periods=n).strftime("%Y-%m-%d"),
                       "ventes": rng.normal(100, 10, n), "cout": rng.normal(50, 5, n),
                       "zone": rng.choice(["Nord", "Sud", "Est"], n), "canal": rng.choice(["Web", "Boutique"], n)})
    r = I.analyse(df)
    testees = {x[k] for f in ("correlations", "associations", "comparaisons") for x in r[f] for k in ("a", "b", "num", "cat") if k in x}
    assert testees == {"ventes", "cout", "zone", "canal"}


def test_lien_de_structure_mis_a_part():
    """Chaque hôtel dans une seule ville, une marge calculée sur le chiffre d'affaires : ce n'est pas une découverte."""
    rng = np.random.default_rng(4)
    n = 200
    hotel = rng.choice(["H1", "H2", "H3", "H4"], n)
    ca = rng.normal(1000, 200, n)
    df = pd.DataFrame({"hotel": hotel, "ville": pd.Series(hotel).map({"H1": "Abidjan", "H2": "Abidjan", "H3": "Bouaké", "H4": "Bouaké"}),
                       "client": rng.choice(["Pro", "Particulier"], n), "ca": ca, "marge": ca * 0.3, "nuits": rng.normal(3, 1, n)})
    r = I.analyse(df)
    lien = next(x for x in r["associations"] if {x["a"], x["b"]} == {"hotel", "ville"})
    marge = next(x for x in r["correlations"] if {x["a"], x["b"]} == {"ca", "marge"})
    assert lien["structurel"] and marge["structurel"] and not lien["significatif"] and not marge["significatif"]
    assert r["n_structure"] >= 2 and r["n_significatifs"] == 0
    assert r["n_tests"] + r["n_structure"] == len(r["correlations"]) + len(r["associations"]) + len(r["comparaisons"])
    assert r["associations"][-1]["structurel"] and r["correlations"][-1]["structurel"]


def test_lien_de_structure_hors_de_la_correction():
    """Les liens évidents ne comptent pas dans la correction : leurs p minuscules feraient passer les autres."""
    rng = np.random.default_rng(5)
    n = 300
    ca = rng.normal(100, 20, n)
    df = pd.DataFrame({"ca": ca, "marge": ca * 0.3, "m1": rng.normal(size=n), "m2": rng.normal(size=n) + ca * 0.01, "m3": rng.normal(size=n)})
    r = I.analyse(df)
    assert any(x["structurel"] for x in r["correlations"])
    libres = [x for x in r["correlations"] if not x["structurel"]]
    assert [x["p_ajuste"] for x in libres] == pytest.approx(I._bh([x["p"] for x in libres]), abs=1e-4)


def test_une_ligne_par_lieu_et_periode_est_signalee():
    rng = np.random.default_rng(6)
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "district": d, "cas": float(rng.integers(10, 90)), "tests": float(rng.integers(100, 900))}
                       for m in range(1, 13) for d in ("Bouaké", "Korhogo", "Man", "Daloa")])
    r = I.analyse(df)
    assert r["lignes_dependantes"] and any("indépendantes" in x for x in r["limitations"])


def test_lignes_individuelles_non_signalees():
    rng = np.random.default_rng(7)
    n = 400
    df = pd.DataFrame({"date": rng.choice(pd.date_range("2025-01-01", periods=30).strftime("%Y-%m-%d"), n),
                       "ville": rng.choice(["Abidjan", "Bouaké", "Man"], n), "montant": rng.normal(100, 10, n), "poids": rng.normal(5, 1, n)})
    assert not I.analyse(df)["lignes_dependantes"]


def test_fichier_agrege_signale():
    rng = np.random.default_rng(8)
    df = pd.DataFrame({"zone": rng.choice(["A", "B"], 80), "cas": rng.normal(10, 2, 80), "morts": rng.normal(2, 1, 80)})
    df.attrs["agrege"] = True
    assert I.analyse(df)["lignes_dependantes"]


def test_douze_mesures_ne_privent_pas_les_categories_de_tests():
    rng = np.random.default_rng(9)
    n = 200
    df = pd.DataFrame({f"mesure{i}": rng.normal(size=n) for i in range(12)})
    df["zone"], df["canal"] = rng.choice(["A", "B", "C"], n), rng.choice(["W", "X"], n)
    r = I.analyse(df)
    assert r["associations"] and r["comparaisons"]
    assert any("Trop de colonnes" in x for x in r["limitations"])


def test_mesures_seules_gardent_tout_le_plafond():
    rng = np.random.default_rng(10)
    r = I.analyse(pd.DataFrame({f"mesure{i}": rng.normal(size=100) for i in range(12)}))
    assert len(r["correlations"]) == 60


def test_force_de_l_effet():
    assert I._force_effet(-0.9, "d de Cohen") == "forte"
    assert I._force_effet(0.1, "d de Cohen") == "négligeable"
    assert I._force_effet(0.07, "êta²") == "modérée"
    assert I._force_effet(None, "êta²") is None


# ---------- branchement dans l'analyse et dans la page ----------

def _fichier(tmp_path):
    rng = np.random.default_rng(11)
    p = tmp_path / "ventes.csv"
    pd.DataFrame({"zone": rng.choice(["Nord", "Sud", "Est"], 120), "canal": rng.choice(["Web", "Boutique"], 120),
                  "montant": rng.normal(100, 10, 120), "cout": rng.normal(50, 5, 120)}).to_csv(p, index=False)
    return str(p)


def _server():
    from _harness import reset_app_modules
    reset_app_modules()
    import server
    return server


def test_analyse_donne_les_tests_en_json(tmp_path):
    a = _server().analyze(_fichier(tmp_path))
    assert a["inference"]["n_tests"] > 0
    json.dumps(a["inference"], allow_nan=False)


def test_tests_absents_si_non_autorise(tmp_path, monkeypatch):
    server = _server()
    monkeypatch.setattr(server, "_predictif_actif", lambda: False)
    assert "inference" not in server.analyze(_fichier(tmp_path))


def test_une_panne_des_tests_ne_bloque_pas_l_analyse(tmp_path, monkeypatch):
    server = _server()
    monkeypatch.setitem(sys.modules, "inferential", None)
    a = server.analyze(_fichier(tmp_path))
    assert "inference" not in a and a["rows"] == 120


def _bloc(inf):
    """Exécute inferenceBlock de la page avec ses trois aides, telles qu'elles sont écrites dans index.html."""
    html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
    code = html[html.index("const esc=s=>"):].split("\n", 1)[0]
    code += "\n" + next(x for x in html.splitlines() if x.startswith("const nf=n=>"))
    code += "\n" + next(x for x in html.splitlines() if x.startswith("const detailsBlock="))
    code += "\n" + html[html.index("function inferenceBlock(inf){"):html.index("// Lecture chiffrée d'une comparaison")]
    code += "\nprocess.stdout.write(inferenceBlock(" + json.dumps(inf) + "))"
    return subprocess.run(["node", "-e", code], capture_output=True, encoding="utf-8", check=True).stdout


sans_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js absent")


@sans_node
def test_page_ecrit_les_resultats_en_clair():
    inf = {"alpha": 0.05, "n_tests": 2, "n_significatifs": 1, "n_structure": 1, "lignes_dependantes": False, "limitations": [],
           "correlations": [{"a": "ca", "b": "cout", "r": 0.82, "ic95": [0.7, 0.9], "p_ajuste": 0.0, "significatif": True, "structurel": False, "force": "forte"},
                            {"a": "ca", "b": "marge", "r": 1, "ic95": None, "p_ajuste": 0, "significatif": False, "structurel": True}],
           "associations": [],
           "comparaisons": [{"num": "ca", "cat": "<zone>", "test": "ANOVA à un facteur", "effet": -0.61, "effet_nom": "d de Cohen", "p_ajuste": 0.4, "significatif": False, "structurel": False, "force": "modérée",
                             "groupes": [{"label": "Sud", "moyenne": 12}, {"label": "Nord", "moyenne": 30.5}]}]}
    page = _bloc(inf)
    assert "1 résultat significatif sur 2 tests" in page
    assert "&lt; 0,001" in page and "Lien probable" in page and "Peut venir du hasard" in page
    assert "Nord (30,5), Sud (12)" in page and "0,61" in page and "−0,61" not in page and "-0,61" not in page
    assert "&lt;zone&gt;" in page and "<zone>" not in page
    assert "ca et marge" in page and "Un lien n'est pas une cause" in page


@sans_node
def test_page_nuance_si_lignes_dependantes():
    inf = {"alpha": 0.05, "n_tests": 1, "n_significatifs": 1, "n_structure": 0, "lignes_dependantes": True, "limitations": [],
           "correlations": [{"a": "cas", "b": "tests", "r": 0.8, "ic95": [0.6, 0.9], "p_ajuste": 0.01, "significatif": True, "structurel": False, "force": "forte"}],
           "associations": [], "comparaisons": []}
    page = _bloc(inf)
    assert "À vérifier" in page and "Lien probable" not in page and "ne sont pas indépendantes" in page
