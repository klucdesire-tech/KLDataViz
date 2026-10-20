# -*- coding: utf-8 -*-
"""Partage d'un tableau de bord : lien sans compte (expiration, révocation, isolement), tableaux
enregistrés dans l'espace, envoi par e-mail (aucun envoi réel)."""
import datetime as dt
import json
import os
import tempfile
import urllib.error
import urllib.request

import pandas as pd
import pytest

from _harness import reset_app_modules, start


def _tableau(sources):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "ventes_importees.csv")
    df = pd.DataFrame({"magasin": ["A", "B"], "ca": [10, 20]})
    df.to_csv(p, index=False)
    return sources.connect(p, "Ventes", {"kpis": []}, df, "<html><body>TABLEAU 30</body></html>")


def test_lien_de_partage_public_expiration_revocation():
    reset_app_modules()
    import server  # noqa: PLC0415
    T, sources, shares = server.T, server.sources, server.shares
    with T.use(T.LOCAL):
        s = _tableau(sources)
        lien = shares.create(s["id"], 30)
        assert len(lien["token"]) >= 30 and shares.list_for(s["id"])[0]["token"] == lien["token"]
        with pytest.raises(ValueError):
            shares.create(s["id"], 365)                                     # durée non prévue
    httpd, base = start(server)
    try:
        with urllib.request.urlopen(base + "/t/" + lien["token"]) as r:
            html, csp = r.read().decode(), r.headers.get("Content-Security-Policy", "")
            assert "TABLEAU 30" in html and "sandbox" in csp and r.headers.get("X-Robots-Tag", "").startswith("noindex")
        with T.use(T.LOCAL):
            shares.revoke(lien["token"])
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/t/" + lien["token"])
        assert e.value.code == 404 and "expiré ou a été révoqué" in e.value.read().decode()
    finally:
        httpd.shutdown()
    # Lien expiré : refusé
    with T.use(T.LOCAL):
        l2 = shares.create(s["id"], 7)
    items = json.load(open(shares._path(), encoding="utf-8"))
    for x in items:
        x["expires_at"] = (dt.datetime.now() - dt.timedelta(minutes=1)).isoformat()
    json.dump(items, open(shares._path(), "w", encoding="utf-8"))
    assert shares.resolve(l2["token"]) is None


def test_un_espace_ne_revoque_pas_les_liens_d_un_autre():
    reset_app_modules()
    import tenancy as T  # noqa: PLC0415
    import shares  # noqa: PLC0415
    a, b = T.Ctx(key="a", org_id="org-a"), T.Ctx(key="b", org_id="org-b")
    with T.use(a):
        lien = shares.create("tab1", 30)
    with T.use(b):
        assert shares.list_for("tab1") == []
        with pytest.raises(KeyError):
            shares.revoke(lien["token"])
    assert shares.resolve(lien["token"]) == ("org-a", "tab1")


def test_tableau_enregistre_et_envoi_par_e_mail(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    import studio  # noqa: PLC0415
    T, sources = server.T, server.sources
    envois = []
    monkeypatch.setattr(studio, "send_mail", lambda to, sujet, corps, piece=None: envois.append((to, sujet, open(piece, encoding="utf-8").read())))
    with T.use(T.LOCAL):
        monkeypatch.setattr(sources, "connectable", lambda path: False)   # comme un fichier importé en ligne
        s = _tableau(sources)
        assert s["connected"] is False and [x["id"] for x in sources.list_sources()] == [s["id"]]
        with pytest.raises(ValueError, match="réimportez"):
            sources.refresh(s["id"], lambda p: (None, None), lambda df, spec: {})
        with pytest.raises(ValueError, match="invalide"):
            sources.set_mail(s["id"], "pas-une-adresse", "weekly")
        r = sources.set_mail(s["id"], "chef@exemple.org; collegue@exemple.org", "weekly")
        assert r["mail_to"] == ["chef@exemple.org", "collegue@exemple.org"] and r["next_mail"]
        sources.send_mail_now(s["id"], lambda p: (None, None), lambda df, spec: {})
        to, sujet, piece = envois[-1]
        assert to == ["chef@exemple.org", "collegue@exemple.org"] and "Ventes" in sujet and "TABLEAU 30" in piece
        assert sources.due_mails(dt.datetime.now() + dt.timedelta(days=8)) == [s["id"]]


def test_sens_souhaite_des_indicateurs_hors_sante():
    import sys  # noqa: PLC0415
    reset_app_modules()
    import server  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import _bon  # noqa: PLC0415
    assert _bon("Taux d'annulation") == -1 and _bon("Jours d'absence moyens") == -1 and _bon("Taux de départ volontaire") == -1
    assert _bon("Chiffre d'affaires total") == 1 and _bon("Taux d'admission") == 1
    assert _bon("Population par département") == 1 and _bon("Ventes de panneaux solaires") == 1   # mots entiers
