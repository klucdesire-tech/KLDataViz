# -*- coding: utf-8 -*-
"""Réponse instantanée du chat : seulement les questions de structure SANS condition, sur le fichier en cours.
Toute question filtrée / groupée / datée reste à l'agent (un raccourci y donnerait un chiffre faux). Données fictives."""
import pandas as pd

from _harness import reset_app_modules

DF = pd.DataFrame({"id": range(40), "region": ["Nawa", "Poro"] * 20, "cv_supprimee": [True] * 30 + [False] * 10})


def _r(q):
    reset_app_modules()
    import server  # noqa: PLC0415
    return server._reponse_immediate(q, DF, "patients.csv")


def test_questions_de_structure_instantanees():
    assert "40 lignes" in _r("Combien de lignes ?")["answer"]
    assert "40 lignes" in _r("combien d'enregistrements y a-t-il dans le fichier ?")["answer"]
    assert "3 colonnes" in _r("Nombre de colonnes")["answer"]
    t = _r("Quelles sont les colonnes ?")
    assert t["type"] == "table" and [r[0] for r in t["table"]["rows"]] == ["id", "region", "cv_supprimee"]


def test_questions_avec_condition_laissees_a_l_agent():
    for q in ["Combien de patients ont une CV non supprimée ?", "Quels districts ont le plus de perdus de vue ?",
              "Quelle est la moyenne d'âge des femmes sous TARV ?", "Nombre de patients décédés par région en 2025",
              "Le total des tests positifs a-t-il baissé depuis juin ?", "Combien de lignes par région ?",
              "Combien de lignes en 2025 ?", "Quelles colonnes contiennent des valeurs manquantes ?"]:
        assert _r(q) is None, q


def test_sans_fichier_en_cours_rien():
    reset_app_modules()
    import server  # noqa: PLC0415
    assert server._reponse_immediate("Combien de lignes ?", None, "x") is None
