# -*- coding: utf-8 -*-
"""Journal des actions sensibles : qui a fait quoi, quand (traçabilité, exigée pour des données de santé).

- journal de la plateforme : actions des administrateurs (forfaits, suspensions, invitations…),
  <DATA_DIR>/journal_admin.jsonl ;
- journal de chaque espace : export ou suppression des données, liens de partage, connexion DHIS2,
  dictionnaire d'indicateurs — <espace>/storage/journal.jsonl.

Une ligne JSON par action ; jamais de mot de passe, de clé ni de contenu de fichier (seuls des champs
d'identification choisis sont gardés). Le fichier est borné : au-delà de MAX_LIGNES, les plus anciennes partent.
"""
import datetime as dt
import json
import os
import threading

import tenancy as T

MAX_LIGNES = 5000
CHAMPS = ("org_id", "plan", "email", "days", "trial_days", "suspended", "key", "id", "role", "nom", "instance", "jours")
_lock = threading.Lock()


def _chemin(espace: bool) -> str:
    base = os.environ.get("KLDV_JOURNAL_DIR")
    if base:
        d = os.path.join(base, "espace" if espace else "plateforme")
    else:
        d = T.current().dir("storage") if espace else T.DATA_DIR
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "journal.jsonl" if espace else "journal_admin.jsonl")


def ajouter(action: str, qui: str = "", details: dict | None = None, espace: bool = True) -> None:
    """Ajoute une ligne ; ne lève jamais (la traçabilité ne doit pas bloquer l'action)."""
    try:
        d = {k: v for k, v in (details or {}).items() if k in CHAMPS and v not in (None, "")}
        ligne = json.dumps({"date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                            "qui": qui or T.current().email or "local", "action": action, **d},
                           ensure_ascii=False, default=str)
        p = _chemin(espace)
        with _lock:
            with open(p, "a", encoding="utf-8") as f:
                f.write(ligne + "\n")
            if os.path.getsize(p) > MAX_LIGNES * 400:  # rotation simple : on garde les MAX_LIGNES dernières
                with open(p, encoding="utf-8") as f:
                    garde = f.readlines()[-MAX_LIGNES:]
                with open(p + ".tmp", "w", encoding="utf-8") as f:
                    f.writelines(garde)
                os.replace(p + ".tmp", p)
    except Exception:
        pass


def lire(n: int = 200, espace: bool = True) -> list:
    """Les n dernières actions, la plus récente d'abord."""
    try:
        with open(_chemin(espace), encoding="utf-8") as f:
            lignes = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for l in reversed(lignes):
        try:
            out.append(json.loads(l))
        except ValueError:
            continue
    return out
