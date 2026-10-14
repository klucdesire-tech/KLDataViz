# -*- coding: utf-8 -*-
"""Non-régression de la console locale : python -m pytest tests/test_local.py"""
import os
import time

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


def test_architecte_format_large(base, tmp_path, monkeypatch):
    """Export type DHIS2 (« ; », dimensions dans les en-têtes, ligne d'agrégat) → format long."""
    monkeypatch.setattr(server, "_llm_config", lambda: None)  # repli par règles, sans appel réseau
    ages = ["00-04 ans", "05-09 ans", "50 ans et plus"]
    cols = {f"Mars 2026 SIG - Nombre de {i} {a}": [3, 5, 7, 3] for i in ("consultations", "consultants") for a in ages}
    p = tmp_path / "dhis2.csv"
    pd.DataFrame({"orgid": ["a1", "b2", "c3", "d4"], "nom": ["CSR-D A", "CSR-D B", "CSU C", "DISTRICT"], **cols}) \
        .to_csv(p, sep=";", index=False, encoding="utf-8-sig")
    code, job = call(base, "/api/architect", {"file_path": str(p)})
    assert code == 200 and job.get("job"), job
    for _ in range(100):  # tâche de fond
        code, j = call(base, f"/api/architect/job?id={job['job']}")
        if j.get("status") == "done":
            break
        time.sleep(0.1)
    r = j["result"]
    assert not r.get("error"), r
    assert call(base, "/api/architect/job?id=inconnu")[1].get("error")
    assert r["ia"] is False and set(r["proposition"]["comprehension"]["dimensions"]) == {"periode", "tranche_age"}
    assert r["apercu"]["fait"]["lignes"] == 4 * len(cols)
    code, a = call(base, "/api/architect/apply", {"file_path": str(p), "spec": r["proposition"]["spec"]})
    assert code == 200 and a["url"].startswith("/download/"), a
    assert call(base, "/api/exports/delete", {"file": a["file"]})[0] == 200
