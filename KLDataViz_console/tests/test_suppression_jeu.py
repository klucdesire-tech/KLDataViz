# -*- coding: utf-8 -*-
"""Suppression d'un jeu de données : registre, copies sur disque et protections."""
import os
import stat

import pytest

from ingestion import Registry
from ingestion.versioner import Versioner

PG = os.environ.get("KLDV_TEST_PG")


def _ajouter(reg, dataset_id, ph, pre=""):
    conn = reg.connect()
    cur = conn.cursor()
    cur.execute(f"INSERT INTO {pre}datasets (dataset_id, dataset_name, source_kind, origin, created_at, updated_at) "
                f"VALUES ({ph},'jeu','fichier','x','2026-01-01','2026-01-01')", (dataset_id,))
    v = dataset_id + "-v001"
    cur.execute(f"INSERT INTO {pre}dataset_versions (dataset_version_id, dataset_id, version_no, content_sha256, row_count, "
                f"column_count, status, ingested_at) VALUES ({ph},{ph},1,'a',1,1,'OK','2026-01-01')", (v, dataset_id))
    cur.execute(f"INSERT INTO {pre}dataset_columns (dataset_version_id, ordinal, column_name) VALUES ({ph},1,'c')", (v,))
    cur.execute(f"INSERT INTO {pre}ingestion_runs (run_id, dataset_id, dataset_version_id) VALUES ({ph},{ph},{ph})",
                ("r-" + dataset_id, dataset_id, v))
    if reg.backend != "postgres":
        conn.commit()
    conn.close()


@pytest.fixture(params=["sqlite", "postgres"])
def registre(request, tmp_path):
    if request.param == "postgres":
        if not PG:
            pytest.skip("KLDV_TEST_PG non défini")
        reg = Registry(dsn=PG, schema="t_test_suppr")
        reg.ensure()
        c = reg.connect()
        c.execute("TRUNCATE t_test_suppr.ingestion_runs, t_test_suppr.datasets CASCADE")
        c.close()
        return reg, "t_test_suppr."
    reg = Registry(sqlite_path=tmp_path / "r.db", force_sqlite=True)
    reg.ensure()
    return reg, ""


def test_supprime_tout_le_jeu_et_seulement_lui(registre):
    reg, pre = registre
    _ajouter(reg, "DS-A", reg.placeholder, pre)
    _ajouter(reg, "DS-B", reg.placeholder, pre)
    reg.delete_dataset("DS-A")
    assert [d["dataset_id"] for d in reg.list_datasets()] == ["DS-B"]
    assert reg.list_versions("DS-A") == [] and len(reg.list_versions("DS-B")) == 1
    for table, n in (("dataset_columns", 1), ("ingestion_runs", 1)):
        assert reg.query(f"SELECT count(*) AS n FROM {pre}{table}")[0]["n"] == n


def test_copies_effacees_meme_en_lecture_seule(tmp_path):
    v = Versioner(tmp_path)
    source = tmp_path / "origine.csv"
    source.write_text("a\n1\n", encoding="utf-8")
    copie = v.freeze_source(source, "DS-A", 1)["raw_path"]
    autre = v.freeze_source(source, "DS-B", 1)["raw_path"]
    v.work_dir("DS-A", 1)
    v.version_dir("DS-A", 1)
    assert not os.stat(copie).st_mode & stat.S_IWRITE
    v.delete_dataset("DS-A")
    assert not os.path.exists(copie) and not (v.work / "DS-A").exists() and not (v.versions / "DS-A").exists()
    assert os.path.exists(autre) and source.exists()


@pytest.mark.parametrize("mauvais", ["", "..", "../..", "DS/../..", "a\\b", "DS A", "é"])
def test_identifiant_dangereux_refuse(tmp_path, mauvais):
    v = Versioner(tmp_path / "s")
    temoin = tmp_path / "temoin.txt"
    temoin.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        v.delete_dataset(mauvais)
    assert temoin.exists()


def test_suppression_rejouable(tmp_path):
    v = Versioner(tmp_path)
    v.delete_dataset("DS-INCONNU")
