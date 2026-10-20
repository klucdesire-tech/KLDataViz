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
MAX_SOURCES = 20
_lock = threading.Lock()


def _base():
    # KLDV_SOURCES_DIR : dossier imposé (tests) ; sinon le stockage de l'espace courant
    return os.environ.get("KLDV_SOURCES_DIR") or T.current().dir("storage")


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
    """Une source se relit si c'est un lien https (ou http en local), ou un fichier local en mode local."""
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
    return {k: s.get(k) for k in ("id", "name", "path", "freq", "connected_at", "refreshed_at", "changed_at",
                                  "next_refresh", "status", "rows", "structure_changed")}


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
    """Mémorise (ou met à jour) la source d'un tableau de bord qui vient d'être généré."""
    if not connectable(path):
        return None
    with _lock:
        items = _load()
        s = next((x for x in items if x["path"] == path), None)
        if s is None:
            if len(items) >= MAX_SOURCES:
                return None
            s = {"id": uuid.uuid4().hex[:10], "path": path, "freq": "off", "connected_at": _now()}
            items.insert(0, s)
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
    return [s["id"] for s in _load() if FREQS.get(s.get("freq")) and s.get("next_refresh") and s["next_refresh"] <= now]


def start_auto_refresh(read_df, build, org_ctx=None):
    """Actualisation automatique (toutes les heures / chaque jour) : une boucle de fond, vérification chaque minute.
    En ligne : chaque espace est parcouru dans son propre contexte (ses fichiers, son registre)."""
    def tick():
        if not T.CLOUD:
            for sid in due():
                refresh(sid, read_df, build)
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

    def loop():
        while True:
            try:
                tick()
            except Exception:
                pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True).start()
