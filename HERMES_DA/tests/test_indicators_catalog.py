# -*- coding: utf-8 -*-
"""Tests du catalogue d'indicateurs (dictionnaire importable + normes intégrées)."""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hermes_da import indicators_catalog as cat  # noqa: E402


# Petit dictionnaire synthétique (2 domaines) pour tester sans dépendre du fichier MER
def _dict_csv(tmp_path):
    p = tmp_path / "dico.csv"
    pd.DataFrame([
        {"Domaine": "Finance", "Code": "ROA", "Indicateur": "Rentabilité des actifs",
         "Type": "Pourcentage", "Numérateur": "Résultat net", "Dénominateur": "Total actif"},
        {"Domaine": "Finance", "Code": "DETTE", "Indicateur": "Ratio d'endettement",
         "Type": "Pourcentage", "Numérateur": "Dettes", "Dénominateur": "Capitaux propres"},
        {"Domaine": "Santé", "Code": "TX_PVLS", "Indicateur": "% de patients avec une charge virale supprimée",
         "Type": "Pourcentage", "Numérateur": "CV <1000", "Dénominateur": "CV documentée"},
    ]).to_csv(p, index=False)
    return p


def test_parse_dictionary_maps_mer_headers(tmp_path):
    p = _dict_csv(tmp_path)
    inds = cat.parse_dictionary(p)
    assert len(inds) == 3
    assert inds[0]["code"] == "ROA" and inds[0]["domaine"] == "Finance" and inds[0]["type"] == "Pourcentage"


def test_to_domains_builds_domains(tmp_path):
    doms = cat.to_domains(cat.parse_dictionary(_dict_csv(tmp_path)))
    assert set(doms) == {"finance", "sante"}
    assert {k["nom"] for k in doms["finance"]["indicateurs"]} == {"Rentabilité des actifs", "Ratio d'endettement"}
    assert doms["sante"]["indicateurs"][0]["nom"] == "% de patients avec une charge virale supprimée"


def test_keywords_include_code_and_bigrams():
    tous, un = cat._keywords("TX_PVLS", "% de patients avec une charge virale supprimée")
    assert tous == ["tx_pvls"]
    assert "charge virale" in un  # bigramme distinctif


def test_negative_indicator_flagged_bad():
    assert cat._bon("Nombre de décès") == -1
    assert cat._bon("Rentabilité des actifs") == 1


def test_builtin_domains_present():
    assert "vih" in cat.BUILTIN_DOMAINS
    assert cat.BUILTIN_DOMAINS["vih"]["indicateurs"]


# --- Registre PostgreSQL (testé uniquement si la base locale est joignable) ---
def _pg_available():
    try:
        cat.load_dictionary("__test_ping__")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pg_available(), reason="PostgreSQL local non joignable")
def test_import_load_roundtrip(tmp_path):
    p = _dict_csv(tmp_path)
    r = cat.import_dictionary(p, structure="test_struct")
    assert r["importe"] == 3
    inds = cat.load_dictionary("test_struct")
    assert len(inds) == 3
    # Mode B : catalogue renvoie le dictionnaire, pas les normes intégrées
    doms, has_dict = cat.catalogue("test_struct")
    assert has_dict is True
    assert "finance" in doms


@pytest.mark.skipif(not _pg_available(), reason="PostgreSQL local non joignable")
def test_no_dictionary_falls_back_to_builtin():
    doms, has_dict = cat.catalogue("__aucune_structure__")
    assert has_dict is False
    assert "vih" in doms  # normes intégrées (Mode A)
