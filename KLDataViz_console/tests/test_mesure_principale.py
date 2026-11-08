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
