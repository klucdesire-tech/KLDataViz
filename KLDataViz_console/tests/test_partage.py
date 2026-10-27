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


def test_envoyer_maintenant_avec_le_texte_du_champ(monkeypatch):
    """Bug : le texte « a@x.org, b@y.org » passait tel quel → Brevo recevait chaque caractère comme adresse."""
    reset_app_modules()
    import server  # noqa: PLC0415
    import studio  # noqa: PLC0415
    T, sources = server.T, server.sources
    envois = []
    monkeypatch.setattr(studio, "send_mail", lambda to, sujet, corps, piece=None: envois.append(to))
    with T.use(T.LOCAL):
        monkeypatch.setattr(sources, "connectable", lambda path: False)
        s = _tableau(sources)
        sources.send_mail_now(s["id"], None, None, to="chef@exemple.org, collegue@exemple.org")
        assert envois[-1] == ["chef@exemple.org", "collegue@exemple.org"]
        with pytest.raises(ValueError, match="invalide"):
            sources.send_mail_now(s["id"], None, None, to="chef@exemple.org, pas-une-adresse")


def test_supprimer_un_tableau_et_ses_liens(tmp_path):
    reset_app_modules()
    import sources  # noqa: PLC0415
    import shares  # noqa: PLC0415
    s = _tableau(sources)
    lien = shares.create(s["id"], 7)
    victime = tmp_path / "autre_espace.html"
    victime.write_text("tableau d'un autre espace", encoding="utf-8")
    with pytest.raises(KeyError):  # identifiant hors registre : rien n'est effacé, même par un chemin « ../ »
        sources.delete(os.path.relpath(str(victime.with_suffix("")), sources._dir()))
    assert victime.exists()
    sources.delete(s["id"])
    shares.revoke_all(s["id"])
    assert sources.list_sources() == [] and shares.resolve(lien["token"]) is None


def test_e_mail_avec_lien_de_partage(monkeypatch):
    """L'e-mail contient le lien de partage (actif réutilisé, sinon créé pour 30 jours) ; sans adresse publique
    (console locale), pas de lien ; l'adresse vue à l'envoi sert aussi aux envois planifiés."""
    reset_app_modules()
    import server  # noqa: PLC0415
    import studio  # noqa: PLC0415
    T, sources, shares = server.T, server.sources, server.shares
    corps = []
    monkeypatch.setattr(studio, "send_mail", lambda to, sujet, texte, piece=None: corps.append(texte))
    monkeypatch.delenv("KLDV_PUBLIC_URL", raising=False)
    with T.use(T.LOCAL):
        s = _tableau(sources)  # source connectée (fichier local)
        lecture = (lambda p: (pd.read_csv(p), None))
        sources.send_mail_now(s["id"], lecture, lambda df, spec: {"html": "<html><body>V2</body></html>"},
                              to="chef@exemple.org")
        assert "/t/" not in corps[-1] and "Ouvrez la pièce jointe" in corps[-1]           # local : pas de lien
        assert shares.list_for(s["id"]) == []
        r = sources.send_mail_now(s["id"], lecture, None, to="chef@exemple.org", base_url="https://kldv.exemple.org")
        lien = shares.list_for(s["id"])
        assert len(lien) == 1 and f"https://kldv.exemple.org/t/{lien[0]['token']}" in corps[-1]
        assert "Actualiser" in corps[-1] and "avec le lien de partage" in r["mail_status"]
        sources.send_mail_now(s["id"], lecture, None, to="chef@exemple.org")             # envoi planifié : adresse mémorisée
        assert len(shares.list_for(s["id"])) == 1 and lien[0]["token"] in corps[-1]      # même lien réutilisé
        # lien bientôt expiré : un nouveau lien est créé pour l'e-mail
        items = json.load(open(shares._path(), encoding="utf-8"))
        for x in items:
            x["expires_at"] = (dt.datetime.now() + dt.timedelta(days=2)).isoformat(timespec="seconds")
        json.dump(items, open(shares._path(), "w", encoding="utf-8"))
        sources.send_mail_now(s["id"], lecture, None, to="chef@exemple.org")
        assert len(shares.list_for(s["id"])) == 2 and lien[0]["token"] not in corps[-1]


def test_page_partagee_actualisable(monkeypatch):
    """Bandeau « Actualiser » sur la page partagée : relit la source, redirige vers l'adresse propre,
    limite a une relecture toutes les SHARE_REFRESH_S secondes ; pas de bouton pour un fichier importe."""
    reset_app_modules()
    import server  # noqa: PLC0415
    T, sources, shares = server.T, server.sources, server.shares
    with T.use(T.LOCAL):
        s = _tableau(sources)
        lien = shares.create(s["id"], 30)
    appels = []
    monkeypatch.setattr(server, "build_dashboard",
                        lambda df, spec=None, **k: appels.append(1) or {"html": "<html><body>TABLEAU NEUF</body></html>"})
    p = os.path.join(os.path.dirname(sources.get(s["id"], with_html=False)["path"]), "ventes_importees.csv")
    pd.DataFrame({"magasin": ["A", "B", "C"], "ca": [10, 20, 30]}).to_csv(p, index=False)   # la source change

    class SansRedirection(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    brut = urllib.request.build_opener(SansRedirection)
    httpd, base = start(server)
    try:
        with urllib.request.urlopen(base + "/t/" + lien["token"]) as r:
            html = r.read().decode()
        assert 'href="?actualiser=1"' in html and "Données lues le" in html and "TABLEAU 30" in html
        with pytest.raises(urllib.error.HTTPError) as e:
            brut.open(base + "/t/" + lien["token"] + "?actualiser=1")
        assert e.value.code == 303 and e.value.headers["Location"] == f"/t/{lien['token']}?maj=ok"
        with urllib.request.urlopen(base + e.value.headers["Location"]) as r:
            html = r.read().decode()
        assert "TABLEAU NEUF" in html and "actualisées à l'instant" in html and appels == [1]
        with pytest.raises(urllib.error.HTTPError) as e:                                # 2e clic tout de suite
            brut.open(base + "/t/" + lien["token"] + "?actualiser=1")
        assert e.value.headers["Location"].endswith("?maj=wait") and appels == [1]
        with pytest.raises(urllib.error.HTTPError) as e:                                # lien révoqué : rien n'est relu
            brut.open(base + "/t/jeton-inconnu?actualiser=1")
        assert e.value.code == 404
        # fichier importe (non connecte) : pas de bouton, mais le pourquoi est explique des le premier chargement
        with T.use(T.LOCAL):
            sources._update(s["id"], {"connected": False})
        with urllib.request.urlopen(base + "/t/" + lien["token"]) as r:
            page = r.read().decode()
        assert "?actualiser=1" not in page and "n'a pas de source à relire" in page
    finally:
        httpd.shutdown()
