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
    """Plusieurs questions différentes ; un type ne revient (sur une autre mesure) que s'il reste plus parlant que les autres."""
    types = [c["type"] for c in _cartes(plan(J.JEUX["sante"]()))]
    assert len(set(types)) >= 5 and max(types.count(t) for t in types) <= 2, types


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


# ---- choix selon ce que les données montrent (données fictives de même forme, messages différents)
def _meme_forme(graine, saison=0.0, divergence=0.0, dominante=1.0, hausse=0.0):
    import numpy as np  # noqa: PLC0415
    rng = np.random.default_rng(graine)
    lignes = []
    for i, m in enumerate(pd.period_range("2024-01", "2025-12", freq="M").astype(str)):
        for j, r in enumerate(["Nord", "Sud", "Est", "Ouest", "Centre"]):
            base = 100 * (dominante if r == "Nord" else 1)
            mois = int(m[5:])
            v = base * (1 + saison * np.sin(mois / 12 * 2 * np.pi)) * (1 + divergence * (j - 2) * i / 24) * (1 + hausse * (m >= "2025"))
            lignes.append({"mois": m, "region": r, "ventes": round(v * rng.normal(1, .03), 1), "clients": round(v / 4 * rng.normal(1, .03))})
    return pd.DataFrame(lignes)


def _types(p):
    return {c["type"] for c in _cartes(p)}


def test_meme_forme_pages_differentes(plan):
    saison = plan(_meme_forme(1, saison=.45, divergence=.6))
    plate = plan(_meme_forme(2, dominante=6.0))
    assert _signature(saison) != _signature(plate)
    assert "chaleur" in _types(saison) and "comparee" in _types(saison)   # des moments et des lieux qui se distinguent
    assert {"anneau", "classement"} & _types(plate)                       # un lieu écrase les autres
    assert not {"comparee", "pente", "chaleur"} & _types(plate)           # tout évolue pareil : rien à comparer


def test_bruit_seul_ne_fait_pas_de_graphique(plan):
    p = plan(_meme_forme(3))
    assert not {"comparee", "pente", "passage", "chaleur"} & _types(p), _types(p)
    assert 1 <= len(_cartes(p)) <= 8


def test_variation_nette_amene_le_passage(plan):
    assert "passage" in _types(plan(_meme_forme(4, hausse=.3)))
    assert "passage" not in _types(plan(_meme_forme(5)))


def test_fichier_annuel_garde_pente_et_passage():
    """Une valeur par an (relecture Hermes, PR 185) : la comparaison 2024 → 2025 ne doit pas tomber à 0."""
    from hermes_da.adapters import catalogue, fiche, interet  # noqa: PLC0415
    lignes = [{"annee": f"{a}-12", "region": r, "montant": base * (1.25 if a == 2025 else 1) * (1.4 if r == "Sud" and a == 2025 else 1)}
              for a in (2023, 2024, 2025) for r, base in (("Nord", 900.0), ("Sud", 300.0), ("Est", 500.0), ("Ouest", 700.0))]
    df = pd.DataFrame(lignes)
    f, large = fiche.lire_fiche(df), fiche._long_to_wide(df)[0]
    props = catalogue.proposer(f, large)
    notes = {p["type"]: n for p, n in zip(props, interet.noter(props, f, large))}
    assert notes.get("passage", 0) >= interet.SEUIL and notes.get("pente", 0) >= interet.SEUIL, notes


def test_lien_entre_mesures_sans_scipy(monkeypatch):
    """La note des barres jumelées ne passe pas par scipy (corr(method="spearman") l'importe à la volée)."""
    import builtins  # noqa: PLC0415
    from hermes_da.adapters import catalogue, fiche, interet  # noqa: PLC0415
    vrai = builtins.__import__

    def sans_scipy(nom, *a, **k):
        if nom.startswith("scipy"):
            raise ImportError("scipy absent")
        return vrai(nom, *a, **k)
    monkeypatch.setattr(builtins, "__import__", sans_scipy)
    df = pd.DataFrame({"centre": [f"C{i}" for i in range(12)], "visites": [10.0 * i + 5 for i in range(12)],
                       "taux_reussite": [40.0 + 4 * i for i in range(12)]})
    f, large = fiche.lire_fiche(df), fiche._long_to_wide(df)[0]
    props = [p for p in catalogue.proposer(f, large) if p["type"] == "bulles"]
    assert props and max(interet.noter(props, f, large)) >= 0.9


def test_notes_entre_0_et_1_et_stables():
    from hermes_da.adapters import catalogue, fiche, interet  # noqa: PLC0415
    df = _meme_forme(6, saison=.3)
    f, large = fiche.lire_fiche(df), fiche._long_to_wide(df)[0]
    props = catalogue.proposer(f, large)
    notes = interet.noter(props, f, large)
    assert len(notes) == len(props) and all(0 <= n <= 1 for n in notes)
    assert notes == interet.noter(props, f, large)
