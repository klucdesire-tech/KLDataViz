# -*- coding: utf-8 -*-
"""Domaine du tableau de bord avec un dictionnaire importé — données et dictionnaires fictifs.

Vu en ligne le 1er novembre 2026 : un fichier de ventes affiché « Systèmes de santé » et « Nouveaux patients »,
parce que le dictionnaire contenait le mot « categorie » et que le fichier avait une colonne de ce nom.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "HERMES_DA"))

from hermes_da import indicators_catalog as C
from hermes_da.adapters.dashboard import _detect_domaine, _patient_items, generate_dashboard

SANTE = [
    {"domaine": "Systèmes de santé", "code": "HRH", "libelle": "Agents de santé par catégorie et par site"},
    {"domaine": "Systèmes de santé", "code": "SC", "libelle": "Flacons ARV pédiatriques dispensés"},
    {"domaine": "Traitement", "code": "TX_NEW", "libelle": "Nouveaux patients sous TARV"},
    {"domaine": "Suppression virale", "code": "TX_PVLS", "libelle": "Patients avec une charge virale supprimée"},
]
FINANCE = [
    {"domaine": "Finance", "code": "ROA", "libelle": "Rentabilité des actifs"},
    {"domaine": "Finance", "code": "DTE", "libelle": "Ratio d'endettement"},
]


def _ventes(n=60):
    return pd.DataFrame({"date": pd.date_range("2025-01-01", periods=n, freq="5D"), "magasin": ["Nord", "Sud", "Est"] * (n // 3),
                         "categorie": ["Alimentaire", "Hygiène"] * (n // 2), "chiffre_affaires": range(100, 100 + n)})


def test_une_colonne_categorie_ne_fait_pas_un_domaine():
    assert _detect_domaine(_ventes(), C.to_domains(SANTE)) is None


def test_mots_courants_ne_representent_pas_un_domaine():
    doms = C.to_domains(SANTE)
    mots = {m for d in doms.values() for m in d["mots"]}
    assert not mots & {"categorie", "sites", "agents"}


def test_ligne_de_valeurs_courantes_ne_fait_pas_un_domaine():
    df = pd.DataFrame({"statut": ["Actif", "Parti"] * 20, "sexe": ["Hommes", "Femmes"] * 20, "groupe": ["Enfants", "Adultes"] * 20, "age": range(40)})
    assert _detect_domaine(df, C.to_domains(SANTE)) is None


def test_expression_du_dictionnaire_suffit():
    df = pd.DataFrame({"district": ["A", "B"] * 10, "charge_virale": range(20)})
    assert _detect_domaine(df, C.to_domains(SANTE))["nom"] == "Suppression virale"


def test_mot_sur_suffit():
    df = pd.DataFrame({"district": ["A", "B"] * 10, "patients_tarv": range(20)})
    assert _detect_domaine(df, C.to_domains(SANTE))["nom"] == "Traitement"


def test_deux_mots_du_dictionnaire_suffisent():
    df = pd.DataFrame({"agence": ["A", "B"] * 10, "rentabilite": range(20), "actifs": range(20)})
    assert _detect_domaine(df, C.to_domains(FINANCE))["nom"] == "Finance"


def test_sans_dictionnaire_rien_ne_change():
    df = pd.DataFrame({"district": ["A", "B"] * 10, "resultat_vih": ["P", "N"] * 10})
    assert _detect_domaine(df)["cle"] == "vih"
    assert _detect_domaine(_ventes()) is None


@pytest.mark.parametrize("colonne,attendu", [("patient_id", "Nouveaux patients"), ("date_initiation_arv", "Nouveaux patients"), ("beneficiaire", "Nouveaux patients")])
def test_lignes_de_patients(colonne, attendu):
    df = pd.DataFrame({colonne: range(5), "date": pd.date_range("2025-01-01", periods=5)})
    assert _patient_items(df, [], {"cle": "vih", "nom": "VIH"}, "date")[0]["nom"] == attendu


def test_domaine_non_sante_ne_parle_pas_de_patients():
    df = pd.DataFrame({"rentabilite": range(5), "actifs": range(5), "date": pd.date_range("2025-01-01", periods=5)})
    assert _patient_items(df, [], {"cle": "finance", "nom": "Finance"}, "date")[0]["nom"] == "Nouveaux enregistrements"


def test_page_de_ventes_avec_dictionnaire_de_sante():
    r = generate_dashboard(_ventes(), "Essai", llm=False, dictionnaire=SANTE)
    assert "Systèmes de santé" not in r["html"].split("function")[0]
    assert (r.get("domaine") or None) is None
    assert "Nouveaux patients" not in str(r.get("gaps"))
