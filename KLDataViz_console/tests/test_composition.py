# -*- coding: utf-8 -*-
"""Composition de la page : une disposition différente selon les données, toujours valide et sans doublon."""
import os
import sys

import pandas as pd
import pytest

from _harness import APP, reset_app_modules

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jeux_reference as J  # noqa: E402

MODELES = {"sante": "territoire", "ventes": "pilotage", "dhis2_long": "territoire", "formulaire": "repartition",
           "simple": "repartition", "sans_dates": "repartition"}


@pytest.fixture
def plan():
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters import composition, fiche  # noqa: PLC0415

    def faire(df):
        return composition.composer(fiche.lire_fiche(df), fiche._long_to_wide(df)[0])
    return faire


def _cartes(p):
    return [c for ligne in p["lignes"] for c in ligne]


def _signature(p):
    return tuple(tuple((c["type"], c["span"]) for c in ligne) for ligne in p["lignes"])


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_modele_attendu(plan, jeu):
    assert plan(J.JEUX[jeu]())["modele"] == MODELES[jeu]


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_chaque_ligne_fait_12_colonnes(plan, jeu):
    for ligne in plan(J.JEUX[jeu]())["lignes"]:
        assert sum(c["span"] for c in ligne) == 12, ligne


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_au_plus_8_cartes(plan, jeu):
    assert 1 <= len(_cartes(plan(J.JEUX[jeu]()))) <= 8


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_pas_de_doublon(plan, jeu):
    vus = [(c["type"], c.get("mesure"), c.get("dimension")) for c in _cartes(plan(J.JEUX[jeu]()))]
    assert len(vus) == len(set(vus))


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_une_seule_vue_de_partage_par_couple(plan, jeu):
    vus = [(c.get("mesure"), c.get("dimension")) for c in _cartes(plan(J.JEUX[jeu]())) if c["type"] in ("classement", "anneau", "treemap")]
    assert len(vus) == len(set(vus))


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_deterministe(plan, jeu):
    assert plan(J.JEUX[jeu]()) == plan(J.JEUX[jeu]())


def test_dispositions_differentes(plan):
    signatures = {j: _signature(plan(J.JEUX[j]())) for j in J.JEUX}
    assert len(set(signatures.values())) >= 5, signatures


def test_pilotage_commence_par_l_objectif(plan):
    p = plan(J.JEUX["ventes"]())
    assert _cartes(p)[0]["type"] == "cible" and p["mesure"] == "chiffre_affaires"


def test_territoire_commence_par_le_temps(plan):
    assert _cartes(plan(J.JEUX["sante"]()))[0]["type"] == "courbe"


def test_hierarchie_prend_le_niveau_intermediaire(plan):
    assert plan(J.JEUX["sante"]())["dimension"] == "district"


def test_variete_sur_un_fichier_riche(plan):
    types = [c["type"] for c in _cartes(plan(J.JEUX["sante"]()))]
    assert len(set(types)) == len(types) >= 6


@pytest.mark.parametrize("jeu", ["sante", "ventes"])
def test_plusieurs_mesures_montrees(plan, jeu):
    assert len({c.get("mesure") for c in _cartes(plan(J.JEUX[jeu]()))}) >= 2


def test_petit_fichier_sans_remplissage(plan):
    p = plan(J.JEUX["simple"]())
    assert len(_cartes(p)) <= 2 and sum(c["span"] for c in p["lignes"][0]) == 12


def test_deux_cartes_a_parts_egales(plan):
    p = plan(J.JEUX["formulaire"]())
    assert [[c["span"] for c in ligne] for ligne in p["lignes"]] in ([[6, 6]], [[4, 4, 4]])


def test_jamais_de_carte_en_dessous_de_son_minimum(plan):
    from hermes_da.adapters.composition import MINIMA, MINIMUM  # noqa: PLC0415
    for f in J.JEUX.values():
        for c in _cartes(plan(f())):
            assert c["span"] >= MINIMA.get(c["type"], MINIMUM), c


def test_fichier_riche_sans_carte_isolee_en_pleine_largeur(plan):
    assert all(len(ligne) > 1 for ligne in plan(J.JEUX["sante"]())["lignes"])


def test_carte_seule_prend_toute_la_largeur(plan):
    p = plan(pd.DataFrame({"zone": ["A", "B", "C"], "taux_couverture": [80.0, 70.0, 60.0]}))
    assert [[c["span"] for c in ligne] for ligne in p["lignes"]] == [[12]]


def test_fichier_vide(plan):
    p = plan(pd.DataFrame())
    assert p["lignes"] == [] and p["cartes"] == 0


def test_fichier_sans_mesure(plan):
    p = plan(pd.DataFrame({"nom_ville": ["Abidjan", "Daloa"], "note": ["a", "b"]}))
    assert p["cartes"] == 0


def test_toutes_les_cartes_viennent_du_catalogue(plan):
    from hermes_da.adapters import catalogue, fiche  # noqa: PLC0415
    for jeu, f in J.JEUX.items():
        df = f()
        props = catalogue.proposer(fiche.lire_fiche(df), fiche._long_to_wide(df)[0])
        for c in _cartes(plan(df)):
            sans = {k: v for k, v in c.items() if k != "span"}
            assert sans in props, (jeu, sans)
