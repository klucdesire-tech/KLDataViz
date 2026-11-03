# -*- coding: utf-8 -*-
"""
Sources connectées : un tableau de bord reste lié à sa source (lien Google Sheets, lien web,
ou fichier local en mode local) et s'actualise sans réimporter — à la demande ou automatiquement.

Principe (comme l'actualisation de Power BI) :
- à la connexion, l'IA (AGT-11) choisit une seule fois les indicateurs : leur « spec » est mémorisée ;
- à chaque actualisation, la source est relue ; si son contenu n'a pas changé (empreinte), rien n'est
  recalculé ; sinon le tableau de bord est reconstruit avec la MÊME spec, sans appel à l'IA
  (rapide, gratuit, reproductible) ;
- si la structure change (colonnes ajoutées / disparues), on le signale : « Régénérer » relance l'IA.

Stockage : <espace>/storage/sources.json (+ storage/sources/<id>.html), sur le disque de l'espace.
"""
import datetime as dt
import hashlib
import json
import os
import threading
import time
import uuid

import tenancy as T

FREQS = {"off": None, "hourly": dt.timedelta(hours=1), "daily": dt.timedelta(days=1)}
MAX_SOURCES = 50
MAIL_FREQS = ("off", "weekly", "monthly")  # envoi du tableau de bord par e-mail
_lock = threading.Lock()


def _base():
    # KLDV_SOURCES_DIR : dossier imposé (tests) ; sinon le stockage de l'espace courant
    d = os.environ.get("KLDV_SOURCES_DIR") or T.current().dir("storage")
    os.makedirs(d, exist_ok=True)
    return d


def _dir():
    d = os.path.join(_base(), "sources")
    os.makedirs(d, exist_ok=True)
    return d


def _index_path():
    return os.path.join(_base(), "sources.json")


def _load():
    try:
        with open(_index_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(items):
    tmp = _index_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _index_path())


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def connectable(path: str) -> bool:
    """Une source se relit si c'est un lien https (ou http en local), un fichier local, ou une source DHIS2."""
    if path.startswith("dhis2://"):
        return True
    if path.startswith("https://") or (not T.CLOUD and path.startswith("http://")):
        return True
    return not T.CLOUD and os.path.isfile(path)


def fingerprint(df) -> str:
    """Empreinte du contenu (colonnes + valeurs) : identique ⇒ rien n'a changé dans la source."""
    import pandas as pd
    h = hashlib.sha256("|".join(map(str, df.columns)).encode())
    h.update(pd.util.hash_pandas_object(df.astype(str), index=False).values.tobytes())
    return h.hexdigest()


def _public(s):
    out = {k: s.get(k) for k in ("id", "name", "path", "freq", "connected_at", "refreshed_at", "changed_at",
                                 "next_refresh", "status", "rows", "structure_changed", "mail_to", "mail_freq",
                                 "next_mail", "mail_status")}
    out["connected"] = s.get("connected", True)  # anciennes fiches : toutes étaient des sources connectées
    return out


def list_sources():
    return [_public(s) for s in _load()]


def get(source_id: str, with_html=True):
    s = next((x for x in _load() if x["id"] == source_id), None)
    if s is None:
        raise KeyError("Source connectée introuvable.")
    out = _public(s)
    if with_html:
        try:
            with open(os.path.join(_dir(), source_id + ".html"), encoding="utf-8") as f:
                out["html"] = f.read()
        except OSError:
            out["html"] = None
    return out


def connect(path: str, name: str, spec: dict, df, html: str):
    """Enregistre le tableau de bord qui vient d'être généré, dans l'espace (visible des membres).
    Source relisible (lien, DHIS2, fichier local en mode local) → « connectée » : actualisable ;
    fichier importé → tableau enregistré tel quel (partageable, mais pas actualisable)."""
    live = connectable(path)
    with _lock:
        items = _load()
        s = next((x for x in items if x["path"] == path), None)
        if s is None:
            if len(items) >= MAX_SOURCES:  # place : on retire le plus ancien tableau non connecté et non envoyé
                old = next((x for x in reversed(items) if not x.get("connected", True) and x.get("mail_freq", "off") == "off"), None)
                if old is None:
                    return None
                items.remove(old)
                try:
                    os.remove(os.path.join(_dir(), old["id"] + ".html"))
                except OSError:
                    pass
            s = {"id": uuid.uuid4().hex[:10], "path": path, "freq": "off", "connected_at": _now()}
            items.insert(0, s)
        s["connected"] = live
        now = _now()
        s.update(name=name or os.path.basename(path.split("?")[0]) or path, spec=spec, fingerprint=fingerprint(df),
                 columns=[str(c) for c in df.columns], rows=int(len(df)), refreshed_at=now, changed_at=now,
                 status="À jour", structure_changed=False)
        _save(items)
    with open(os.path.join(_dir(), s["id"] + ".html"), "w", encoding="utf-8") as f:
        f.write(html)
    return _public(s)


def set_freq(source_id: str, freq: str):
    if freq not in FREQS:
        raise ValueError("Fréquence inconnue.")
    with _lock:
        items = _load()
        s = next((x for x in items if x["id"] == source_id), None)
        if s is None:
            raise KeyError("Source connectée introuvable.")
        s["freq"] = freq
        s["next_refresh"] = (dt.datetime.now() + FREQS[freq]).isoformat(timespec="seconds") if FREQS[freq] else None
        _save(items)
    return _public(s)


def delete(source_id: str):
    """Supprime un tableau de bord de l'espace. L'identifiant doit exister dans le registre de l'espace :
    il n'est jamais utilisé tel quel pour construire un chemin (pas de « ../ » vers un autre espace)."""
    with _lock:
        items = _load()
        s = next((x for x in items if x["id"] == source_id), None)
        if s is None:
            raise KeyError("Tableau de bord introuvable.")
        _save([x for x in items if x["id"] != s["id"]])
    try:
        os.remove(os.path.join(_dir(), s["id"] + ".html"))
    except OSError:
        pass


def refresh(source_id: str, read_df, build):
    """Relit la source. `read_df(path) -> (df, err)` ; `build(df, spec) -> résultat de generate_dashboard`.
    Renvoie la source à jour, avec `changed` (contenu modifié ?) et `html` si le tableau a été reconstruit."""
    s = next((x for x in _load() if x["id"] == source_id), None)
    if s is None:
        raise KeyError("Source connectée introuvable.")
    if not s.get("connected", True):
        raise ValueError("Ce tableau de bord vient d'un fichier importé : réimportez le fichier pour le mettre à jour.")
    df, err = read_df(s["path"])
    now = dt.datetime.now()
    upd = {"refreshed_at": now.isoformat(timespec="seconds")}
    if s.get("freq") and FREQS.get(s["freq"]):
        upd["next_refresh"] = (now + FREQS[s["freq"]]).isoformat(timespec="seconds")
    if err or df is None or not len(df):
        upd["status"] = f"Lecture impossible : {err or 'source vide'}"[:300]
        _update(source_id, upd)
        return dict(get(source_id, with_html=False), changed=False, error=upd["status"])
    fp = fingerprint(df)
    if fp == s.get("fingerprint"):
        upd["status"] = "À jour (aucun changement dans la source)"
        _update(source_id, upd)
        return dict(get(source_id, with_html=False), changed=False)
    result = build(df, s.get("spec"))
    cols = [str(c) for c in df.columns]
    upd.update(fingerprint=fp, rows=int(len(df)), changed_at=upd["refreshed_at"], columns=cols,
               structure_changed=set(cols) != set(s.get("columns") or cols),
               status="Actualisé : nouvelles données")
    if upd["structure_changed"]:
        upd["status"] = "Actualisé — la structure a changé (colonnes) : cliquez sur Régénérer pour revoir les indicateurs"
    with open(os.path.join(_dir(), source_id + ".html"), "w", encoding="utf-8") as f:
        f.write(result["html"])
    _update(source_id, upd)
    return dict(get(source_id), changed=True)


def remplacer_html(source_id: str, html: str):
    """Nouvelle version du tableau de bord enregistré (ex. correspondance des indicateurs validée)."""
    s = get(source_id, with_html=False)  # l'identifiant doit exister dans cet espace
    with open(os.path.join(_dir(), s["id"] + ".html"), "w", encoding="utf-8") as f:
        f.write(html)
    _update(s["id"], {"status": "Mis à jour : correspondance des indicateurs"})


def _update(source_id, changes):
    with _lock:
        items = _load()
        for x in items:
            if x["id"] == source_id:
                x.update(changes)
        _save(items)


def due(now=None):
    now = (now or dt.datetime.now()).isoformat(timespec="seconds")
    return [s["id"] for s in _load() if s.get("connected", True) and FREQS.get(s.get("freq"))
            and s.get("next_refresh") and s["next_refresh"] <= now]


# ---------------------------------------------------------------- envoi du tableau de bord par e-mail
def _emails(to) -> list:
    """« a@x.org, b@y.org » (texte ou liste) → liste d'adresses valides ; erreur claire sinon."""
    import re
    items = to if isinstance(to, (list, tuple)) else re.split(r"[,;\s]+", str(to or ""))
    out = [str(x).strip() for x in items if str(x).strip()]
    bad = [x for x in out if not re.match(r"^[^@\s]{1,64}@[^@\s.]{1,63}(?:\.[^@\s.]{1,63})+$", x)]
    if bad:
        raise ValueError("Adresse e-mail invalide : " + ", ".join(bad))
    return out


def set_mail(source_id: str, to, freq: str, hour: int = 7, base_url=None):
    import studio
    if freq not in MAIL_FREQS:
        raise ValueError("Fréquence d'envoi inconnue.")
    to = _emails(to)
    if freq != "off" and not to:
        raise ValueError("Indiquez au moins un destinataire.")
    if len(to) > 20:
        raise ValueError("20 destinataires au maximum.")
    upd = {"mail_to": to, "mail_freq": freq,
           "next_mail": studio.next_run(freq, hour).isoformat(timespec="seconds") if freq != "off" else None}
    if base_url:
        upd["public_base"] = base_url  # adresse du service : le lien de partage des envois planifiés
    if not next((x for x in _load() if x["id"] == source_id), None):
        raise KeyError("Tableau de bord introuvable.")
    _update(source_id, upd)
    return get(source_id, with_html=False)


LINK_MIN_DAYS = 7  # un lien envoyé par e-mail reste valable au moins une semaine (sinon on en crée un neuf)


def share_link(source_id: str, base_url):
    """Lien de partage à joindre à un e-mail : le lien actif le plus durable, ou un lien neuf de 30 jours.
    (lien, date d'expiration) ; (None, None) sans adresse publique du service (console locale)."""
    import shares
    base = (os.environ.get("KLDV_PUBLIC_URL") or base_url or "").rstrip("/")
    if not base:
        return None, None
    limite = (dt.datetime.now() + dt.timedelta(days=LINK_MIN_DAYS)).isoformat()
    actifs = sorted(shares.list_for(source_id), key=lambda x: x["expires_at"])
    lien = actifs[-1] if actifs and actifs[-1]["expires_at"] > limite else shares.create(source_id, 30)
    return f"{base}/t/{lien['token']}", lien["expires_at"]


def send_mail_now(source_id: str, read_df, build, to=None, base_url=None):
    """Envoie le tableau de bord (fichier HTML interactif en pièce jointe), actualisé d'abord s'il est connecté,
    avec le lien de partage : la version en ligne, toujours à jour, actualisable d'un clic."""
    import re
    import studio
    s = next((x for x in _load() if x["id"] == source_id), None)
    if s is None:
        raise KeyError("Tableau de bord introuvable.")
    dest = _emails(to) if to else list(s.get("mail_to") or [])  # texte du champ → liste d'adresses
    if not dest:
        raise ValueError("Aucun destinataire.")
    if len(dest) > 20:
        raise ValueError("20 destinataires au maximum.")
    if s.get("connected", True):
        try:
            refresh(source_id, read_df, build)
        except Exception:
            pass  # on envoie la dernière version disponible
        s = next((x for x in _load() if x["id"] == source_id), s)  # date de lecture à jour dans le message
    if base_url and base_url != s.get("public_base"):
        _update(source_id, {"public_base": base_url})
    try:
        lien, expire = share_link(source_id, base_url or s.get("public_base"))
    except ValueError:  # 10 liens actifs au maximum : on envoie sans lien plutôt que de bloquer l'envoi
        lien, expire = None, None
    en_ligne = ""
    if lien:
        jusqu = dt.datetime.fromisoformat(expire).strftime("%d/%m/%Y")
        en_ligne = (f"Version en ligne, toujours à jour (sans compte, valable jusqu'au {jusqu}) :\n{lien}\n"
                    + ("Le bouton « Actualiser » en haut de la page relit les données à la source.\n\n"
                       if s.get("connected", True) else "\n"))
    name = re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(str(s.get("name") or "tableau")))[0])[:40] or "tableau"
    att = os.path.join(_dir(), f"tableau_de_bord_{name}.html")
    with open(os.path.join(_dir(), source_id + ".html"), encoding="utf-8") as f, open(att, "w", encoding="utf-8") as g:
        g.write(f.read())
    try:
        studio.send_mail(dest, f"Tableau de bord KLDataViz : {s.get('name')}",
                         f"Bonjour,\n\nVoici le tableau de bord « {s.get('name')} » (données lues le "
                         f"{str(s.get('refreshed_at') or '')[:16].replace('T', ' à ')}).\n\n"
                         + en_ligne +
                         ("En pièce jointe, la même version à ouvrir dans votre navigateur" if lien else
                          "Ouvrez la pièce jointe dans votre navigateur")
                         + " : le tableau est interactif (filtres, périodes), même sans connexion.\n\nKLDataViz"
                         + studio.pied_envoi(), att)
        status = (f"Envoyé à {len(dest)} destinataire(s) le {_now()[:16].replace('T', ' à ')}"
                  + (" avec le lien de partage" if lien else ""))
    except Exception as exc:
        status = f"Envoi impossible : {exc}"[:300]
    finally:
        try:
            os.remove(att)
        except OSError:
            pass
    upd = {"mail_status": status}
    if s.get("mail_freq", "off") != "off":
        upd["next_mail"] = studio.next_run(s["mail_freq"], 7, dt.datetime.now()).isoformat(timespec="seconds")
    _update(source_id, upd)
    if status.startswith("Envoi impossible"):
        raise ValueError(status)
    return get(source_id, with_html=False)


def envois_actifs():
    """Tableaux de bord envoyés automatiquement par e-mail, dans l'espace courant."""
    return [{k: x.get(k) for k in ("id", "name", "mail_to", "mail_freq", "next_mail", "mail_status")}
            for x in _load() if x.get("mail_freq", "off") != "off"]


def arreter_envoi(source_id: str):
    """Arrête l'envoi automatique et garde les destinataires, pour le réactiver sans les ressaisir."""
    if not next((x for x in _load() if x["id"] == source_id), None):
        raise KeyError("Tableau de bord introuvable.")
    _update(source_id, {"mail_freq": "off", "next_mail": None})


def envois_tous(org_ctx):
    """Envois automatiques de tableaux de bord de tous les espaces, pour l'administration."""
    out = []
    for org_id in _espaces():
        try:
            ctx = org_ctx(org_id)
            with T.use(ctx):
                out += [dict(e, org_id=org_id, espace=ctx.plan.get("org_name"), espace_actif=ctx.plan.get("key") != "suspendu")
                        for e in envois_actifs()]
        except Exception:  # dossier d'un espace supprimé, fichier abîmé : il ne cache pas les autres
            continue
    return out


def _espaces():
    base = os.path.join(T.DATA_DIR, "tenants")
    return [o for o in (os.listdir(base) if os.path.isdir(base) else [])
            if os.path.isfile(os.path.join(base, o, "storage", "sources.json"))]


def due_mails(now=None):
    now = (now or dt.datetime.now()).isoformat(timespec="seconds")
    return [s["id"] for s in _load() if s.get("mail_freq", "off") != "off" and s.get("next_mail") and s["next_mail"] <= now]


def start_auto_refresh(read_df, build, org_ctx=None):
    """Actualisation automatique (toutes les heures / chaque jour) : une boucle de fond, vérification chaque minute.
    En ligne : chaque espace est parcouru dans son propre contexte (ses fichiers, son registre)."""
    def tick():
        if not T.CLOUD:
            for sid in due():
                refresh(sid, read_df, build)
            for sid in due_mails():
                try:
                    send_mail_now(sid, read_df, build)
                except Exception:
                    pass
            return
        for org_id in _espaces():
            try:
                ctx = org_ctx(org_id)
            except Exception:  # espace supprimé : il ne bloque pas les suivants
                continue
            if ctx.plan.get("key") == "suspendu":  # essai ou abonnement terminé : plus d'actualisation ni d'envoi
                continue
            with T.use(ctx):
                for sid in due():
                    try:
                        refresh(sid, read_df, build)
                    except Exception as exc:  # une source en échec ne bloque pas les autres
                        _update(sid, {"status": f"Erreur : {exc}"[:300]})
                for sid in due_mails():
                    try:
                        send_mail_now(sid, read_df, build)
                    except Exception:
                        pass  # statut déjà enregistré par send_mail_now

    def loop():
        import surveillance
        while True:
            surveillance.battement("tableaux")
            try:
                tick()
            except Exception as exc:
                surveillance.erreur_interne("", "boucle des tableaux de bord", "", exc, genre="tache")
            time.sleep(60)
    threading.Thread(target=loop, daemon=True).start()
