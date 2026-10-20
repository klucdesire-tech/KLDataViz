# -*- coding: utf-8 -*-
"""Tests du connecteur DHIS2 — réponse simulée, aucun appel réseau, aucune donnée réelle."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dhis2  # noqa: E402


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Stockage des identifiants isolé du vrai storage/ (aucune écriture hors test)."""
    monkeypatch.setenv("KLDV_DHIS2_DIR", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------
# URI
# --------------------------------------------------------------------------
def test_parse_uri_analytics():
    uri = ("dhis2://play.dhis2.org/2.41/api/analytics.json"
           "?dimension=dx:Uvn6LCg7dVU&dimension=ou:LEVEL-1&dimension=pe:LAST_12_MONTHS")
    instance, url = dhis2.parse_uri(uri)
    assert instance == "play.dhis2.org/2.41"
    assert url == ("https://play.dhis2.org/2.41/api/analytics.json"
                   "?dimension=dx:Uvn6LCg7dVU&dimension=ou:LEVEL-1&dimension=pe:LAST_12_MONTHS")


def test_parse_uri_datavaluesets():
    instance, url = dhis2.parse_uri(
        "dhis2://play.dhis2.org/2.41/api/dataValueSets.json?dataSet=pBOMPrpg1QX&period=LAST_12_MONTHS")
    assert instance == "play.dhis2.org/2.41"
    assert url.startswith("https://play.dhis2.org/2.41/api/dataValueSets.json")


def test_parse_uri_reject_non_dhis2():
    with pytest.raises(ValueError):
        dhis2.parse_uri("https://play.dhis2.org/2.41/api/analytics.json")


def test_parse_uri_reject_missing_api():
    with pytest.raises(ValueError):
        dhis2.parse_uri("dhis2://play.dhis2.org/2.41/autre/chemin")


def test_parse_uri_reject_empty_endpoint():
    with pytest.raises(ValueError):
        dhis2.parse_uri("dhis2://play.dhis2.org/2.41/api/")


# --------------------------------------------------------------------------
# Chiffrement des identifiants
# --------------------------------------------------------------------------
def test_credentials_roundtrip(isolated):
    dhis2.save_credentials("play.dhis2.org/2.41", "lecture_seule", "s3cret")
    assert dhis2.load_credentials("play.dhis2.org/2.41") == ("lecture_seule", "s3cret")
    assert dhis2.load_credentials("autre.instance") is None


def test_credentials_encrypted_at_rest(isolated):
    dhis2.save_credentials("play.dhis2.org/2.41", "lecture_seule", "s3cret")
    raw = open(isolated / "dhis2_credentials.enc", "rb").read()
    # ni l'utilisateur, ni le mot de passe, ni l'instance en clair ; pas du JSON lisible
    assert b"lecture_seule" not in raw
    assert b"s3cret" not in raw
    assert b"play.dhis2.org" not in raw
    assert not raw.lstrip().startswith(b"{")


def test_credentials_multiple_instances_and_update(isolated):
    dhis2.save_credentials("a", "u1", "p1")
    dhis2.save_credentials("b", "u2", "p2")
    dhis2.save_credentials("a", "u1-v2", "p1-v2")  # mise à jour
    assert dhis2.load_credentials("a") == ("u1-v2", "p1-v2")
    assert dhis2.load_credentials("b") == ("u2", "p2")


def test_credentials_unknown_when_no_vault(isolated):
    assert dhis2.load_credentials("x") is None


# --------------------------------------------------------------------------
# Conversion des réponses en DataFrame long
# --------------------------------------------------------------------------
ANALYTICS = {
    "headers": [
        {"name": "dx", "column": "Data"},
        {"name": "ou", "column": "Organisation unit"},
        {"name": "pe", "column": "Period"},
        {"name": "value", "column": "Value"},
    ],
    "rows": [
        ["Uvn6LCg7dVU", "ImspTQPwCqd", "202401", "42.0"],
        ["Uvn6LCg7dVU", "PMa2VCYOdqJ", "202401", "17.0"],
    ],
    "metaData": {"items": {
        "Uvn6LCg7dVU": {"name": "Cas de paludisme confirmés"},
        "ImspTQPwCqd": {"name": "District A"},
        "PMa2VCYOdqJ": {"name": "District B"},
    }},
}

DATAVALUES = {"dataValues": [
    {"dataElement": "Uvn6LCg7dVU", "period": "202401", "orgUnit": "ImspTQPwCqd", "value": "42"},
    {"dataElement": "YtbsuPPo010", "period": "202402", "orgUnit": "ImspTQPwCqd", "value": "9"},
]}


def test_analytics_to_long():
    df = dhis2._analytics_to_long(ANALYTICS)
    assert list(df.columns) == ["indicateur", "periode", "unite_organisation", "valeur"]
    assert len(df) == 2
    assert df["indicateur"].tolist() == ["Cas de paludisme confirmés"] * 2
    assert df["unite_organisation"].tolist() == ["District A", "District B"]
    assert df["valeur"].tolist() == ["42.0", "17.0"]


def test_data_values_to_long():
    df = dhis2._data_values_to_long(DATAVALUES)
    assert list(df.columns) == ["indicateur", "periode", "unite_organisation", "valeur"]
    assert len(df) == 2
    assert df["valeur"].tolist() == ["42", "9"]


# --------------------------------------------------------------------------
# Lecture de bout en bout (réseau simulé, zéro appel réel)
# --------------------------------------------------------------------------
def test_read_dhis2_end_to_end(isolated, monkeypatch):
    dhis2.save_credentials("play.dhis2.org/2.41", "lecture_seule", "s3cret")
    captured = {}

    def fake_fetch(url, user, password, allow_private=False):
        captured["url"], captured["auth"] = url, (user, password)
        return ANALYTICS

    monkeypatch.setattr(dhis2, "_fetch_json", fake_fetch)
    df, err = dhis2.read_dhis2(
        "dhis2://play.dhis2.org/2.41/api/analytics.json?dimension=dx:Uvn6LCg7dVU&dimension=pe:LAST_12_MONTHS")
    assert err is None
    assert list(df.columns) == ["indicateur", "periode", "unite_organisation", "valeur"]
    assert captured["auth"] == ("lecture_seule", "s3cret")
    assert "LAST_12_MONTHS" in captured["url"]


def test_read_dhis2_missing_credentials(isolated, monkeypatch):
    called = []
    monkeypatch.setattr(dhis2, "_fetch_json", lambda *a, **k: called.append(1))
    df, err = dhis2.read_dhis2("dhis2://inconnue.example/api/analytics.json?dimension=dx:X")
    assert df is None
    assert "identifiant" in err
    assert not called  # aucun appel réseau sans identifiants


def test_read_dhis2_http_error_message(isolated, monkeypatch):
    import urllib.error
    dhis2.save_credentials("play.dhis2.org/2.41", "u", "p")

    def fake_fetch(url, user, password, allow_private=False):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", None, None)

    monkeypatch.setattr(dhis2, "_fetch_json", fake_fetch)
    df, err = dhis2.read_dhis2("dhis2://play.dhis2.org/2.41/api/analytics.json?dimension=dx:X")
    assert df is None
    assert "401" in err and "identifiants" in err


def test_fetch_rejects_internal_instance(isolated):
    """Sécurité : netguard bloque un appel vers une adresse interne (anti-SSRF)."""
    netguard = dhis2.netguard  # le module réellement utilisé (d'autres tests rechargent les modules)
    dhis2.save_credentials("127.0.0.1", "u", "p")
    with pytest.raises(netguard.BlockedURL):
        dhis2._fetch_json("https://127.0.0.1/api/analytics.json", "u", "p", allow_private=False)
