# -*- coding: utf-8 -*-
"""Échec d'envoi d'e-mail : la cause donnée par Brevo est rendue lisible (et un conseil adapté)."""
import io
import urllib.error

from _harness import reset_app_modules


def _refus(monkeypatch, cloud, code, body):
    def boom(*a, **k):
        raise urllib.error.HTTPError("https://api.brevo.com", code, "x", {}, io.BytesIO(body.encode()))
    monkeypatch.setattr(cloud, "BREVO_API_KEY", "k")
    monkeypatch.setattr(cloud, "MAIL_FROM", "a@b.c")
    monkeypatch.setattr(cloud.urllib.request, "urlopen", boom)
    try:
        cloud.send_mail(["x@y.z"], "s", "b")
    except RuntimeError as exc:
        return cloud.mail_failure(exc)
    raise AssertionError("aucune erreur levée")


def test_cause_de_l_echec_affichee(monkeypatch):
    reset_app_modules()
    import cloud  # noqa: PLC0415
    ip = _refus(monkeypatch, cloud, 401, '{"code":"unauthorized","message":"We have detected you are using an unrecognised IP address 1.2.3.4"}')
    assert "401" in ip and "unrecognised IP" in ip and "IP autorisées" in ip
    cle = _refus(monkeypatch, cloud, 401, '{"code":"unauthorized","message":"Key not found"}')
    assert "Clé API invalide" in cle
    monkeypatch.setattr(cloud, "BREVO_API_KEY", "")
    try:
        cloud.send_mail(["x@y.z"], "s", "b")
    except RuntimeError as exc:
        assert "non configuré" in cloud.mail_failure(exc)
