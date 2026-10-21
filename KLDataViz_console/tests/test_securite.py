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
