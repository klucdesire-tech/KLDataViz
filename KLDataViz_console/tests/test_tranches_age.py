# -*- coding: utf-8 -*-
"""Répartition par tranche d'âge (données fictives) : toutes les tranches, dans l'ordre des âges, même à 0.

Une mosaïque laissait les petites tranches sans nom (« Non nommés : 10-14 ans… ») et faisait disparaître une tranche
à 0. Les tranches d'âge ont un ordre naturel : des bandes rangées de « <1 an » à « 50+ ans »."""
import html as html_lib
import json
import os
import re
import subprocess
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_courbes_ensemble import BANC, NODE, _module, sans_node  # noqa: E402

AGES = ["50+ ans", "<1 an", "10-14 ans", "1-4 ans", "25-49 ans", "5-9 ans", "15-19 ans", "20-24 ans"]
ORDRE = ["<1 an", "1-4 ans", "5-9 ans", "10-14 ans", "15-19 ans", "20-24 ans", "25-49 ans", "50+ ans"]


def _executer(tmp_path, html, exprs, filtres=None):
    (tmp_path / "banc.js").write_text(BANC, encoding="utf-8")
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "banc.js"), str(tmp_path / "page.html"), "[]", json.dumps(filtres or {}),
                        json.dumps(exprs)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[:900]
    return json.loads(r.stdout)["evals"]


def _donnees():
    lignes = []
    for s in range(3):
        for i, a in enumerate(AGES):
            lignes.append({"periode": "FY26Q1", "district": f"District {s}", "site": f"Centre {s}", "tranche_age": a,
                           "Nombre de positifs": 0 if a == "<1 an" else 2 + 7 * i + s})
    return pd.DataFrame(lignes)


@sans_node
def test_ordre_des_tranches(tmp_path):
    _, _, dashboard, _ = _module()
    html = dashboard.generate_dashboard(_donnees(), "", llm=False, dictionnaire=[], composition=True)["html"]
    ordre, autre, mois = _executer(tmp_path, html, [
        "JSON.stringify(cpOrdreAges(" + json.dumps(AGES, ensure_ascii=False) + "))",
        "JSON.stringify(cpOrdreAges(['Nord','Sud','Est']))",
        "JSON.stringify(cpOrdreAges(['1-4 ans','0-11 mois','5-9 ans']))"])
    assert json.loads(ordre) == ORDRE
    assert json.loads(autre) is None  # des lieux ne sont pas des tranches d'âge : la mosaïque reste
    assert json.loads(mois) == ["0-11 mois", "1-4 ans", "5-9 ans"]


@sans_node
def test_toutes_les_tranches_visibles_meme_a_zero(tmp_path):
    _, _, dashboard, _ = _module()
    df = _donnees()
    html = dashboard.generate_dashboard(df, "", llm=False, dictionnaire=[], composition=True)["html"]
    carte = json.dumps({"type": "treemap", "mesure": "Nombre de positifs", "dimension": "tranche_age", "agregation": "somme",
                        "span": 7}, ensure_ascii=False)
    sortie, = _executer(tmp_path, html, ["CP_DESSINS.treemap(" + carte + ",filtered())"])
    assert "ordre des âges" in sortie and "Non nommés" not in sortie
    vus = [(html_lib.unescape(n), v) for n, v in re.findall(r"<title>([^<:]+?) : ([^<(]+?)(?: \([^)]*\))?</title>", sortie)]
    assert [n for n, _ in vus] == ORDRE  # toutes les tranches, dans l'ordre, « <1 an » à 0 compris
    attendu = df.groupby("tranche_age")["Nombre de positifs"].sum()
    assert {n: int(re.sub(r"\D", "", v)) for n, v in vus} == {a: int(attendu[a]) for a in ORDRE}


@sans_node
def test_filtre_sur_une_tranche_negatif_et_libelles_varies(tmp_path):
    _, _, dashboard, _ = _module()
    df = _donnees()
    df.loc[df.tranche_age == "1-4 ans", "Nombre de positifs"] = -3  # valeur négative : hors des parts, jamais « non renseigné »
    html = dashboard.generate_dashboard(df, "", llm=False, dictionnaire=[], composition=True)["html"]
    carte = json.dumps({"type": "treemap", "mesure": "Nombre de positifs", "dimension": "tranche_age", "agregation": "somme",
                        "span": 7}, ensure_ascii=False)
    filtre, = _executer(tmp_path, html, ["CP_DESSINS.treemap(" + carte + ",filtered())"], {"tranche_age": "25-49 ans"})
    # filtre sur une tranche : toutes les tranches restent (données filtrées, pas absentes), la retenue mise en avant
    assert "25-49 ans mis en avant" in filtre and "Non renseigné" not in filtre and "non renseigné" not in filtre
    # 8 tranches : <1 an (0) et 1-4 ans (négatif) sans bande, 25-49 en plein, les 5 autres atténuées
    assert filtre.count('fill-opacity=".28"') == 5 and filtre.count("<rect ") == 6
    assert "négatif, hors des parts" in filtre and "Valeur négative, hors des parts : 1-4 ans" in filtre
    autres = _executer(tmp_path, html, [
        "JSON.stringify(cpOrdreAges(['15-19','0-4','25-49','15+'],'tranche_age'))",  # sans « ans », colonne d'âge
        "JSON.stringify(cpOrdreAges(['1-4 ans','5-9 ans','10-14 ans','Inconnu'],'tranche_age'))",  # une valeur hors tranche
        "JSON.stringify(cpOrdreAges(['Nord 1 an','Sud 2 ans','Est 3 ans'],'zone'))"])  # pas des tranches d'âge
    assert json.loads(autres[0]) == ["0-4", "15-19", "15+", "25-49"]
    assert json.loads(autres[1]) == ["1-4 ans", "5-9 ans", "10-14 ans", "Inconnu"]
    assert json.loads(autres[2]) is None


def test_largeurs_tranches_age_etroites_et_temps_large():
    """Tranches d'âge en bandes : 5 colonnes, pas 7 (mosaïque) ; le reste de la ligne va aux courbes comparées."""
    catalogue, composition, _, _ = _module()
    assert catalogue.tranches_age(pd.DataFrame({"tranche_age": ["<1 an", "1-4 ans", "5-9 ans", "50+ ans"]}), "tranche_age")
    assert catalogue.tranches_age(pd.DataFrame({"tranche_age": ["0-4", "15-19", "25-49", "Inconnu"]}), "tranche_age")
    assert not catalogue.tranches_age(pd.DataFrame({"zone": ["Nord 1 an", "Sud 2 ans", "Est 3 ans"]}), "zone")
    assert not catalogue.tranches_age(pd.DataFrame({"produit": ["A", "B", "C", "D"]}), "produit")
    lignes = composition.placer([{"type": "treemap", "ages": True}, {"type": "comparee"}])
    assert [(c["type"], c["span"]) for c in lignes[0]] == [("treemap", 5), ("comparee", 7)]
    # une mosaïque ordinaire garde sa largeur
    lignes = composition.placer([{"type": "treemap"}, {"type": "comparee"}])
    assert [(c["type"], c["span"]) for c in lignes[0]] == [("treemap", 7), ("comparee", 5)]


@sans_node
def test_serveur_et_page_reconnaissent_les_memes_tranches(tmp_path):
    """catalogue.tranches_age (largeur de la carte) et cpOrdreAges (dessin) : même règle, sinon une mosaïque serait
    mise à 5 colonnes, ou des bandes à 7."""
    catalogue, _, dashboard, _ = _module()
    html = dashboard.generate_dashboard(_donnees(), "", llm=False, dictionnaire=[], composition=True)["html"]
    cas = [(["<1 an", "1-4 ans", "5-9 ans", "50+ ans"], "tranche_age"), (["0-4", "15-19", "25-49", "Inconnu"], "tranche_age"),
           (["0-4", "15-19", "25-49"], "zone"), (["Nord 1 an", "Sud 2 ans", "Est 3 ans"], "zone"), (["A", "B", "C"], "produit"),
           (["0-11 mois", "1-4 ans", "5-9 ans"], "age"), (["moins de 1 an", "1-4 ans", "5+ ans"], "classe"),
           ([f"{i}-{i + 4} ans" for i in range(0, 80, 5)], "tranche_age"), (["Inconnu", "Autre", "1-4 ans", "5-9 ans", "10-14 ans"], "age")]
    page = _executer(tmp_path, html, ["JSON.stringify(cpOrdreAges(%s,%s)!==null)" % (json.dumps(v, ensure_ascii=False), json.dumps(g))
                                      for v, g in cas])
    for (v, g), dessin in zip(cas, page):
        assert catalogue.tranches_age(pd.DataFrame({g: v}), g) == json.loads(dessin), (v, g)
