# -*- coding: utf-8 -*-
"""Les tests n'écrivent jamais dans le stockage réel de la console (sources connectées comprises)."""
import pytest


@pytest.fixture(autouse=True)
def _sources_isolees(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_SOURCES_DIR", str(tmp_path / "sources-test"))


@pytest.fixture(autouse=True)
def _coffre_dhis2_isole(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_DHIS2_DIR", str(tmp_path / "dhis2-test"))


@pytest.fixture(autouse=True)
def _partages_isoles(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_SHARES_DIR", str(tmp_path / "partages-test"))


@pytest.fixture(autouse=True)
def _dictionnaire_isole(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_DICT_DIR", str(tmp_path / "dictionnaire-test"))
