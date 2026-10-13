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
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

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
    try:
        return psycopg.connect(DATABASE_URL, autocommit=True, prepare_threshold=None, connect_timeout=10)
    except psycopg.OperationalError as exc:
        import sys
        sys.stderr.write("[base] connexion impossible : " + str(exc).splitlines()[0][:200] + "\n")
        raise Unavailable("La base de données est momentanément injoignable. Réessayez dans quelques minutes.")


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


def resolve(user: dict) -> Ctx | None:
    """Contexte de l'utilisateur : son organisation et son rôle. Accepte une invitation en attente."""
    m = q("""select m.org_id::text, m.role, o.plan, o.trial_end, o.name, o.suspended
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
    expired = catalog.get(plan_key, {}).get("days") and m["trial_end"] and m["trial_end"] < dt.datetime.now(dt.timezone.utc)
    if m["suspended"] or expired:
        plan_key = "suspendu"
    plan = dict(catalog.get(plan_key, _FALLBACK_PLANS["suspendu"]), key=plan_key, trial_end=m["trial_end"],
                org_name=m["name"], expired=bool(expired), suspended=bool(m["suspended"]))
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
                                "Votre période d'essai est terminée. Contactez-nous pour activer un abonnement.")
        raise QuotaExceeded(f"Quota mensuel atteint : {limit} {what} pour le plan {ctx.plan.get('label')}. "
                            "Contactez-nous pour passer au plan supérieur.")
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
                     "trial_end": p["trial_end"].isoformat() if p.get("trial_end") else None},
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
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
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
        sent = True
    except Exception:
        sent = False
    return {"ok": True, "email_sent": sent, "link": link}


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
        q("update app.organizations set plan = %s, trial_end = now() + make_interval(days => %s) where id = %s",
          (plan, int(days), org_id))
    else:
        q("update app.organizations set plan = %s where id = %s", (plan, org_id))


def admin_extend_trial(ctx: Ctx, org_id: str, days: int):
    _admin(ctx)
    days = int(days)
    if not 1 <= days <= 365:
        raise ValueError("Entre 1 et 365 jours.")
    q("""update app.organizations set trial_end = greatest(coalesce(trial_end, now()), now()) + make_interval(days => %s)
         where id = %s""", (days, org_id))


def admin_suspend(ctx: Ctx, org_id: str, suspended: bool):
    _admin(ctx)
    q("update app.organizations set suspended = %s where id = %s", (bool(suspended), org_id))


def admin_invite_client(ctx: Ctx, org_name: str, email: str, plan: str) -> dict:
    """Crée l'espace d'un client et invite son propriétaire."""
    _admin(ctx)
    org_name, email = (org_name or "").strip()[:80], (email or "").strip().lower()
    if len(org_name) < 2:
        raise ValueError("Indiquez le nom de l'organisation du client.")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("Adresse e-mail invalide.")
    catalog = plans()
    if plan not in catalog:
        raise ValueError("Forfait inconnu.")
    if q("select 1 from app.memberships m join auth.users u on u.id = m.user_id where lower(u.email) = %s",
         (email,), one=True):
        raise ValueError("Cette personne fait déjà partie d'un espace.")
    days = catalog[plan].get("days")
    org = q("""insert into app.organizations (name, plan, trial_end) values (%s, %s,
               case when %s::int is null then null else now() + make_interval(days => %s::int) end) returning id::text""",
            (org_name, plan, days, days), one=True)
    q("""insert into app.invitations (org_id, email, role, token, invited_by, expires_at)
         values (%s, %s, 'owner', %s, %s, now() + interval '30 days')""",
      (org["id"], email, secrets.token_urlsafe(24), ctx.user_id))
    link = f"{APP_URL}/login?invite=1&email={urllib.request.quote(email)}"
    try:
        send_mail([email], f"Votre espace KLDataViz « {org_name} » est prêt",
                  f"Bonjour,\n\nVotre espace « {org_name} » a été créé sur KLDataViz (forfait {catalog[plan]['label']}).\n\n"
                  f"Créez votre compte avec cette adresse e-mail ({email}) pour y accéder :\n{link}\n\n"
                  "L'invitation est valable 30 jours.")
        sent = True
    except Exception:
        sent = False
    return {"ok": True, "org_id": org["id"], "email_sent": sent, "link": link}


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
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
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


def site_settings() -> dict:
    def load():
        try:
            return {r["key"]: r["value"] for r in q("select key, value from app.site_settings")}
        except Exception:
            return {}
    return _cached("site", 30, load)


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
    """Planifications à exécuter, toutes organisations confondues (tâche de fond)."""
    rows = q(f"""select s.org_id::text as org_id, {', '.join('s.' + c for c in SCHEDULE_COLS.split(', '))}
                 from app.schedules s join app.organizations o on o.id = s.org_id
                 where s.enabled and s.next_run <= now() and o.plan <> 'suspendu' and not o.suspended""")
    return [(r["org_id"], _sched_out(r)) for r in rows]


def org_ctx(org_id: str) -> Ctx:
    """Contexte technique d'une organisation (tâches de fond : rapports planifiés)."""
    o = q("select name, plan, trial_end from app.organizations where id = %s", (org_id,), one=True)
    catalog = plans()
    plan = dict(catalog.get(o["plan"], _FALLBACK_PLANS["essai"]), key=o["plan"], org_name=o["name"], trial_end=o["trial_end"])
    return Ctx(key=f"job:{org_id}", org_id=org_id, role="analyst", plan=plan)


# ---------------------------------------------------------------- e-mail (Brevo)
def mail_ready() -> bool:
    return bool(BREVO_API_KEY and MAIL_FROM)


def send_mail(recipients, subject, body, attachment=None):
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
        raise RuntimeError(f"Envoi refusé par Brevo ({exc.code}).")
