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
