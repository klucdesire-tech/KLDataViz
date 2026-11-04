# -*- coding: utf-8 -*-
"""Un nom qui finit par un chiffre ne se colle pas à sa valeur (« Korhogo 2 755,5 » se lisait 2 755,5). Données fictives."""
import pandas as pd

from _harness import reset_app_modules


def _server():
    reset_app_modules()
    import server
    return server


def _districts():
    return pd.DataFrame({"district": ["Korhogo 1", "Korhogo 2", "Korhogo 12", "Soubré", "Méagui"] * 2,
                         "periode": ["Août 2025"] * 5 + ["Septembre 2025"] * 5,
                         "code": ["D-01", "D-02", "D-12", "D-03", "D-04"] * 2, "valeur": range(10)})


def test_noms_reperes():
    s = _server()
    noms = s._noms_finissant_par_chiffre(_districts())
    assert set(noms) >= {"Korhogo 1", "Korhogo 2", "Korhogo 12", "Août 2025", "D-01"}  # un code avec des lettres compte aussi
    assert "Soubré" not in noms
    assert [len(n) for n in noms] == sorted((len(n) for n in noms), reverse=True)  # les plus longs d'abord


def test_separation_dans_la_reponse():
    s = _server()
    noms = s._noms_finissant_par_chiffre(_districts())
    texte = "Soubré 1 102 > Guéyo 833,5 > Korhogo 2 755,5 > Korhogo 1 452 > Korhogo 12 300 > Méagui 327."
    sortie = s._separer_noms_chiffres(texte, noms)
    assert "Korhogo 2 : 755,5" in sortie and "Korhogo 1 : 452" in sortie and "Korhogo 12 : 300" in sortie
    assert "Soubré 1 102" in sortie and "Méagui 327" in sortie   # les autres noms ne bougent pas
    assert s._separer_noms_chiffres(sortie, noms) == sortie        # rien de plus au second passage


def test_rien_a_changer():
    s = _server()
    noms = s._noms_finissant_par_chiffre(_districts())
    for texte in ("Korhogo 2 : 755,5", "Korhogo 2 (755,5)", "Korhogo 1 et Korhogo 2 sont proches", "Korhogo 2, 755 cas",
                  "Korhogo 21 est un autre district"):
        assert s._separer_noms_chiffres(texte, noms) == texte
    assert s._separer_noms_chiffres("texte", []) == "texte" and s._noms_finissant_par_chiffre(None) == []


def test_colonne_trop_variee_ignoree():
    s = _server()
    df = pd.DataFrame({"id": [f"Patient {i}" for i in range(3000)]})
    assert s._noms_finissant_par_chiffre(df) == []


def test_consigne_donnee_a_adifa():
    s = _server()
    assert "Korhogo 2 : 755,5" in s.CHAT_METHODE
