# -*- coding: utf-8 -*-
"""
KLDataViz Studio — services de la console : réglages, rapports planifiés,
envoi par e-mail et points clés rédigés par l'IA.

En local, réglages et planifications sont stockés dans storage/ (ignoré par Git).
En ligne (KLDV_MODE=cloud), ils sont stockés par organisation dans la base (cloud.py)
et les e-mails partent par le service d'envoi de la plateforme.
"""
import datetime as dt
import json
import os
import re
import smtplib
import threading
import time
import uuid
from email.message import EmailMessage

import tenancy as T

if T.CLOUD:
    import cloud

BASE = os.path.dirname(os.path.abspath(__file__))
STORAGE = os.path.join(BASE, "storage")
SETTINGS_PATH = os.path.join(STORAGE, "settings.json")
SCHEDULES_PATH = os.path.join(STORAGE, "schedules.json")
_lock = threading.Lock()

DEFAULT_SETTINGS = {
    "name": "",
    "org": "Équipe Data",
    "langue": "Français",
    "detail": "simple",
    "smtp": {"host": "", "port": 587, "user": "", "password": "", "sender": "", "tls": True},
}


# ---------------------------------------------------------------- réglages
def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_settings():
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    if T.CLOUD:
        ctx = T.current()
        s.update(cloud.get_settings(ctx.org_id))
        s["org"] = ctx.plan.get("org_name") or ""
        s["name"] = ctx.email or ""
        return s
    saved = _read_json(SETTINGS_PATH, {})
    for k, v in saved.items():
        if k == "smtp" and isinstance(v, dict):
            s["smtp"].update(v)
        elif k in s:
            s[k] = v
    return s


def public_settings():
    """Réglages renvoyés au navigateur : le mot de passe SMTP n'est jamais exposé."""
    s = load_settings()
    if T.CLOUD:
        s.pop("smtp", None)
        s["smtp_ready"] = cloud.mail_ready()
        s["cloud"] = True
        return s
    smtp = dict(s["smtp"])
    smtp["has_password"] = bool(smtp.pop("password", ""))
    s["smtp"] = smtp
    s["smtp_ready"] = smtp_ready(load_settings())
    return s


def save_settings(payload):
    if T.CLOUD:
        langue = payload.get("langue") if payload.get("langue") in ("Français", "English") else "Français"
        detail = payload.get("detail") if payload.get("detail") in ("simple", "detaille") else "simple"
        cloud.save_settings(T.current(), langue, detail, payload.get("org"))
        if payload.get("org") and T.current().role == "owner":
            T.current().plan["org_name"] = str(payload["org"]).strip()[:80]
        return public_settings()
    with _lock:
        s = load_settings()
        for k in ("name", "org", "langue", "detail"):
            if k in payload:
                s[k] = str(payload[k] or "").strip()[:120]
        smtp = payload.get("smtp")
        if isinstance(smtp, dict):
            for k in ("host", "user", "sender"):
                if k in smtp:
                    s["smtp"][k] = str(smtp[k] or "").strip()
            if "port" in smtp:
                try:
                    s["smtp"]["port"] = int(smtp["port"])
                except (TypeError, ValueError):
                    pass
            if "tls" in smtp:
                s["smtp"]["tls"] = bool(smtp["tls"])
            if smtp.get("password"):  # vide = conserver le mot de passe existant
                s["smtp"]["password"] = str(smtp["password"])
        _write_json(SETTINGS_PATH, s)
    return public_settings()


def chat_preamble():
    """Consignes de langue et de niveau de détail ajoutées au prompt du chat."""
    s = load_settings()
    lang = "anglais" if s.get("langue") == "English" else "français"
    detail = ("détaillé, avec la méthode et les chiffres utiles pour un analyste"
              if s.get("detail") == "detaille" else "simple et direct, compréhensible par un décideur non technique")
    return f"Réponds en {lang}. Style : {detail}.\n"


# ---------------------------------------------------------------- e-mail
def smtp_ready(s):
    if T.CLOUD:
        return cloud.mail_ready()
    m = s["smtp"]
    return bool(m.get("host") and (m.get("sender") or m.get("user")))


def send_mail(recipients, subject, body, attachment=None):
    if T.CLOUD:
        return cloud.send_mail(recipients, subject, body, attachment)
    s = load_settings()
    if not smtp_ready(s):
        raise RuntimeError("Envoi par e-mail non configuré (Réglages → E-mail d'envoi).")
    m = s["smtp"]
    msg = EmailMessage()
    msg["From"] = m.get("sender") or m.get("user")
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment and os.path.exists(attachment):
        with open(attachment, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="octet-stream",
                               filename=os.path.basename(attachment))
    port = int(m.get("port") or 587)
    if port == 465:
        server = smtplib.SMTP_SSL(m["host"], port, timeout=30)
    else:
        server = smtplib.SMTP(m["host"], port, timeout=30)
        if m.get("tls", True):
            server.starttls()
    try:
        if m.get("user"):
            server.login(m["user"], m.get("password", ""))
        server.send_message(msg)
    finally:
        server.quit()


# ---------------------------------------------------------------- planification
FREQS = {"daily": "Chaque jour", "weekly": "Chaque lundi", "monthly": "Le 1er du mois"}


def next_run(freq, hour, after=None):
    after = after or dt.datetime.now()
    hour = max(0, min(23, int(hour)))
    cand = after.replace(hour=hour, minute=0, second=0, microsecond=0)
    if freq == "daily":
        return cand if cand > after else cand + dt.timedelta(days=1)
    if freq == "weekly":
        days = (7 - cand.weekday()) % 7
        cand = cand + dt.timedelta(days=days)
        return cand if cand > after else cand + dt.timedelta(days=7)
    cand = cand.replace(day=1)  # monthly
    if cand > after:
        return cand
    y, mth = (cand.year + 1, 1) if cand.month == 12 else (cand.year, cand.month + 1)
    return cand.replace(year=y, month=mth)


def list_schedules():
    if T.CLOUD:
        return cloud.schedules(T.current().org_id)
    return _read_json(SCHEDULES_PATH, [])


def _save_schedules(items):
    _write_json(SCHEDULES_PATH, items)


def create_schedule(payload):
    source = str(payload.get("source") or "").strip()
    if not source:
        raise ValueError("Indiquez la source à réanalyser.")
    freq = payload.get("freq") if payload.get("freq") in FREQS else "monthly"
    fmt = payload.get("format") if payload.get("format") in ("pdf", "excel", "html") else "pdf"
    hour = int(payload.get("hour") or 7)
    to = [x.strip() for x in re.split(r"[,;\s]+", str(payload.get("to") or "")) if x.strip()]
    bad = [x for x in to if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", x)]
    if bad:
        raise ValueError("Adresse e-mail invalide : " + ", ".join(bad))
    job = {"id": uuid.uuid4().hex[:10], "source": source,
           "name": str(payload.get("name") or os.path.basename(source.split("?")[0]) or source),
           "freq": freq, "hour": hour, "format": fmt, "to": to, "on": True,
           "created": dt.datetime.now().isoformat(timespec="seconds"),
           "last_run": None, "last_status": None, "last_file": None,
           "next_run": next_run(freq, hour).isoformat(timespec="seconds")}
    if T.CLOUD:
        ctx = T.current()
        if not (source.startswith("https://") or ctx.owns(source)):
            raise ValueError("Source non autorisée.")
        if len(cloud.schedules(ctx.org_id)) >= 20:
            raise ValueError("20 planifications au maximum par espace.")
        cloud.schedule_insert(ctx.org_id, dict(job, created=dt.datetime.fromisoformat(job["created"]),
                                               next_run=dt.datetime.fromisoformat(job["next_run"])))
        return job
    with _lock:
        items = list_schedules()
        items.insert(0, job)
        _save_schedules(items)
    return job


def update_schedule(job_id, **changes):
    if T.CLOUD:
        org = T.current().org_id
        job = cloud.schedule_get(org, job_id)
        if job is None:
            raise KeyError("Planification introuvable.")
        if changes.get("on"):
            changes["next_run"] = next_run(job["freq"], job["hour"]).isoformat(timespec="seconds")
        for k in ("last_run", "next_run"):
            if isinstance(changes.get(k), str):
                changes[k] = dt.datetime.fromisoformat(changes[k])
        return cloud.schedule_update(org, job_id, **changes)
    with _lock:
        items = list_schedules()
        for j in items:
            if j["id"] == job_id:
                if "on" in changes:
                    j["on"] = bool(changes["on"])
                    if j["on"]:
                        j["next_run"] = next_run(j["freq"], j["hour"]).isoformat(timespec="seconds")
                j.update({k: v for k, v in changes.items() if k != "on"})
                _save_schedules(items)
                return j
    raise KeyError("Planification introuvable.")


def delete_schedule(job_id):
    if T.CLOUD:
        return cloud.schedule_delete(T.current().org_id, job_id)
    with _lock:
        items = [j for j in list_schedules() if j["id"] != job_id]
        _save_schedules(items)


def run_schedule(job_id, analyze_fn, exporters, exports_dir, commentary_fn=None):
    job = next((j for j in list_schedules() if j["id"] == job_id), None)
    if job is None:
        raise KeyError("Planification introuvable.")
    now = dt.datetime.now()
    status, fn = "OK", None
    result = analyze_fn(job["source"])
    if "error" in result:
        status = "Erreur : " + str(result["error"])
    else:
        # Ce qui a changé depuis l'envoi précédent (Hermes, ou règles sans IA) : en tête du rapport et dans l'e-mail
        ev = None
        if commentary_fn is not None:
            try:
                ev = commentary_fn(job, result)
            except Exception:
                ev = None
        if ev:
            result = dict(result, _evolution=ev)
        fn = exporters[job["format"]](result)
        if job.get("to"):
            try:
                import hermes_report
                send_mail(job["to"], f"Rapport KLDataViz — {job['name']} — {now:%d/%m/%Y}",
                          hermes_report.email_text(job["name"], result, ev),
                          os.path.join(exports_dir, fn))
                status = f"Envoyé à {len(job['to'])} destinataire(s)"
            except Exception as exc:
                status = f"Rapport créé, e-mail non envoyé : {exc}"
        else:
            status = "Rapport créé"
    return update_schedule(job_id, last_run=now.isoformat(timespec="seconds"), last_status=status, last_file=fn,
                           next_run=next_run(job["freq"], job["hour"], now).isoformat(timespec="seconds"))


def start_scheduler(analyze_fn, exporters, exports_dir, commentary_fn=None):
    if T.CLOUD:
        def cloud_loop():
            while True:
                try:
                    for org_id, j in cloud.due_schedules():
                        with T.use(cloud.org_ctx(org_id)):
                            try:
                                run_schedule(j["id"], analyze_fn, exporters, T.exports_dir(), commentary_fn)
                            except Exception as exc:
                                update_schedule(j["id"], last_status=f"Erreur : {exc}"[:300],
                                                next_run=next_run(j["freq"], j["hour"]).isoformat(timespec="seconds"))
                except Exception:
                    pass
                time.sleep(30)
        threading.Thread(target=cloud_loop, daemon=True).start()
        return

    def loop():
        while True:
            try:
                now = dt.datetime.now().isoformat(timespec="seconds")
                for j in list_schedules():
                    if j.get("on") and j.get("next_run") and j["next_run"] <= now:
                        try:
                            run_schedule(j["id"], analyze_fn, exporters, exports_dir, commentary_fn)
                        except Exception as exc:
                            update_schedule(j["id"], last_status=f"Erreur : {exc}",
                                            next_run=next_run(j["freq"], j["hour"]).isoformat(timespec="seconds"))
            except Exception:
                pass
            time.sleep(30)
    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------- points clés (IA)
def _summary(r):
    bv = r.get("bivariate") or {}
    groupes = []
    for g in (bv.get("groupes") or [])[:6]:
        pairs = sorted(zip(g.get("labels", []), g.get("moyennes", [])), key=lambda x: -(x[1] or 0))
        groupes.append({"variable": g.get("num"), "par": g.get("cat"),
                        "plus_hauts": pairs[:3], "plus_bas": pairs[-3:], "n_groupes": len(pairs)})
    return {
        "fichier": r.get("file"), "lignes": r.get("rows"), "colonnes": r.get("columns"),
        "completude_pct": r.get("completeness_pct"),
        "variables": [{k: c[k] for k in ("name", "dtype", "missing_pct", "unique")} for c in r.get("columns_detail", [])][:40],
        "stats_numeriques": {k: {x: v[x] for x in ("mean", "min", "median", "max", "outliers")}
                             for k, v in list((r.get("numeric_stats") or {}).items())[:20]},
        "correlations": (bv.get("correlations") or r.get("correlations") or [])[:6],
        "anomalies": (r.get("anomalies") or [])[:8],
        "moyennes_par_groupe": groupes,
        "categories_frequentes": dict(list((r.get("categorical_top") or {}).items())[:8]),
    }


def insights(result, llm_call):
    """3 points clés en phrases, à partir des seuls chiffres de l'analyse. None si l'IA est indisponible."""
    if not result:
        return None
    s = load_settings()
    lang = "anglais" if s.get("langue") == "English" else "français"
    system = (
        f"Tu es Hermes, analyste de données. Écris en {lang}, phrases courtes, pour un décideur non technique.\n"
        "À partir du résumé statistique fourni, rédige les 3 constats les plus utiles (tendance, écart entre groupes, "
        "anomalie, relation, problème de qualité). Utilise UNIQUEMENT des chiffres présents dans le résumé, arrondis "
        "simplement. N'invente aucune cause : si une explication est une hypothèse, dis-le.\n"
        "Réponds avec un objet JSON valide et rien d'autre :\n"
        '{"insights":[{"tag":"Tendance|Relation|Anomalie|À surveiller|Qualité","severity":"ok|warn|bad",'
        '"text":"constat en une phrase, chiffres clés entre **","detail":"précision ou méthode en une phrase",'
        '"question":"question de suivi à poser à l\'assistant"}]}'
    )
    content = llm_call([{"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(_summary(result), ensure_ascii=False, default=str)}],
                       max_tokens=8000)  # le modèle raisonne avant de répondre : budget large
    if not content:
        return None
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", content.strip())
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None
    out = []
    for it in (data.get("insights") or [])[:3]:
        if isinstance(it, dict) and it.get("text"):
            out.append({"tag": str(it.get("tag") or "Constat")[:30],
                        "severity": it.get("severity") if it.get("severity") in ("ok", "warn", "bad") else "warn",
                        "text": str(it["text"])[:400], "detail": str(it.get("detail") or "")[:300],
                        "question": str(it.get("question") or "")[:200]})
    return out or None
