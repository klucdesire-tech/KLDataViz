# -*- coding: utf-8 -*-
"""Export des recettes en Power Query (M) : chaque opération a sa traduction attendue, la requête est bien formée
(parenthèses, accolades, guillemets équilibrés ; chaque étape part de la précédente), et la source est celle qu'a lue
KLDataViz (feuille, lignes de titre sautées). Le langage M n'est pas exécuté ici : il n'existe pas de moteur Power Query
hors d'Excel et de Power BI."""
import os
import re
import sys

import pytest

from _harness import APP, reset_app_modules

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)


@pytest.fixture
def M():
    reset_app_modules()
    import recettes
    import recettes_m
    return recettes, recettes_m


SOURCE = {"type": "excel", "feuille": "Feuil1", "entete": 2, "chemin": "rapport.xlsx"}


def _bien_formee(texte):
    """Parenthèses, accolades et crochets équilibrés hors chaînes ; guillemets pairs ; chaque étape part de la
    précédente ; le résultat (après « in ») est la dernière étape."""
    corps = re.sub(r'"(?:[^"]|"")*"', '""', "\n".join(l for l in texte.splitlines() if not l.strip().startswith("//")))
    for o, f in ("()", "{}", "[]"):
        assert corps.count(o) == corps.count(f), (o, corps.count(o), corps.count(f))
    noms = re.findall(r'^    (#"(?:[^"]|"")*"|\w+) = ', texte, re.M)
    for prec, nom in zip(noms, noms[1:]):
        ligne = next(l for l in texte.splitlines() if l.startswith(f"    {nom} = "))
        if nom not in ('#"Lignes de titre sautées"', "Classeur", "Feuille", "Texte"):
            assert prec in ligne, f"{nom} ne part pas de {prec}"
    assert texte.rstrip().endswith(noms[-1])
    return noms


@pytest.mark.parametrize("etape, attendu", [
    ({"op": "recopier_bas", "colonnes": ["District"]}, 'Table.FillDown(#"En-têtes", {"District"})'),
    ({"op": "depivoter", "colonnes": ["Janv.", "Févr."], "nom": "periode", "valeur": "valeur"},
     'Table.Unpivot(#"En-têtes", {"Janv.", "Févr."}, "periode", "valeur")'),
    ({"op": "fractionner", "colonne": "lieu", "separateur": " - ", "noms": ["District", "Centre"]},
     'Table.SplitColumn(#"En-têtes", "lieu", Splitter.SplitTextByEachDelimiter({" - "}, QuoteStyle.None), {"District", "Centre"})'),
    ({"op": "renommer", "colonnes": {"a": "b"}}, 'Table.RenameColumns(#"En-têtes", {{"a", "b"}})'),
    ({"op": "retirer_colonnes", "colonnes": ["x", "y"]}, 'Table.RemoveColumns(#"En-têtes", {"x", "y"})'),
    ({"op": "remplacer", "colonne": "c", "remplacements": {"A": "B"}},
     'Table.ReplaceValue(#"En-têtes", "A", "B", Replacer.ReplaceValue, {"c"})'),
    ({"op": "convertir", "colonne": "v", "type": "nombre"}, 'Table.TransformColumnTypes(#"En-têtes", {{"v", type number}}, "fr-FR")'),
    ({"op": "garder_lignes", "colonne": "zone", "valeurs": ["Nord"]}, 'List.Contains({"nord"}'),
    ({"op": "retirer_lignes", "vides": True}, 'each not (List.IsEmpty(List.RemoveMatchingItems(Record.FieldValues(_), {"", null})))'),
    ({"op": "regrouper", "par": ["District"], "mesures": {"v": "somme"}},
     'Table.Group(#"En-têtes", {"District"}, {{"v", each List.Sum(Table.Column(_, "v")), type nullable number}})'),
])
def test_chaque_operation_traduite(M, etape, attendu):
    recettes, recettes_m = M
    texte = recettes_m.vers_m([etape], SOURCE)
    assert attendu in texte, texte
    _bien_formee(texte)


def test_totaux_comme_kldataviz(M):
    """Le test M des totaux suit la règle de KLDataViz : premier mot total/totaux/totale/totals, « Totalité » exclue."""
    recettes, recettes_m = M
    texte = recettes_m.vers_m([{"op": "retirer_lignes", "motif": recettes.TOTAL.pattern}], SOURCE)
    assert 'List.Contains({"total", "totale", "totals", "totaux"}, p) or t = "ensemble"' in texte
    for oui in ("Total", "Sous-total Abobo", "Total général", "TOTAUX", "Grand total"):
        assert recettes.TOTAL.match(oui), oui
    assert not recettes.TOTAL.match("Totalité")
    _bien_formee(texte)


def test_totaux_reconnus_partout(M):
    """« Totaux » est une ligne de total partout où KLDataViz en cherche (l'ancien motif total(e|s|aux) attendait
    « totalaux ») ; « Totalité » n'en est pas une."""
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import pdf_report, qualite, recettes, server  # noqa: E401, PLC0415
    from hermes_da.adapters import dashboard  # noqa: PLC0415
    import oracle_chiffres  # noqa: PLC0415
    motifs = {"recettes": recettes.TOTAL, "serveur": server.TOTAL_LABEL, "pdf": pdf_report.TOTAL_RE,
              "qualite": qualite.TOTAUX, "tableau": dashboard.TOTAL_LIGNE, "oracle": oracle_chiffres.TOTAL}
    for nom, rx in motifs.items():
        for oui in ("Total", "TOTAUX", "Totaux", "Sous-total", "Totale", "Totals"):
            assert rx.match(oui), (nom, oui)
        for non in ("Totalité", "Totaliser", "Tota"):
            assert not rx.match(non), (nom, non)


def test_motif_expression_reguliere_signale(M):
    recettes, recettes_m = M
    texte = recettes_m.vers_m([{"op": "retirer_lignes", "colonne": "Centre", "motif": "Sous-total.*"}], SOURCE)
    assert "// motif « Sous-total.* »" in texte and '"sous-total")' in texte
    _bien_formee(texte)


def test_source_csv(M):
    recettes, recettes_m = M
    texte = recettes_m.vers_m([], {"type": "csv", "separateur": ";", "encodage": "cp1252", "entete": 0, "chemin": "x.csv"})
    assert 'Csv.Document(File.Contents(Fichier), [Delimiter=";", Encoding=1252' in texte and "Table.Skip" not in texte
    assert texte.rstrip().endswith('#"En-têtes"')


def test_export_du_classeur_mal_forme_par_l_api(M, tmp_path, monkeypatch):
    """De bout en bout : le classeur fictif du banc des recettes, sa recette proposée, la requête M exportée."""
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import server  # noqa: PLC0415
    import test_recettes as T  # noqa: PLC0415
    monkeypatch.setattr(server, "_dossier_recettes", lambda: str(tmp_path / "recettes"))
    src = T._classeur(str(tmp_path / "mars.xlsx"))
    etapes = server.recette_action("proposer", {"file_path": src})["etapes"]
    d = server.recette_action("export_m", {"file_path": src, "etapes": etapes})
    texte = d["m"]
    assert d["nom"] == "recette_mars.pq" and 'Fichier = "mars.xlsx"' in d["m"]
    assert 'Feuille = Classeur{[Item="Sheet", Kind="Sheet"]}[Data]' in texte      # feuille lue par KLDataViz
    assert "Table.Skip(Feuille, 2)" in texte                                         # titre + ligne vide sautés
    assert 'Table.FillDown(#"En-têtes", {"District"})' in texte
    assert 'Table.Unpivot(' in texte and '{"Janv.", "Févr.", "Mars", "Avr."}, "periode", "valeur"' in texte
    noms = _bien_formee(texte)
    assert len(noms) == 5 + len(etapes)              # Fichier, Classeur, Feuille, sautées, en-têtes, puis la recette
    mauvais = server.recette_action("export_m", {"file_path": src, "etapes": [{"op": "recopier_bas", "colonnes": ["Région"]}]})
    assert "Région" in mauvais["error"]              # une recette qui ne s'applique pas ne s'exporte pas


def test_bouton_export_toujours_accessible():
    """Le bouton est dans l'en-tête de la carte (pas en bas à droite, sous la bulle d'Adifa’ sur téléphone), et la carte
    s'affiche même quand aucune étape n'est proposée (fichier déjà bien formé) : sinon on ne peut ni ajouter d'étape ni
    exporter."""
    html = open(os.path.join(APP, "index.html"), encoding="utf-8").read()
    entete = re.search(r'<div class="card" id="recCard" hidden>\s*<div class="toolbar"[^>]*>(.*?)</div>', html, re.S).group(1)
    assert 'id="recExportM"' in entete
    rendu = html[html.index("function renderRecette()"):][:400]
    assert "card.hidden=!R||!!R.chargement;" in rendu and "etapes" not in rendu.split("card.hidden=", 1)[1].split(";")[0]
