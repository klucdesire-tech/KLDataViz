# -*- coding: utf-8 -*-
"""Suppression d'un jeu de données : registre, copies sur disque et protections."""
import os
import stat

import pytest

from _harness import reset_app_modules
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


def test_echec_des_fichiers_laisse_le_jeu_rejouable(registre):
    reg, pre = registre
    _ajouter(reg, "DS-A", reg.placeholder, pre)

    def casse():
        raise OSError("disque")
    with pytest.raises(OSError):
        reg.delete_dataset("DS-A", pendant=casse)
    if reg.backend == "postgres":
        assert len(reg.list_versions("DS-A")) == 1
    reg.delete_dataset("DS-A")
    assert reg.list_versions("DS-A") == []


# ---- _supprimer_jeu : les règles métier, avec un vrai registre SQLite et de vrais fichiers
class Monde:
    def __init__(self, server, tmp, monkeypatch):
        self.server, self.tmp = server, tmp
        self.depot, self.nettoyes, self.meta = tmp / "uploads", tmp / "cleaned", tmp / "meta"
        for d in (self.depot, self.nettoyes, self.meta):
            d.mkdir()
        self.reg = Registry(sqlite_path=tmp / "r.db", force_sqlite=True)
        self.reg.ensure()
        self.v = Versioner(tmp / "stock")
        self.planifs, self.sources = [], []
        T = server.T
        monkeypatch.setattr(T, "registry", lambda: self.reg)
        monkeypatch.setattr(T, "storage_root", lambda: tmp / "stock")
        monkeypatch.setattr(T, "uploads_dir", lambda: str(self.depot))
        monkeypatch.setattr(T, "cleaned_dir", lambda: str(self.nettoyes))
        monkeypatch.setattr(server, "_registry_meta_dir", lambda: str(self.meta))
        monkeypatch.setattr(server.studio, "list_schedules", lambda: self.planifs)
        monkeypatch.setattr(server.sources, "list_sources", lambda: self.sources)

    def jeu(self, dataset_id, source):
        conn = self.reg.connect()
        cur = conn.cursor()
        cur.execute("INSERT INTO datasets (dataset_id, dataset_name, source_kind, origin, created_at, updated_at) "
                    "VALUES (?,'jeu.csv','fichier','x','2026-01-01','2026-01-01')", (dataset_id,))
        work = self.v.work_dir(dataset_id, 1) / "travail.csv"
        work.write_text("a\n1\n", encoding="utf-8")
        cur.execute("INSERT INTO dataset_versions (dataset_version_id, dataset_id, version_no, content_sha256, source_path, "
                    "work_path, row_count, column_count, status, ingested_at) VALUES (?,?,1,'a',?,?,1,1,'OK','2026-01-01')",
                    (dataset_id + "-v1", dataset_id, str(source), str(work)))
        conn.commit()
        conn.close()
        return str(work)


@pytest.fixture
def monde(tmp_path, monkeypatch):
    monkeypatch.delenv("KLDV_MODE", raising=False)
    reset_app_modules()
    import server  # noqa: PLC0415
    return Monde(server, tmp_path, monkeypatch)


def test_fichier_de_l_utilisateur_jamais_touche(monde, tmp_path):
    mien = tmp_path / "mes_documents.csv"
    mien.write_text("a\n1\n", encoding="utf-8")
    monde.jeu("DS-A", mien)
    monde.server._supprimer_jeu("DS-A")
    assert mien.exists() and monde.reg.list_datasets() == []


def test_copies_deposee_et_nettoyee_effacees(monde):
    depot = monde.depot / "ventes.csv"
    nettoye = monde.nettoyes / "ventes_nettoye.csv"
    depot.write_text("a\n", encoding="utf-8")
    nettoye.write_text("a\n", encoding="utf-8")
    monde.server.mark_agrege(str(nettoye))
    monde.jeu("DS-A", nettoye)
    monde.server._supprimer_jeu("DS-A")
    assert not depot.exists() and not nettoye.exists()
    assert os.listdir(monde.nettoyes) == []


def test_copie_partagee_avec_un_autre_jeu_conservee(monde):
    depot = monde.depot / "ventes.csv"
    depot.write_text("a\n", encoding="utf-8")
    monde.jeu("DS-A", depot)
    monde.jeu("DS-B", depot)
    monde.server._supprimer_jeu("DS-A")
    assert depot.exists() and [d["dataset_id"] for d in monde.reg.list_datasets()] == ["DS-B"]


def test_refus_si_tableau_de_bord_lie(monde):
    work = monde.jeu("DS-A", monde.depot / "ventes.csv")
    monde.sources.append({"id": "1", "name": "Mon tableau", "path": work})
    with pytest.raises(ValueError, match="Mon tableau"):
        monde.server._supprimer_jeu("DS-A")
    assert len(monde.reg.list_datasets()) == 1 and os.path.exists(work)


def test_refus_si_planification_liee(monde):
    work = monde.jeu("DS-A", monde.depot / "ventes.csv")
    monde.planifs.append({"source": work})
    with pytest.raises(ValueError, match="planifi"):
        monde.server._supprimer_jeu("DS-A")
    assert os.path.exists(work)


def test_planification_sur_url_ne_bloque_pas(monde):
    url = "https://exemple.ci/donnees.csv"
    monde.jeu("DS-A", url)
    monde.planifs.append({"source": url})
    monde.server._supprimer_jeu("DS-A")
    assert monde.reg.list_datasets() == []


def test_jeu_inconnu(monde):
    with pytest.raises(FileNotFoundError):
        monde.server._supprimer_jeu("DS-INCONNU")


def test_statistiques_effacees(monde):
    (monde.meta / "DS-A.json").write_text("{}", encoding="utf-8")
    monde.jeu("DS-A", monde.depot / "ventes.csv")
    monde.server._supprimer_jeu("DS-A")
    assert os.listdir(monde.meta) == []
