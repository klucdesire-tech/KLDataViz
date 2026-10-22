# -*- coding: utf-8 -*-
"""Audit analytique (octobre 2026) : les erreurs qui discréditeraient une analyse ne doivent pas revenir.
Données fictives construites dans les tests."""
import os
import sys

import numpy as np
import pandas as pd

from _harness import reset_app_modules


def _server():
    reset_app_modules()
    import server  # noqa: PLC0415
    return server


def _charts(df):
    return _server()._smart_charts(df)


def test_un_stock_n_est_pas_additionne_sur_plusieurs_annees():
    rows = [{"region": r, "annee": a, "population": p + 10 * (a - 2020)}
            for a in range(2020, 2025) for r, p in (("Abidjan", 5000), ("Bouaké", 1000))]
    ch = next(c for c in _charts(pd.DataFrame(rows)) if c["kind"] == "ranking")
    assert ch["values"][0][0] == 5040.0          # dernière année (2024), pas la somme des 5 années (25 200)
    assert ch["subtitle"].startswith("Année 2024")


def test_un_prix_unitaire_n_est_jamais_additionne():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"magasin": rng.choice(["A", "B", "C"], 200), "quantite": rng.integers(1, 9, 200),
                       "prix_unitaire_fcfa": rng.choice([500, 1500, 2500], 200)})
    for c in _charts(df):
        if "prix" in c["title"].lower():
            assert not c.get("subtitle", "").startswith("Somme"), c["title"]


def test_mois_en_cours_incomplet_ecarte_de_la_courbe():
    dates = list(pd.date_range("2025-01-01", "2025-05-31", freq="D")) + list(pd.date_range("2025-06-01", "2025-06-05", freq="D"))
    df = pd.DataFrame({"date_vente": dates, "montant_fcfa": [1000 + (i % 7) * 10 for i in range(len(dates))]})
    ch = next(c for c in _charts(df) if c["kind"] == "trend")
    assert "incomplet" in ch["subtitle"] and "2025-06-02" not in ch["labels"][-1]  # dernière période (5 jours) écartée


def test_lecture_d_une_moyenne_sans_part_du_total():
    reset_app_modules()
    import pdf_report  # noqa: PLC0415
    c = {"kind": "ranking", "type": "bar", "agg": "mean", "subtitle": "Moyenne · ligne : moyenne",
         "labels": ["F", "M"], "values": [[87.6, 87.5]]}
    assert "du total" not in pdf_report.interpret_chart(c)


def test_pas_de_valeurs_aberrantes_quand_la_moitie_des_valeurs_sont_nulles():
    server = _server()
    import tempfile  # noqa: PLC0415
    d = tempfile.mkdtemp()
    p = os.path.join(d, "naissances.csv")
    pd.DataFrame({"tranche": ["0-14", "15-24", "25-34", "35-44"] * 25,
                  "naissances": [0, 120, 0, 0] * 25}).to_csv(p, index=False)
    a = server.analyze(p)
    assert not [x for x in a["anomalies"] if x["column"] == "naissances"]


def test_regles_du_tableau_de_bord():
    reset_app_modules()
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import generate_dashboard  # noqa: PLC0415
    rng = np.random.default_rng(1)
    n = 300
    ventes = pd.DataFrame({"magasin": rng.choice(["A", "B"], n), "annee": rng.choice([2023, 2024], n),
                           "prix_unitaire_fcfa": rng.choice([500, 1500], n), "quantite": rng.integers(1, 5, n),
                           "delai_livraison_jours": np.r_[rng.integers(0, 3, n - 20), rng.integers(60, 120, 20)]})
    k = {x["colonne"]: x for x in generate_dashboard(ventes, "t", llm=False)["kpis"]}
    assert k["prix_unitaire_fcfa"]["agregation"] != "somme"          # jamais la somme des prix
    assert "annee" not in k                                           # une année n'est pas un indicateur
    assert k["delai_livraison_jours"]["agregation"] == "mediane"      # valeurs très dispersées
    demo = pd.DataFrame([{"region": r, "annee": a, "population": 100 * (a - 2019), "part_population_pct": 50}
                         for a in (2023, 2024) for r in ("A", "B")])
    k = {x["colonne"]: x for x in generate_dashboard(demo, "t", llm=False)["kpis"]}
    assert k["population"]["agregation"] == "dernier" and k["population"]["valeur"] == 1000.0
    assert "part_population_pct" not in k and None not in k           # ni moyenne de parts, ni « effectif » de strates
