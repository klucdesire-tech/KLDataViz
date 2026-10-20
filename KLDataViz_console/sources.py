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
    with _lock:
        _save([x for x in _load() if x["id"] != source_id])
    try:
        os.remove(os.path.join(_dir(), source_id + ".html"))
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
def set_mail(source_id: str, to, freq: str, hour: int = 7):
    import re
    import studio
    if freq not in MAIL_FREQS:
        raise ValueError("Fréquence d'envoi inconnue.")
    to = [x.strip() for x in re.split(r"[,;\s]+", str(to or "")) if x.strip()]
    bad = [x for x in to if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", x)]
    if bad:
        raise ValueError("Adresse e-mail invalide : " + ", ".join(bad))
    if freq != "off" and not to:
        raise ValueError("Indiquez au moins un destinataire.")
    if len(to) > 20:
        raise ValueError("20 destinataires au maximum.")
    upd = {"mail_to": to, "mail_freq": freq,
           "next_mail": studio.next_run(freq, hour).isoformat(timespec="seconds") if freq != "off" else None}
    if not next((x for x in _load() if x["id"] == source_id), None):
        raise KeyError("Tableau de bord introuvable.")
    _update(source_id, upd)
    return get(source_id, with_html=False)


def send_mail_now(source_id: str, read_df, build, to=None):
    """Envoie le tableau de bord (fichier HTML interactif en pièce jointe), actualisé d'abord s'il est connecté."""
    import re
    import studio
    s = next((x for x in _load() if x["id"] == source_id), None)
    if s is None:
        raise KeyError("Tableau de bord introuvable.")
    dest = to or s.get("mail_to") or []
    if not dest:
        raise ValueError("Aucun destinataire.")
    if s.get("connected", True):
        try:
            refresh(source_id, read_df, build)
        except Exception:
            pass  # on envoie la dernière version disponible
    name = re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(str(s.get("name") or "tableau")))[0])[:40] or "tableau"
    att = os.path.join(_dir(), f"tableau_de_bord_{name}.html")
    with open(os.path.join(_dir(), source_id + ".html"), encoding="utf-8") as f, open(att, "w", encoding="utf-8") as g:
        g.write(f.read())
    try:
        studio.send_mail(dest, f"Tableau de bord KLDataViz : {s.get('name')}",
                         f"Bonjour,\n\nVoici le tableau de bord « {s.get('name')} » (données lues le "
                         f"{str(s.get('refreshed_at') or '')[:16].replace('T', ' à ')}).\n\n"
                         "Ouvrez la pièce jointe dans votre navigateur : le tableau est interactif (filtres, périodes), "
                         "même sans connexion.\n\nKLDataViz", att)
        status = f"Envoyé à {len(dest)} destinataire(s) le {_now()[:16].replace('T', ' à ')}"
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
        base = os.path.join(T.DATA_DIR, "tenants")
        for org_id in (os.listdir(base) if os.path.isdir(base) else []):
            if not os.path.isfile(os.path.join(base, org_id, "storage", "sources.json")):
                continue
            with T.use(org_ctx(org_id)):
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
        while True:
            try:
                tick()
            except Exception:
                pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True).start()
