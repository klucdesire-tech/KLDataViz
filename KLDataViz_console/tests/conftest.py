# -*- coding: utf-8 -*-
"""Les tests n'écrivent jamais dans le stockage réel de la console (sources connectées comprises)."""
import pytest


@pytest.fixture(autouse=True)
def _sources_isolees(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_SOURCES_DIR", str(tmp_path / "sources-test"))
