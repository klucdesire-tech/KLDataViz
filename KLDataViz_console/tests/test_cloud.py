# -*- coding: utf-8 -*-
"""Sécurité de la version en ligne, avec comptes simulés (sans Supabase) :
python -m pytest tests/test_cloud.py"""
import base64
import datetime as dt
import os
import tempfile
import uuid

import pytest

DATA = tempfile.mkdtemp(prefix="kldv-cloud-")
os.environ.update(KLDV_MODE="cloud", KLDV_DATA_DIR=DATA, KLDV_SECRET="test-secret")
from _harness import call, reset_app_modules, start  # noqa: E402
reset_app_modules()
import cloud  # noqa: E402
import server  # noqa: E402
import tenancy as T  # noqa: E402

ORG_A, ORG_B = str(uuid.uuid4()), str(uuid.uuid4())
USERS = {"tok-a": ("ua", ORG_A, "owner"), "tok-b": ("ub", ORG_B, "owner"), "tok-v": ("uv", ORG_A, "viewer")}
PLAN = {"key": "pro", "label": "Pro", "analyses": 100, "ai": 100, "members": 5, "org_name": "Test"}
quota_full = set()


def fake_verify(token):
    if token not in USERS:
        raise T.AuthError("Session expirée.")
    return {"id": USERS[token][0], "email": f"{USERS[token][0]}@test.ci", "name": ""}


def fake_resolve(user):
    tok = next(t for t, v in USERS.items() if v[0] == user["id"])
    _, org, role = USERS[tok]
    return T.Ctx(key=user["id"], org_id=org, user_id=user["id"], email=user["email"], role=role, plan=dict(PLAN))


def fake_consume(ctx, kind):
    if ctx.org_id in quota_full:
        raise T.QuotaExceeded("Quota mensuel atteint.")


cloud.verify_token, cloud.resolve, cloud.consume = fake_verify, fake_resolve, fake_consume
CSV = b"region,montant\n" + b"".join(f"R{i % 3},{i}\n".encode() for i in range(30))


@pytest.fixture(scope="module")
def base():
    httpd, url = start(server)
    yield url
    httpd.shutdown()


def upload(base, tok, name="ventes.csv"):
    code, r = call(base, "/api/upload-source", {"name": name, "data": base64.b64encode(CSV).decode()}, tok)
    assert code == 200, r
    return r["path"]


def test_connexion_obligatoire(base):
    assert call(base, "/api/exports")[0] == 401
    assert call(base, "/api/exports", token="faux")[0] == 401


def test_pas_de_console_classique_en_ligne(base):
    assert call(base, "/classic", raw=True)[0] == 404


def test_isolation_fichiers_et_exports(base):
    path_a = upload(base, "tok-a")
    assert ORG_A in path_a
    code, a = call(base, "/api/analyze", {"file_path": path_a}, "tok-a")
    assert code == 200 and a["rows"] == 30
    # B ne peut pas lire le fichier de A, ni par chemin direct ni par chemin relatif
    code, r = call(base, "/api/analyze", {"file_path": path_a}, "tok-b")
    assert "error" in r and "introuvable" in r["error"]
    sneaky = os.path.join(DATA, "tenants", ORG_B, "uploads", "..", "..", ORG_A, "uploads", "ventes.csv")
    assert "error" in call(base, "/api/analyze", {"file_path": sneaky}, "tok-b")[1]
    assert "error" in call(base, "/api/clean", {"file_path": path_a}, "tok-b")[1]
    # export de A invisible et non téléchargeable par B
    code, e = call(base, "/api/export/html", {}, "tok-a")
    assert code == 200 and "?k=" in e["url"]
    assert call(base, e["url"], raw=True)[0] == 200                       # lien signé de A : OK
    assert all(f["file"] != e["file"] for f in call(base, "/api/exports", token="tok-b")[1])
    forged = e["url"].replace(ORG_A, ORG_B)
    assert call(base, forged, raw=True)[0] == 403                         # signature invalide
    assert call(base, f"/download/{e['file']}", raw=True)[0] == 403       # sans signature
    code, r = call(base, "/api/exports/delete", {"file": e["file"]}, "tok-b")
    assert code == 400                                                    # B ne voit pas le fichier
    assert os.path.exists(os.path.join(DATA, "tenants", ORG_A, "exports", e["file"]))


def test_etat_par_utilisateur(base):
    code, r = call(base, "/api/export/pdf", {}, "tok-b")                 # B n'a rien analysé
    assert code == 400


def test_ssrf_refuse(base):
    for url in ("http://127.0.0.1:8642/api/exports", "https://127.0.0.1/x.csv", "https://169.254.169.254/latest",
                "https://localhost/x.csv", "file:///etc/passwd", "https://10.0.0.1/a.csv"):
        code, r = call(base, "/api/analyze", {"file_path": url}, "tok-a")
        assert "error" in r, url
    code, r = call(base, "/api/ingest", {"file_path": "http://127.0.0.1/x.csv"}, "tok-a")
    assert code == 400


def test_role_lecteur(base):
    assert call(base, "/api/upload-source", {"name": "x.csv", "data": ""}, "tok-v")[0] == 403
    assert call(base, "/api/exports", token="tok-v")[0] == 200


def test_extension_interdite(base):
    code, r = call(base, "/api/upload-source", {"name": "virus.exe", "data": "AA=="}, "tok-a")
    assert code == 400


def test_quota(base):
    path_b = upload(base, "tok-b")
    quota_full.add(ORG_B)
    try:
        code, r = call(base, "/api/analyze", {"file_path": path_b}, "tok-b")
        assert code == 402 and r.get("quota")
    finally:
        quota_full.discard(ORG_B)


def test_planification_source_etrangere(base):
    path_a = os.path.join(DATA, "tenants", ORG_A, "uploads", "ventes.csv")
    code, r = call(base, "/api/schedules", {"source": path_a, "freq": "daily"}, "tok-b")
    assert code == 400


# ---- page d'accueil, administration, données publiques
cloud.is_platform_admin = lambda email: email == "ua@test.ci"
cloud.admin_stats = lambda ctx: {"orgs": 2} if ctx.is_admin else (_ for _ in ()).throw(T.Forbidden("non"))
cloud.public_plans = lambda: [{"key": "essai", "label": "Essai", "price": 0}]
cloud.announcements = lambda audience: [{"id": "1", "title": "Bienvenue", "audience": audience}]
cloud.site_settings = lambda: {"hero_title": "Titre"}


def test_pages_publiques_en_ligne(base):
    assert b"motion" in call(base, "/", raw=True)[1].lower() or call(base, "/", raw=True)[0] == 200
    assert call(base, "/app", raw=True)[0] == 200
    assert call(base, "/admin", raw=True)[0] == 200
    assert call(base, "/login", raw=True)[0] == 200
    assert call(base, "/guide", raw=True)[0] == 200
    assert call(base, "/guide/importer.jpg", raw=True)[0] == 200


def test_donnees_publiques_sans_connexion(base):
    assert call(base, "/api/public/plans")[1][0]["key"] == "essai"
    assert call(base, "/api/public/site")[1]["hero_title"] == "Titre"
    assert call(base, "/api/public/announcements")[1][0]["audience"] == "public"


def test_administration_reservee(base):
    assert call(base, "/api/admin/stats")[0] == 401                     # sans connexion
    assert call(base, "/api/admin/stats", token="tok-b")[0] == 403      # utilisateur normal
    assert call(base, "/api/admin/plans/save", {"key": "x"}, "tok-b")[0] == 403
    code, r = call(base, "/api/admin/stats", token="tok-a")             # administrateur
    assert code == 200 and r["orgs"] == 2


def test_annonces_application(base):
    code, r = call(base, "/api/announcements", token="tok-a")
    assert code == 200 and r[0]["audience"] == "app"


def test_notification_paiement_sans_session(base, monkeypatch):
    vus = []
    monkeypatch.setattr(cloud, "notification", lambda b: (vus.append(b), b.get("notify_token") == "bon")[1])
    assert call(base, "/api/paiement/notification", {"merchant_transaction_id": "T", "notify_token": "bon"})[0] == 200
    assert call(base, "/api/paiement/notification", {"merchant_transaction_id": "T", "notify_token": "faux"})[0] == 401
    assert len(vus) == 2


def test_notification_paiement_rejouee_si_cinetpay_ne_repond_pas(base, monkeypatch):
    def panne(_):
        raise server.paiement.PaiementError("injoignable")
    monkeypatch.setattr(cloud, "notification", panne)
    assert call(base, "/api/paiement/notification", {"merchant_transaction_id": "T"})[0] == 502


def test_lecteur_ne_peut_pas_supprimer_un_jeu(base):
    assert call(base, "/api/ingest/delete", {"id": "DS-X"}, "tok-v")[0] == 403
    assert call(base, "/api/ingest/delete", {"id": "DS-X"})[0] == 401


def test_lecteur_ne_peut_pas_payer(base):
    assert call(base, "/api/billing/checkout", {"plan": "pro", "months": 1}, "tok-v")[0] == 403
    assert call(base, "/api/billing/sync", {}, "tok-v")[0] == 403


def test_paiement_exige_une_session(base):
    assert call(base, "/api/billing/checkout", {"plan": "pro", "months": 1})[0] == 401
    assert call(base, "/api/billing")[0] == 401


# ---------- envois automatiques : rien ne doit partir sans qu'on puisse le voir et l'arrêter ----------

@pytest.fixture(autouse=True)
def _dossiers_par_espace(monkeypatch):
    monkeypatch.delenv("KLDV_SOURCES_DIR", raising=False)  # le vrai chemin : un dossier par espace


def _espace(org, nom="Espace B", cle="pro"):
    return T.Ctx(key=f"job:{org}", org_id=org, role="analyst", plan={"key": cle, "org_name": nom})


def _envoi(org, cle="pro", freq="weekly"):
    ctx = _espace(org, cle=cle)
    with T.use(ctx):
        server.sources._save([{"id": "abc", "name": "Ventes", "path": "x", "mail_to": ["a@exemple.org"], "mail_freq": freq, "next_mail": "2020-01-01T07:00:00"}])
    return ctx


def test_envois_de_l_espace_visibles_et_arretables(base):
    _envoi(ORG_B)
    code, liste = call(base, "/api/envois", token="tok-b")
    assert code == 200 and [e["name"] for e in liste] == ["Ventes"] and liste[0]["mail_to"] == ["a@exemple.org"]
    assert call(base, "/api/envois", token="tok-a")[1] == []  # l'autre espace n'y a pas accès
    assert call(base, "/api/envois/arreter", {"id": "abc"}, "tok-b")[0] == 200
    assert call(base, "/api/envois", token="tok-b")[1] == []
    with T.use(_espace(ORG_B)):
        s = server.sources._load()[0]
    assert s["mail_freq"] == "off" and s["next_mail"] is None and s["mail_to"] == ["a@exemple.org"]  # destinataires gardés


def test_arreter_envoi_inconnu(base):
    assert call(base, "/api/envois/arreter", {"id": "absent"}, "tok-b")[0] in (400, 404, 500)


def test_lecteur_ne_peut_pas_arreter_un_envoi(base):
    assert call(base, "/api/envois/arreter", {"id": "abc"}, "tok-v")[0] == 403


def test_administration_des_envois_reservee(base):
    assert call(base, "/api/admin/envois")[0] == 401
    assert call(base, "/api/admin/envois", token="tok-b")[0] == 403
    assert call(base, "/api/admin/envois/arreter", {"id": "x", "genre": "tableau", "org_id": ORG_B}, "tok-b")[0] == 403


def test_administrateur_voit_et_arrete_les_envois_de_tous_les_espaces(base, monkeypatch):
    _envoi(ORG_B)
    monkeypatch.setattr(cloud, "org_ctx", lambda org: _espace(org))
    monkeypatch.setattr(cloud, "admin_planifications", lambda ctx: [{"id": "p1", "org_id": ORG_B, "espace": "KLD"}])
    arretees = []
    monkeypatch.setattr(cloud, "admin_arreter_planification", lambda ctx, org, job: arretees.append((org, job)))
    code, d = call(base, "/api/admin/envois", token="tok-a")
    assert code == 200 and d["planifications"][0]["espace"] == "KLD"
    assert [(x["name"], x["espace"], x["org_id"]) for x in d["tableaux"]] == [("Ventes", "Espace B", ORG_B)]
    assert call(base, "/api/admin/envois/arreter", {"id": "p1", "genre": "planification", "org_id": ORG_B}, "tok-a")[0] == 200
    assert arretees == [(ORG_B, "p1")]
    assert call(base, "/api/admin/envois/arreter", {"id": "abc", "genre": "tableau", "org_id": ORG_B}, "tok-a")[0] == 200
    assert call(base, "/api/admin/envois", token="tok-a")[1]["tableaux"] == []


def test_espace_abime_ne_cache_pas_les_autres(monkeypatch):
    _envoi(ORG_B)

    def contexte(org):
        if org == ORG_B:
            return _espace(org)
        raise TypeError("espace supprimé")
    monkeypatch.setattr(server.sources, "_espaces", lambda: ["autre", ORG_B])
    assert [x["org_id"] for x in server.sources.envois_tous(contexte)] == [ORG_B]


APRES = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
AVANT = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
CATALOGUE = {"essai": {"days": 14}, "pro": {}}


@pytest.mark.parametrize("cle,essai,paye,attendu", [("essai", AVANT, None, True), ("essai", APRES, None, False), ("pro", AVANT, None, False),
                                                    ("pro", None, AVANT, True), ("pro", None, APRES, False), ("pro", None, None, False)])
def test_espace_expire(cle, essai, paye, attendu):
    assert cloud.expire(cle, essai, paye, CATALOGUE) is attendu


def _base_factice(monkeypatch, org, **colonnes):
    ligne = dict({"name": "KLD", "plan": "essai", "trial_end": APRES, "paid_until": None, "suspended": False}, **colonnes)
    monkeypatch.setattr(cloud, "plans", lambda: CATALOGUE)
    monkeypatch.setattr(cloud, "q", lambda sql, args=None, one=False: ligne if one else [{"org_id": org, "id": "j1"}])
    monkeypatch.setattr(cloud, "_sched_out", lambda r: {"id": r["id"]})


def test_essai_expire_n_envoie_plus(monkeypatch):
    """Un espace abandonné continuait d'envoyer chaque jour : personne ne s'y connecte pour l'arrêter."""
    _base_factice(monkeypatch, ORG_A, trial_end=AVANT)
    assert cloud.org_ctx(ORG_A).plan["key"] == "suspendu"
    assert cloud.due_schedules() == []


def test_essai_en_cours_envoie(monkeypatch):
    _base_factice(monkeypatch, ORG_A)
    assert cloud.org_ctx(ORG_A).plan["key"] == "essai"
    assert cloud.due_schedules() == [(ORG_A, {"id": "j1"})]


def test_abonnement_termine_n_envoie_plus(monkeypatch):
    _base_factice(monkeypatch, ORG_A, plan="pro", paid_until=AVANT)
    assert cloud.due_schedules() == []


def test_espace_suspendu_n_envoie_plus(monkeypatch):
    _base_factice(monkeypatch, ORG_A, suspended=True)
    assert cloud.due_schedules() == []


def test_chaque_email_dit_d_ou_il_vient():
    with T.use(_espace(ORG_B, nom="MSHP")):
        pied = server.studio.pied_envoi()
    assert "« MSHP »" in pied and "Planifié" in pied


def test_rapport_planifie_porte_son_origine(monkeypatch):
    envois = []
    monkeypatch.setattr(server.studio, "send_mail", lambda dest, sujet, corps, piece=None: envois.append(corps))
    job = {"id": "j1", "name": "Ventes", "source": "x.csv", "to": ["a@exemple.org"], "format": "pdf", "freq": "daily", "hour": 7}
    monkeypatch.setattr(server.studio, "list_schedules", lambda: [job])
    monkeypatch.setattr(server.studio, "update_schedule", lambda *a, **k: None)
    with T.use(_espace(ORG_B, nom="MSHP")):
        server.studio.run_schedule("j1", lambda s: {"rows": 3, "completeness_pct": 100}, {"pdf": lambda r: "r.pdf"}, T.exports_dir())
    assert envois and "« MSHP »" in envois[0]


def test_sante_du_service_reservee_aux_administrateurs(base, monkeypatch):
    assert call(base, "/api/admin/sante")[0] == 401
    assert call(base, "/api/admin/sante", token="tok-b")[0] == 403
    assert call(base, "/api/admin/sante/essai", {}, "tok-b")[0] == 403
    code, d = call(base, "/api/admin/sante", token="tok-a")
    noms = [c["nom"] for c in d["controles"]]
    assert code == 200 and {"Base de données", "Disque", "E-mail", "Intelligence artificielle"} <= set(noms)
    envoyes = []
    monkeypatch.setenv("KLDV_ALERTES", "alerte@test.ci")
    monkeypatch.setattr(cloud, "send_mail", lambda to, s, b, attachment=None: envoyes.append(to))
    code, r = call(base, "/api/admin/sante/essai", {}, "tok-a")
    assert code == 200 and r["envoye"] and envoyes == [["alerte@test.ci"]]


def test_erreur_interne_relevee_par_la_surveillance(base, monkeypatch):
    def plante(*a, **k):
        raise ZeroDivisionError("valeur du fichier : Aya Kouassi")
    monkeypatch.setattr(server.Handler, "_route", plante)
    code, r = call(base, "/api/me", token="tok-a")
    ref = r["error"].split("réf. ")[1].split(")")[0]
    inc = next(i for i in server.surveillance.bilan()["incidents"] if i["ref"] == ref)
    assert code == 500 and "ZeroDivisionError" in inc["resume"] and "Aya" not in inc["resume"] and inc["espace"] == ORG_A


def test_detecteur_de_robots_a_la_connexion(base, monkeypatch):
    """Clé publique exposée seulement si elle est propre ; Cloudflare autorisé par la politique de sécurité."""
    def config():
        return call(base, "/config.js", raw=True)[1].decode()
    monkeypatch.delenv("TURNSTILE_SITE_KEY", raising=False)
    assert "turnstileSiteKey" not in config()
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "0x4AAAAAAAabcdefGHIJ")
    assert '"turnstileSiteKey": "0x4AAAAAAAabcdefGHIJ"' in config()
    monkeypatch.setenv("TURNSTILE_SITE_KEY", '"; alert(1); "')
    assert "turnstileSiteKey" not in config()
    assert "frame-src https://challenges.cloudflare.com" in server.CSP and "https://challenges.cloudflare.com;" in server.CSP
    page = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "login.html"), encoding="utf-8").read()
    assert page.count("captchaToken:await jetonRobot()") == 3  # connexion, inscription, mot de passe oublié


def test_pages_montrent_les_envois():
    dossier = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(dossier, "index.html"), encoding="utf-8").read()
    admin = open(os.path.join(dossier, "admin.html"), encoding="utf-8").read()
    assert 'id="dashMailList"' in page and "/api/envois/arreter" in page and "même si personne n'est connecté" in page
    assert 'data-tab="envois"' in admin and "envois:loadEnvois" in admin and "/api/admin/envois/arreter" in admin
    assert 'data-tab="sante"' in admin and "sante:loadSante" in admin and "/api/admin/sante/essai" in admin
