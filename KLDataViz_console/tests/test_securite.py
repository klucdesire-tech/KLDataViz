# -*- coding: utf-8 -*-
"""Audit de sécurité (octobre 2026) : console locale protégée des autres sites, anti-SSRF épinglé."""
import socket
import urllib.request

import pytest

from _harness import reset_app_modules, start


@pytest.fixture()
def base():
    reset_app_modules()
    import server  # noqa: PLC0415
    httpd, url = start(server)
    yield url
    httpd.shutdown()


def _req(url, origin=None, host=None):
    r = urllib.request.Request(url)
    if origin:
        r.add_header("Origin", origin)
    if host:
        r.add_header("Host", host)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers


def test_console_locale_refuse_les_autres_sites(base):
    assert _req(base + "/api/settings")[0] == 200                                   # ses propres pages
    assert _req(base + "/api/settings", origin=base)[0] == 200
    assert _req(base + "/api/settings", origin="https://site-malveillant.example")[0] == 403
    assert _req(base + "/api/settings", host="attaquant.example:80")[0] == 403        # DNS rebinding
    assert _req(base + "/api/settings")[1].get("Access-Control-Allow-Origin") is None


def test_anti_ssrf_resiste_au_dns_rebinding(monkeypatch):
    reset_app_modules()
    import netguard  # noqa: PLC0415
    vrai = socket.getaddrinfo
    appels = {"n": 0}

    def dns_piege(host, *a, **k):  # adresse publique au contrôle, puis interne à la connexion
        if host != "piege.test":
            return vrai(host, *a, **k)
        appels["n"] += 1
        ip = "93.184.215.14" if appels["n"] == 1 else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]
    monkeypatch.setattr(socket, "getaddrinfo", dns_piege)
    with pytest.raises(netguard.BlockedURL):
        netguard.fetch("https://piege.test/donnees.csv")
    assert appels["n"] >= 2


def test_anti_ssrf_essaie_chaque_adresse_verifiee(monkeypatch):
    """Régression relevée par Hermes : la 1re adresse (ex. IPv6 sans route) ne doit pas faire échouer l'import."""
    reset_app_modules()
    import netguard  # noqa: PLC0415
    monkeypatch.setattr(netguard, "public_ips", lambda h, p: ["2001:db8::1", "93.184.215.14"])
    essais = []

    def connexion(addr, *a, **k):
        essais.append(addr[0])
        if addr[0] == "2001:db8::1":
            raise OSError("réseau impossible à atteindre")
        raise ConnectionRefusedError("arrêt du test après le choix de l'adresse")
    monkeypatch.setattr(socket, "create_connection", connexion)
    conn = netguard._PinnedHTTPSConnection("exemple.test", 443, timeout=5)
    with pytest.raises(ConnectionRefusedError):
        conn.connect()
    assert essais == ["2001:db8::1", "93.184.215.14"]


def test_validate_refuse_les_chaines_de_sous_modules():
    reset_app_modules()
    import sandbox  # noqa: PLC0415
    for attr in ("builtins", "importlib", "inspect", "overrides", "getattr"):
        with pytest.raises(sandbox.UnsafeCode):
            sandbox.validate(f"x = np.{attr}")
    sandbox.validate("result = data['x']['code'].nunique()")  # une colonne « code » reste utilisable
