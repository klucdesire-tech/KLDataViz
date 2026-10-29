# -*- coding: utf-8 -*-
"""Correctifs des alertes CodeQL (octobre 2026) : SSRF de l'agent d'ingestion, hôtes Google,
DSN masqués, regex de plan et d'e-mail sans retour arrière catastrophique."""
import os
import re
import time

import pytest

from _harness import APP, reset_app_modules


def test_agent_ingestion_telecharge_via_netguard(monkeypatch):
    """L'agent d'ingestion ne télécharge plus en direct : en ligne, une adresse interne est refusée."""
    reset_app_modules()
    import netguard  # noqa: PLC0415
    from ingestion import agent  # noqa: PLC0415
    monkeypatch.setenv("KLDV_MODE", "cloud")
    monkeypatch.setattr(agent, "_netguard", lambda: netguard)
    with pytest.raises(netguard.BlockedURL):
        agent._download("https://127.0.0.1/donnees.csv")
    with pytest.raises(netguard.BlockedURL):
        agent._download("http://exemple.org/donnees.csv")  # http refusé en ligne


def test_agent_ingestion_suffixe_de_fichier_sain(monkeypatch, tmp_path):
    reset_app_modules()
    from ingestion import agent  # noqa: PLC0415

    class FakeNetguard:
        @staticmethod
        def fetch(url, allow_private=False, https_only=True):
            return b"a,b\n1,2\n", "text/csv"
    monkeypatch.setattr(agent, "_netguard", lambda: FakeNetguard)
    assert agent._download("https://exemple.org/x.csv").suffix == ".csv"
    assert agent._download("https://exemple.org/x.c$v/../../").suffix == ".csv"
    assert agent._download("https://exemple.org/x.XLSX").suffix == ".xlsx"
    assert agent._download("https://exemple.org/x.sh").suffix == ".csv"  # hors liste


def test_dsn_masque_partout():
    reset_app_modules()
    from ingestion import agent  # noqa: PLC0415
    assert agent._mask("postgresql://lecteur:S3cret!@db.exemple.org:5432/base") == \
        "postgresql://lecteur:***@db.exemple.org:5432/base"
    assert "S3cret" not in agent._mask("host=db user=lecteur password=S3cret dbname=base")
    assert agent._mask("postgresql://u:a/b@h/db") == "postgresql://u:***@h/db"  # « / » dans le mot de passe
    assert agent._mask("postgresql://u@h/db") == "postgresql://u@h/db"
    from ingestion import store  # noqa: PLC0415  le registre (--summary) masque aussi la forme URL
    assert store._mask("postgresql://lecteur:S3cret@db/base") == "postgresql://lecteur:***@db/base"
    t = time.time()
    agent._mask("://!:" * 20_000)  # pas de retour arrière catastrophique
    assert time.time() - t < 1


def test_hotes_google_compares_exactement():
    reset_app_modules()
    import server  # noqa: PLC0415
    assert server._host_is("https://docs.google.com/spreadsheets/d/abc/edit", "docs.google.com")
    assert server._host_is("https://drive.google.com/file/d/xyz/view", "google.com")
    assert not server._host_is("https://docs.google.com.piege.test/spreadsheets/d/abc", "docs.google.com")
    assert not server._host_is("https://piege.test/?u=docs.google.com", "docs.google.com")
    assert not server._host_is("https://notgoogle.com/", "google.com")
    # un faux domaine n'est plus réécrit en lien d'export Google
    faux = "https://docs.google.com.piege.test/spreadsheets/d/abc/edit"
    assert server.normalize_source_url(faux) == faux
    vrai = server.normalize_source_url("https://docs.google.com/spreadsheets/d/abc/edit#gid=7")
    assert vrai == "https://docs.google.com/spreadsheets/d/abc/export?format=csv&gid=7"


def test_regex_de_plan_dangereuse_refusee():
    reset_app_modules()
    import data_architect  # noqa: PLC0415
    for ok in (r"^(\S+)", r"(\d+)-(\d+)", r"(?P<periode>\S+ \d{4}) - (?P<mesure>.+?)$"):
        data_architect._spec_regex(ok)
    for mauvaise in (r"(a+)+$", r"(\w*)*x", r"((ab)*)+", r"(x+){2,}", r"(a{1,9})+", "a" * 600, "(non fermée"):
        with pytest.raises(ValueError):
            data_architect._spec_regex(mauvaise)


@pytest.mark.parametrize("module", ["cloud", "sources", "studio"])
def test_regex_email_lineaire(module):
    reset_app_modules()
    src = open(os.path.join(APP, f"{module}.py"), encoding="utf-8").read()
    motif = r"^[^@\s]{1,64}@[^@\s.]{1,63}(?:\.[^@\s.]{1,63})+$"
    assert motif in src and r"^[^@\s]+@[^@\s]+\.[^@\s]+$" not in src
    rx = re.compile(motif)
    assert rx.match("luc.k@mail.gouv.ci") and not rx.match("a@b") and not rx.match("a@b..ci")
    t = time.time()
    rx.match("a@" + "b" * 200_000 + "!")
    assert time.time() - t < 1


def test_liens_onedrive_et_sharepoint_convertis():
    reset_app_modules()
    import server  # noqa: PLC0415
    n = server.normalize_source_url
    # jeton de référence figé (base64url sans « = » du lien d'origine, format « u! » de l'API de partage)
    assert n("https://1drv.ms/x/s!AbCdEf123?e=xyz") == \
        "https://api.onedrive.com/v1.0/shares/u!aHR0cHM6Ly8xZHJ2Lm1zL3gvcyFBYkNkRWYxMjM_ZT14eXo/root/content"
    direct = "https://onedrive.live.com/download?cid=1&resid=2"
    assert n(direct) == direct                                     # déjà un lien de téléchargement
    assert n("https://onedrive.live.com/downloadxyz?a=1").startswith("https://api.onedrive.com/")
    pro = "https://contoso-my.sharepoint.com/:x:/g/personal/awa_contoso_com/EaBc?e=4f2"
    assert n(pro) == pro + "&download=1"
    assert n(pro + "&download=1") == pro + "&download=1"
    assert n(pro + "&Download=0") == pro + "&download=1"           # un seul download, quelle que soit la casse
    assert n("https://contoso.sharepoint.com/:x:/s/ventes/EaBc?e=1&") == "https://contoso.sharepoint.com/:x:/s/ventes/EaBc?e=1&download=1"
    assert n("https://contoso.sharepoint.com/:x:/s/ventes/EaBc#x") == "https://contoso.sharepoint.com/:x:/s/ventes/EaBc?download=1"
    for faux in ("https://1drv.ms.piege.test/x/s!abc", "https://piege.test/?u=sharepoint.com"):
        assert n(faux) == faux                                      # un faux domaine n'est jamais réécrit


def test_nom_de_source_onedrive_stable_et_distinct():
    reset_app_modules()
    import server  # noqa: PLC0415
    a, b = "https://1drv.ms/x/s!AAA", "https://1drv.ms/x/s!BBB"
    assert server.source_label(a) == server.source_label(a) != server.source_label(b)
    assert server.source_label(a).startswith("onedrive_")
    assert server.source_label("https://contoso-my.sharepoint.com/:x:/g/personal/awa/EaBc").startswith("onedrive_")


def test_page_onedrive_au_lieu_du_fichier(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    monkeypatch.setattr(server.netguard, "fetch", lambda *a, **k: (b"<!DOCTYPE html><html>connexion</html>", "text/html"))
    df, err = server._read_df_raw("https://contoso-my.sharepoint.com/:x:/g/personal/awa/EaBc")
    assert df is None and "Toute personne disposant du lien" in err


def test_acces_onedrive_refuse(monkeypatch):
    reset_app_modules()
    import urllib.error  # noqa: PLC0415
    import server  # noqa: PLC0415
    def refus(url, *a, **k):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(server.netguard, "fetch", refus)
    for lien in ("https://1drv.ms/x/s!AbC", "https://contoso-my.sharepoint.com/:x:/g/personal/awa/EaBc"):
        df, err = server._read_df_raw(lien)
        assert df is None and err == server.ONEDRIVE_PARTAGE
