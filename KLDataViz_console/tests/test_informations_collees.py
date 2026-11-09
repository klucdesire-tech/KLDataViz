# -*- coding: utf-8 -*-
"""Informations collées (indicateur, période, sexe, tranche d'âge) dans un titre de colonne ou une cellule : reconnues,
séparées en colonnes, sans jamais changer un total ni compter deux fois un sous-total. Fichiers entièrement fictifs,
de la forme des exports DHIS2 (tableau croisé, export en lignes, ensemble de valeurs)."""
import itertools
import os
import sys

import pandas as pd
import pytest

from _harness import APP, reset_app_modules

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)


@pytest.fixture
def R():
    reset_app_modules()
    import dimensions
    import recettes
    return recettes, dimensions


@pytest.mark.parametrize("texte, reste, dims", [
    ("SIG - Nombre de clients dépistés positif Janvier - Juin 2026 15-19 ans", "SIG - Nombre de clients dépistés positif",
     {"periode": "Janvier - Juin 2026", "tranche_age": "15-19 ans"}),
    ("VIH - Dépistés Masculin, 15-24 ans", "VIH - Dépistés", {"sexe": "Masculin", "tranche_age": "15-24 ans"}),
    ("Masculin, 15-24 ans", "", {"sexe": "Masculin", "tranche_age": "15-24 ans"}),
    ("Féminin, <15y", "", {"sexe": "Féminin", "tranche_age": "<15y"}),
    ("HTS_TST 2026Q1", "HTS_TST", {"periode": "2026Q1"}),
    ("Consultations 202601", "Consultations", {"periode": "202601"}),
    ("PVVIH sous ARV Hommes 50 ans et plus T2 2026", "PVVIH sous ARV",
     {"sexe": "Hommes", "tranche_age": "50 ans et plus", "periode": "T2 2026"}),
    ("Nouveaux cas 0-11 mois Mars 2026", "Nouveaux cas", {"tranche_age": "0-11 mois", "periode": "Mars 2026"}),
    ("Taux de couverture (%) 2024", "Taux de couverture (%)", {"periode": "2024"}),
    ("Semaine 2026W05 cas de paludisme", "cas de paludisme", {"periode": "Semaine 2026W05"}),
    # une population, pas un sexe ; des libellés sans information : laissés tels quels
    ("Femmes enceintes dépistées 2025", "Femmes enceintes dépistées", {"periode": "2025"}),
    ("Femme allaitante", "Femme allaitante", {}),
    ("District sanitaire", "District sanitaire", {}),
    ("Code 1234", "Code 1234", {}),
    ("15-19", "15-19", {}),
])
def test_reconnaissance(R, texte, reste, dims):
    _, dimensions = R
    assert dimensions.decouper(texte) == (reste, dims)


SITES = ["CS Alpha", "CS Beta", "CS Gamma"]
SEXES, AGES = ["Masculin", "Féminin"], ["15-24 ans", "25-49 ans"]


def _croise():
    """Tableau croisé DHIS2 fictif : une colonne par indicateur × sexe × âge × mois, des cases vides."""
    cols = [f"{i} {s}, {a} {m}" for i, s, a, m in itertools.product(["VIH - Dépistés", "VIH - Positifs"], SEXES, AGES,
                                                                    ["Janvier 2026", "Février 2026"])]
    lignes = []
    for k, u in enumerate(SITES):
        lignes.append({"Unité d'organisation": u, **{c: (None if (k + j) % 5 == 0 else k * 100 + j) for j, c in enumerate(cols)}})
    return pd.DataFrame(lignes), cols


def test_decomposer_un_tableau_croise(R):
    recettes, _ = R
    df, cols = _croise()
    out, journal = recettes.appliquer(df, [{"op": "decomposer", "colonnes": cols, "valeur": "Valeur"}])
    assert list(out.columns) == ["Unité d'organisation", "Indicateur", "Période", "Sexe", "Tranche d'âge", "Valeur"]
    assert len(out) == int(df[cols].notna().sum().sum())               # une ligne par case remplie, les vides écartés
    assert out["Valeur"].sum() == df[cols].sum().sum()                   # le total ne change pas
    ligne = out[(out["Unité d'organisation"] == "CS Beta") & (out["Indicateur"] == "VIH - Positifs")
                & (out["Sexe"] == "Féminin") & (out["Tranche d'âge"] == "25-49 ans") & (out["Période"] == "Février 2026")]
    assert ligne["Valeur"].tolist() == [df.loc[1, "VIH - Positifs Féminin, 25-49 ans Février 2026"]]
    assert journal[0]["lignes_avant"] == 3 and "info" not in journal[0]


def test_sous_totaux_ecartes_et_verifies(R):
    """Détail sexe × âge, sous-totaux par sexe et total : seuls les détails restent (sinon tout compte deux fois) ;
    un sous-total du fichier qui ne vaut pas la somme de son détail est signalé."""
    recettes, _ = R
    detail = {f"Dépistés {s} {a}": [10 + k, 20 + k, 30 + k] for k, (s, a) in enumerate(itertools.product(SEXES, AGES))}
    df = pd.DataFrame({"Centre": SITES, **detail, "Dépistés Masculin": [21, 41, 61], "Dépistés Féminin": [25, 45, 66],
                       "Dépistés": [46, 86, 126]})
    etapes = recettes.proposer(df)
    assert [e["op"] for e in etapes] == ["decomposer"]
    assert set(etapes[0]["colonnes"]) == set(df.columns) - {"Centre"}   # le total « Dépistés » est rattaché
    out, journal = recettes.appliquer(df, etapes)
    assert list(out.columns) == ["Centre", "Sexe", "Tranche d'âge", "Dépistés"]   # un indicateur : la colonne porte son nom
    assert len(out) == 12 and out["Dépistés"].sum() == sum(sum(v) for v in detail.values())
    assert "3 colonne(s) de sous-total" in journal[0]["info"]
    assert journal[0]["avertissement"].startswith("1 sous-total")   # Féminin, CS Gamma : 66 au lieu de 65


def test_ventilations_non_croisees_jamais_additionnees(R):
    recettes, _ = R
    df = pd.DataFrame({"Centre": SITES, "Positifs Masculin": [1, 2, 3], "Positifs Féminin": [4, 5, 6],
                       "Positifs 15-24 ans": [2, 3, 4], "Positifs 25-49 ans": [3, 4, 5]})
    out, _ = recettes.appliquer(df, recettes.proposer(df))
    assert sorted(out["Indicateur"].unique()) == ["Positifs (par sexe)", "Positifs (par âge)"]
    par = out.groupby("Indicateur")["Valeur"].sum()
    assert par["Positifs (par sexe)"] == 21 and par["Positifs (par âge)"] == 21   # deux indicateurs, même total


def test_extraire_une_colonne_concatenee(R):
    recettes, _ = R
    df = pd.DataFrame([{"Données": f"VIH - {i} {s}, {a}", "Période": "Janvier 2026", "Unité d'organisation": u, "Valeur": 1}
                       for i, s, a, u in itertools.product(["Dépistés", "Positifs"], SEXES, AGES, SITES)])
    etapes = recettes.proposer(df)
    assert etapes == [{"op": "extraire", "colonne": "Données"}]          # pas de « VIH » / « Dépistés » fractionnés
    out, _ = recettes.appliquer(df, etapes)
    assert list(out.columns) == ["Indicateur", "Sexe", "Tranche d'âge", "Période", "Unité d'organisation", "Valeur"]
    assert sorted(out["Indicateur"].unique()) == ["VIH - Dépistés", "VIH - Positifs"]


def test_extraire_categories_dhis2_avec_default(R):
    """« default » : un élément de données sans ventilation ; ni indicateur, ni sous-total."""
    recettes, _ = R
    df = pd.DataFrame([{"dataelement": "Dépistés", "categoryoptioncombo": f"{s}, {a}", "orgunit": u, "value": 2}
                       for s, a, u in itertools.product(SEXES, AGES, SITES)]
                      + [{"dataelement": "Consultations", "categoryoptioncombo": "default", "orgunit": u, "value": 5}
                         for u in SITES])
    out, _ = recettes.appliquer(df, recettes.proposer(df))
    assert list(out.columns) == ["dataelement", "Sexe", "Tranche d'âge", "orgunit", "value"]
    consult = out[out["dataelement"] == "Consultations"]
    assert set(consult["Sexe"]) == {"Non ventilé"} and consult["value"].sum() == 15


def test_extraire_refuse_details_et_sous_totaux_melanges(R):
    recettes, _ = R
    df = pd.DataFrame({"categorie": ["Masculin, 15-24 ans", "Masculin, 25-49 ans", "Masculin"], "valeur": [1, 2, 3]})
    with pytest.raises(recettes.Erreur, match="sous-totaux"):
        recettes.appliquer(df, [{"op": "extraire", "colonne": "categorie"}])
    assert recettes.proposer(df) == []                                   # donc jamais proposée


def _export_dhis2():
    """Forme du fichier « data (33) » : niveaux orgunitlevel, nom recopié, espace en trop, semestres × âges."""
    titres = [f"SIG - Nombre de clients dépistés positif Janvier - Juin {an} {a}"
              for an, a in itertools.product([2026, 2025], ["15-19 ans", "20-24 ans", "25-49 ans", "50 ans et plus"])]
    lignes = []
    for k, (d, e) in enumerate([("NORD", "CS Alpha"), ("NORD", " CS Beta"), ("SUD", "CS Gamma"), ("SUD", "CS Delta")]):
        lignes.append({"orgunitlevel1": "REGION X", "orgunitlevel2": d, "orgunitlevel3": e, "organisationunitname": e,
                       **{t: (None if (k + j) % 3 == 0 else k + j) for j, t in enumerate(titres)}})
    return pd.DataFrame(lignes), titres


def test_proposition_complete_export_dhis2(R):
    recettes, _ = R
    df, titres = _export_dhis2()
    etapes = recettes.proposer(df)
    assert [e["op"] for e in etapes] == ["nettoyer_texte", "retirer_colonnes", "decomposer", "renommer"]
    assert etapes[1]["colonnes"] == ["organisationunitname"]
    assert etapes[3]["colonnes"] == {"orgunitlevel3": "Établissement", "orgunitlevel2": "District", "orgunitlevel1": "Région"}
    out, _ = recettes.appliquer(df, etapes)
    ind = "SIG - Nombre de clients dépistés positif"
    assert list(out.columns) == ["Région", "District", "Établissement", "Période", "Tranche d'âge", ind]
    assert "CS Beta" in set(out["Établissement"])                        # espace en trop retiré
    # oracle indépendant : sommes par semestre lues directement dans le tableau d'origine
    for an in (2025, 2026):
        attendu = df[[t for t in titres if f"Juin {an}" in t]].sum().sum()
        assert out[out["Période"] == f"Janvier - Juin {an}"][ind].sum() == attendu


def test_colonnes_de_chiffres_identiques_jamais_retirees(R):
    recettes, _ = R
    df = pd.DataFrame({"Centre": SITES, "Dépistés": [1, 2, 3], "Positifs": [1, 2, 3]})
    assert not [e for e in recettes.proposer(df) if e["op"] == "retirer_colonnes"]


def test_export_power_query(R, tmp_path, monkeypatch):
    """La requête emporte la correspondance calculée par KLDataViz et remet les colonnes dans le même ordre."""
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import server  # noqa: PLC0415
    import test_recettes_m as T  # noqa: PLC0415
    monkeypatch.setattr(server, "_dossier_recettes", lambda: str(tmp_path / "recettes"))
    df, _ = _export_dhis2()
    src = str(tmp_path / "export.xlsx")
    df.to_excel(src, index=False)
    etapes = server.recette_action("proposer", {"file_path": src})["etapes"]
    texte = server.recette_action("export_m", {"file_path": src, "etapes": etapes})["m"]
    ind = "SIG - Nombre de clients dépistés positif"
    assert 'correspondance = #table({"_cle", "Période", "Tranche d\'âge"}' in texte
    assert '{"SIG - Nombre de clients dépistés positif Janvier - Juin 2026 15-19 ans", "Janvier - Juin 2026", "15-19 ans"}' in texte
    assert f'"_titre", "{ind}")' in texte                               # Unpivot : colonne nommée par l'indicateur
    assert ('Table.ReorderColumns(propres, {"orgunitlevel1", "orgunitlevel2", "orgunitlevel3", "Période", '
            f'"Tranche d\'âge", "{ind}"}})') in texte
    assert "Text.SplitAny" in texte                                      # espaces en trop
    T._bien_formee(texte)
