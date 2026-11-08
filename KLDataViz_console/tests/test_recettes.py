# -*- coding: utf-8 -*-
"""Recettes de transformation (`recettes.py`) à réponse connue : des classeurs Excel fictifs mal formés, comme ceux du
terrain (titre au-dessus du tableau, cellules fusionnées, mois en colonnes, sous-totaux, « District - Centre » dans
une seule colonne). La recette proposée doit produire exactement la table attendue, être mémorisée, puis rejouée sur
le fichier du mois suivant ; si le fichier change de forme, elle doit le dire."""
import os
import sys

import pandas as pd
import pytest
from openpyxl import Workbook

from _harness import APP, reset_app_modules

MOIS = ["Janv.", "Févr.", "Mars", "Avr."]
CENTRES = {"Abobo": ["CSU Avocatier", "CSU Baoulé"], "Cocody": ["CSU Angré", "CSU Riviera", "CSU Blockhaus"]}


def _valeur(district, centre, m, decalage=0):
    return 100 + 10 * list(CENTRES).index(district) + 3 * CENTRES[district].index(centre) + m + decalage


def _classeur(chemin, decalage=0, mois=MOIS):
    """Rapport mensuel tel qu'on le reçoit : titre, ligne vide, en-tête, district fusionné, sous-total par district,
    total général."""
    wb = Workbook()
    ws = wb.active
    ws.append(["RAPPORT MENSUEL DES CONSULTATIONS — RÉGION FICTIVE"])
    ws.append([])
    ws.append(["District", "Centre"] + mois)
    for d, centres in CENTRES.items():
        debut = ws.max_row + 1
        for c in centres:
            ws.append([d, c] + [_valeur(d, c, m, decalage) for m in range(len(mois))])
        ws.merge_cells(start_row=debut, start_column=1, end_row=debut + len(centres) - 1, end_column=1)
        ws.append([None, f"Sous-total {d}"] + [sum(_valeur(d, c, m, decalage) for c in centres) for m in range(len(mois))])
    ws.append(["Total général", None] + [sum(_valeur(d, c, m, decalage) for d in CENTRES for c in CENTRES[d])
                                         for m in range(len(mois))])
    wb.save(chemin)
    return chemin


def _attendu(decalage=0, mois=MOIS):
    return pd.DataFrame([{"District": d, "Centre": c, "periode": mo, "valeur": _valeur(d, c, m, decalage)}
                         for d, centres in CENTRES.items() for c in centres for m, mo in enumerate(mois)])


@pytest.fixture
def R(tmp_path, monkeypatch):
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import recettes
    import server
    monkeypatch.setattr(server, "_dossier_recettes", lambda: str(tmp_path / "recettes"), raising=False)
    return recettes, server


def _comme_attendu(df, attendu):
    a = df[list(attendu.columns)].sort_values(["District", "Centre", "periode"]).reset_index(drop=True)
    b = attendu.sort_values(["District", "Centre", "periode"]).reset_index(drop=True)
    a["valeur"] = pd.to_numeric(a["valeur"])
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_recette_proposee_donne_la_table_attendue(R, tmp_path):
    recettes, server = R
    brut, _ = server._read_df_raw(_classeur(str(tmp_path / "mars.xlsx")))
    etapes = recettes.proposer(brut)
    assert [e["op"] for e in etapes] == ["recopier_bas", "retirer_lignes", "depivoter"], etapes
    df, journal = recettes.appliquer(brut, etapes)
    _comme_attendu(df, _attendu())
    assert journal[0]["libelle"].startswith("Recopier vers le bas « District »")
    assert journal[1]["touches"] == 3                                     # 2 sous-totaux et le total général
    assert journal[2]["lignes_apres"] == 5 * len(MOIS)


def test_recette_memorisee_rejouee_le_mois_suivant(R, tmp_path):
    recettes, server = R
    dossier = str(tmp_path / "recettes")
    brut, _ = server._read_df_raw(_classeur(str(tmp_path / "mars.xlsx")))
    recettes.enregistrer(dossier, brut, recettes.proposer(brut), "mars.xlsx")
    avril, _ = server._read_df_raw(_classeur(str(tmp_path / "avril.xlsx"), decalage=7))
    df = recettes.rejouer(dossier, avril)
    _comme_attendu(df, _attendu(decalage=7))
    assert df.attrs["recette"]["fichier_modele"] == "mars.xlsx" and len(df.attrs["recette"]["etapes"]) == 3


def test_lecture_du_serveur_rejoue_la_recette(R, tmp_path):
    """Toute lecture (analyse, tableau de bord, source actualisée, rapport planifié) passe par _read_df."""
    recettes, server = R
    brut, _ = server._read_df_raw(_classeur(str(tmp_path / "mars.xlsx")))
    recettes.enregistrer(server._dossier_recettes(), brut, recettes.proposer(brut), "mars.xlsx")
    df, err = server._read_df(_classeur(str(tmp_path / "mai.xlsx"), decalage=3))
    assert err is None
    _comme_attendu(df, _attendu(decalage=3))


def test_fichier_qui_change_de_forme_le_dit(R, tmp_path):
    """Une colonne de moins (mois manquant) : la recette ne s'applique plus ; le fichier est rendu tel quel, avec
    l'erreur, jamais à moitié transformé."""
    recettes, server = R
    dossier = str(tmp_path / "recettes")
    brut, _ = server._read_df_raw(_classeur(str(tmp_path / "mars.xlsx")))
    etapes = recettes.proposer(brut)
    recettes.enregistrer(dossier, brut, etapes, "mars.xlsx")
    # même colonnes, mais une étape qui vise une colonne renommée ensuite : on force le cas
    autre = brut.rename(columns={"Mars": "Mars 2025"})
    assert recettes.signature(autre) != recettes.signature(brut)       # autre forme : pas de recette rejouée
    assert recettes.rejouer(dossier, autre) is autre and "recette" not in autre.attrs
    cassee = [dict(etapes[0], colonnes=["Région"])]
    with pytest.raises(recettes.Erreur, match="colonne\\(s\\) absente\\(s\\).*Région"):
        recettes.appliquer(brut, cassee)


@pytest.mark.parametrize("etape, attendu", [
    ({"op": "fractionner", "colonne": "lieu", "separateur": " - ", "noms": ["District", "Centre"]},
     {"District": ["Abobo", "Cocody"], "Centre": ["CSU A", "CSU B"]}),
    ({"op": "renommer", "colonnes": {"lieu": "Lieu"}}, {"Lieu": ["Abobo - CSU A", "Cocody - CSU B"]}),
    ({"op": "remplacer", "colonne": "lieu", "remplacements": {"Abobo - CSU A": "A"}}, {"lieu": ["A", "Cocody - CSU B"]}),
    ({"op": "convertir", "colonne": "montant", "type": "nombre"}, {"montant": [1234.5, 12500.0]}),
    ({"op": "garder_lignes", "colonne": "lieu", "valeurs": ["Cocody - CSU B"]}, {"lieu": ["Cocody - CSU B"]}),
    ({"op": "retirer_colonnes", "colonnes": ["montant"]}, {"lieu": ["Abobo - CSU A", "Cocody - CSU B"]}),
    ({"op": "regrouper", "par": ["lieu"], "mesures": {"n": "somme"}}, {"lieu": ["Abobo - CSU A", "Cocody - CSU B"], "n": [3, 4]}),
])
def test_chaque_operation(R, etape, attendu):
    recettes, _ = R
    df = pd.DataFrame({"lieu": ["Abobo - CSU A", "Cocody - CSU B"], "montant": ["1 234,5", "12 500 FCFA"], "n": [3, 4]})
    out, journal = recettes.appliquer(df, [etape])
    for col, vals in attendu.items():
        assert out[col].tolist() == vals, (col, out[col].tolist())
    assert journal[0]["libelle"]


def test_proposition_fractionner_district_centre(R):
    recettes, _ = R
    df = pd.DataFrame({"District - Centre": ["Abobo - CSU A", "Abobo - CSU B", "Cocody - CSU C"], "cas": [1, 2, 3]})
    assert recettes.proposer(df) == [{"op": "fractionner", "colonne": "District - Centre", "separateur": " - ",
                                      "noms": ["District", "Centre"]}]


def test_fichier_propre_aucune_proposition(R):
    recettes, _ = R
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import jeux_reference as J  # noqa: PLC0415
    for nom in ("sante", "ventes", "sans_dates", "simple"):
        assert recettes.proposer(J.JEUX[nom]()) == [], nom


# ---------------------------------------------------------------- API : proposer, essayer, enregistrer, supprimer
def test_api_de_bout_en_bout(R, tmp_path):
    recettes, server = R
    src = _classeur(str(tmp_path / "mars.xlsx"))
    prop = server.recette_action("proposer", {"file_path": src})
    assert [e["op"] for e in prop["etapes"]] == ["recopier_bas", "retirer_lignes", "depivoter"]
    assert prop["avant"]["colonnes"][:2] == ["District", "Centre"] and prop["apres"]["total"] == 20
    assert not prop["memorisee"] and prop["qualite"]["note"] >= 90

    # l'utilisateur retire une étape et essaie : les lignes de total restent, le bilan le voit
    essai = server.recette_action("essayer", {"file_path": src, "etapes": [prop["etapes"][0], prop["etapes"][2]]})
    assert essai["apres"]["total"] == 32 and any(c["cle"] in ("ligne_total", "total_faux")
                                                  for d in essai["qualite"]["dimensions"] for c in d["constats"])
    # une étape impossible : l'erreur, et le fichier tel qu'il arrive
    mauvais = server.recette_action("essayer", {"file_path": src, "etapes": [{"op": "recopier_bas", "colonnes": ["Région"]}]})
    assert "Région" in mauvais["error"] and mauvais["avant"]["total"] == 8

    assert server.recette_action("enregistrer", {"file_path": src, "etapes": prop["etapes"]})["memorisee"]
    avril = _classeur(str(tmp_path / "avril.xlsx"), decalage=2)
    assert server.recette_action("proposer", {"file_path": avril})["memorisee"]       # même forme : recette retrouvée
    df, _ = server._read_df(avril)
    _comme_attendu(df, _attendu(decalage=2))
    q = server._bilan_qualite(df)
    assert any(c["cle"] == "recette" for d in q["dimensions"] for c in d["constats"])

    assert server.recette_action("supprimer", {"file_path": src})["supprimee"]
    df, _ = server._read_df(avril)
    assert "recette" not in df.attrs and len(df) == 8                                   # de nouveau tel qu'il arrive


def test_recette_qui_ne_s_applique_plus_signalee_grave(R, tmp_path):
    recettes, server = R
    src = _classeur(str(tmp_path / "mars.xlsx"))
    brut, _ = server._read_df(src, recette=False)
    recettes.enregistrer(server._dossier_recettes(), brut, [{"op": "recopier_bas", "colonnes": ["District"]}], "mars.xlsx")
    # même forme, mais l'étape enregistrée est faussée à la main (fichier de recettes modifié, version ancienne…)
    import json
    chemin = os.path.join(server._dossier_recettes(), "recettes.json")
    tout = json.load(open(chemin, encoding="utf-8"))
    for r in tout.values():
        r["etapes"][0]["colonnes"] = ["Région"]
    json.dump(tout, open(chemin, "w", encoding="utf-8"))
    df, _ = server._read_df(src)
    assert "Région" in df.attrs["recette_erreur"] and len(df) == 8
    q = server._bilan_qualite(df)
    c = next(c for d in q["dimensions"] for c in d["constats"] if c["cle"] == "recette_erreur")
    assert c["gravite"] == "grave"
