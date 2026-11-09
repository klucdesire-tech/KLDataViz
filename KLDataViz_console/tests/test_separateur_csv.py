# -*- coding: utf-8 -*-
"""Choix du séparateur d'un CSV : celui qui donne le même nombre de colonnes sur les premières lignes.

Défaut corrigé : le séparateur était deviné sur la seule ligne d'en-têtes. Un export DHIS2 séparé par « ; » dont les
titres contiennent « Masculin, 15-24 ans » était lu avec la virgule : titres coupés au hasard, chiffres décalés.
Fichiers entièrement fictifs."""
import itertools
import os
import sys

import pytest

from _harness import APP, reset_app_modules


@pytest.fixture
def DA():
    reset_app_modules()
    import data_architect
    return data_architect


def _ecrire(tmp_path, nom, texte, enc="utf-8"):
    p = tmp_path / nom
    p.write_bytes(texte.encode(enc))
    return p


def _dhis2_point_virgule():
    titres = [f"VIH - Dépistés {s}, {a} Janvier 2026" for s, a in itertools.product(["Masculin", "Féminin"], ["15-24 ans", "25-49 ans"])]
    lignes = [";".join(["Unité d'organisation"] + titres)]
    for i, u in enumerate(["CS Alpha", "CS Beta", "CS Gamma"]):
        lignes.append(";".join([u] + [str(10 * i + j) for j in range(len(titres))]))
    return "\n".join(lignes) + "\n"


@pytest.mark.parametrize("nom, texte, attendu", [
    ("dhis2.csv", _dhis2_point_virgule(), ";"),
    ("classique.csv", "district,mois,depistes\nNord,2026-01,120\nSud,2026-01,98\n", ","),
    ("decimales.csv", "district;taux\nNord;12,5\nSud;8,25\nEst;3,0\n", ";"),
    ("tab.tsv", "district\tvaleur\nNord\t1,5\nSud\t2,5\n", "\t"),
    ("guillemets.csv", 'centre,commentaire,valeur\nA,"vu ; revu, noté",3\nB,"ok ; ok",4\n', ","),
    ("barre.csv", "a|b|c\n1|2|3\n4|5|6\n", "|"),
])
def test_separateur_constant_sur_les_lignes(DA, tmp_path, nom, texte, attendu):
    enc, sep = DA._sniff_text(_ecrire(tmp_path, nom, texte))
    assert sep == attendu


def test_une_seule_colonne_garde_l_ancien_comportement(DA, tmp_path):
    """Rien de net (une seule colonne) : retour au détecteur d'avant, sans erreur."""
    enc, sep = DA._sniff_text(_ecrire(tmp_path, "liste.csv", "nom\nAlpha\nBeta\n"))
    assert sep in ";,\t|"


def test_dhis2_lu_de_bout_en_bout(tmp_path):
    """Par le serveur : les 4 titres entiers, 3 centres, et les chiffres à leur place."""
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import server  # noqa: PLC0415
    p = _ecrire(tmp_path, "export.csv", _dhis2_point_virgule())
    lu = server._read_df(str(p), recette=False)
    df = lu[0] if isinstance(lu, tuple) else lu
    assert list(df.columns)[1:] == [f"VIH - Dépistés {s}, {a} Janvier 2026" for s, a in
                                    itertools.product(["Masculin", "Féminin"], ["15-24 ans", "25-49 ans"])]
    assert df.shape == (3, 5)
    assert df.iloc[2, 1:].tolist() == [20, 21, 22, 23]
