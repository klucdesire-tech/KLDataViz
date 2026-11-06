# -*- coding: utf-8 -*-
"""
Services de la version en ligne (KLDV_MODE=cloud) : comptes Supabase, organisations,
rôles, quotas par plan, invitations, administration et envoi d'e-mails (Brevo).

Seul le serveur accède à la base (schéma « app », non exposé par l'API publique Supabase).
Toutes les valeurs secrètes viennent des variables d'environnement.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid

import paiement
from tenancy import AuthError, Ctx, Forbidden, QuotaExceeded, Unavailable  # noqa: F401 (réexportées)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
# Clé « anon » du projet Supabase kldataviz : publique par conception (envoyée à chaque navigateur),
# elle ne donne accès qu'à la connexion ; les données ne sont lisibles que par le serveur.
PROJECT_ANON_KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhzdWtld2trc3Z6ZG5jZWp2dGZnIiwicm9sZSI6"
                    "ImFub24iLCJpYXQiOjE3OTE5MTg5MTIsImV4cCI6MjEwNzQ5NDkxMn0.0APyxEOjzZ_GFWGd10H92eyiPQ5TQDX4f-BUrGqpUAA")


def _anon_key():
    """Clé anon valide : celle de l'environnement si elle est propre, sinon celle du projet.

    Protège contre une clé recopiée depuis un champ masqué (« •••• ») ou avec des espaces,
    qui ferait échouer toutes les connexions dans le navigateur."""
    k = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if k and k.isascii() and re.fullmatch(r"[A-Za-z0-9_\-.]+", k):
        return k
    if "xsukewkksvzdncejvtfg" in SUPABASE_URL:
        return PROJECT_ANON_KEY
    return k


SUPABASE_ANON_KEY = _anon_key()
DATABASE_URL = os.environ.get("DATABASE_URL", "")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "")
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "KLDataViz")
APP_URL = os.environ.get("APP_URL", "").rstrip("/")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}

# Forfaits de secours si la base est injoignable ; les vrais forfaits sont dans app.plans (page Admin).
_FALLBACK_PLANS = {
    "essai": {"label": "Essai", "analyses": 10, "ai": 50, "members": 1, "days": 14},
    "pro": {"label": "Pro", "analyses": 100, "ai": 1000, "members": 5},
    "equipe": {"label": "Équipe", "analyses": 1000, "ai": 10000, "members": 25},
    "suspendu": {"label": "Suspendu", "analyses": 0, "ai": 0, "members": 0},
}
ROLES = ("owner", "analyst", "viewer")
_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    val = fn()
    _cache[key] = (time.time() + ttl, val)
    return val


def plans() -> dict:
    """Forfaits paramétrés par l'administrateur (cache 30 s)."""
    def load():
        try:
            rows = q("select * from app.plans order by sort, key")
        except Exception:
            return _FALLBACK_PLANS
        return {r["key"]: {"label": r["label"], "analyses": r["analyses"], "ai": r["ai"], "members": r["members"],
                           "days": r["trial_days"], "price": float(r["price"]) if r["price"] is not None else None,
                           "currency": r["currency"], "period": r["period"], "description": r["description"],
                           "features": list(r["features"] or []), "highlight": r["highlight"], "public": r["public"],
                           "sort": r["sort"]} for r in rows} or _FALLBACK_PLANS
    return _cached("plans", 30, load)


def is_platform_admin(email: str) -> bool:
    email = (email or "").lower()
    if email in ADMIN_EMAILS:
        return True
    admins = _cached("admins", 30, lambda: {r["email"] for r in q("select email from app.platform_admins")})
    return email in admins


# ---------------------------------------------------------------- base de données
def db():
    import psycopg
    if not DATABASE_URL:
        raise Unavailable("Base de données non configurée.")
    # Le pooler refuse parfois une connexion pendant une seconde (redémarrage, saturation) : on réessaie, avec une
    # attente totale bornée. Sans borne, une vraie panne réseau (délai dépassé à chaque essai) bloquait ~30 s par requête.
    debut, derniere = time.monotonic(), None
    for essai, delai in enumerate(BASE_DELAIS):
        try:
            return psycopg.connect(DATABASE_URL, autocommit=True, prepare_threshold=None, connect_timeout=delai)
        except psycopg.OperationalError as exc:
            derniere = exc
            if _erreur_definitive(exc) or essai == len(BASE_DELAIS) - 1 or time.monotonic() - debut >= BASE_BUDGET:
                break
            time.sleep(0.4 * (essai + 1))
    import sys
    sys.stderr.write("[base] connexion impossible : " + str(derniere).splitlines()[0][:200] + "\n")
    raise Unavailable("La base de données est momentanément injoignable. Réessayez dans quelques minutes.")


BASE_DELAIS = (4, 3, 3)  # délai de chaque essai de connexion, en secondes (libpq : 2 au minimum)
BASE_BUDGET = 6          # au-delà, plus de nouvel essai : ~8 s d'attente au pire au lieu de ~31


def _erreur_definitive(exc) -> bool:
    """Erreur qu'un nouvel essai ne corrigera pas (mot de passe refusé, base ou rôle inexistant)."""
    m = str(exc).lower()
    return any(x in m for x in ("password authentication failed", "authentification par mot de passe",
                                "does not exist", "n'existe pas", "no pg_hba.conf entry"))


def q(sql, params=(), one=False):
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return None
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        return (rows[0] if rows else None) if one else rows


# ---------------------------------------------------------------- authentification
_token_cache = {}
_cache_lock = threading.Lock()


def verify_token(token: str) -> dict:
    """Vérifie le jeton de session auprès de Supabase. Renvoie {"id", "email", "name"}."""
    if not token:
        raise AuthError("Connexion requise.")
    h = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _cache_lock:
        hit = _token_cache.get(h)
        if hit and hit[0] > now:
            return hit[1]
    req = urllib.request.Request(f"{SUPABASE_URL}/auth/v1/user",
                                 headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            u = json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthError("Session expirée. Reconnectez-vous.")
        raise Unavailable("Le service de connexion répond mal. Réessayez dans un instant.")
    except Exception:
        raise Unavailable("Service de connexion injoignable. Réessayez dans un instant.")
    user = {"id": u["id"], "email": (u.get("email") or "").lower(),
            "name": (u.get("user_metadata") or {}).get("full_name") or ""}
    with _cache_lock:
        if len(_token_cache) > 5000:
            _token_cache.clear()
        _token_cache[h] = (now + 60, user)
    return user


def expire(plan_key: str, trial_end, paid_until, catalog: dict, now=None) -> bool:
    """Vrai si l'essai ou l'abonnement est terminé."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return bool((catalog.get(plan_key, {}).get("days") and trial_end and trial_end < now)
                or (paid_until is not None and paid_until < now))


def resolve(user: dict) -> Ctx | None:
    """Contexte de l'utilisateur : son organisation et son rôle. Accepte une invitation en attente."""
    m = q("""select m.org_id::text, m.role, o.plan, o.trial_end, o.paid_until, o.name, o.suspended
             from app.memberships m join app.organizations o on o.id = m.org_id
             where m.user_id = %s order by m.created_at limit 1""", (user["id"],), one=True)
    if m is None:
        inv = q("""select id, org_id::text, role from app.invitations
                   where lower(email) = %s and accepted_at is null and expires_at > now()
                   order by created_at desc limit 1""", (user["email"],), one=True)
        if inv is None:
            return None
        q("insert into app.memberships (user_id, org_id, role) values (%s, %s, %s) on conflict do nothing",
          (user["id"], inv["org_id"], inv["role"]))
        q("update app.invitations set accepted_at = now() where id = %s", (inv["id"],))
        return resolve(user)
    catalog = plans()
    plan_key = m["plan"] if m["plan"] in catalog else "essai"
    expired = expire(plan_key, m["trial_end"], m["paid_until"], catalog)
    if m["suspended"] or expired:
        plan_key = "suspendu"
    plan = dict(catalog.get(plan_key, _FALLBACK_PLANS["suspendu"]), key=plan_key, trial_end=m["trial_end"],
                paid_until=m["paid_until"], org_name=m["name"], expired=bool(expired), suspended=bool(m["suspended"]))
    return Ctx(key=user["id"], org_id=m["org_id"], user_id=user["id"], email=user["email"], role=m["role"],
               plan=plan, is_admin=is_platform_admin(user["email"]))


def create_org(user: dict, name: str) -> None:
    name = (name or "").strip()[:80]
    if len(name) < 2:
        raise ValueError("Indiquez le nom de votre organisation.")
    if q("select 1 from app.memberships where user_id = %s", (user["id"],), one=True):
        raise ValueError("Vous faites déjà partie d'une organisation.")
    days = plans().get("essai", {}).get("days") or 14
    org = q("""insert into app.organizations (name, plan, trial_end, created_by)
               values (%s, 'essai', now() + make_interval(days => %s), %s) returning id::text""",
            (name, int(days), user["id"]), one=True)
    q("insert into app.memberships (user_id, org_id, role) values (%s, %s, 'owner')", (user["id"], org["id"]))


# ---------------------------------------------------------------- rôles et quotas
def require(ctx: Ctx, *roles):
    if ctx.role not in roles:
        raise Forbidden("Votre rôle ne permet pas cette action. Demandez au propriétaire de l'espace.")


def usage(org_id: str) -> dict:
    month = dt.date.today().strftime("%Y-%m")
    r = q("select analyses, ai_calls from app.usage_monthly where org_id = %s and month = %s",
          (org_id, month), one=True)
    return {"month": month, "analyses": (r or {}).get("analyses", 0), "ai": (r or {}).get("ai_calls", 0)}


def consume(ctx: Ctx, kind: str):
    """Décompte une analyse (« analyses ») ou une question IA (« ai ») ; refuse au-delà du plan."""
    limit = ctx.plan.get(kind, 0)
    used = usage(ctx.org_id)[kind]
    if used >= limit:
        what = "analyses" if kind == "analyses" else "questions à l'IA"
        if ctx.plan.get("key") == "suspendu":
            raise QuotaExceeded("Votre espace est suspendu." if ctx.plan.get("suspended") else
                                "Votre essai ou votre abonnement est terminé. Choisissez un forfait dans Réglages, "
                                "section Abonnement.")
        raise QuotaExceeded(f"Quota mensuel atteint : {limit} {what} pour le plan {ctx.plan.get('label')}. "
                            "Passez au forfait supérieur dans Réglages, section Abonnement.")
    col = "analyses" if kind == "analyses" else "ai_calls"
    q(f"""insert into app.usage_monthly (org_id, month, {col}) values (%s, %s, 1)
          on conflict (org_id, month) do update set {col} = app.usage_monthly.{col} + 1""",
      (ctx.org_id, dt.date.today().strftime("%Y-%m")))


def me(ctx: Ctx) -> dict:
    u = usage(ctx.org_id)
    p = ctx.plan
    return {"email": ctx.email, "role": ctx.role, "is_admin": ctx.is_admin, "org": p.get("org_name"),
            "plan": {"key": p.get("key"), "label": p.get("label"), "analyses": p.get("analyses"), "ai": p.get("ai"),
                     "members": p.get("members"),
                     "trial_end": p["trial_end"].isoformat() if p.get("trial_end") else None,
                     "paid_until": p["paid_until"].isoformat() if p.get("paid_until") else None},
            "usage": u}


# ---------------------------------------------------------------- équipe
def team(ctx: Ctx) -> dict:
    members = q("""select m.user_id::text, u.email, coalesce(u.raw_user_meta_data->>'full_name', '') as name, m.role
                   from app.memberships m join auth.users u on u.id = m.user_id
                   where m.org_id = %s order by m.created_at""", (ctx.org_id,))
    invites = q("""select id::text, email, role, expires_at from app.invitations
                   where org_id = %s and accepted_at is null and expires_at > now() order by created_at desc""",
                (ctx.org_id,))
    for i in invites:
        i["expires_at"] = i["expires_at"].isoformat()
    return {"members": members, "invitations": invites}


def invite(ctx: Ctx, email: str, role: str) -> dict:
    require(ctx, "owner")
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]{1,64}@[^@\s.]{1,63}(?:\.[^@\s.]{1,63})+$", email):
        raise ValueError("Adresse e-mail invalide.")
    if role not in ROLES:
        raise ValueError("Rôle inconnu.")
    t = team(ctx)
    if len(t["members"]) + len(t["invitations"]) >= ctx.plan.get("members", 1):
        raise QuotaExceeded(f"Le plan {ctx.plan.get('label')} est limité à {ctx.plan.get('members')} membre(s).")
    if q("select 1 from app.memberships m join auth.users u on u.id = m.user_id where lower(u.email) = %s",
         (email,), one=True):
        raise ValueError("Cette personne fait déjà partie d'un espace KLDataViz.")
    q("""insert into app.invitations (org_id, email, role, token, invited_by, expires_at)
         values (%s, %s, %s, %s, %s, now() + interval '14 days')""",
      (ctx.org_id, email, role, secrets.token_urlsafe(24), ctx.user_id))
    link = f"{APP_URL}/login?invite=1&email={urllib.request.quote(email)}"
    try:
        send_mail([email], f"Invitation à rejoindre {ctx.plan.get('org_name')} sur KLDataViz",
                  f"Bonjour,\n\n{ctx.email} vous invite à rejoindre l'espace « {ctx.plan.get('org_name')} » "
                  f"sur KLDataViz.\n\nCréez votre compte avec cette adresse e-mail ({email}) ici :\n{link}\n\n"
                  "L'invitation est valable 14 jours.")
        sent, why = True, None
    except Exception as exc:
        sent, why = False, mail_failure(exc)
    return {"ok": True, "email_sent": sent, "email_error": why, "link": link}


def cancel_invite(ctx: Ctx, invite_id: str):
    require(ctx, "owner")
    q("delete from app.invitations where id = %s and org_id = %s", (invite_id, ctx.org_id))


def set_role(ctx: Ctx, user_id: str, role: str):
    require(ctx, "owner")
    if role not in ROLES:
        raise ValueError("Rôle inconnu.")
    if user_id == ctx.user_id:
        raise ValueError("Vous ne pouvez pas changer votre propre rôle.")
    q("update app.memberships set role = %s where user_id = %s and org_id = %s", (role, user_id, ctx.org_id))


def remove_member(ctx: Ctx, user_id: str):
    require(ctx, "owner")
    if user_id == ctx.user_id:
        raise ValueError("Vous ne pouvez pas vous retirer vous-même.")
    q("delete from app.memberships where user_id = %s and org_id = %s", (user_id, ctx.org_id))


# ---------------------------------------------------------------- administration de la plateforme
def _admin(ctx: Ctx):
    if not ctx.is_admin:
        raise Forbidden("Réservé aux administrateurs de la plateforme.")


def _iso(rows, *keys):
    for r in rows:
        for k in keys:
            if r.get(k) is not None and hasattr(r[k], "isoformat"):
                r[k] = r[k].isoformat()
    return rows


def admin_stats(ctx: Ctx) -> dict:
    _admin(ctx)
    month = dt.date.today().strftime("%Y-%m")
    catalog = plans()
    orgs = q("select plan, suspended, trial_end, created_at from app.organizations")
    now = dt.datetime.now(dt.timezone.utc)
    by_plan, mrr, currency = {}, 0.0, None
    for o in orgs:
        key = "suspendu" if o["suspended"] else o["plan"]
        if catalog.get(key, {}).get("days") and o["trial_end"] and o["trial_end"] < now:
            key = "expire"
        by_plan[key] = by_plan.get(key, 0) + 1
        price = catalog.get(o["plan"], {}).get("price")
        if price and not o["suspended"] and not catalog.get(o["plan"], {}).get("days"):
            mrr += price
            currency = currency or catalog[o["plan"]].get("currency")
    users = q("select count(*) as n from auth.users", one=True)["n"]
    usage = q("""select coalesce(sum(analyses), 0) as analyses, coalesce(sum(ai_calls), 0) as ai,
                        count(*) filter (where analyses + ai_calls > 0) as active
                 from app.usage_monthly where month = %s""", (month,), one=True)
    signups = q("""select to_char(d, 'YYYY-MM-DD') as day,
                          (select count(*) from app.organizations o where o.created_at::date = d) as n
                   from generate_series(current_date - 29, current_date, interval '1 day') d order by d""")
    recent = _iso(q("""select u.email, u.created_at, o.name as org, o.plan
                       from auth.users u
                       left join app.memberships m on m.user_id = u.id
                       left join app.organizations o on o.id = m.org_id
                       order by u.created_at desc limit 8"""), "created_at")
    return {"orgs": len(orgs), "users": users, "by_plan": by_plan, "mrr": mrr, "currency": currency or "FCFA",
            "usage": {"analyses": usage["analyses"], "ai": usage["ai"], "active_orgs": usage["active"]},
            "signups": signups, "recent": recent, "month": month}


def admin_orgs(ctx: Ctx, search: str = "") -> list:
    _admin(ctx)
    month = dt.date.today().strftime("%Y-%m")
    like = f"%{(search or '').strip().lower()}%"
    rows = q("""select o.id::text, o.name, o.plan, o.trial_end, o.created_at, o.suspended,
                       (select count(*) from app.memberships m where m.org_id = o.id) as members,
                       (select u.email from app.memberships m join auth.users u on u.id = m.user_id
                         where m.org_id = o.id and m.role = 'owner' order by m.created_at limit 1) as owner_email,
                       coalesce(um.analyses, 0) as analyses, coalesce(um.ai_calls, 0) as ai
                from app.organizations o
                left join app.usage_monthly um on um.org_id = o.id and um.month = %s
                where lower(o.name) like %s or exists (
                    select 1 from app.memberships m join auth.users u on u.id = m.user_id
                    where m.org_id = o.id and lower(u.email) like %s)
                order by o.created_at desc limit 500""", (month, like, like))
    return _iso(rows, "trial_end", "created_at")


def admin_org_detail(ctx: Ctx, org_id: str) -> dict:
    _admin(ctx)
    members = _iso(q("""select u.email, coalesce(u.raw_user_meta_data->>'full_name', '') as name, m.role,
                               u.last_sign_in_at, m.created_at
                        from app.memberships m join auth.users u on u.id = m.user_id
                        where m.org_id = %s order by m.created_at""", (org_id,)), "last_sign_in_at", "created_at")
    invites = _iso(q("""select email, role, expires_at, accepted_at from app.invitations
                        where org_id = %s order by created_at desc limit 20""", (org_id,)), "expires_at", "accepted_at")
    usage = q("select month, analyses, ai_calls as ai from app.usage_monthly where org_id = %s order by month desc limit 6",
              (org_id,))
    return {"members": members, "invitations": invites, "usage": usage}


def admin_set_plan(ctx: Ctx, org_id: str, plan: str, trial_days: int | None = None):
    _admin(ctx)
    catalog = plans()
    if plan not in catalog:
        raise ValueError("Forfait inconnu.")
    days = trial_days or catalog[plan].get("days")
    if days:
        q("""update app.organizations set plan = %s, trial_end = now() + make_interval(days => %s), paid_until = null
             where id = %s""", (plan, int(days), org_id))
    else:  # forfait attribué à la main : sans échéance
        q("update app.organizations set plan = %s, paid_until = null where id = %s", (plan, org_id))


def admin_extend_trial(ctx: Ctx, org_id: str, days: int):
    _admin(ctx)
    days = int(days)
    if not 1 <= days <= 365:
        raise ValueError("Entre 1 et 365 jours.")
    q("""update app.organizations set trial_end = greatest(coalesce(trial_end, now()), now()) + make_interval(days => %s),
           paid_until = case when paid_until is null then null
                             else greatest(paid_until, now()) + make_interval(days => %s) end
         where id = %s""", (days, days, org_id))


def admin_suspend(ctx: Ctx, org_id: str, suspended: bool):
    _admin(ctx)
    q("update app.organizations set suspended = %s where id = %s", (bool(suspended), org_id))


def admin_invite_client(ctx: Ctx, org_name: str, email: str, plan: str) -> dict:
    """Crée l'espace d'un client et invite son propriétaire."""
    _admin(ctx)
    org_name, email = (org_name or "").strip()[:80], (email or "").strip().lower()
    if len(org_name) < 2:
        raise ValueError("Indiquez le nom de l'organisation du client.")
    if not re.match(r"^[^@\s]{1,64}@[^@\s.]{1,63}(?:\.[^@\s.]{1,63})+$", email):
        raise ValueError("Adresse e-mail invalide.")
    catalog = plans()
    if plan not in catalog:
        raise ValueError("Forfait inconnu.")
    if q("select 1 from app.memberships m join auth.users u on u.id = m.user_id where lower(u.email) = %s",
         (email,), one=True):
        raise ValueError("Cette personne fait déjà partie d'un espace.")
    pending = q("""select o.name from app.invitations i join app.organizations o on o.id = i.org_id
                   where lower(i.email) = %s and i.accepted_at is null and i.expires_at > now() limit 1""",
                (email,), one=True)
    if pending:  # sinon un second espace serait créé pour la même personne
        raise ValueError(f"Une invitation est déjà en attente pour cette adresse (espace « {pending['name']} »). "
                         "Ouvrez « Gérer » sur cet espace et cliquez sur « Renvoyer ».")
    days = catalog[plan].get("days")
    org = q("""insert into app.organizations (name, plan, trial_end) values (%s, %s,
               case when %s::int is null then null else now() + make_interval(days => %s::int) end) returning id::text""",
            (org_name, plan, days, days), one=True)
    q("""insert into app.invitations (org_id, email, role, token, invited_by, expires_at)
         values (%s, %s, 'owner', %s, %s, now() + interval '30 days')""",
      (org["id"], email, secrets.token_urlsafe(24), ctx.user_id))
    sent, why, link = _client_invite_mail(email, org_name, catalog[plan]["label"])
    return {"ok": True, "org_id": org["id"], "email_sent": sent, "email_error": why, "link": link}


def _client_invite_mail(email: str, org_name: str, plan_label: str):
    """E-mail d'invitation du propriétaire d'un nouvel espace → (envoyé, cause de l'échec, lien)."""
    link = f"{APP_URL}/login?invite=1&email={urllib.request.quote(email)}"
    try:
        send_mail([email], f"Votre espace KLDataViz « {org_name} » est prêt",
                  f"Bonjour,\n\nVotre espace « {org_name} » a été créé sur KLDataViz (forfait {plan_label}).\n\n"
                  f"Créez votre compte avec cette adresse e-mail ({email}) pour y accéder :\n{link}\n\n"
                  "L'invitation est valable 30 jours.")
        return True, None, link
    except Exception as exc:
        return False, mail_failure(exc), link


def admin_resend_invite(ctx: Ctx, org_id: str, email: str) -> dict:
    """Renvoie l'invitation d'un espace (compte pas encore créé) et la prolonge de 30 jours."""
    _admin(ctx)
    email = (email or "").strip().lower()
    inv = q("""select i.id::text, o.name, o.plan from app.invitations i join app.organizations o on o.id = i.org_id
               where i.org_id = %s and lower(i.email) = %s and i.accepted_at is null
               order by i.created_at desc limit 1""", (org_id, email), one=True)
    if not inv:
        raise ValueError("Aucune invitation en attente pour cette adresse dans cet espace.")
    q("update app.invitations set expires_at = now() + interval '30 days' where id = %s", (inv["id"],))
    label = (plans().get(inv["plan"]) or {}).get("label", inv["plan"])
    sent, why, link = _client_invite_mail(email, inv["name"], label)
    return {"ok": True, "email_sent": sent, "email_error": why, "link": link}


def admin_test_mail(ctx: Ctx) -> dict:
    """E-mail de test à l'administrateur connecté : vérifie la configuration Brevo sans créer d'espace."""
    _admin(ctx)
    info = {"to": ctx.email, "sender": MAIL_FROM or None, "configured": mail_ready()}
    try:
        send_mail([ctx.email], "KLDataViz — e-mail de test",
                  "Bonjour,\n\nSi vous lisez ce message, l'envoi d'e-mails de KLDataViz fonctionne.\n"
                  "Pensez à vérifier aussi qu'il n'est pas arrivé dans les indésirables.")
        return dict(info, ok=True, email_sent=True, email_error=None)
    except Exception as exc:
        return dict(info, ok=True, email_sent=False, email_error=mail_failure(exc))



# ---- demandes de démonstration (page d'accueil, sans compte)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]{1,63}(?:\.[^@\s.]{1,63})+$")
DEMO_STATUTS = ("nouvelle", "traitee", "ecartee")


def _ligne(v, n):
    """Texte d'une seule ligne, sans caractères de contrôle (pas d'en-tête ni de ligne injectée dans l'e-mail)."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(v or "")).strip()[:n]


def _destinataires_admin() -> list:
    try:
        base = {r["email"] for r in q("select email from app.platform_admins")}
    except Exception:
        base = set()
    return sorted(ADMIN_EMAILS | {e.lower() for e in base if e})


def demande_demo(data: dict) -> dict:
    """Enregistre une demande de démo et prévient les administrateurs. Un robot (champ piège rempli) reçoit
    la même réponse qu'une personne, mais rien n'est enregistré."""
    if not isinstance(data, dict):
        raise ValueError("Demande invalide.")
    if str(data.get("site_web") or "").strip():
        return {"ok": True}
    name, org = _ligne(data.get("name"), 120), _ligne(data.get("org"), 120)
    email, phone = _ligne(data.get("email"), 254).lower(), _ligne(data.get("phone"), 40)
    message = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]+", " ", str(data.get("message") or "")).strip()[:1500]
    if len(name) < 2:
        raise ValueError("Indiquez votre nom.")
    if not EMAIL_RE.match(email):
        raise ValueError("Adresse e-mail invalide.")
    if len(org) < 2:
        raise ValueError("Indiquez le nom de votre organisation.")
    if phone and not re.fullmatch(r"[+()\d][\d ()+.-]{5,39}", phone):
        raise ValueError("Numéro de téléphone invalide.")
    # Même adresse dans les dernières 24 h : on ne crée pas de doublon et on ne relance pas les administrateurs
    if q("""select 1 from app.demo_requests where lower(email) = %s and created_at > now() - interval '24 hours'
            limit 1""", (email,), one=True):
        return {"ok": True}
    q("""insert into app.demo_requests (name, email, org, phone, message) values (%s, %s, %s, %s, %s)""",
      (name, email, org, phone or None, message or None))
    a = _destinataires_admin()
    if a:
        try:
            send_mail(a, f"Demande de démo : {org}",
                      f"Nouvelle demande de démonstration sur KLDataViz.\n\nNom : {name}\nOrganisation : {org}\n"
                      f"E-mail : {email}\nTéléphone : {phone or '—'}\n\nMessage :\n{message or '—'}\n\n"
                      f"À traiter dans l'administration, onglet « Demandes de démo » : {APP_URL}/admin")
        except Exception as exc:  # la demande reste enregistrée et visible dans l'administration
            import sys
            sys.stderr.write("[demo] e-mail aux administrateurs non parti : " + mail_failure(exc) + "\n")
    return {"ok": True}


def admin_demandes_demo(ctx: Ctx) -> list:
    _admin(ctx)
    rows = q("""select d.id::text, d.created_at, d.name, d.email, d.org, d.phone, d.message, d.status, d.handled_by,
                       d.handled_at, d.org_id::text, o.name as org_name
                from app.demo_requests d left join app.organizations o on o.id = d.org_id
                order by d.created_at desc limit 300""")
    return _iso(rows, "created_at", "handled_at")


def admin_demande_demo_statut(ctx: Ctx, demande_id: str, statut: str, org_id: str | None = None):
    _admin(ctx)
    if statut not in DEMO_STATUTS:
        raise ValueError("Statut inconnu.")
    try:
        uuid.UUID(str(demande_id))
        org_id = str(uuid.UUID(str(org_id))) if org_id else None
    except ValueError:
        raise ValueError("Demande introuvable.")
    r = q("""update app.demo_requests set status = %s, org_id = coalesce(%s::uuid, org_id),
               handled_by = case when %s = 'nouvelle' then null else %s end,
               handled_at = case when %s = 'nouvelle' then null else now() end
             where id = %s returning id""", (statut, org_id, statut, ctx.email, statut, demande_id), one=True)
    if not r:
        raise ValueError("Demande introuvable.")

# ---- forfaits
PLAN_FIELDS = ("label", "price", "currency", "period", "analyses", "ai", "members", "trial_days", "description",
               "features", "highlight", "public", "sort")


def admin_plans(ctx: Ctx) -> list:
    _admin(ctx)
    rows = q("""select p.*, (select count(*) from app.organizations o where o.plan = p.key) as orgs
                from app.plans p order by sort, key""")
    for r in rows:
        r["price"] = float(r["price"]) if r["price"] is not None else None
        r["features"] = list(r["features"] or [])
    return _iso(rows, "updated_at")


def admin_plan_save(ctx: Ctx, data: dict):
    _admin(ctx)
    key = str(data.get("key") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{2,30}", key):
        raise ValueError("Identifiant du forfait : 2 à 30 lettres minuscules, chiffres, - ou _.")
    v = {k: data.get(k) for k in PLAN_FIELDS}
    v["label"] = str(v["label"] or "").strip()[:40] or key
    v["price"] = None if v["price"] in (None, "") else float(v["price"])
    for k in ("analyses", "ai", "members", "sort"):
        v[k] = int(v[k] or 0)
    v["trial_days"] = None if v["trial_days"] in (None, "", 0, "0") else int(v["trial_days"])
    v["features"] = [str(f).strip()[:120] for f in (v["features"] or []) if str(f).strip()][:12]
    v["currency"] = str(v["currency"] or "FCFA").strip()[:10]
    v["period"] = str(v["period"] or "mois").strip()[:20]
    v["description"] = str(v["description"] or "").strip()[:300]
    v["highlight"], v["public"] = bool(v["highlight"]), bool(v["public"]) and key != "suspendu"
    cols = ", ".join(PLAN_FIELDS)
    q(f"""insert into app.plans (key, {cols}) values (%s, {', '.join(['%s'] * len(PLAN_FIELDS))})
          on conflict (key) do update set {', '.join(f'{c} = excluded.{c}' for c in PLAN_FIELDS)}, updated_at = now()""",
      (key,) + tuple(v[k] for k in PLAN_FIELDS))
    _cache.pop("plans", None)


def admin_plan_delete(ctx: Ctx, key: str):
    _admin(ctx)
    if key in ("essai", "suspendu"):
        raise ValueError("Les forfaits « essai » et « suspendu » sont indispensables.")
    if q("select 1 from app.organizations where plan = %s limit 1", (key,), one=True):
        raise ValueError("Des espaces utilisent ce forfait : changez-les de forfait d'abord.")
    if q("""select 1 from app.payments where plan = %s and status = 'pending'
             and created_at > now() - make_interval(days => %s) limit 1""", (key, JOURS_RELECTURE), one=True):
        raise ValueError("Un paiement de ce forfait est en cours : réessayez plus tard.")
    q("delete from app.plans where key = %s", (key,))
    _cache.pop("plans", None)


def public_plans() -> list:
    return [dict(v, key=k) for k, v in sorted(plans().items(), key=lambda kv: kv[1].get("sort", 0)) if v.get("public")]


# ---- administrateurs
def admin_admins(ctx: Ctx) -> list:
    _admin(ctx)
    rows = _iso(q("select email, added_by, created_at from app.platform_admins order by created_at"), "created_at")
    env = [{"email": e, "added_by": "configuration du serveur", "created_at": None, "locked": True} for e in sorted(ADMIN_EMAILS)]
    return env + [r for r in rows if r["email"] not in ADMIN_EMAILS]


def admin_admin_add(ctx: Ctx, email: str):
    _admin(ctx)
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]{1,64}@[^@\s.]{1,63}(?:\.[^@\s.]{1,63})+$", email):
        raise ValueError("Adresse e-mail invalide.")
    q("insert into app.platform_admins (email, added_by) values (%s, %s) on conflict do nothing", (email, ctx.email))
    _cache.pop("admins", None)


def admin_admin_remove(ctx: Ctx, email: str):
    _admin(ctx)
    email = (email or "").strip().lower()
    if email in ADMIN_EMAILS:
        raise ValueError("Cet administrateur est défini dans la configuration du serveur (ADMIN_EMAILS).")
    if email == ctx.email:
        raise ValueError("Vous ne pouvez pas retirer vos propres droits.")
    q("delete from app.platform_admins where email = %s", (email,))
    _cache.pop("admins", None)


# ---- annonces
def admin_announcements(ctx: Ctx) -> list:
    _admin(ctx)
    return _iso(q("select * from app.announcements order by created_at desc limit 100"),
                "starts_at", "ends_at", "created_at")


def _ts(v):
    return dt.datetime.fromisoformat(v) if v else None


def admin_announcement_save(ctx: Ctx, data: dict):
    _admin(ctx)
    title = str(data.get("title") or "").strip()[:120]
    if not title:
        raise ValueError("Indiquez le titre de l'annonce.")
    level = data.get("level") if data.get("level") in ("info", "success", "warning") else "info"
    audience = data.get("audience") if data.get("audience") in ("all", "public", "app") else "all"
    link_url = str(data.get("link_url") or "").strip() or None
    if link_url and not re.match(r"^(https://|/)", link_url):
        raise ValueError("Le lien doit commencer par https:// ou /.")
    vals = (title, str(data.get("body") or "").strip()[:600], level, audience,
            str(data.get("link_label") or "").strip()[:40] or None, link_url,
            _ts(data.get("starts_at")), _ts(data.get("ends_at")), bool(data.get("active", True)))
    if data.get("id"):
        q("""update app.announcements set title=%s, body=%s, level=%s, audience=%s, link_label=%s, link_url=%s,
             starts_at=%s, ends_at=%s, active=%s where id = %s""", vals + (data["id"],))
    else:
        q("""insert into app.announcements (title, body, level, audience, link_label, link_url, starts_at, ends_at,
             active, created_by) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", vals + (ctx.email,))
    _cache.pop("ann", None)


def admin_announcement_delete(ctx: Ctx, ann_id: str):
    _admin(ctx)
    q("delete from app.announcements where id = %s", (ann_id,))
    _cache.pop("ann", None)


def announcements(audience: str) -> list:
    """Annonces actives pour la page d'accueil (« public ») ou l'application (« app »)."""
    rows = _cached("ann", 30, lambda: _iso(q("""select id::text, title, body, level, audience, link_label, link_url
        from app.announcements where active and (starts_at is null or starts_at <= now())
        and (ends_at is null or ends_at > now()) order by created_at desc limit 5""")))
    return [r for r in rows if r["audience"] in ("all", audience)]


# ---- réglages du site public
SITE_KEYS = ("hero_title", "hero_subtitle", "contact_email", "contact_whatsapp")


def _all_settings() -> dict:
    def load():
        try:
            return {r["key"]: r["value"] for r in q("select key, value from app.site_settings")}
        except Exception:
            return {}
    return _cached("site", 30, load)


def site_settings() -> dict:
    """Réglages publics de la page d'accueil (jamais les réglages techniques comme le fournisseur d'IA)."""
    return {k: v for k, v in _all_settings().items() if k in SITE_KEYS}


# ---- fournisseur d'IA, choisi dans l'administration (les clés restent des variables d'environnement)
IA_FOURNISSEURS = ("auto", "anthropic", "deepseek")
IA_MODELES = {"anthropic": ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"),
              "deepseek": ("deepseek-v4-pro", "deepseek-flash")}


def ia_settings() -> dict:
    """{provider, model} choisis par l'administrateur ; {} si rien n'est réglé (on suit alors l'environnement)."""
    s = _all_settings()
    return {k: s[f"ia_{k}"] for k in ("provider", "model") if s.get(f"ia_{k}")}


def admin_ia(ctx: Ctx) -> dict:
    _admin(ctx)
    r = ia_settings()
    return {"provider": r.get("provider", "auto"), "model": r.get("model", ""), "modeles": IA_MODELES,
            "cles": {"anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
                     "deepseek": bool(os.environ.get("DEEPSEEK_API_KEY"))}}


def admin_ia_save(ctx: Ctx, data: dict):
    _admin(ctx)
    prov = str(data.get("provider") or "auto").strip().lower()
    if prov not in IA_FOURNISSEURS:
        raise ValueError("Fournisseur d'IA inconnu.")
    model = str(data.get("model") or "").strip()
    if model and (prov == "auto" or model not in IA_MODELES.get(prov, ())):
        raise ValueError("Ce modèle ne correspond pas au fournisseur choisi.")
    env = {"anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}.get(prov)
    if env and not os.environ.get(env):
        raise ValueError(f"Aucune clé {env} n'est configurée sur le serveur : ajoutez-la dans les variables "
                         "d'environnement (Render) avant de choisir ce fournisseur.")
    for k, v in (("ia_provider", prov), ("ia_model", model)):
        q("""insert into app.site_settings (key, value) values (%s, %s::jsonb)
             on conflict (key) do update set value = excluded.value, updated_at = now()""", (k, json.dumps(v)))
    _cache.pop("site", None)


def admin_site_save(ctx: Ctx, data: dict):
    _admin(ctx)
    for k in SITE_KEYS:
        if k in data:
            q("""insert into app.site_settings (key, value) values (%s, %s::jsonb)
                 on conflict (key) do update set value = excluded.value, updated_at = now()""",
              (k, json.dumps(str(data[k] or "").strip()[:400])))
    _cache.pop("site", None)


# ---------------------------------------------------------------- réglages et planifications (par organisation)
def get_settings(org_id: str) -> dict:
    r = q("select langue, detail from app.org_settings where org_id = %s", (org_id,), one=True)
    return r or {"langue": "Français", "detail": "simple"}


def save_settings(ctx: Ctx, langue: str, detail: str, org_name: str | None):
    require(ctx, "owner", "analyst")
    q("""insert into app.org_settings (org_id, langue, detail) values (%s, %s, %s)
         on conflict (org_id) do update set langue = excluded.langue, detail = excluded.detail, updated_at = now()""",
      (ctx.org_id, langue, detail))
    if org_name and ctx.role == "owner":
        q("update app.organizations set name = %s where id = %s", (org_name.strip()[:80], ctx.org_id))


SCHEDULE_COLS = "id, source, name, freq, hour, format, recipients, enabled, created, last_run, last_status, last_file, next_run"


def _sched_out(r):
    if r is None:
        return None
    d = {"id": r["id"], "source": r["source"], "name": r["name"], "freq": r["freq"], "hour": r["hour"],
         "format": r["format"], "to": list(r["recipients"] or []), "on": r["enabled"],
         "last_status": r["last_status"], "last_file": r["last_file"]}
    for k in ("created", "last_run", "next_run"):
        d[k] = r[k].replace(tzinfo=None).isoformat(timespec="seconds") if r.get(k) else None
    return d


def schedules(org_id: str) -> list:
    return [_sched_out(r) for r in q(f"select {SCHEDULE_COLS} from app.schedules where org_id = %s order by created desc",
                                     (org_id,))]


def schedule_get(org_id: str, job_id: str):
    return _sched_out(q(f"select {SCHEDULE_COLS} from app.schedules where org_id = %s and id = %s",
                        (org_id, job_id), one=True))


def schedule_insert(org_id: str, job: dict):
    q("""insert into app.schedules (id, org_id, source, name, freq, hour, format, recipients, enabled, created, next_run)
         values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
      (job["id"], org_id, job["source"], job["name"], job["freq"], job["hour"], job["format"], job["to"], job["on"],
       job["created"], job["next_run"]))


def schedule_update(org_id: str, job_id: str, **f):
    cols = {"on": "enabled", "last_run": "last_run", "last_status": "last_status", "last_file": "last_file",
            "next_run": "next_run"}
    sets = [(cols[k], v) for k, v in f.items() if k in cols]
    if sets:
        q(f"update app.schedules set {', '.join(c + ' = %s' for c, _ in sets)} where org_id = %s and id = %s",
          tuple(v for _, v in sets) + (org_id, job_id))
    return schedule_get(org_id, job_id)


def schedule_delete(org_id: str, job_id: str):
    q("delete from app.schedules where org_id = %s and id = %s", (org_id, job_id))


def due_schedules() -> list:
    """Planifications à exécuter, toutes organisations confondues (tâche de fond). Un espace dont l'essai ou l'abonnement
    est terminé n'envoie plus rien : personne ne s'y connecte plus, et ses destinataires n'ont aucun moyen d'arrêter."""
    rows = q(f"""select s.org_id::text as org_id, {', '.join('s.' + c for c in SCHEDULE_COLS.split(', '))}
                 from app.schedules s join app.organizations o on o.id = s.org_id
                 where s.enabled and s.next_run <= now() and o.plan <> 'suspendu' and not o.suspended""")
    actifs = {}
    for r in rows:
        if r["org_id"] not in actifs:
            actifs[r["org_id"]] = org_ctx(r["org_id"]).plan.get("key") != "suspendu"
    return [(r["org_id"], _sched_out(r)) for r in rows if actifs[r["org_id"]]]


def org_ctx(org_id: str) -> Ctx:
    """Contexte technique d'une organisation (tâches de fond : rapports planifiés)."""
    o = q("select name, plan, trial_end, paid_until, suspended from app.organizations where id = %s", (org_id,), one=True)
    catalog = plans()
    cle = o["plan"] if o["plan"] in catalog else "essai"
    if o["suspended"] or expire(cle, o["trial_end"], o["paid_until"], catalog):
        cle = "suspendu"
    plan = dict(catalog.get(cle, _FALLBACK_PLANS["suspendu"]), key=cle, org_name=o["name"], trial_end=o["trial_end"])
    return Ctx(key=f"job:{org_id}", org_id=org_id, role="analyst", plan=plan)


def admin_planifications(ctx: Ctx) -> list:
    """Planifications de tous les espaces : chaque espace ne voit que les siennes."""
    _admin(ctx)
    return _iso(q("""select s.id, s.org_id::text as org_id, o.name as espace, s.name, s.freq, s.hour, s.recipients, s.enabled,
                            s.last_run, s.last_status, s.next_run
                     from app.schedules s join app.organizations o on o.id = s.org_id
                     order by s.enabled desc, o.name, s.name"""), "last_run", "next_run")


def admin_arreter_planification(ctx: Ctx, org_id: str, job_id: str):
    _admin(ctx)
    return schedule_update(org_id, job_id, on=False)


# ---------------------------------------------------------------- e-mail (Brevo)
def mail_ready() -> bool:
    return bool(BREVO_API_KEY and MAIL_FROM)


def send_mail(recipients, subject, body, attachment=None):
    """Envoi par Brevo ; chaque réussite ou échec est noté par la surveillance (bilan, alertes)."""
    import surveillance
    try:
        envoyer_brevo(recipients, subject, body, attachment)
    except Exception as exc:
        surveillance.envoi(False, str(exc) or type(exc).__name__)
        raise
    surveillance.envoi(True)


def envoyer_brevo(recipients, subject, body, attachment=None):
    if not mail_ready():
        raise RuntimeError("Envoi d'e-mails non configuré sur la plateforme.")
    payload = {"sender": {"email": MAIL_FROM, "name": MAIL_FROM_NAME},
               "to": [{"email": r} for r in recipients], "subject": subject, "textContent": body}
    if attachment and os.path.exists(attachment):
        with open(attachment, "rb") as f:
            payload["attachment"] = [{"name": os.path.basename(attachment),
                                      "content": base64.b64encode(f.read()).decode()}]
    req = urllib.request.Request("https://api.brevo.com/v3/smtp/email", method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except urllib.error.HTTPError as exc:
        try:  # Brevo explique le refus : {"code": "unauthorized", "message": "unrecognised IP address …"}
            detail = json.loads(exc.read().decode() or "{}").get("message", "")
        except Exception:
            detail = ""
        raise RuntimeError(f"Envoi refusé par Brevo ({exc.code}){' : ' + detail if detail else ''}. {_mail_hint(exc.code, detail)}".strip())
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Brevo injoignable depuis le serveur ({exc.reason}).")


def _mail_hint(code, detail: str) -> str:
    """Que faire dans Brevo selon le refus (les causes les plus fréquentes)."""
    d = (detail or "").lower()
    if "ip" in d:
        return "Brevo → Sécurité → IP autorisées : désactivez le blocage (les adresses de Render changent)."
    if code == 401:
        return "Clé API invalide : régénérez-la dans Brevo (SMTP & API) et mettez à jour BREVO_API_KEY sur Render."
    if "sender" in d or "expéditeur" in d:
        return "Expéditeur non validé : vérifiez l'adresse MAIL_FROM dans Brevo → Expéditeurs."
    if code == 403:
        return "Compte Brevo non activé pour l'envoi transactionnel : finalisez l'activation dans Brevo."
    return ""


def mail_failure(exc: Exception) -> str:
    """Raison lisible d'un échec d'envoi, aussi écrite dans les journaux du serveur."""
    msg = str(exc) or exc.__class__.__name__
    print(f"[mail] échec d'envoi : {msg}", flush=True)
    return msg


# ---------------------------------------------------------------- abonnements prépayés (CinetPay)
DUREES = (1, 3, 12)
DEVISES = {"FCFA": "XOF", "XOF": "XOF", "XAF": "XAF"}
ECHECS = ("FAILED", "EXPIRED", "INSUFFICIENT_BALANCE", "USER_IS_BLOCKED", "NOT_ALLOWED")
JOURS_RELECTURE = 7       # au-delà, un paiement resté sans réponse définitive est clos
JOURS_RATTRAPAGE = 30     # le retour du client relit encore un paiement clos : activer deux fois est impossible
LOT_RECONCILIATION = 20
DUREE_MAX_PASSAGE = 240   # secondes
_lock_rappels = threading.Lock()


def paiement_actif() -> bool:
    return paiement.pret() and APP_URL.startswith("https://")


def _offres(sans_prix=False) -> list:
    return [p for p in public_plans() if ((p.get("price") or 0) > 0 or (sans_prix and p.get("price") is None))
            and not p.get("days") and p["key"] != "suspendu"]


def _paiement_ouvert(ctx: Ctx) -> bool:
    """Une clé de test active un vrai forfait sans argent : elle reste réservée aux administrateurs."""
    return paiement_actif() and (not paiement.mode_test() or ctx.is_admin)


def billing(ctx: Ctx) -> dict:
    org = q("select plan, paid_until, trial_end from app.organizations where id = %s", (ctx.org_id,), one=True) or {}
    hist = q("""select merchant_tx, plan, months, amount, currency, status, created_at, paid_at
                from app.payments where org_id = %s order by created_at desc limit 20""", (ctx.org_id,))
    ouvert = _paiement_ouvert(ctx)
    apercu = ctx.is_admin and not ouvert   # l'administrateur voit l'écran avant l'ouverture du paiement
    return {"actif": ouvert, "apercu": apercu, "test": paiement.mode_test(), "peut_payer": ctx.role == "owner",
            "plan": ctx.plan.get("key"), "plan_paye": org.get("plan"), "offres": _offres(sans_prix=apercu),
            "durees": list(DUREES),
            "paid_until": org["paid_until"].isoformat() if org.get("paid_until") else None,
            "paiements": [dict(h, created_at=h["created_at"].isoformat(),
                               paid_at=h["paid_at"].isoformat() if h["paid_at"] else None) for h in hist]}


def checkout(ctx: Ctx, plan: str, months) -> dict:
    """Prépare le paiement d'un forfait : le prix vient de la base, jamais du navigateur."""
    require(ctx, "owner")
    if not _paiement_ouvert(ctx):
        raise Unavailable("Le paiement en ligne n'est pas encore activé. Contactez-nous pour choisir un forfait.")
    offre = next((o for o in _offres() if o["key"] == plan), None)
    if offre is None:
        raise ValueError("Ce forfait ne peut pas être acheté en ligne.")
    try:
        months = int(months)
    except (TypeError, ValueError):
        raise ValueError("Durée invalide.") from None
    if months not in DUREES:
        raise ValueError("Durée invalide.")
    devise = DEVISES.get(str(offre.get("currency") or "FCFA").upper())
    if devise is None:
        raise ValueError("Devise non prise en charge pour ce forfait.")
    montant = int(round(offre["price"] * months))
    if montant < paiement.MONTANT_MIN:
        raise ValueError("Ce montant est trop faible pour un paiement en ligne.")
    if montant > paiement.MONTANT_MAX:
        raise ValueError("Ce montant ne peut pas être réglé en une fois. Choisissez une durée plus courte.")
    org = q("select plan, paid_until, suspended from app.organizations where id = %s", (ctx.org_id,), one=True)
    # Lu en base : un abonnement expiré se présente aussi comme « suspendu » dans ctx.plan, et doit pouvoir payer.
    if org and (org["suspended"] or org["plan"] == "suspendu"):
        raise ValueError("Votre espace est suspendu. Contactez-nous avant de payer.")
    if org and org["paid_until"] and org["paid_until"] > dt.datetime.now(dt.timezone.utc) and org["plan"] != plan \
            and not (plans().get(org["plan"], {}).get("price") or 0) > 0:
        raise ValueError("Votre forfait actuel est sur devis. Contactez-nous pour en changer.")
    tx = "KLD" + uuid.uuid4().hex[:24]
    row = q("""insert into app.payments (org_id, user_id, email, plan, months, amount, currency, mode, merchant_tx)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id::text""",
            (ctx.org_id, ctx.user_id, ctx.email, plan, months, montant, devise, paiement.mode(), tx), one=True)
    try:
        r = paiement.initier(tx, montant, devise, f"KLDataViz {offre['label']} - {months} mois", ctx.email, "Client",
                             (ctx.plan.get("org_name") or "KLDataViz").ljust(2, "."), f"{APP_URL}/app?paiement=ok",
                             f"{APP_URL}/app?paiement=echec", f"{APP_URL}/api/paiement/notification")
    except paiement.PaiementError as e:
        q("update app.payments set status = 'failed' where id = %s", (row["id"],))
        raise Unavailable(f"Paiement impossible pour le moment. {e}") from None
    q("update app.payments set notify_token = %s, transaction_id = %s where id = %s",
      (r["notify_token"], r["transaction_id"], row["id"]))
    return {"payment_url": r["payment_url"]}


def _activer(pay_id: str, transaction_id=None):
    """Active le forfait une seule fois, en une instruction : deux confirmations simultanées n'en comptent qu'une.

    Même forfait : la durée s'ajoute. Changement de forfait : le temps restant est converti au prix réellement payé,
    et conservé tel quel si ce prix n'est plus connu : un paiement ne doit jamais effacer du temps payé."""
    return q("""with paye as (
                  update app.payments set status = 'paid', paid_at = now(),
                         transaction_id = coalesce(%s, transaction_id)
                  where id = %s and status <> 'paid'
                  returning id, org_id, plan, months, amount, currency, email),
                maj as (
                  update app.organizations o set plan = paye.plan, trial_end = null, rappel_envoye = null,
                    paid_until = case when o.plan = paye.plan
                      then greatest(coalesce(o.paid_until, now()), now()) + make_interval(months => paye.months)
                      else now() + make_interval(months => paye.months)
                        + greatest(coalesce(o.paid_until, now()) - now(), interval '0')
                          * (coalesce((select a.amount::float8 / a.months from app.payments a
                                       where a.org_id = o.id and a.plan = o.plan and a.status = 'paid' and a.id <> paye.id
                                       order by a.paid_at desc limit 1),
                                      nullif((select price from app.plans where key = o.plan), 0)::float8,
                                      paye.amount::float8 / paye.months)
                             / (paye.amount::float8 / paye.months))
                      end
                  from paye where o.id = paye.org_id
                  returning o.name, o.paid_until)
                select paye.*, maj.name, maj.paid_until from paye, maj""", (transaction_id, pay_id), one=True)


def _recu(a: dict):
    if not mail_ready():
        return
    fin = a["paid_until"].strftime("%d/%m/%Y")
    label = plans().get(a["plan"], {}).get("label") or a["plan"]
    try:
        send_mail([a["email"]], "Votre abonnement KLDataViz est actif",
                  f"Bonjour,\n\nNous avons bien reçu votre paiement de {a['amount']} {a['currency']} "
                  f"pour le forfait {label} ({a['months']} mois) de l'espace {a['name']}.\n"
                  f"Votre abonnement est actif jusqu'au {fin}.\n\nMerci de votre confiance,\nL'équipe KLDataViz")
    except Exception as e:
        print(f"[paiement] reçu non envoyé : {type(e).__name__}", flush=True)


def _synchroniser(p: dict) -> str:
    """Lit l'état réel chez CinetPay et applique le résultat. Seule voie qui active un forfait."""
    s = paiement.statut(p["merchant_tx"])
    etat = str(s.get("status") or "")
    if etat == "SUCCESS":
        if str(s.get("merchant_transaction_id") or p["merchant_tx"]) != p["merchant_tx"]:
            raise paiement.PaiementError("Réponse de CinetPay pour une autre transaction.")
        a = _activer(p["id"], s.get("transaction_id") or None)
        if a:
            _recu(a)
        return "paid"
    if etat in ECHECS:
        q("update app.payments set status = 'failed' where id = %s and status = 'pending'", (p["id"],))
        return "failed"
    return p["status"]


def notification(body: dict) -> bool:
    """Notification de CinetPay : le jeton l'authentifie, puis le statut est relu chez CinetPay."""
    tx = str(body.get("merchant_transaction_id") or "")[:40]
    jeton = str(body.get("notify_token") or "")
    p = q("""select id::text, merchant_tx, status, notify_token from app.payments where merchant_tx = %s""",
          (tx,), one=True) if tx else None
    if not p or not p["notify_token"] or not hmac.compare_digest(p["notify_token"].encode(), jeton.encode()):
        return False
    if p["status"] != "paid":
        _synchroniser(p)
    return True


def synchroniser_espace(ctx: Ctx) -> dict:
    """Au retour de la page de paiement : relit les paiements en attente, si la notification tarde ou s'est perdue."""
    require(ctx, "owner")
    attente = q("""select id::text, merchant_tx, status from app.payments
                   where org_id = %s and status in ('pending', 'failed') and mode = %s
                     and created_at > now() - make_interval(days => %s)
                   order by created_at desc limit 5""", (ctx.org_id, paiement.mode(), JOURS_RATTRAPAGE))
    resultats = []
    for p in attente:
        try:
            resultats.append(_synchroniser(p))
        except paiement.PaiementError as e:
            print(f"[paiement] vérification impossible : {e}", flush=True)
    # La notification arrive souvent avant le client : le paiement est alors déjà activé.
    recent = q("""select 1 from app.payments where org_id = %s and status = 'paid'
                  and paid_at > now() - interval '30 minutes' limit 1""", (ctx.org_id,), one=True)
    return {"resultats": resultats, "paye": "paid" in resultats or bool(recent)}


def reconcilier_paiements():
    """Rattrape les paiements dont la notification s'est perdue ou est arrivée avant la fin de l'opération."""
    mode = paiement.mode()
    lot = q("""select id::text, merchant_tx, status, created_at < now() - make_interval(days => %s) as ancien
               from app.payments where status = 'pending' and mode = %s and created_at < now() - interval '2 minutes'
               order by checked_at asc nulls first, created_at limit %s""", (JOURS_RELECTURE, mode, LOT_RECONCILIATION))
    fin = time.monotonic() + DUREE_MAX_PASSAGE
    for p in lot:
        if time.monotonic() > fin:
            break
        try:
            etat = _synchroniser(p)
            # Close seulement sur une réponse de CinetPay : une panne ne doit jamais effacer un paiement réel.
            if etat == "pending" and p["ancien"]:
                q("update app.payments set status = 'failed' where id = %s and status = 'pending'", (p["id"],))
                print(f"[paiement] {p['merchant_tx']} clos sans confirmation", flush=True)
        except Exception as e:
            print(f"[paiement] {p['merchant_tx']} non vérifié : {type(e).__name__}", flush=True)
        try:
            q("update app.payments set checked_at = now() where id = %s", (p["id"],))
        except Exception as e:
            print(f"[paiement] suivi non mis à jour : {type(e).__name__}", flush=True)
    q("""update app.payments set status = 'failed' where status = 'pending' and mode <> %s
         and created_at < now() - make_interval(days => %s)""", (mode, JOURS_RELECTURE))


def admin_verifier_paiement(ctx: Ctx, merchant_tx: str) -> dict:
    """Relit un paiement chez CinetPay, quel que soit son état : répare un paiement réel resté sans suite."""
    _admin(ctx)
    p = q("select id::text, merchant_tx, status from app.payments where merchant_tx = %s", (merchant_tx,), one=True)
    if p is None:
        raise ValueError("Paiement introuvable.")
    if p["status"] != "paid":
        try:
            _synchroniser(p)
        except paiement.PaiementError as e:
            raise Unavailable(str(e)) from None
    return q("select status from app.payments where id = %s", (p["id"],), one=True)


def admin_payments(ctx: Ctx) -> list:
    _admin(ctx)
    rows = q("""select p.merchant_tx, p.transaction_id, p.email, p.plan, p.months, p.amount, p.currency, p.mode,
                       p.status, p.created_at, p.paid_at, o.name as org
                from app.payments p join app.organizations o on o.id = p.org_id
                order by p.created_at desc limit 200""")
    return [dict(r, created_at=r["created_at"].isoformat(), paid_at=r["paid_at"].isoformat() if r["paid_at"] else None)
            for r in rows]


def rappels_abonnement():
    """Prévient 7 jours puis 1 jour avant la fin : le mobile money ne se prélève pas tout seul."""
    if not mail_ready():
        return
    with _lock_rappels:
        orgs = q("""update app.organizations set rappel_envoye = current_date
                    where paid_until > now() and paid_until < now() + interval '7 days'
                      and (rappel_envoye is null
                           or (paid_until < now() + interval '1 day' and rappel_envoye < current_date))
                    returning id::text, name, paid_until""")
    for o in orgs:
        dest = q("""select email from app.payments where org_id = %s and status = 'paid'
                    order by paid_at desc limit 1""", (o["id"],), one=True)
        if not dest:
            continue
        try:
            send_mail([dest["email"]], "Votre abonnement KLDataViz arrive à échéance",
                      f"Bonjour,\n\nL'abonnement de l'espace {o['name']} prend fin le "
                      f"{o['paid_until'].strftime('%d/%m/%Y')}.\nPour continuer sans interruption, renouvelez-le "
                      f"dans Réglages, section Abonnement : {APP_URL}/app\n\nVos données restent conservées "
                      "si l'abonnement s'arrête.\n\nL'équipe KLDataViz")
        except Exception as e:
            print(f"[paiement] rappel non envoyé : {type(e).__name__}", flush=True)


def boucle_paiements():
    if paiement.pret() and paiement.mode_test() and not os.environ.get("CINETPAY_API_KEY", "").startswith("sk_test_"):
        print("[paiement] CINETPAY_API_KEY ne commence ni par sk_live_ ni par sk_test_ : paiement fermé aux clients.", flush=True)
    tour = 0
    while True:
        for actif, tache in ((paiement_actif(), reconcilier_paiements), (tour % 12 == 0, rappels_abonnement)):
            try:
                if actif:
                    tache()
            except Exception as e:
                print(f"[paiement] {tache.__name__} : {type(e).__name__}", flush=True)
        tour += 1
        time.sleep(300)
