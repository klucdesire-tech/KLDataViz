# -*- coding: utf-8 -*-
"""Sécurité de la version en ligne, avec comptes simulés (sans Supabase) :
python -m pytest tests/test_cloud.py   (à lancer séparément de test_local.py)"""
import base64
import os
import tempfile
import uuid

import pytest

DATA = tempfile.mkdtemp(prefix="kldv-cloud-")
os.environ.update(KLDV_MODE="cloud", KLDV_DATA_DIR=DATA, KLDV_SECRET="test-secret")
from _harness import call, start  # noqa: E402
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
