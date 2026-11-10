# -*- coding: utf-8 -*-
"""Tableau de bord sur des périodes trimestrielles (données fictives) : « FY26Q3 » (exercice) ou « T1 2026 ».

Sans cela, la colonne de période restait un simple filtre : aucune courbe d'évolution, aucune carte de chaleur,
et des tuiles cumulées sur tous les trimestres sans période affichée."""
import json
import os
import re
import sys

import pandas as pd

from _harness import reset_app_modules

FY = ["FY25Q2", "FY25Q3", "FY25Q4", "FY26Q1", "FY26Q2", "FY26Q3"]


def _module():
    reset_app_modules()
    import server  # noqa: PLC0415
    racine = os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA")
    if racine not in sys.path:
        sys.path.insert(0, racine)
    from hermes_da.adapters import dashboard  # noqa: PLC0415
    return dashboard


def _meta(html):
    m = re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", html, re.S)
    assert m, "META introuvable dans la page"
    return json.loads(m.group(1))


def _donnees(periodes):
    return pd.DataFrame([{"periode": p, "district": d, "site": f"Centre {d}{j}", "Clients dépistés": 10 * (i + 1) + 3 * j + k}
                         for i, p in enumerate(periodes) for k, d in enumerate(["Nord", "Sud", "Est"]) for j in range(2)])


def test_lecture_des_trimestres():
    D = _module()
    attendu = {"FY26Q1": ("fy", "2025-10"), "FY26Q2": ("fy", "2026-01"), "FY26Q3": ("fy", "2026-04"),
               "FY26Q4": ("fy", "2026-07"), "fy 2026 q3": ("fy", "2026-04"), "2026Q1": ("cal", "2026-01"),
               "T2 2026": ("cal", "2026-04"), "Q4-2025": ("cal", "2025-10"), "3e trimestre 2025": ("cal", "2025-07")}
    for v, r in attendu.items():
        assert D._trimestre(v) == r, v
    # le libellé revient à l'identique : FY26Q1 = oct.-déc. 2025, jamais « T4 2025 » ni « oct. 2025 »
    for q in ("FY25Q4", "FY26Q1", "FY26Q2", "FY26Q3"):
        g, k = D._trimestre(q)
        assert D._nom_trimestre(k, g) == q
    assert D._nom_trimestre("2026-04", "cal") == "T2 2026"
    for v in ("Dépistage_fy26q1", "2026-04", "Q5 2026", "FY26", "Centre 3"):
        assert D._trimestre(v) is None, v


def test_trimestres_d_exercice_donnent_une_courbe():
    D = _module()
    r = D.generate_dashboard(_donnees(FY), "t", llm=False, composition=True)
    meta = _meta(r["html"])
    assert meta["period"] == "periode" and meta["trimestre"] == "fy"
    assert meta["range"] == "FY25Q2 – FY26Q3"
    assert meta["gran"] == "Q" and meta["grans"] == ["Q"]  # ni mois inventés, ni année civile à cheval sur deux exercices
    assert "periode" not in [f["col"] for f in meta["filters"]]  # c'est l'axe du temps, plus un simple filtre
    cartes = [c["type"] for ligne in meta["composition"]["lignes"] for c in ligne]
    assert "courbe" in cartes
    per = meta["composition"].get("periode") or {}
    assert not per or not per.get("comparable_n1")
    # la série par trimestre : 6 points, sommes justes
    df = _donnees(FY)
    _, serie = D._period(df)
    par_cle = df.assign(k=serie).groupby("k")["Clients dépistés"].sum()
    assert list(par_cle.index) == ["2025-01", "2025-04", "2025-07", "2025-10", "2026-01", "2026-04"]
    assert par_cle.tolist() == df.groupby("periode", sort=False)["Clients dépistés"].sum().tolist()


def test_trimestres_civils():
    D = _module()
    r = D.generate_dashboard(_donnees(["T1 2025", "T2 2025", "T3 2025", "T4 2025"]), "t", llm=False)
    meta = _meta(r["html"])
    assert meta["trimestre"] == "cal" and meta["range"] == "T1 2025 – T4 2025" and meta["grans"] == ["Q", "Y"]


def test_libelle_d_un_stock_au_dernier_trimestre():
    D = _module()
    df = _donnees(FY).rename(columns={"Clients dépistés": "File active"})
    spec = {"kpis": [{"nom": "File active", "agregation": "somme", "colonne": "File active"}], "graphiques": []}
    r = D.generate_dashboard(df, "t", llm=lambda m, n: json.dumps(spec))
    k = r["kpis"][0]
    assert k["nom"] == "File active (FY26Q3)"
    assert k["valeur"] == float(df[df.periode == "FY26Q3"]["File active"].sum())


def test_les_mois_restent_des_mois():
    D = _module()
    df = _donnees(["2025-01", "2025-02", "2025-03", "2025-04"])
    meta = _meta(D.generate_dashboard(df, "t", llm=False)["html"])
    assert not meta.get("trimestre") and meta["gran"] == "M"


def test_une_valeur_parasite_ne_fait_pas_revenir_les_mois():
    """« - » parmi des trimestres FY : la période reste en FY partout (jamais « oct. 2025 » pour FY26Q1)."""
    D = _module()
    df = _donnees(["FY26Q1", "FY26Q2", "FY26Q3", "FY26Q4"])
    df.loc[0, "periode"] = "-"
    meta = _meta(D.generate_dashboard(df, "t", llm=False)["html"])
    assert meta["trimestre"] == "fy" and meta["grans"] == ["Q"] and meta["range"] == "FY26Q1 – FY26Q4"
    assert D._genre_trimestre(df, "periode") == "fy"
