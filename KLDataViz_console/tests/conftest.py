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


@pytest.fixture(autouse=True)
def _journal_isole(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_JOURNAL_DIR", str(tmp_path / "journal-test"))


@pytest.fixture(autouse=True)
def _correspondances_isolees(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_CORR_DIR", str(tmp_path / "correspondances-test"))


@pytest.fixture(autouse=True)
def _mode_local_par_defaut(request, monkeypatch):
    """test_cloud.py met KLDV_MODE=cloud dans l'environnement du processus pytest dès son import, sans le retirer :
    les autres fichiers se retrouvaient en mode « en ligne » selon l'ordre d'exécution (fichier local non connecté,
    console qui refuse 127.0.0.1…). Mode local par défaut, sauf pour test_cloud.py ; un test qui veut le mode en
    ligne le demande lui-même (monkeypatch.setenv)."""
    if request.module.__name__ != "test_cloud":
        monkeypatch.delenv("KLDV_MODE", raising=False)
