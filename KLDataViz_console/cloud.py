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

from tenancy import AuthError, Ctx, Forbidden, QuotaExceeded  # noqa: F401 (réexportées)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "")
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "KLDataViz")
APP_URL = os.environ.get("APP_URL", "").rstrip("/")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}

PLANS = {
    "essai": {"label": "Essai", "analyses": 10, "ai": 50, "members": 1, "days": 14},
    "pro": {"label": "Pro", "analyses": 100, "ai": 1000, "members": 5},
    "equipe": {"label": "Équipe", "analyses": 1000, "ai": 10000, "members": 25},
    "suspendu": {"label": "Suspendu", "analyses": 0, "ai": 0, "members": 0},
}
ROLES = ("owner", "analyst", "viewer")


# ---------------------------------------------------------------- base de données
def db():
    import psycopg
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL manquant")
    return psycopg.connect(DATABASE_URL, autocommit=True, prepare_threshold=None, connect_timeout=10)


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
    except urllib.error.HTTPError:
        raise AuthError("Session expirée. Reconnectez-vous.")
    except Exception:
        raise AuthError("Service de connexion injoignable. Réessayez.")
    user = {"id": u["id"], "email": (u.get("email") or "").lower(),
            "name": (u.get("user_metadata") or {}).get("full_name") or ""}
    with _cache_lock:
        if len(_token_cache) > 5000:
            _token_cache.clear()
        _token_cache[h] = (now + 60, user)
    return user


def resolve(user: dict) -> Ctx | None:
    """Contexte de l'utilisateur : son organisation et son rôle. Accepte une invitation en attente."""
    m = q("""select m.org_id::text, m.role, o.plan, o.trial_end, o.name
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
    plan_key = m["plan"]
    if plan_key == "essai" and m["trial_end"] and m["trial_end"] < dt.datetime.now(dt.timezone.utc):
        plan_key = "suspendu"
    plan = dict(PLANS.get(plan_key, PLANS["essai"]), key=plan_key, trial_end=m["trial_end"], org_name=m["name"])
    return Ctx(key=user["id"], org_id=m["org_id"], user_id=user["id"], email=user["email"], role=m["role"],
               plan=plan, is_admin=user["email"] in ADMIN_EMAILS)


def create_org(user: dict, name: str) -> None:
    name = (name or "").strip()[:80]
    if len(name) < 2:
        raise ValueError("Indiquez le nom de votre organisation.")
    if q("select 1 from app.memberships where user_id = %s", (user["id"],), one=True):
        raise ValueError("Vous faites déjà partie d'une organisation.")
    org = q("""insert into app.organizations (name, plan, trial_end, created_by)
               values (%s, 'essai', now() + interval '14 days', %s) returning id::text""",
            (name, user["id"]), one=True)
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
            raise QuotaExceeded("Votre période d'essai est terminée. Contactez-nous pour activer un abonnement.")
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


# ---------------------------------------------------------------- administration (toi)
def admin_orgs(ctx: Ctx) -> list:
    if not ctx.is_admin:
        raise Forbidden("Réservé à l'administrateur.")
    month = dt.date.today().strftime("%Y-%m")
    rows = q("""select o.id::text, o.name, o.plan, o.trial_end, o.created_at,
                       (select count(*) from app.memberships m where m.org_id = o.id) as members,
                       (select u.email from auth.users u where u.id = o.created_by) as owner_email,
                       coalesce(um.analyses, 0) as analyses, coalesce(um.ai_calls, 0) as ai
                from app.organizations o
                left join app.usage_monthly um on um.org_id = o.id and um.month = %s
                order by o.created_at desc""", (month,))
    for r in rows:
        for k in ("trial_end", "created_at"):
            r[k] = r[k].isoformat() if r[k] else None
    return rows


def admin_set_plan(ctx: Ctx, org_id: str, plan: str, trial_days: int | None = None):
    if not ctx.is_admin:
        raise Forbidden("Réservé à l'administrateur.")
    if plan not in PLANS:
        raise ValueError("Plan inconnu.")
    if trial_days:
        q("update app.organizations set plan = %s, trial_end = now() + make_interval(days => %s) where id = %s",
          (plan, int(trial_days), org_id))
    else:
        q("update app.organizations set plan = %s where id = %s", (plan, org_id))


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
                 where s.enabled and s.next_run <= now() and o.plan <> 'suspendu'""")
    return [(r["org_id"], _sched_out(r)) for r in rows]


def org_ctx(org_id: str) -> Ctx:
    """Contexte technique d'une organisation (tâches de fond : rapports planifiés)."""
    o = q("select name, plan, trial_end from app.organizations where id = %s", (org_id,), one=True)
    plan = dict(PLANS.get(o["plan"], PLANS["essai"]), key=o["plan"], org_name=o["name"], trial_end=o["trial_end"])
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
