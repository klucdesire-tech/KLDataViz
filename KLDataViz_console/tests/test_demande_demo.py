# -*- coding: utf-8 -*-
"""« Demander une démo » sur la page d'accueil : enregistrement, e-mail aux administrateurs, robots, doublons,
traitement dans l'administration (base simulée, aucun appel réseau, coordonnées fictives)."""
import os

import pytest

from _harness import reset_app_modules

ICI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMANDE = {"name": "Awa Test", "org": "Clinique Fictive", "email": "Awa@Exemple.ci", "phone": "+225 07 00 00 00 00",
           "message": "Suivi mensuel des consultations."}


@pytest.fixture()
def cloud(monkeypatch):
    reset_app_modules()
    import cloud as c  # noqa: PLC0415
    import tenancy as T  # noqa: PLC0415
    base = {"demandes": [], "recent": set()}
    sent = []

    def fake_q(sql, params=(), one=False):
        if "from app.demo_requests where lower(email)" in sql:
            return {"?": 1} if params[0] in base["recent"] else None
        if "insert into app.demo_requests" in sql:
            base["demandes"].append(params)
            base["recent"].add(params[1])
            return None
        if "from app.platform_admins" in sql:
            return [{"email": "Second@Admin.ci"}]
        if "update app.demo_requests" in sql:
            return {"id": params[-1]} if params[-1] == "11111111-1111-1111-1111-111111111111" else None
        return [] if not one else None
    monkeypatch.setattr(c, "q", fake_q)
    monkeypatch.setattr(c, "ADMIN_EMAILS", {"admin@test.ci"})
    monkeypatch.setattr(c, "send_mail", lambda to, subject, body, attachment=None: sent.append((to, subject, body)))
    c.base, c.sent = base, sent
    c.ctx = T.Ctx(key="adm", user_id="u", email="admin@test.ci", is_admin=True)
    return c


def test_demande_enregistree_et_admins_prevenus(cloud):
    assert cloud.demande_demo(dict(DEMANDE)) == {"ok": True}
    name, email, org, phone, message = cloud.base["demandes"][0]
    assert (name, email, org, phone) == ("Awa Test", "awa@exemple.ci", "Clinique Fictive", "+225 07 00 00 00 00")
    to, sujet, corps = cloud.sent[0]
    assert to == ["admin@test.ci", "second@admin.ci"] and sujet == "Demande de démo : Clinique Fictive"
    assert "awa@exemple.ci" in corps and "Suivi mensuel" in corps and "Demandes de démo" in corps


def test_meme_adresse_pas_de_doublon_ni_de_relance(cloud):
    cloud.demande_demo(dict(DEMANDE))
    assert cloud.demande_demo(dict(DEMANDE, name="Awa T.")) == {"ok": True}
    assert len(cloud.base["demandes"]) == 1 and len(cloud.sent) == 1


def test_robot_meme_reponse_rien_enregistre(cloud):
    assert cloud.demande_demo(dict(DEMANDE, site_web="http://spam.example")) == {"ok": True}
    assert cloud.base["demandes"] == [] and cloud.sent == []


@pytest.mark.parametrize("champ, valeur, erreur", [("name", "A", "nom"), ("email", "pas-une-adresse", "e-mail"),
                                                   ("org", " ", "organisation"), ("phone", "appelez-moi", "téléphone")])
def test_champs_controles(cloud, champ, valeur, erreur):
    with pytest.raises(ValueError, match=erreur):
        cloud.demande_demo(dict(DEMANDE, **{champ: valeur}))
    assert cloud.base["demandes"] == []


def test_pas_de_ligne_injectee_dans_l_e_mail(cloud):
    cloud.demande_demo(dict(DEMANDE, org="Clinique\r\nBcc: tous@exemple.ci", name="Awa\nTest"))
    _, sujet, corps = cloud.sent[0]
    assert "\n" not in sujet and "\r" not in sujet and "Nom : Awa Test" in corps


def test_echec_de_l_e_mail_garde_la_demande(cloud, monkeypatch):
    def panne(*a, **k):
        raise RuntimeError("Brevo indisponible")
    monkeypatch.setattr(cloud, "send_mail", panne)
    assert cloud.demande_demo(dict(DEMANDE)) == {"ok": True} and len(cloud.base["demandes"]) == 1


def test_statut_dans_l_administration(cloud):
    cloud.admin_demande_demo_statut(cloud.ctx, "11111111-1111-1111-1111-111111111111", "traitee")
    with pytest.raises(ValueError, match="Statut inconnu"):
        cloud.admin_demande_demo_statut(cloud.ctx, "11111111-1111-1111-1111-111111111111", "supprimee")
    with pytest.raises(ValueError, match="introuvable"):
        cloud.admin_demande_demo_statut(cloud.ctx, "pas-un-id", "traitee")
    with pytest.raises(ValueError, match="introuvable"):
        cloud.admin_demande_demo_statut(cloud.ctx, "22222222-2222-2222-2222-222222222222", "traitee")


def test_reserve_aux_administrateurs(cloud):
    import tenancy as T  # noqa: PLC0415
    simple = T.Ctx(key="x", user_id="x", email="client@exemple.ci", is_admin=False)
    with pytest.raises(T.Forbidden):
        cloud.admin_demandes_demo(simple)


def _lire(nom):
    with open(os.path.join(ICI, nom), encoding="utf-8") as f:
        return f.read()


def test_pages_et_routes():
    accueil = _lire("landing.html")
    assert accueil.count("data-demo>Demander une démo</button>") == 3  # navigation, haut de page, appel final
    assert 'id="demoDlg"' in accueil and "fetch('/api/public/demo'" in accueil and 'name="site_web"' in accueil
    admin = _lire("admin.html")
    assert 'data-tab="demos"' in admin and "/api/admin/demos/statut" in admin and "inviter({org:d.org,email:d.email" in admin
    serveur = _lire("server.py")
    assert 'path == "/api/public/demo"' in serveur and '_rate_ok(f"demo:{ip}", 3, 600)' in serveur
    migration = _lire(os.path.join("..", "supabase", "migrations", "20261104000000_demandes_demo.sql"))
    assert "enable row level security" in migration and "revoke all on app.demo_requests from anon, authenticated" in migration
