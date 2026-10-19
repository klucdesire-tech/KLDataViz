# -*- coding: utf-8 -*-
"""Administration : e-mail de test, renvoi d'invitation, pas de second espace pour une invitation en attente
(base simulée, aucun appel réseau)."""
import pytest

from _harness import reset_app_modules


@pytest.fixture()
def cloud(monkeypatch):
    reset_app_modules()
    import cloud as c  # noqa: PLC0415
    import tenancy as T  # noqa: PLC0415
    sent, calls = [], []

    def fake_q(sql, params=(), one=False):
        calls.append(sql)
        if "from app.invitations i join app.organizations o" in sql and "expires_at > now()" in sql:
            return {"name": "Espace existant"} if params[0] == "deja@test.ci" else None
        if "from app.invitations i join app.organizations o" in sql:
            return {"id": "inv1", "name": "Clinique", "plan": "essai"} if params[1] == "client@test.ci" else None
        if "insert into app.organizations" in sql:
            return {"id": "org-new"}
        return None
    monkeypatch.setattr(c, "q", fake_q)
    monkeypatch.setattr(c, "_admin", lambda ctx: None)
    monkeypatch.setattr(c, "plans", lambda: {"essai": {"label": "Essai", "days": 14}})
    monkeypatch.setattr(c, "send_mail", lambda to, subject, body, attachment=None: sent.append((to, subject)))
    c.sent, c.calls, c.ctx = sent, calls, T.Ctx(key="adm", user_id="u", email="admin@test.ci", is_admin=True)
    return c


def test_pas_de_second_espace_pour_une_invitation_en_attente(cloud):
    with pytest.raises(ValueError, match="déjà en attente.*Espace existant"):
        cloud.admin_invite_client(cloud.ctx, "Nouvel espace", "deja@test.ci", "essai")
    assert not any("insert into app.organizations" in s for s in cloud.calls)
    r = cloud.admin_invite_client(cloud.ctx, "Nouvel espace", "neuf@test.ci", "essai")
    assert r["email_sent"] and cloud.sent[-1][0] == ["neuf@test.ci"]


def test_renvoyer_l_invitation(cloud):
    r = cloud.admin_resend_invite(cloud.ctx, "org1", "Client@Test.ci")
    assert r["email_sent"] and "invite=1" in r["link"] and cloud.sent[-1][0] == ["client@test.ci"]
    assert any("interval '30 days'" in s for s in cloud.calls)          # invitation prolongée
    with pytest.raises(ValueError, match="Aucune invitation"):
        cloud.admin_resend_invite(cloud.ctx, "org1", "inconnu@test.ci")


def test_e_mail_de_test(cloud, monkeypatch):
    r = cloud.admin_test_mail(cloud.ctx)
    assert r["email_sent"] and r["to"] == "admin@test.ci" and cloud.sent[-1][0] == ["admin@test.ci"]

    def refus(*a, **k):
        raise RuntimeError("Envoi refusé par Brevo (401) : unrecognised IP address.")
    monkeypatch.setattr(cloud, "send_mail", refus)
    r = cloud.admin_test_mail(cloud.ctx)
    assert not r["email_sent"] and "unrecognised IP" in r["email_error"]
