# -*- coding: utf-8 -*-
"""Non-régression de la console locale : python -m pytest tests/test_local.py"""
import os

import pandas as pd
import pytest

os.environ.pop("KLDV_MODE", None)
from _harness import call, start  # noqa: E402
import server  # noqa: E402


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    httpd, url = start(server)
    yield url
    httpd.shutdown()


@pytest.fixture(scope="module")
def csv(tmp_path_factory):
    p = tmp_path_factory.mktemp("d") / "ventes_test.csv"
    pd.DataFrame({"region": ["A", "B", "A", "C"] * 10, "montant": range(40)}).to_csv(p, index=False)
    return str(p)


def test_pages_publiques(base):
    assert call(base, "/", raw=True)[0] == 200
    assert call(base, "/classic", raw=True)[0] == 200
    assert call(base, "/healthz")[1] == {"ok": True}


def test_parcours_analyse_export(base, csv):
    code, a = call(base, "/api/analyze", {"file_path": csv})
    assert code == 200 and a["rows"] == 40, a
    code, c = call(base, "/api/clean", {"file_path": csv})
    assert code == 200 and "cleaned_path" in c
    code, e = call(base, "/api/export/html", {})
    assert code == 200 and e["url"].startswith("/download/")
    code, data = call(base, e["url"], raw=True)
    assert code == 200 and len(data) > 100
    code, files = call(base, "/api/exports")
    assert any(f["file"] == e["file"] for f in files)
    assert call(base, "/api/exports/delete", {"file": e["file"]})[0] == 200
    os.remove(c["cleaned_path"])
