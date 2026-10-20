# -*- coding: utf-8 -*-
"""
Liens de partage d'un tableau de bord (comme « Publier » dans Power BI) : une adresse /t/<jeton>
que des collègues ouvrent SANS compte. Le lien affiche toujours la dernière version enregistrée
du tableau de bord (donc les données à jour si la source est connectée).

Sécurité :
- jeton aléatoire de 192 bits, impossible à deviner ;
- expiration obligatoire (7, 30 ou 90 jours), révocation immédiate ;
- création et révocation réservées au propriétaire et aux analystes de l'espace ;
- la page partagée est servie isolée (CSP sandbox) et non indexée par les moteurs de recherche.

Index global (le lien public ne porte pas d'espace) : <données>/shares.json.
"""
import datetime as dt
import json
import os
import secrets
import threading

import tenancy as T

DAYS = (7, 30, 90)
MAX_PER_DASHBOARD = 10
_lock = threading.Lock()


def _path():
    base = os.environ.get("KLDV_SHARES_DIR") or (T.DATA_DIR if T.CLOUD else os.path.join(T.BASE, "storage"))
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "shares.json")


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(items):
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path())


def _org():
    return T.current().org_id or "local"


def _now():
    return dt.datetime.now()


def _public(x):
    return {"token": x["token"], "source_id": x["source_id"], "created_at": x["created_at"],
            "expires_at": x["expires_at"], "created_by": x.get("created_by")}


def create(source_id: str, days: int):
    if int(days) not in DAYS:
        raise ValueError("Durée de validité inconnue.")
    now = _now()
    with _lock:
        items = [x for x in _load() if x["expires_at"] > now.isoformat()]  # ménage des liens expirés
        mine = [x for x in items if x["org"] == _org() and x["source_id"] == source_id]
        if len(mine) >= MAX_PER_DASHBOARD:
            raise ValueError(f"{MAX_PER_DASHBOARD} liens actifs au maximum par tableau de bord : révoquez-en un.")
        x = {"token": secrets.token_urlsafe(24), "org": _org(), "source_id": source_id,
             "created_at": now.isoformat(timespec="seconds"),
             "expires_at": (now + dt.timedelta(days=int(days))).isoformat(timespec="seconds"),
             "created_by": T.current().email}
        items.append(x)
        _save(items)
    return _public(x)


def list_for(source_id: str):
    now = _now().isoformat()
    return [_public(x) for x in _load() if x["org"] == _org() and x["source_id"] == source_id and x["expires_at"] > now]


def revoke(token: str):
    with _lock:
        items = _load()
        keep = [x for x in items if not (x["token"] == token and x["org"] == _org())]  # seulement dans son espace
        if len(keep) == len(items):
            raise KeyError("Lien introuvable.")
        _save(keep)


def revoke_all(source_id: str):
    with _lock:
        _save([x for x in _load() if not (x["org"] == _org() and x["source_id"] == source_id)])


def resolve(token: str):
    """Jeton valide → (espace, tableau de bord) ; None si inconnu, révoqué ou expiré."""
    if not token or len(token) > 64:
        return None
    now = _now().isoformat()
    x = next((x for x in _load() if secrets.compare_digest(x["token"], token)), None)
    if x is None or x["expires_at"] <= now:
        return None
    return x["org"], x["source_id"]
