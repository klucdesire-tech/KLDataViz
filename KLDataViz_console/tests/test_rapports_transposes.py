# -*- coding: utf-8 -*-
"""Rapports transposés (formulaire de collecte : indicateurs en lignes, un établissement par colonne, région et
district empilés au-dessus, période écrite en haut) : lus avec tout leur contexte, empilés période par période, et
leurs pièges signalés (période recopiée d'un autre trimestre, colonne de sous-total sans district, trimestre moins
couvert). Classeur entièrement fictif."""
import os
import sys

import openpyxl
import pandas as pd
import pytest

from _harness import APP, reset_app_modules

SITES = [("REGION A", "NORD", "CS Alpha"), ("REGION A", "NORD", "CS Beta"), ("REGION A", "SUD", "CS Gamma"),
         ("REGION B", "EST", "CS Delta"), ("REGION B", "EST", "CS Epsilon")]
AGES = ["<15 ans", "15-24 ans", "25+ ans"]


def _entete(ws, titre, periode_ecrite, sites, intitules=True):
    ws.cell(2, 2, titre)
    ws.cell(3, 2, f"Période de rapportage: {periode_ecrite}")
    ws.cell(5, 2, "Region Sanitaire")
    ws.cell(6, 2, "Districts Sanitaires" if intitules else None)
    ws.cell(7, 2, "Etablissements Sanitaires" if intitules else None)
    for j, (r, d, e) in enumerate(sites, start=3):
        ws.cell(5, j, r)
        ws.cell(6, j, d)
        ws.cell(7, j, e)


def _depistage(ws, periode_ecrite, sites, k):
    """Indicateur → Masculin/Féminin → âges ; le total vaut la somme des sexes, chaque sexe la somme de ses âges."""
    _entete(ws, "DONNEES DE DEPISTAGE", periode_ecrite, sites)
    r = 9
    for ind, base in (("Nombre de clients dépistés", 10), ("Nombre de clients dépistés positifs", 1)):
        ws.cell(r, 2, ind)
        lignes = []
        for sexe in ("Masculin", "Féminin"):
            lignes.append((sexe, None))
            for a, age in enumerate(AGES):
                lignes.append((age, (sexe, a)))
        vals = {}
        for j, _ in enumerate(sites, start=3):
            for sexe in ("Masculin", "Féminin"):
                for a in range(3):
                    vals[(j, sexe, a)] = base * (j + a + k) + (1 if sexe == "Féminin" else 0)
            ws.cell(r, j, sum(v for (jj, _, _), v in vals.items() if jj == j))
        for i, (lab, cle) in enumerate(lignes, start=1):
            ws.cell(r + i, 2, lab)
            for j, _ in enumerate(sites, start=3):
                if cle is None:
                    ws.cell(r + i, j, sum(vals[(j, lab, a)] for a in range(3)))
                else:
                    ws.cell(r + i, j, vals[(j, cle[0], cle[1])])
        r += len(lignes) + 1


def _ptme(ws, periode_ecrite, sites, k, intitules=True):
    """Sans ventilation, avec intertitres ; une colonne de sous-total sans district à la fin."""
    _entete(ws, "DONNEES PTME", periode_ecrite, sites, intitules)
    ws.cell(9, 2, "Dépistage en CPN")
    ws.cell(10, 2, "Nombre total de CPN 1")
    ws.cell(11, 2, "Nombre de femmes enceintes testées")
    ws.cell(13, 2, "Suivi des enfants exposés")
    ws.cell(14, 2, "Nombre d'enfants dépistés précocement")
    for j, _ in enumerate(sites, start=3):
        ws.cell(10, j, 20 + j + k)
        ws.cell(11, j, 15 + j + k)
        ws.cell(14, j, j)


def _classeur(path, couverture_faible=True):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    wb.create_sheet("Synthèse").cell(1, 1, "Tableau de synthèse")
    peu = SITES[:2] if couverture_faible else SITES
    _depistage(wb.create_sheet("Dépistage_fy26q1"), "fy26q1", peu, 0)
    _depistage(wb.create_sheet("Dépistage_fy26q2"), "FY25Q4", SITES, 1)          # période recopiée d'un autre trimestre
    _ptme(wb.create_sheet("PTME_fy26q1"), "FY26Q1", peu, 0, intitules=False)     # intitulés District / Établissement absents
    ws = wb.create_sheet("PTME_fy26q2")
    _ptme(ws, "FY26Q2", SITES + [("", "", "SOUS-TOTAL EST")], 1)               # colonne de sous-total sans district
    wb.save(path)
    return path


def _somme_brute(path, feuille):
    raw = pd.read_excel(path, sheet_name=feuille, header=None)
    return float(pd.to_numeric(raw.iloc[8:, 2:].stack(), errors="coerce").sum())


@pytest.fixture
def S(tmp_path, monkeypatch):
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import server
    monkeypatch.setattr(server.T, "uploads_dir", lambda: str(tmp_path / "depot"))
    os.makedirs(tmp_path / "depot", exist_ok=True)
    return server, _classeur(str(tmp_path / "collecte.xlsx"))


def test_periode_du_nom_de_feuille():
    reset_app_modules()
    import data_architect as da
    assert da.periode_normalisee("Traitement_fy26q3") == "FY26Q3"
    assert da.periode_normalisee("Période de rapportage: fy 26 q 1") == "FY26Q1"
    assert da.periode_normalisee("Rapport 2026-03") == "2026-03"
    assert da.periode_normalisee("Synthèse") is None


def test_feuilles_reconnues_et_regroupees(S):
    server, src = S
    import data_architect as da
    f = {x["nom"]: x for x in da.excel_sheets(src)}
    assert [n for n, x in f.items() if x["rapport"]] == ["Dépistage_fy26q1", "Dépistage_fy26q2", "PTME_fy26q1", "PTME_fy26q2"]
    # regroupées par leurs indicateurs, malgré des établissements différents d'un trimestre à l'autre
    assert f["Dépistage_fy26q1"]["groupe"] == f["Dépistage_fy26q2"]["groupe"] == "Dépistage_fy26q1"
    assert f["PTME_fy26q1"]["groupe"] == f["PTME_fy26q2"]["groupe"] == "PTME_fy26q1"


def test_rapport_lu_avec_son_contexte(S):
    server, src = S
    df, err = server._read_df(src, sheet="Dépistage_fy26q2", recette=False)
    assert err is None
    assert list(df.columns) == ["periode", "region", "district", "indicateur", "niveau", "sexe", "tranche_age", "site", "valeur"]
    assert set(df["periode"]) == {"FY26Q2"}                                      # le nom de la feuille fait foi
    assert df.attrs["rapport"] == [{"cle": "periode_contradictoire", "feuille": "Dépistage_fy26q2",
                                    "periode": "FY26Q2", "ecrite": "FY25Q4"}]
    assert df.groupby("site")["district"].first().to_dict() == {s[2]: s[1] for s in SITES}
    assert df["valeur"].sum() == _somme_brute(src, "Dépistage_fy26q2")         # rien de perdu, rien en double
    assert server.report_checks(df) == []                                        # totaux = somme des sexes et des âges


def test_rapport_sans_ventilation_avec_intertitres(S):
    server, src = S
    df, _ = server._read_df(src, sheet="PTME_fy26q1", recette=False)
    assert "section" in df.columns and set(df["section"]) == {"Dépistage en CPN", "Suivi des enfants exposés"}
    assert set(df.loc[df["indicateur"] == "Nombre d'enfants dépistés précocement", "section"]) == {"Suivi des enfants exposés"}
    assert set(df["district"]) == {"NORD"}                                       # lignes sans intitulé : par position
    assert df["valeur"].sum() == _somme_brute(src, "PTME_fy26q1")


def test_colonne_de_sous_total_sans_district_signalee(S):
    server, src = S
    df, _ = server._read_df(src, sheet="PTME_fy26q2", recette=False)
    assert df.attrs["rapport"] == [{"cle": "colonne_sans_niveau", "feuille": "PTME_fy26q2", "niveau": "district",
                                    "sites": ["SOUS-TOTAL EST"], "n": 1}]


def test_empilement_par_volet_avec_constats(S):
    server, src = S
    sorties = {}
    for volet in ("Dépistage", "PTME"):
        r = server.sheets_extract(src, [f"{volet}_fy26q1", f"{volet}_fy26q2"], stack=True)
        sorties[volet] = r["fichiers"][0]["path"]
    assert sorties["Dépistage"] != sorties["PTME"]                               # deux empilements, deux fichiers
    df, _ = server._read_df(sorties["Dépistage"], recette=False)
    assert sorted(df["periode"].unique()) == ["FY26Q1", "FY26Q2"]
    assert df["valeur"].sum() == _somme_brute(src, "Dépistage_fy26q1") + _somme_brute(src, "Dépistage_fy26q2")
    cles = [n["cle"] for n in df.attrs["rapport"]]
    assert cles == ["periode_contradictoire", "couverture_variable"]
    bilan = server._bilan_qualite(df)
    titres = [c["titre"] for d in bilan["dimensions"] for c in d["constats"]]
    assert "1 feuille(s) : la période écrite ne correspond pas au nom de la feuille" in titres
    assert "FY26Q1 : 2 site(s) déclarant(s), contre 5 au mieux" in titres


def test_constats_d_un_ancien_empilement_effaces(S):
    server, src = S
    p = server.sheets_extract(src, ["Dépistage_fy26q1", "Dépistage_fy26q2"], stack=True)["fichiers"][0]["path"]
    assert os.path.exists(p + server.NOTES_RAPPORT_SUFFIX)
    propre = _classeur(src, couverture_faible=False)
    wb = openpyxl.load_workbook(propre)
    wb["Dépistage_fy26q2"].cell(3, 2, "Période de rapportage: FY26Q2")
    wb.save(propre)
    server.sheets_extract(propre, ["Dépistage_fy26q1", "Dépistage_fy26q2"], stack=True)
    assert not os.path.exists(p + server.NOTES_RAPPORT_SUFFIX)


def test_graphiques_par_periode_district_et_taux_ecartes(S):
    server, src = S
    p = server.sheets_extract(src, ["Dépistage_fy26q1", "Dépistage_fy26q2"], stack=True)["fichiers"][0]["path"]
    df, _ = server._read_df(p, recette=False)
    taux = df[df["niveau"] == "total"].head(5).assign(indicateur="Taux de positivité", valeur=0.5)
    charts = server._report_charts(pd.concat([df, taux], ignore_index=True))
    assert charts[0]["title"] == "Nombre de clients dépistés — par période"
    assert charts[0]["labels"] == ["FY26Q1", "FY26Q2"]
    attendu = [df[(df["niveau"] == "total") & (df["indicateur"] == "Nombre de clients dépistés") & (df["periode"] == p)]
               ["valeur"].sum() for p in ("FY26Q1", "FY26Q2")]
    assert charts[0]["values"][0] == attendu
    assert all(c["subtitle"].endswith("FY26Q2") for c in charts[1:])              # jamais une somme de trimestres
    assert not any("Taux" in str(c["labels"]) or "Taux" in c["title"] for c in charts)
    par_district = next(c for c in charts if c["title"].endswith("par district"))
    assert set(par_district["labels"]) == {"NORD", "SUD", "EST"}


def test_bilan_rapport_total_niveau_et_vrai_ecart(S):
    """« Total » d'un rapport remis à plat est un niveau : pas d'alerte « lignes de total mêlées » ; un total qui ne vaut
    pas la somme de ses sexes reste un constat grave."""
    server, src = S
    df, _ = server._read_df(src, sheet="Dépistage_fy26q2", recette=False)
    cles = [c["cle"] for d in server._bilan_qualite(df)["dimensions"] for c in d["constats"]]
    assert "ligne_total" not in cles and "total_faux" not in cles
    wb = openpyxl.load_workbook(src)
    wb["Dépistage_fy26q2"].cell(9, 3, 1)                                        # total faux pour CS Alpha
    wb.save(src)
    df, _ = server._read_df(src, sheet="Dépistage_fy26q2", recette=False)
    graves = [c["titre"] for d in server._bilan_qualite(df)["dimensions"] for c in d["constats"] if c["gravite"] == "grave"]
    assert any("différent(s) de la somme de leurs désagrégations" in t for t in graves)


def test_ordre_des_periodes():
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import server
    assert sorted(["FY26Q1", "FY25Q4", "FY25Q2", "FY26Q3"], key=server.cle_periode) == ["FY25Q2", "FY25Q4", "FY26Q1", "FY26Q3"]
    assert sorted(["Mars 2026", "Janvier 2026", "Décembre 2025"], key=server.cle_periode) == ["Décembre 2025", "Janvier 2026", "Mars 2026"]


def test_indicateur_sans_valeur_n_est_pas_un_intertitre():
    """« Nombre de… » sans aucune valeur reste un indicateur (vide), jamais une section pour les suivants."""
    reset_app_modules()
    import data_architect as da
    df = pd.DataFrame({"libelle": ["Nombre de visites", "Nombre de clients", "Masculin", "Féminin"],
                       "CS Alpha": [None, 3, 1, 2], "CS Beta": [None, 5, 2, 3]})
    out = da.tidy_report(df)
    assert "section" not in out.columns and set(out["indicateur"]) == {"Nombre de clients"}
