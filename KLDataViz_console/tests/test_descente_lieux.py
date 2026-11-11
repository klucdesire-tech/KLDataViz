# -*- coding: utf-8 -*-
"""Filtre sur une région (données fictives) : les cartes découpées par région passent à ses districts.

Avant, une carte « Part de chaque région » sous le filtre Région n'avait plus qu'une part, et « Courbes comparées par
région » une seule courbe. On descend d'un niveau dans la hiérarchie région > district, avec un graphique permis pour
le nombre de districts (barres classées au-delà de 5 parts)."""
import html as html_lib
import json
import os
import re
import subprocess
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_courbes_ensemble import BANC, NODE, _module, sans_node  # noqa: E402

DISTRICTS = {"Région Est": ["District E1", "District E2", "District E3"],
             "Région Ouest": [f"District O{i}" for i in range(1, 8)]}


def _donnees():
    lignes = []
    for i, p in enumerate(["FY25Q4", "FY26Q1", "FY26Q2", "FY26Q3"]):
        for region, districts in DISTRICTS.items():
            for d, district in enumerate(districts):
                lignes.append({"periode": p, "region": region, "district": district, "Nombre de consultants": 50 + 10 * d + 3 * i})
    return pd.DataFrame(lignes)


def _rendre(tmp_path, html, carte, filtres):
    (tmp_path / "banc.js").write_text(BANC, encoding="utf-8")
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    expr = "CP_DESSINS[cpDescendre(%s,filtered()).type](cpDescendre(%s,filtered()),filtered())" % (carte, carte)
    r = subprocess.run([NODE, str(tmp_path / "banc.js"), str(tmp_path / "page.html"), "[]", json.dumps(filtres), json.dumps([expr])],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[:900]
    return json.loads(r.stdout)["evals"][0]


@sans_node
def test_filtre_region_detaille_par_district(tmp_path):
    _, _, dashboard, _ = _module()
    df = _donnees()
    html = dashboard.generate_dashboard(df, "", llm=False, dictionnaire=[], composition=True)["html"]
    meta = json.loads(re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", html, re.S).group(1))
    assert meta["composition"]["lieux"][:2] == ["region", "district"]
    anneau = json.dumps({"type": "anneau", "mesure": "Nombre de consultants", "dimension": "region", "agregation": "somme",
                         "nature": "volume", "span": 6}, ensure_ascii=False)
    # 3 districts : l'anneau reste un anneau, découpé par district, les parts = sommes de la région
    est = _rendre(tmp_path, html, anneau, {"region": "Région Est"})
    assert "Détail par district de Région Est" in est and "cp-vide" not in est
    parts = {html_lib.unescape(n): v for n, v in re.findall(r"<title>([^<:]+) : ([^<]+)</title>", est)}
    attendu = df[df.region == "Région Est"].groupby("district")["Nombre de consultants"].sum()
    assert set(parts) == set(attendu.index)
    assert {n: int(re.sub(r"\D", "", v)) for n, v in parts.items()} == attendu.astype(int).to_dict()
    # 7 districts : trop de parts pour un anneau, barres classées
    ouest = _rendre(tmp_path, html, anneau, {"region": "Région Ouest"})
    assert "Détail par district de Région Ouest" in ouest and 'class="cp-do"' not in ouest
    assert all(d in html_lib.unescape(ouest) for d in DISTRICTS["Région Ouest"])
    # courbes comparées par région → par district de la région
    comparee = json.dumps({"type": "comparee", "mesure": "Nombre de consultants", "dimension": "region", "agregation": "somme",
                           "nature": "volume", "span": 6}, ensure_ascii=False)
    courbes = _rendre(tmp_path, html, comparee, {"region": "Région Est"})
    assert "Détail par district de Région Est" in courbes and all(d in courbes for d in DISTRICTS["Région Est"])
    # sans filtre : rien ne change
    sans = _rendre(tmp_path, html, anneau, {})
    assert "Détail par district" not in sans and "Région Est" in sans and "Région Ouest" in sans
