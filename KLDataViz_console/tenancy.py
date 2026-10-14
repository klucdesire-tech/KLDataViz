# -*- coding: utf-8 -*-
"""
Contexte de requête : à qui appartiennent les fichiers, le registre et l'état en cours.

- Mode local (par défaut) : un seul « locataire », les dossiers historiques de la console
  (exports/, uploads/, storage/) et le schéma de registre « ingestion ». Rien ne change.
- Mode cloud (KLDV_MODE=cloud) : chaque organisation a son dossier
  KLDV_DATA_DIR/tenants/<org_id>/ et son schéma PostgreSQL t_<org8>. Le contexte est posé
  par server.py après vérification du jeton de connexion, pour le fil d'exécution courant.
"""
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

BASE = os.path.dirname(os.path.abspath(__file__))
CLOUD = os.environ.get("KLDV_MODE", "local").lower() == "cloud"
DATA_DIR = os.environ.get("KLDV_DATA_DIR", "/data" if CLOUD else BASE)

class AuthError(Exception):
    """Connexion absente ou expirée (HTTP 401)."""


class Forbidden(Exception):
    """Action interdite pour ce rôle (HTTP 403)."""


class QuotaExceeded(Exception):
    """Quota du plan atteint (HTTP 402)."""


class Unavailable(Exception):
    """Service tiers injoignable : connexion Supabase ou base de données (HTTP 503)."""


_local = threading.local()
_states = {}
_states_lock = threading.Lock()


@dataclass
class Ctx:
    key: str                      # identifiant de l'état (utilisateur, ou « local »)
    org_id: str | None = None
    user_id: str | None = None
    email: str | None = None
    role: str = "owner"
    plan: dict = field(default_factory=dict)
    is_admin: bool = False

    @property
    def root(self) -> str:
        if not CLOUD:
            return BASE
        if not self.org_id or not re.fullmatch(r"[0-9a-f-]{36}", self.org_id):
            raise PermissionError("organisation inconnue")
        return os.path.join(DATA_DIR, "tenants", self.org_id)

    def dir(self, name: str) -> str:
        sub = {"storage_root": os.path.join("storage", "ingestion") if CLOUD else None,
               "cleaned": os.path.join("storage", "cleaned")}.get(name, name)
        if sub is None:  # stockage d'ingestion local : emplacement par défaut du Versioner
            return None
        p = os.path.join(self.root, sub)
        os.makedirs(p, exist_ok=True)
        return p

    @property
    def schema(self) -> str:
        return f"t_{self.org_id.replace('-', '')[:12]}" if CLOUD else "ingestion"

    def owns(self, path: str) -> bool:
        """Vrai si `path` est dans les dossiers de ce locataire (toujours vrai en local)."""
        if not CLOUD:
            return True
        root = os.path.realpath(self.root)
        return os.path.realpath(path).startswith(root + os.sep)


LOCAL = Ctx(key="local")


def current() -> Ctx:
    return getattr(_local, "ctx", None) or LOCAL


@contextmanager
def use(ctx: Ctx):
    prev = getattr(_local, "ctx", None)
    _local.ctx = ctx
    try:
        yield ctx
    finally:
        _local.ctx = prev


def state() -> dict:
    """État de travail de l'utilisateur courant (dernière analyse, fichier en cours…)."""
    k = current().key
    with _states_lock:
        return _states.setdefault(k, {"last_result": None, "current_path": None, "last_ingest": None})


def exports_dir() -> str:
    return current().dir("exports")


def uploads_dir() -> str:
    return current().dir("uploads")


def cleaned_dir() -> str:
    return current().dir("cleaned")


def storage_root():
    return current().dir("storage_root")


def registry():
    from ingestion import Registry
    return Registry(schema=current().schema)
