# -*- coding: utf-8 -*-
"""Fiche d'un fichier : comparée à la fiche écrite à la main pour les six jeux, puis sur les pièges connus."""
import os
import sys

import pandas as pd
import pytest

from _harness import APP, reset_app_modules

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jeux_reference as J  # noqa: E402


@pytest.fixture
def lire():
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters.fiche import lire_fiche  # noqa: PLC0415
    return lire_fiche


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
@pytest.mark.parametrize("rubrique", ["lignes", "lieux", "hierarchie", "categories", "objectifs", "volumes", "moyennes", "taux",
                                      "stocks", "oui_non", "identifiants"])
def test_fiche_attendue(lire, jeu, rubrique):
    f, att = lire(J.JEUX[jeu]()), J.ATTENDU[jeu][rubrique]
    assert (sorted(f[rubrique]) if isinstance(att, list) and rubrique != "lieux" else f[rubrique]) == \
        (sorted(att) if isinstance(att, list) and rubrique != "lieux" else att)


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_periode_attendue(lire, jeu):
    p = lire(J.JEUX[jeu]())["periode"]
    assert (p or {}).get("colonne") == J.ATTENDU[jeu]["periode"]


def test_capacites_differentes_selon_le_fichier(lire):
    v = lire(J.JEUX["ventes"]())["capacites"]
    assert v["objectif"] and v["comparaison_n1"] and v["relation"] and v["part_du_total"]
    s = lire(J.JEUX["simple"]())["capacites"]
    assert not s["evolution"] and not s["objectif"] and not s["relation"] and s["part_du_total"]
    d = lire(J.JEUX["sans_dates"]())["capacites"]
    assert not d["evolution"] and not d["saisonnalite"] and d["geographie"]
    z = lire(J.JEUX["sante"]())["capacites"]
    assert z["saisonnalite"] and z["structure"] and not z["objectif"]


def test_format_long_reconnu(lire):
    f = lire(J.JEUX["dhis2_long"]())
    assert f["format"] == "long" and f["agrege"] is True


def test_moins_d_un_an_pas_de_comparaison(lire):
    df = pd.DataFrame({"mois": [f"2025-{m:02d}" for m in range(1, 7)] * 2, "zone": ["A"] * 6 + ["B"] * 6, "ventes": range(12)})
    c = lire(df)["capacites"]
    assert c["evolution"] and not c["comparaison_n1"] and not c["saisonnalite"]


@pytest.mark.parametrize("valeurs,unite", [(["janv. 2025", "févr. 2025", "mars 2025"], "mois"),
                                           (["15/01/2025", "16/01/2025", "20/02/2025"], "jour"),
                                           (["2025-01", "2025-02", "2025-03"], "mois")])
def test_formats_de_periode(lire, valeurs, unite):
    p = lire(pd.DataFrame({"periode": valeurs, "cas": [1, 2, 3]}))["periode"]
    assert p and p["unite"] == unite and p["debut"].startswith("2025-01")


def test_annee_chiffree_est_un_axe_pas_une_mesure(lire):
    f = lire(pd.DataFrame({"annee": [2023, 2024, 2025], "ventes": [10, 20, 30]}))
    assert f["periode"]["colonne"] == "annee" and f["periode"]["unite"] == "annee" and f["volumes"] == ["ventes"]


def test_code_chiffre_jamais_une_mesure(lire):
    f = lire(pd.DataFrame({"code_site": [101, 102, 103, 104], "cas": [1, 2, 3, 4]}))
    assert f["identifiants"] == ["code_site"] and "code_site" not in f["volumes"] + f["moyennes"]


def test_taux_jamais_additionne(lire):
    f = lire(pd.DataFrame({"district": ["A", "B", "C"], "taux_couverture": [80.0, 70.5, 60.0], "ratio": [.2, .5, .7]}))
    assert sorted(f["taux"]) == ["ratio", "taux_couverture"] and not f["capacites"]["part_du_total"]


def test_signe_pourcent_dans_le_nom(lire):
    f = lire(pd.DataFrame({"district": ["A", "B", "C"], "couverture (%)": [80, 70, 60]}))
    assert f["taux"] == ["couverture (%)"]


def test_fichier_vide_a_des_capacites(lire):
    assert lire(pd.DataFrame())["capacites"]["evolution"] is False


def test_prix_unitaire_n_est_pas_un_volume(lire):
    f = lire(pd.DataFrame({"produit": ["a", "b", "c"], "prix_unitaire": [100, 200, 300], "quantite": [5, 6, 7]}))
    assert f["moyennes"] == ["prix_unitaire"] and f["volumes"] == ["quantite"]


def test_colonne_01_lue_comme_oui_non(lire):
    assert lire(pd.DataFrame({"statut": [0, 1, 1, 0]}))["oui_non"] == ["statut"]


def test_lieux_sans_hierarchie(lire):
    df = pd.DataFrame({"region": ["R1", "R2", "R1"], "ville": ["V1", "V1", "V2"], "cas": [1, 2, 3]})
    assert lire(df)["hierarchie"] is False


@pytest.mark.parametrize("df", [pd.DataFrame(), pd.DataFrame({"a": []}), pd.DataFrame({"vide": [None, None], "x": [None, None]}),
                                pd.DataFrame({0: [1, 2], 1: ["a", "b"]})])
def test_fichiers_limites_sans_erreur(lire, df):
    assert isinstance(lire(df), dict)


def test_colonnes_en_double(lire):
    df = pd.DataFrame([[1, 2], [3, 4]], columns=["cas", "cas"])
    assert lire(df)["volumes"] == ["cas"]


def test_fichier_inchange(lire):
    df = J.JEUX["ventes"]()
    avant = df.copy()
    lire(df)
    assert df.equals(avant)
