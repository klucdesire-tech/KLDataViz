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


# --- Matching indicateur → données + calcul par type ---
def test_match_compute_rate_from_linelist():
    # Liste de patients (une ligne = une observation) : taux de suppression virale
    df = pd.DataFrame({"STATUT RESULTAT (S/NS)": ["SUPPRIME", "SUPPRIME", "SUPPRIME", "NON-SUPPRIME"]})
    inds = [{"domaine": "Suppression virale", "code": "TX_PVLS", "libelle": "charge virale supprimée",
             "type": "Pourcentage", "numerateur": "", "denominateur": ""}]
    res = cat.match_and_compute(df, inds)
    assert res[0]["present"] is True
    assert res[0]["valeur"] == 75.0  # 3/4
    assert res[0]["detail"] == "3 / 4"


def test_match_compute_long_format_sum():
    # Format long : colonne indicateur + valeur ; indicateur « Nombre » → somme
    df = pd.DataFrame({"indicateur": ["TX_NEW", "TX_NEW", "TX_CURR"], "valeur": [10, 5, 100]})
    inds = [{"domaine": "Traitement", "code": "TX_NEW", "libelle": "Nouveaux sous TARV",
             "type": "Nombre", "numerateur": "", "denominateur": ""}]
    res = cat.match_and_compute(df, inds)
    assert res[0]["present"] is True
    assert res[0]["valeur"] == 15.0  # 10 + 5


def test_match_compute_absent_indicator():
    df = pd.DataFrame({"indicateur": ["AUTRE_CHOSE"], "valeur": [7]})
    inds = [{"domaine": "X", "code": "TX_PVLS", "libelle": "charge virale supprimée",
             "type": "Pourcentage", "numerateur": "", "denominateur": ""}]
    res = cat.match_and_compute(df, inds)
    assert res[0]["present"] is False


# --- Matching assisté par LLM (réponse simulée, aucun appel réseau) ---
def test_match_compute_llm_taux():
    df = pd.DataFrame({"STATUT RESULTAT (S/NS)": ["SUPPRIME", "SUPPRIME", "SUPPRIME", "NON-SUPPRIME"]})
    inds = [{"domaine": "Suppression virale", "code": "TX_PVLS", "libelle": "charge virale supprimée",
             "type": "Pourcentage", "numerateur": "", "denominateur": ""}]

    def fake_llm(messages, max_tokens):
        return ('{"matches":[{"code":"TX_PVLS","present":true,"colonne":"STATUT RESULTAT (S/NS)",'
                '"calcul":"taux","positif":"SUPPRIME","negatif":"NON-SUPPRIME"}]}')

    res = cat.match_and_compute_llm(df, inds, fake_llm)
    assert res[0]["present"] is True
    assert res[0]["valeur"] == 75.0
    assert res[0]["detail"] == "3 / 4"


def test_match_compute_llm_somme_et_absent():
    df = pd.DataFrame({"valeur": [10, 5]})
    inds = [{"domaine": "T", "code": "TX_NEW", "libelle": "nouveaux", "type": "Nombre",
             "numerateur": "", "denominateur": ""},
            {"domaine": "T", "code": "TX_CURR", "libelle": "actifs", "type": "Nombre",
             "numerateur": "", "denominateur": ""}]

    def fake_llm(messages, max_tokens):
        return ('{"matches":[{"code":"TX_NEW","present":true,"colonne":"valeur","calcul":"somme"},'
                '{"code":"TX_CURR","present":false}]}')

    res = cat.match_and_compute_llm(df, inds, fake_llm)
    assert res[0]["present"] and res[0]["valeur"] == 15.0
    assert res[1]["present"] is False


def test_match_compute_llm_garbage_returns_none():
    df = pd.DataFrame({"x": [1, 2]})
    inds = [{"domaine": "X", "code": "A", "libelle": "a", "type": "Nombre",
             "numerateur": "", "denominateur": ""}]
    assert cat.match_and_compute_llm(df, inds, lambda m, t: "pas du json") is None


# --- Règle « total » (Somme vs Snapshot) et « % jamais sommé » ---
def test_long_format_rate_not_summed():
    # TX_PVLS (Pourcentage) sur 4 trimestres : 80+85+90+88 = 343 serait FAUX → on prend la dernière (88)
    df = pd.DataFrame({"indicateur": ["TX_PVLS"] * 4, "periode": ["2025T1", "2025T2", "2025T3", "2025T4"],
                       "valeur": [80, 85, 90, 88]})
    inds = [{"domaine": "S", "code": "TX_PVLS", "libelle": "charge virale supprimée", "type": "Pourcentage",
             "numerateur": "", "denominateur": "", "total": "Indicateur instantané (snapshot) : valeur de fin de période"}]
    res = cat.match_and_compute(df, inds)
    assert res[0]["valeur"] == 88.0  # pas 343


def test_long_format_snapshot_takes_last():
    # TX_CURR (instantané) : 1000+1050+1100+1150 = 4300 serait FAUX → dernière = 1150
    df = pd.DataFrame({"indicateur": ["TX_CURR"] * 4, "periode": ["2025T1", "2025T2", "2025T3", "2025T4"],
                       "valeur": [1000, 1050, 1100, 1150]})
    inds = [{"domaine": "T", "code": "TX_CURR", "libelle": "actifs sous TARV", "type": "Nombre",
             "numerateur": "", "denominateur": "", "total": "Indicateur instantané (snapshot) : valeur de fin de période"}]
    res = cat.match_and_compute(df, inds)
    assert res[0]["valeur"] == 1150.0


def test_long_format_somme_sums():
    # TX_NEW (additionnable) : somme des 4 trimestres
    df = pd.DataFrame({"indicateur": ["TX_NEW"] * 4, "periode": ["2025T1", "2025T2", "2025T3", "2025T4"],
                       "valeur": [10, 20, 30, 40]})
    inds = [{"domaine": "T", "code": "TX_NEW", "libelle": "nouveaux sous TARV", "type": "Nombre",
             "numerateur": "", "denominateur": "", "total": "Somme des résultats trimestriels (T1+T2+T3+T4)"}]
    res = cat.match_and_compute(df, inds)
    assert res[0]["valeur"] == 100.0


def test_period_col_recognizes_date_debut_pec():
    df = pd.DataFrame({"date_debut_pec": ["01/01/2025", "01/02/2025"], "x": [1, 2]})
    assert cat._period_col(df) == "date_debut_pec"


def test_period_col_skips_identifier():
    df = pd.DataFrame({"code site": ["1226", "367"], "x": [1, 2]})
    assert cat._period_col(df) is None


# --- Confidentialité du matching LLM : jamais de codes patients ni de valeurs brutes ---
def test_llm_payload_never_sends_identifiers():
    df = pd.DataFrame({"code patient": ["P001", "P002", "P003", "P004"],
                       "statut resultat": ["SUPPRIME"] * 2 + ["NON-SUPPRIME"] * 2})
    inds = [{"domaine": "S", "code": "TX_PVLS", "libelle": "charge virale supprimée",
             "type": "Pourcentage", "numerateur": "", "denominateur": ""}]
    captured = {}

    def fake_llm(messages, max_tokens):
        captured["user"] = messages[1]["content"]
        return '{"matches":[]}'

    cat.match_and_compute_llm(df, inds, fake_llm)
    assert "P001" not in captured["user"]  # code patient jamais envoyé (même à 4 valeurs)
    assert "SUPPRIME" in captured["user"]   # étiquette de catégorie, elle, est envoyée


def test_llm_payload_never_sends_numeric_values():
    df = pd.DataFrame({"resultat cpml": [42, 17, 9], "statut": ["S"] * 2 + ["NS"]})
    inds = [{"domaine": "S", "code": "TX_PVLS", "libelle": "charge virale supprimée",
             "type": "Pourcentage", "numerateur": "", "denominateur": ""}]
    captured = {}

    def fake_llm(messages, max_tokens):
        captured["user"] = messages[1]["content"]
        return '{"matches":[]}'

    cat.match_and_compute_llm(df, inds, fake_llm)
    assert "42" not in captured["user"]  # la valeur numérique n'est pas envoyée
