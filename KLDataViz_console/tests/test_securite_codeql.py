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
    # OneDrive personnel : lien laissé tel quel, lu par netguard.fetch_onedrive (l'API anonyme répond 401)
    for perso in ("https://1drv.ms/x/s!AbCdEf123?e=xyz", "https://1drv.ms/x/c/7374a4e6/IQBTHmm4?e=74a2Tl",
                  "https://onedrive.live.com/download?cid=1&resid=2"):
        assert n(perso) == perso
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
    monkeypatch.setattr(server.netguard, "fetch_onedrive", refus)
    for lien in ("https://1drv.ms/x/s!AbC", "https://contoso-my.sharepoint.com/:x:/g/personal/awa/EaBc"):
        df, err = server._read_df_raw(lien)
        assert df is None and err == server.ONEDRIVE_PARTAGE


def test_onedrive_personnel_comme_un_navigateur():
    """Nouveau OneDrive (1drv.ms/x/c/…) : lien → page d'aperçu avec cookie de partage → download.aspx?UniqueId=…
    Simulé par un petit serveur local ; le fichier n'est rendu qu'avec le cookie déposé à la première étape."""
    import http.server  # noqa: PLC0415
    import threading  # noqa: PLC0415
    import netguard  # noqa: PLC0415
    uid = "b8691e53-6a5f-469d-a008-4b320652d5b6"

    class Faux(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/x/c/"):
                self.send_response(302)
                self.send_header("Set-Cookie", "FedAuth=jeton; Path=/")
                self.send_header("Location", f"/personal/abc/_layouts/15/Doc.aspx?sourcedoc=%7B{uid}%7D&action=default")
                self.end_headers()
            elif self.path.startswith("/personal/abc/_layouts/15/Doc.aspx"):
                self._repondre(b"<!DOCTYPE html><title>Classeur1.xlsx</title>", "text/html")
            elif self.path == f"/personal/abc/_layouts/15/download.aspx?UniqueId={uid}&Translate=false" \
                    and "FedAuth=jeton" in (self.headers.get("Cookie") or ""):
                self._repondre(b"PK classeur" + b"x" * 5000, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            elif self.path.startswith("/direct"):   # lien qui renvoie déjà le fichier
                self._repondre(b"a;b\n1;2\n", "text/csv")
            elif self.path.startswith("/vide"):     # lien expiré ou restreint : page sans fichier
                self._repondre(b"<!DOCTYPE html><title>Connexion</title>", "text/html")
            else:
                self.send_response(403)
                self.end_headers()

        def _repondre(self, corps, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

    srv = http.server.HTTPServer(("127.0.0.1", 0), Faux)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    lire = lambda chemin, **k: netguard.fetch_onedrive(base + chemin, allow_private=True, https_only=False, **k)  # noqa: E731
    try:
        data, ctype = lire("/x/c/abc/IQB?e=1")
        assert data.startswith(b"PK") and "spreadsheetml" in ctype
        assert lire("/direct") == (b"a;b\n1;2\n", "text/csv")
        data, ctype = lire("/vide")                      # renvoyée telle quelle : l'appelant affiche ONEDRIVE_PARTAGE
        assert "text/html" in ctype and b"Connexion" in data
        with pytest.raises(ValueError, match="trop volumineux"):
            lire("/x/c/abc/IQB?e=1", max_bytes=1000)     # plafond appliqué aussi au téléchargement final
    finally:
        srv.shutdown()


def test_page_web_jamais_prise_pour_un_fichier():
    reset_app_modules()
    import server  # noqa: PLC0415
    assert server._est_page_web(b"<!DOCTYPE html><html>", "")
    assert server._est_page_web(b"a;b", "text/html; charset=utf-8")
    assert not server._est_page_web(b"PK\x03\x04", "application/octet-stream")


def test_onedrive_personnel_refuse_les_adresses_internes():
    import netguard  # noqa: PLC0415
    import pytest  # noqa: PLC0415
    with pytest.raises(netguard.BlockedURL):
        netguard.fetch_onedrive("http://127.0.0.1:9/x/c/abc", allow_private=False, https_only=False)


def test_ingestion_locale_onedrive_et_page_web(monkeypatch):
    """Version installée : l'agent d'ingestion lit OneDrive comme un navigateur, refuse une page web et nomme
    un classeur sans extension d'après son contenu (avant : enregistré en .csv)."""
    reset_app_modules()
    import netguard  # noqa: PLC0415
    from ingestion import agent  # noqa: PLC0415
    appels = []
    monkeypatch.setattr(netguard, "fetch_onedrive", lambda url, **k: (appels.append("onedrive"), (b"PK\x03\x04", "application/octet-stream"))[1])
    monkeypatch.setattr(netguard, "fetch", lambda url, **k: (appels.append("web"), (b"<!DOCTYPE html><html>", "text/html"))[1])
    chemin = agent._download("https://1drv.ms/x/c/abc/IQB?e=1")
    assert appels == ["onedrive"] and chemin.suffix == ".xlsx"
    with pytest.raises(ValueError, match="page web"):
        agent._download("https://exemple.org/rapport")
    assert appels == ["onedrive", "web"]
