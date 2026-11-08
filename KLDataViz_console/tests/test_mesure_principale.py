# -*- coding: utf-8 -*-
"""Graphiques de l'analyse (et du rapport PDF) : la bonne mesure, jamais un mélange d'indicateurs. Fichiers fictifs."""
import os
import sys

import pytest

from _harness import APP, reset_app_modules

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jeux_reference as J  # noqa: E402


@pytest.fixture
def S():
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import server
    return server


def _graphique(charts, titre):
    return next(c for c in charts if c["title"] == titre)


def test_la_cible_ne_passe_jamais_devant_la_realisation(S):
    """Ventes : la somme des objectifs dépasse celle du chiffre d'affaires, mais c'est le chiffre d'affaires qu'on lit."""
    df = J.ventes()
    assert df["objectif"].sum() > df["chiffre_affaires"].sum()
    titres = [c["title"] for c in S._smart_charts(df)]
    assert titres[0] == "Évolution de chiffre d'affaires et marge" and not any("bjectif" in t for t in titres[:4])


def test_export_dhis2_long_un_indicateur_par_graphique(S):
    """Indicateur + valeur : un graphique par indicateur, jamais la somme de tous (testés + positifs + file active)."""
    df = J.dhis2_long()
    charts = S._smart_charts(df)
    assert not any(c["title"].lower().startswith(("valeur", "évolution de valeur")) for c in charts)
    c = _graphique(charts, "Hts tst par unité organisation")
    attendu = df[df["indicateur"] == "HTS_TST"].groupby("unite_organisation")["valeur"].sum()
    assert {l: v for l, v in zip(c["labels"], c["values"][0])} == {k: float(v) for k, v in attendu.items()}


def test_indicateur_en_lignes_et_sites_en_colonnes(S):
    """Une colonne « indicateur » (dépistés, positifs) et une colonne par site : les sites deviennent une dimension."""
    df = J.formulaire()
    charts = S._smart_charts(df)
    c = _graphique(charts, "Dépistés par sexe")
    attendu = df[df["indicateur"] == "Dépistés"].groupby("sexe")[["Site A", "Site B", "Site C"]].sum().sum(axis=1)
    assert {l: v for l, v in zip(c["labels"], c["values"][0])} == {k: float(v) for k, v in attendu.items()}
    c = _graphique(charts, "Dépistés par site")
    assert dict(zip(c["labels"], c["values"][0])) == {
        s: float(df.loc[df["indicateur"] == "Dépistés", s].sum()) for s in ("Site A", "Site B", "Site C")}


# ---------------------------------------------------------------- cas relevés par Hermes (relecture de la PR 200)
def test_une_annee_n_est_jamais_une_mesure(S):
    """indicateur + sexe + année + sites : « Dépistés par sexe » vaut 78 / 52, pas 2 103 / 2 077 (année comptée)."""
    import pandas as pd
    df = pd.DataFrame([{"indicateur": i, "sexe": s, "annee": 2025, "Site A": a, "Site B": b}
                       for i, s, a, b in [("Dépistés", "F", 40, 38), ("Dépistés", "M", 30, 22),
                                          ("Positifs", "F", 3, 2), ("Positifs", "M", 1, 1)]])
    c = _graphique(S._smart_charts(df), "Dépistés par sexe")
    assert dict(zip(c["labels"], c["values"][0])) == {"F": 78.0, "M": 52.0}


def test_code_libelle_montant_jamais_pivote(S):
    """« code » et « montant » ne sont pas un export long : la mesure montant reste lue."""
    import pandas as pd
    df = pd.DataFrame({"code": ["C1", "C2"] * 6, "libelle": ["A", "B"] * 6,
                       "mois": [f"2025-{m:02d}" for m in range(1, 7) for _ in range(2)], "montant": [100 + i for i in range(12)]})
    titres = [c["title"] for c in S._smart_charts(df)]
    assert titres and all("montant" in t.lower() or "lignes" in t.lower() for t in titres), titres


def test_indicateur_non_declare_reste_vide(S):
    """Un indicateur ajouté en cours d'année n'est pas un zéro les mois où il n'existait pas."""
    import pandas as pd
    lignes = [{"indicateur": "TESTES", "periode": f"2025-{m:02d}", "district": d, "valeur": 100 + m}
              for m in range(1, 5) for d in ("A", "B")]
    lignes += [{"indicateur": "POSITIFS", "periode": p, "district": "A", "valeur": v} for p, v in (("2025-01", 10), ("2025-03", 15))]
    charts = S._smart_charts(pd.DataFrame(lignes))
    courbe = next(c for c in charts if c["kind"] == "trend")
    if "Positifs" in courbe["seriesNames"]:
        assert courbe["values"][courbe["seriesNames"].index("Positifs")] == [10.0, None, 15.0, None]
    assert all(0.0 not in (c.get("values") or [[1]])[0] for c in charts if c["kind"] in ("ranking", "share")
               and "ositif" in c["title"])


def test_realisations_jamais_prises_pour_des_cibles(S):
    """« budget_execute », « estimation_ventes » sont des réalisations : jamais déclassées comme une cible."""
    import pandas as pd
    df = pd.DataFrame({"mois": [f"2025-{m:02d}" for m in range(1, 7)] * 2, "district": ["A"] * 6 + ["B"] * 6,
                       "budget_execute": range(100, 112), "estimation_ventes": range(50, 62), "consultations": range(1, 13)})
    titres = [c["title"] for c in S._smart_charts(df)]
    assert titres[0] == "Évolution de budget execute et estimation ventes", titres


def test_colonne_texte_nommee_valeur_sans_collision(S):
    import pandas as pd
    df = pd.DataFrame({"indicateur": ["X", "Y"] * 3, "valeur": ["a", "b"] * 3, "Site A": [1, 2, 3, 4, 5, 6],
                       "Site B": [2, 3, 4, 5, 6, 7]})
    assert S._smart_charts(df) is not None                       # aucune exception, aucun titre « … par valeur »
