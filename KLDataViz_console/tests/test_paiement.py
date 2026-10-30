# -*- coding: utf-8 -*-
"""Abonnements CinetPay : le client HTTP et le parcours complet, avec un faux prestataire.

Le parcours tourne sur un vrai PostgreSQL (migrations du dépôt) si KLDV_TEST_PG est défini ; sinon il est ignoré."""
import datetime as dt
import glob
import os
import threading
import uuid

import pytest

from _harness import reset_app_modules

PG = os.environ.get("KLDV_TEST_PG")
HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS = sorted(glob.glob(os.path.join(HERE, "..", "..", "supabase", "migrations", "*.sql")))
USER = "00000000-0000-0000-0000-000000000001"


class FauxCinetPay:
    """Répond comme l'API v1 : connexion, création de paiement et statut."""

    def __init__(self):
        self.statuts, self.appels, self.expire_une_fois, self.panne = {}, [], False, False

    def __call__(self, method, path, body=None, jeton=None):
        self.appels.append((method, path))
        if self.panne:
            raise self.err("CinetPay injoignable.")
        if path == "/v1/oauth/login":
            return 200, {"code": 200, "status": "OK", "access_token": "jwt"}
        if self.expire_une_fois:
            self.expire_une_fois = False
            return 401, {"code": 1003, "status": "EXPIRED_TOKEN", "description": "expiré"}
        if method == "POST" and path == "/v1/payment":
            self.dernier = body
            return 200, {"code": 200, "status": "OK", "payment_token": "pt", "notify_token": "nt-" + body["merchant_transaction_id"],
                         "transaction_id": "cp-1", "merchant_transaction_id": body["merchant_transaction_id"],
                         "payment_url": "https://checkout.cinetpay.co/pay"}
        tx = path.rsplit("/", 1)[1]
        return 200, {"code": 2001, "status": "INITIATED", "merchant_transaction_id": tx, **self.statuts.get(tx, {})}


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.setenv("CINETPAY_API_KEY", "sk_test_cle")
    monkeypatch.setenv("CINETPAY_API_PASSWORD", "secret")
    if PG:
        monkeypatch.setenv("DATABASE_URL", PG)
    reset_app_modules()
    import cloud
    import paiement
    faux = FauxCinetPay()
    faux.err = paiement.PaiementError
    monkeypatch.setattr(paiement, "_http", faux)
    monkeypatch.setattr(paiement, "_jeton", (0.0, ""))
    monkeypatch.setattr(cloud, "APP_URL", "https://kld.test")
    monkeypatch.setattr(cloud, "mail_ready", lambda: False)
    return cloud, paiement, faux


def test_cle_de_test_vise_le_bac_a_sable(modules, monkeypatch):
    _, paiement, _ = modules
    assert paiement._base() == paiement.URL_TEST
    monkeypatch.setenv("CINETPAY_API_KEY", "sk_live_cle")
    assert paiement._base() == paiement.URL_PROD


def test_jeton_expire_renouvele_une_fois(modules):
    _, paiement, faux = modules
    faux.expire_une_fois = True
    assert paiement.statut("TX")["status"] == "INITIATED"
    assert [a for a in faux.appels if a[1] == "/v1/oauth/login"].__len__() == 2


def test_montant_hors_limites_refuse(modules):
    _, paiement, _ = modules
    with pytest.raises(paiement.PaiementError):
        paiement.initier("T", 50, "XOF", "x", "a@b.ci", "Cl", "No", "https://a", "https://b", "https://c")


def test_erreur_sans_identifiant_dans_le_message(modules, monkeypatch):
    _, paiement, _ = modules
    monkeypatch.setattr(paiement, "_http", lambda *a, **k: (401, {"code": 1005, "status": "INVALID_CREDENTIALS"}))
    with pytest.raises(paiement.PaiementError) as e:
        paiement.statut("TX")
    assert "secret" not in str(e.value) and "sk_test" not in str(e.value)


def _billing(cloud, monkeypatch, is_admin):
    forfaits = [{"key": "essai", "price": 0, "days": 14}, {"key": "pro", "price": None, "days": None},
                {"key": "equipe", "price": 50000.0, "days": None}, {"key": "suspendu", "price": 0, "days": None}]
    monkeypatch.setattr(cloud, "public_plans", lambda: forfaits)
    monkeypatch.setattr(cloud, "q", lambda *a, **k: {} if k.get("one") else [])
    ctx = cloud.Ctx(key="u", org_id="o", user_id="u", email="a@b.ci", role="owner", is_admin=is_admin, plan={"key": "essai"})
    return cloud.billing(ctx)


def test_apercu_reserve_a_l_administrateur(modules, monkeypatch):
    cloud, _, _ = modules
    monkeypatch.delenv("CINETPAY_API_KEY")
    b = _billing(cloud, monkeypatch, True)
    assert b["apercu"] is True and b["actif"] is False and [o["key"] for o in b["offres"]] == ["pro", "equipe"]
    b = _billing(cloud, monkeypatch, False)
    assert b["apercu"] is False and b["actif"] is False and [o["key"] for o in b["offres"]] == ["equipe"]


def test_un_forfait_sans_prix_reste_invendable(modules, monkeypatch):
    cloud, _, _ = modules
    monkeypatch.setattr(cloud, "public_plans", lambda: [{"key": "pro", "price": None, "days": None}])
    assert cloud._offres() == []


# ---- parcours complet, sur PostgreSQL
@pytest.fixture
def pg(modules):
    if not PG:
        pytest.skip("KLDV_TEST_PG non défini")
    cloud, paiement, faux = modules
    cloud._cache.clear()
    org = str(uuid.uuid4())
    cloud.q("""insert into app.organizations (id, name, plan, trial_end) values (%s, 'Espace test', 'essai', now() + interval '3 days')""", (org,))
    cloud.q("update app.plans set price = 15000 where key = 'pro'")
    cloud.q("update app.plans set price = 50000 where key = 'equipe'")
    ctx = cloud.Ctx(key=USER, org_id=org, user_id=USER, email="o@test.ci", role="owner", is_admin=True, plan={"key": "essai", "org_name": "Espace test"})
    return cloud, paiement, faux, ctx, org


def _org(cloud, org):
    return cloud.q("select plan, paid_until, trial_end from app.organizations where id = %s", (org,), one=True)


def _tx(cloud, org):
    return cloud.q("select * from app.payments where org_id = %s order by created_at desc limit 1", (org,), one=True)


def _payer(cloud, faux, ctx, plan="pro", mois=1):
    cloud.checkout(ctx, plan, mois)
    p = _tx(cloud, ctx.org_id)
    faux.statuts[p["merchant_tx"]] = {"code": 100, "status": "SUCCESS", "transaction_id": "cp-1"}
    assert cloud.notification({"merchant_transaction_id": p["merchant_tx"], "notify_token": p["notify_token"]})
    return p


def test_le_prix_vient_de_la_base(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 3)
    assert faux.dernier["amount"] == 45000 and faux.dernier["currency"] == "XOF"
    assert _tx(cloud, org)["amount"] == 45000


@pytest.mark.parametrize("plan,mois", [("essai", 1), ("suspendu", 1), ("inconnu", 1), ("pro", 2), ("pro", "x"), ("pro", None)])
def test_offre_ou_duree_invalide(pg, plan, mois):
    cloud, _, _, ctx, _ = pg
    with pytest.raises(ValueError):
        cloud.checkout(ctx, plan, mois)


def test_seul_le_proprietaire_paie(pg):
    cloud, _, _, ctx, _ = pg
    ctx.role = "analyst"
    with pytest.raises(cloud.Forbidden):
        cloud.checkout(ctx, "pro", 1)


def test_paiement_active_le_forfait(pg):
    cloud, _, _, ctx, org = pg
    p = _payer(cloud, pg[2], ctx)
    o = _org(cloud, org)
    assert o["plan"] == "pro" and o["trial_end"] is None
    assert 27 <= (o["paid_until"] - dt.datetime.now(dt.timezone.utc)).days <= 31
    assert _tx(cloud, org)["status"] == "paid" and p["mode"] == "test"


def test_notification_rejouee_ne_compte_qu_une_fois(pg):
    cloud, _, faux, ctx, org = pg
    p = _payer(cloud, faux, ctx)
    fin = _org(cloud, org)["paid_until"]
    assert cloud.notification({"merchant_transaction_id": p["merchant_tx"], "notify_token": p["notify_token"]})
    assert _org(cloud, org)["paid_until"] == fin


def test_confirmations_simultanees_comptent_une_fois(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    res = []
    ts = [threading.Thread(target=lambda: res.append(cloud._activer(str(p["id"])))) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len([r for r in res if r]) == 1


def test_faux_jeton_refuse(pg):
    cloud, _, _, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    assert not cloud.notification({"merchant_transaction_id": p["merchant_tx"], "notify_token": "pirate"})
    assert not cloud.notification({"merchant_transaction_id": p["merchant_tx"], "notify_token": "é"})
    assert not cloud.notification({"merchant_transaction_id": "inconnu", "notify_token": "x"})
    assert _org(cloud, org)["plan"] == "essai"


def test_notification_authentique_sans_paiement_reel(pg):
    """Le jeton seul n'active rien : c'est le statut lu chez CinetPay qui décide."""
    cloud, _, _, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    assert cloud.notification({"merchant_transaction_id": p["merchant_tx"], "notify_token": p["notify_token"]})
    assert _org(cloud, org)["plan"] == "essai" and _tx(cloud, org)["status"] == "pending"


def test_paiement_echoue(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    faux.statuts[p["merchant_tx"]] = {"code": 2010, "status": "FAILED"}
    cloud.synchroniser_espace(ctx)
    assert _tx(cloud, org)["status"] == "failed" and _org(cloud, org)["plan"] == "essai"


def test_succes_apres_echec_active_quand_meme(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    cloud.q("update app.payments set status = 'failed' where id = %s", (p["id"],))
    faux.statuts[p["merchant_tx"]] = {"code": 100, "status": "SUCCESS"}
    assert cloud.notification({"merchant_transaction_id": p["merchant_tx"], "notify_token": p["notify_token"]})
    assert _org(cloud, org)["plan"] == "pro"


def test_retour_navigateur_sans_notification(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    faux.statuts[_tx(cloud, org)["merchant_tx"]] = {"code": 100, "status": "SUCCESS"}
    assert cloud.synchroniser_espace(ctx)["paye"] is True
    assert _org(cloud, org)["plan"] == "pro"


def test_cinetpay_en_panne_pendant_la_verification(pg):
    cloud, paiement, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    faux.panne = True
    with pytest.raises(paiement.PaiementError):
        cloud.notification({"merchant_transaction_id": p["merchant_tx"], "notify_token": p["notify_token"]})
    assert cloud.synchroniser_espace(ctx)["paye"] is False
    assert _tx(cloud, org)["status"] == "pending"


def test_renouvellement_ajoute_la_duree(pg):
    cloud, _, faux, ctx, org = pg
    _payer(cloud, faux, ctx)
    fin = _org(cloud, org)["paid_until"]
    _payer(cloud, faux, ctx)
    assert (_org(cloud, org)["paid_until"] - fin).days >= 27


def test_passer_au_forfait_superieur_convertit_le_temps(pg):
    cloud, _, faux, ctx, org = pg
    _payer(cloud, faux, ctx, "pro", 12)
    _payer(cloud, faux, ctx, "equipe", 1)
    o = _org(cloud, org)
    jours = (o["paid_until"] - dt.datetime.now(dt.timezone.utc)).days
    assert o["plan"] == "equipe" and 130 <= jours <= 145   # 1 mois + environ 365 j x 15 000 / 50 000


def test_fin_d_abonnement_suspend_sans_perte(pg):
    cloud, _, faux, ctx, org = pg
    _payer(cloud, faux, ctx)
    cloud.q("delete from app.memberships where user_id = %s", (USER,))
    cloud.q("insert into app.memberships (user_id, org_id, role) values (%s, %s, 'owner')", (USER, org))
    user = {"id": USER, "email": "o@test.ci"}
    assert cloud.resolve(user).plan["key"] == "pro"
    cloud.q("update app.organizations set paid_until = now() - interval '1 hour' where id = %s", (org,))
    assert cloud.resolve(user).plan["key"] == "suspendu"
    assert _org(cloud, org)["plan"] == "pro"


def test_admin_voit_les_paiements(pg):
    cloud, _, faux, ctx, _ = pg
    p = _payer(cloud, faux, ctx)
    assert any(x["merchant_tx"] == p["merchant_tx"] and x["org"] == "Espace test"
               for x in cloud.admin_payments(cloud.Ctx(key="a", is_admin=True)))
    ctx.is_admin = False
    with pytest.raises(cloud.Forbidden):
        cloud.admin_payments(ctx)


def test_forfait_manuel_sans_echeance(pg):
    cloud, _, faux, ctx, org = pg
    _payer(cloud, faux, ctx)
    cloud.admin_set_plan(cloud.Ctx(key="a", is_admin=True), org, "equipe")
    assert _org(cloud, org)["paid_until"] is None


def test_rappel_envoye_une_fois_par_echeance(pg, monkeypatch):
    cloud, _, faux, ctx, org = pg
    _payer(cloud, faux, ctx)
    envois = []
    monkeypatch.setattr(cloud, "mail_ready", lambda: True)
    monkeypatch.setattr(cloud, "send_mail", lambda dest, sujet, corps: envois.append(dest))
    cloud.q("update app.organizations set paid_until = now() + interval '5 days' where id = %s", (org,))
    cloud.rappels_abonnement()
    cloud.rappels_abonnement()
    assert envois.count(["o@test.ci"]) == 1
    cloud.q("update app.organizations set paid_until = now() + interval '12 hours', rappel_envoye = current_date - 2 where id = %s", (org,))
    cloud.rappels_abonnement()
    assert envois.count(["o@test.ci"]) == 2


@pytest.mark.parametrize("sql", ["update app.organizations set suspended = true where id = %s",
                                 "update app.organizations set plan = 'suspendu' where id = %s"])
def test_espace_suspendu_ne_peut_pas_payer(pg, sql):
    cloud, _, faux, ctx, org = pg
    cloud.q(sql, (org,))
    with pytest.raises(ValueError):
        cloud.checkout(ctx, "pro", 1)
    assert _tx(cloud, org) is None and "/v1/payment" not in [a[1] for a in faux.appels]


def test_prix_supprime_n_efface_pas_le_temps_paye(pg):
    cloud, _, faux, ctx, org = pg
    _payer(cloud, faux, ctx, "equipe", 12)
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    cloud.q("update app.plans set price = null where key = 'equipe'")
    faux.statuts[p["merchant_tx"]] = {"code": 100, "status": "SUCCESS"}
    cloud.notification({"merchant_transaction_id": p["merchant_tx"], "notify_token": p["notify_token"]})
    assert (_org(cloud, org)["paid_until"] - dt.datetime.now(dt.timezone.utc)).days >= 380


def test_changement_refuse_depuis_un_forfait_sur_devis(pg):
    cloud, _, faux, ctx, org = pg
    _payer(cloud, faux, ctx, "equipe", 1)
    cloud.q("update app.plans set price = null where key = 'equipe'")
    cloud._cache.clear()
    with pytest.raises(ValueError):
        cloud.checkout(ctx, "pro", 1)


def test_montant_trop_eleve_sans_ligne_de_paiement(pg):
    cloud, _, _, ctx, org = pg
    cloud.q("update app.plans set price = 300000 where key = 'equipe'")
    cloud._cache.clear()
    with pytest.raises(ValueError):
        cloud.checkout(ctx, "equipe", 12)
    assert _tx(cloud, org) is None


def test_cle_de_test_reservee_aux_administrateurs(pg):
    cloud, _, _, ctx, org = pg
    ctx.is_admin = False
    assert cloud.billing(ctx)["actif"] is False
    with pytest.raises(cloud.Unavailable):
        cloud.checkout(ctx, "pro", 1)


def test_cle_reelle_ouverte_aux_clients(pg, monkeypatch):
    cloud, _, _, ctx, _ = pg
    ctx.is_admin = False
    monkeypatch.setenv("CINETPAY_API_KEY", "sk_live_cle")
    assert cloud.billing(ctx)["actif"] is True


def test_notification_avant_la_fin_rattrapee(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    assert cloud.notification({"merchant_transaction_id": p["merchant_tx"], "notify_token": p["notify_token"]})
    faux.statuts[p["merchant_tx"]] = {"code": 100, "status": "SUCCESS"}
    cloud.q("update app.payments set created_at = now() - interval '1 hour' where id = %s", (p["id"],))
    cloud.reconcilier_paiements()
    assert _org(cloud, org)["plan"] == "pro"


def _vieillir(cloud, org, jours):
    cloud.q("update app.payments set created_at = now() - make_interval(days => %s) where org_id = %s", (jours, org))


def test_abonnement_expire_peut_payer(pg):
    cloud, _, faux, ctx, org = pg
    _payer(cloud, faux, ctx)
    cloud.q("update app.organizations set paid_until = now() - interval '1 day' where id = %s", (org,))
    ctx.plan.update(key="suspendu", expired=True)
    assert cloud.checkout(ctx, "pro", 1)["payment_url"]


def test_ancien_paiement_jamais_clos_sans_reponse(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    _vieillir(cloud, org, 30)
    faux.panne = True
    cloud.reconcilier_paiements()
    assert _tx(cloud, org)["status"] == "pending"


def test_ancien_paiement_reussi_toujours_active(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    _vieillir(cloud, org, 30)
    faux.statuts[p["merchant_tx"]] = {"code": 100, "status": "SUCCESS"}
    cloud.reconcilier_paiements()
    assert _org(cloud, org)["plan"] == "pro"


def test_paiement_abandonne_clos_apres_confirmation_de_cinetpay(pg):
    cloud, _, _, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    _vieillir(cloud, org, 8)
    cloud.reconcilier_paiements()
    assert _tx(cloud, org)["status"] == "failed"


def test_paiement_recent_reste_en_attente(pg):
    cloud, _, _, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    _vieillir(cloud, org, 3)
    cloud.reconcilier_paiements()
    assert _tx(cloud, org)["status"] == "pending"


def test_reconciliation_ne_s_arrete_pas_sur_une_ligne(pg, monkeypatch):
    cloud, _, _, ctx, org = pg
    for _ in range(3):
        cloud.checkout(ctx, "pro", 1)
    _vieillir(cloud, org, 1)
    vus = []

    def casse(p):
        vus.append(p["merchant_tx"])
        raise RuntimeError("base coupée")
    monkeypatch.setattr(cloud, "_synchroniser", casse)
    cloud.reconcilier_paiements()
    assert len(vus) >= 3
    assert cloud.q("select count(*) n from app.payments where org_id = %s and checked_at is not null", (org,), one=True)["n"] == 3


def test_reconciliation_tourne_sur_tout_le_lot(pg, monkeypatch):
    cloud, _, _, ctx, org = pg
    monkeypatch.setattr(cloud, "LOT_RECONCILIATION", 2)
    cloud.q("update app.payments set checked_at = now()")
    for _ in range(3):
        cloud.checkout(ctx, "pro", 1)
    _vieillir(cloud, org, 1)
    cloud.reconcilier_paiements()
    cloud.reconcilier_paiements()
    assert cloud.q("select count(*) n from app.payments where org_id = %s and checked_at is null", (org,), one=True)["n"] == 0


def test_client_retrouve_un_paiement_clos_a_tort(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    cloud.q("update app.payments set status = 'failed', created_at = now() - interval '20 days' where id = %s", (p["id"],))
    faux.statuts[p["merchant_tx"]] = {"code": 100, "status": "SUCCESS"}
    assert cloud.synchroniser_espace(ctx)["paye"] is True


def test_paiement_d_un_autre_mode_ignore(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    cloud.q("update app.payments set mode = 'live', created_at = now() - interval '1 hour' where org_id = %s", (org,))
    faux.appels.clear()
    cloud.reconcilier_paiements()
    p = _tx(cloud, org)
    assert ("GET", "/v1/payment/" + p["merchant_tx"]) not in faux.appels and p["status"] == "pending"


def test_conversion_au_prix_reellement_paye(pg):
    cloud, _, faux, ctx, org = pg
    _payer(cloud, faux, ctx, "equipe", 12)
    cloud.q("update app.plans set price = 200000 where key = 'equipe'")
    _payer(cloud, faux, ctx, "pro", 1)
    jours = (_org(cloud, org)["paid_until"] - dt.datetime.now(dt.timezone.utc)).days
    assert 1200 <= jours <= 1260   # 1 mois + environ 365 j x 50 000 / 15 000


def test_forfait_avec_paiement_en_cours_non_supprimable(pg):
    cloud, _, _, ctx, _ = pg
    cloud.checkout(ctx, "equipe", 1)
    with pytest.raises(ValueError):
        cloud.admin_plan_delete(cloud.Ctx(key="a", is_admin=True), "equipe")
    cloud.q("update app.payments set created_at = now() - interval '8 days' where plan = 'equipe' and status = 'pending'")
    cloud.q("update app.organizations set plan = 'pro' where plan = 'equipe'")
    cloud.admin_plan_delete(cloud.Ctx(key="a", is_admin=True), "equipe")
    assert not cloud.q("select 1 from app.plans where key = 'equipe'")
    cloud.q("""insert into app.plans (key, label, price, analyses, ai, members, public, sort)
               values ('equipe', 'Équipe', 50000, 1000, 10000, 25, true, 3)""")


def test_admin_repare_un_paiement_reel(pg):
    cloud, _, faux, ctx, org = pg
    cloud.checkout(ctx, "pro", 1)
    p = _tx(cloud, org)
    cloud.q("update app.payments set status = 'failed' where id = %s", (p["id"],))
    faux.statuts[p["merchant_tx"]] = {"code": 100, "status": "SUCCESS"}
    assert cloud.admin_verifier_paiement(cloud.Ctx(key="a", is_admin=True), p["merchant_tx"])["status"] == "paid"
    assert _org(cloud, org)["plan"] == "pro"
    with pytest.raises(cloud.Forbidden):
        cloud.admin_verifier_paiement(cloud.Ctx(key="b"), p["merchant_tx"])


def test_admin_prolonge_un_abonnement_expire(pg):
    cloud, _, faux, ctx, org = pg
    _payer(cloud, faux, ctx)
    cloud.q("update app.organizations set paid_until = now() - interval '10 days' where id = %s", (org,))
    cloud.admin_extend_trial(cloud.Ctx(key="a", is_admin=True), org, 30)
    assert 28 <= (_org(cloud, org)["paid_until"] - dt.datetime.now(dt.timezone.utc)).days <= 30
