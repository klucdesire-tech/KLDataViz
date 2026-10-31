# -*- coding: utf-8 -*-
"""Catalogue de graphiques : ce que chaque fichier permet, et surtout ce qu'il interdit."""
import os
import sys

import pandas as pd
import pytest

from _harness import APP, reset_app_modules

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jeux_reference as J  # noqa: E402

PRESENTS = {"sante": {"courbe", "chaleur", "multiples", "classement", "anneau", "treemap", "pente", "passage", "bulles"},
            "ventes": {"courbe", "chaleur", "classement", "anneau", "pente", "passage", "cible", "bulles"},
            "dhis2_long": {"courbe", "chaleur", "multiples"},
            "simple": {"treemap"}, "sans_dates": {"classement", "histogramme"}}
ABSENTS = {"sante": {"cible"}, "dhis2_long": {"cible", "pente", "passage"},
           "simple": {"courbe", "chaleur", "cible", "pente", "passage", "multiples", "bulles", "histogramme"},
           "sans_dates": {"courbe", "chaleur", "cible", "pente", "passage", "multiples", "anneau", "treemap"}}


@pytest.fixture
def cat():
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters import catalogue, fiche  # noqa: PLC0415
    return lambda df: catalogue.proposer(fiche.lire_fiche(df), fiche._long_to_wide(df)[0])


def _types(props):
    return {p["type"] for p in props}


@pytest.mark.parametrize("jeu", sorted(PRESENTS))
def test_graphiques_permis(cat, jeu):
    t = _types(cat(J.JEUX[jeu]()))
    assert PRESENTS[jeu] <= t, PRESENTS[jeu] - t


@pytest.mark.parametrize("jeu", sorted(ABSENTS))
def test_graphiques_interdits(cat, jeu):
    t = _types(cat(J.JEUX[jeu]()))
    assert not ABSENTS[jeu] & t, ABSENTS[jeu] & t


def test_chaque_fichier_a_sa_propre_palette(cat):
    jeux = {j: frozenset(_types(cat(J.JEUX[j]()))) for j in J.JEUX}
    assert len(set(jeux.values())) >= 4, jeux


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_jamais_de_part_sur_autre_chose_qu_un_volume(cat, jeu):
    for p in cat(J.JEUX[jeu]()):
        if p["type"] in ("anneau", "treemap", "passage"):
            assert p["nature"] == "volume", p


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_stock_jamais_additionne(cat, jeu):
    for p in cat(J.JEUX[jeu]()):
        if p.get("nature") == "stock":
            assert p["agregation"] == "dernier" and p["type"] not in ("anneau", "treemap", "passage", "pente", "chaleur"), p


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_taux_et_moyenne_jamais_sommes(cat, jeu):
    for p in cat(J.JEUX[jeu]()):
        if p.get("nature") in ("taux", "moyenne"):
            assert p["agregation"] == "moyenne", p


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_colonnes_toujours_reelles(cat, jeu):
    from hermes_da.adapters.fiche import _long_to_wide  # noqa: PLC0415
    colonnes = _long_to_wide(J.JEUX[jeu]())[0].columns
    for p in cat(J.JEUX[jeu]()):
        for k in ("mesure", "mesure_y", "dimension", "periode", "objectif"):
            assert p.get(k) is None or p[k] in colonnes, (k, p)


def test_trop_de_categories_pas_d_anneau(cat):
    df = pd.DataFrame({"produit": [f"P{i}" for i in range(8)], "ventes": range(1, 9)})
    t = _types(cat(df))
    assert "anneau" not in t and "treemap" in t and "classement" in t


def test_deux_parts_ne_font_pas_un_anneau(cat):
    df = pd.DataFrame({"zone": ["A", "B"], "ventes": [10, 20]})
    assert "anneau" not in _types(cat(df))


def test_valeur_negative_interdit_les_parts(cat):
    df = pd.DataFrame({"zone": ["A", "B", "C"], "resultat": [100, -40, 60]})
    assert not {"anneau", "treemap", "passage"} & _types(cat(df))


def test_deux_points_ne_font_pas_une_courbe(cat):
    df = pd.DataFrame({"mois": ["2025-01", "2025-02"], "ventes": [10, 12]})
    assert "courbe" not in _types(cat(df))


def test_chaleur_demande_assez_de_colonnes(cat):
    df = pd.DataFrame({"mois": [f"2025-{m:02d}" for m in range(1, 5)] * 2, "zone": ["A"] * 4 + ["B"] * 4, "ventes": range(8)})
    assert "chaleur" not in _types(cat(df))


def test_objectif_sans_mesure_proche_ignore(cat):
    df = pd.DataFrame({"zone": ["A", "B", "C"], "ventes": [10, 20, 30], "budget": [5000, 6000, 7000]})
    assert "cible" not in _types(cat(df))


def test_objectif_relie_par_le_nom(cat):
    df = pd.DataFrame({"zone": ["A", "B", "C"], "ventes": [10, 20, 30], "couts": [2, 3, 4], "objectif_ventes": [12, 18, 35]})
    assert [p["mesure"] for p in cat(df) if p["type"] == "cible"] == ["ventes"]


def test_objectif_ambigu_ignore(cat):
    df = pd.DataFrame({"zone": ["A", "B", "C"], "ventes": [10, 20, 30], "recettes": [11, 21, 31], "objectif": [12, 18, 35]})
    assert "cible" not in _types(cat(df))


def test_objectif_ventes_sur_le_bon_chiffre(cat):
    p = [x for x in cat(J.JEUX["ventes"]()) if x["type"] == "cible"]
    assert [x["mesure"] for x in p] == ["chiffre_affaires"]


def test_pas_de_comparaison_sur_une_seule_annee(cat):
    df = pd.DataFrame({"mois": [f"2025-{m:02d}" for m in range(1, 13)] * 2, "zone": ["A"] * 12 + ["B"] * 12, "ventes": range(24)})
    assert not {"pente", "passage"} & _types(cat(df))


def test_comparaison_limitee_aux_mois_communs(cat):
    """Le jeu santé couvre 12 mois en 2024 et 6 en 2025 : seuls janvier à juin se comparent."""
    mois = {tuple(p["mois_communs"]) for p in cat(J.JEUX["sante"]()) if p["type"] in ("pente", "passage")}
    assert mois == {("01", "02", "03", "04", "05", "06")}


def test_annees_entieres_comparees_en_entier(cat):
    mois = {len(p["mois_communs"]) for p in cat(J.JEUX["ventes"]()) if p["type"] in ("pente", "passage")}
    assert mois == {12}


def test_fichier_vide(cat):
    assert cat(pd.DataFrame()) == []


def test_fichier_inchange(cat):
    df = J.JEUX["ventes"]()
    avant = df.copy()
    cat(df)
    assert df.equals(avant)
