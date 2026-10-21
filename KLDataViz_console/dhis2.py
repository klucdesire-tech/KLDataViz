# -*- coding: utf-8 -*-
"""Connecteur DHIS2 — sources connectées de KLDataViz (AGT-01 Ingestion).

Lit des données DHIS2 par l'API (``/api/analytics`` ou ``/api/dataValueSets``) au lieu
d'un export manuel, et renvoie un DataFrame au format long :

    indicateur | periode | unite_organisation | valeur

AGT-11 (dashboard) le convertit ensuite en une colonne par indicateur (``_long_to_wide``).

URI de source : ``dhis2://<instance>/api/<endpoint>?<params>`` — ex. ::

    dhis2://play.dhis2.org/2.41/api/analytics.json?dimension=dx:Uvn6LCg7dVU&dimension=ou:LEVEL-1&dimension=pe:LAST_12_MONTHS
    dhis2://play.dhis2.org/2.41/api/dataValueSets.json?dataSet=pBOMPrpg1QX&orgUnit=ImspTQPwCqd&period=LAST_12_MONTHS

Les périodes relatives de DHIS2 (``LAST_12_MONTHS``, ``THIS_YEAR``…) sont transmises telles
quelles : c'est l'instance qui les résout.

Sécurité :
- identifiants stockés **chiffrés** (HMAC-SHA256 authentifié, clé locale) dans ``storage/``,
  jamais dans le dépôt ni dans les journaux ;
- compte DHIS2 en **lecture seule** (recommandé) ;
- appels **uniquement** vers l'instance déclarée (``netguard`` : https + anti-SSRF).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

import netguard

SCHEME = "dhis2://"

# Colonnes de sortie (format long) — compatibles avec _long_to_wide d'AGT-11
# (_IND_COLS contient « indicateur », _VAL_COLS contient « valeur », _PER_COLS « periode »).
COLS = ["indicateur", "periode", "unite_organisation", "valeur"]


# --------------------------------------------------------------------------
# Chiffrement authentifié des identifiants — stdlib uniquement (HMAC-SHA256 + chiffre de flux)
# Clé aléatoire 256 bits dans storage/dhis2.key ; coffre chiffré storage/dhis2_credentials.enc.
# --------------------------------------------------------------------------
def _storage_dir() -> str:
    """Coffre de l'ESPACE courant (en ligne : /data/tenants/<org>/storage, disque persistant) :
    les identifiants d'un client ne sont jamais visibles d'un autre. KLDV_DHIS2_DIR : dossier imposé (tests)."""
    d = os.environ.get("KLDV_DHIS2_DIR")
    if not d:
        import tenancy as T
        d = T.current().dir("storage")
    os.makedirs(d, exist_ok=True)
    return d


def _key() -> bytes:
    """En ligne : clé dérivée du secret serveur (KLDV_SECRET, hors du disque) et de l'espace ;
    en local : clé aléatoire conservée dans storage/dhis2.key."""
    secret = os.environ.get("KLDV_SECRET")
    if secret:
        return hmac.new(secret.encode(), ("dhis2-vault:" + _storage_dir()).encode(), hashlib.sha256).digest()
    kp = os.path.join(_storage_dir(), "dhis2.key")
    if os.path.isfile(kp):
        with open(kp, "rb") as f:
            return f.read()
    key = os.urandom(32)
    with open(kp, "wb") as f:
        f.write(key)
    return key


def _subkeys():
    """Clés distinctes pour chiffrer et pour authentifier (jamais la même clé pour les deux usages)."""
    k = _key()
    return hmac.new(k, b"enc", hashlib.sha256).digest(), hmac.new(k, b"mac", hashlib.sha256).digest()


def _stream(key: bytes, iv: bytes, n: int) -> bytes:
    """Keystream déterministe : HMAC-SHA256(key, iv || compteur), sur n octets."""
    blocks = (n + 31) // 32
    return b"".join(
        hmac.new(key, iv + i.to_bytes(8, "big"), hashlib.sha256).digest()
        for i in range(blocks)
    )


def _encrypt(plaintext: bytes) -> bytes:
    ek, mk = _subkeys()
    iv = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(plaintext, _stream(ek, iv, len(plaintext))))
    tag = hmac.new(mk, iv + ct, hashlib.sha256).digest()
    return iv + ct + tag


def _decrypt(blob: bytes) -> bytes:
    ek, mk = _subkeys()
    iv, ct, tag = blob[:16], blob[16:-32], blob[-32:]
    if not hmac.compare_digest(tag, hmac.new(mk, iv + ct, hashlib.sha256).digest()):
        raise ValueError("Identifiants corrompus ou clé de chiffrement incorrecte.")
    return bytes(a ^ b for a, b in zip(ct, _stream(ek, iv, len(ct))))


def _cred_path() -> str:
    return os.path.join(_storage_dir(), "dhis2_credentials.enc")


def save_credentials(instance: str, user: str, password: str) -> None:
    """Enregistre (ou met à jour) les identifiants d'une instance, chiffrés."""
    vault = {}
    cp = _cred_path()
    if os.path.isfile(cp):
        try:
            vault = json.loads(_decrypt(open(cp, "rb").read()).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            # Coffre illisible (clé KLDV_SECRET changée, ancien format…) : on le met de côté au lieu de
            # l'écraser — il redevient lisible si l'ancienne clé est restaurée. Les autres instances devront
            # être reconnectées (read_dhis2 le dit clairement).
            import glob
            import time
            os.replace(cp, f"{cp}.illisible-{time.strftime('%Y%m%d-%H%M%S')}")
            for old in glob.glob(cp + ".illisible-*"):  # copies de plus de 30 jours : supprimées (anciens secrets)
                if time.time() - os.path.getmtime(old) > 30 * 86400:
                    os.remove(old)
            print("[dhis2] coffre illisible mis de côté (clé changée ?) : reconnectez les instances DHIS2", flush=True)
            vault = {}
    vault[instance] = {"user": user, "password": password}
    with open(cp, "wb") as f:
        f.write(_encrypt(json.dumps(vault, ensure_ascii=False).encode("utf-8")))


def _unreadable(cp) -> bool:
    try:
        _decrypt(open(cp, "rb").read())
        return False
    except ValueError:
        return True


def load_credentials(instance: str):
    """Renvoie ``(user, password)`` déchiffrés, ou ``None`` si l'instance est inconnue."""
    cp = _cred_path()
    if not os.path.isfile(cp):
        return None
    try:
        vault = json.loads(_decrypt(open(cp, "rb").read()).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    c = vault.get(instance)
    if not c:
        return None
    return c["user"], c["password"]


# --------------------------------------------------------------------------
# URI
# --------------------------------------------------------------------------
def parse_uri(uri: str):
    """``dhis2://<instance>/api/<endpoint>?<params>`` → ``(instance, url_https)``."""
    if not uri.startswith(SCHEME):
        raise ValueError("Source DHIS2 attendue au format dhis2://instance/api/…")
    rest = uri[len(SCHEME):]
    marker = "/api/"
    idx = rest.find(marker)
    if idx == -1:
        raise ValueError("URI DHIS2 sans /api/ (utilisez /api/analytics ou /api/dataValueSets).")
    instance = rest[:idx]                        # ex. play.dhis2.org/2.41
    api_path = rest[idx + len(marker):]          # ex. analytics.json?dimension=…
    if not api_path.split("?", 1)[0].strip("/"):
        raise ValueError("URI DHIS2 sans endpoint (analytics ou dataValueSets).")
    return instance, f"https://{instance}/api/{api_path}"


# --------------------------------------------------------------------------
# Appel HTTP — validé par netguard, authentifié (Basic), jamais vers une autre instance
# --------------------------------------------------------------------------
class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Redirection acceptée seulement vers la MÊME instance (sinon le mot de passe partirait ailleurs),
    et revérifiée par netguard (anti-SSRF)."""
    def __init__(self, host, allow_private):
        self.host, self.allow_private = host, allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).netloc.lower() != self.host:
            raise netguard.BlockedURL("Redirection DHIS2 vers un autre serveur refusée.")
        netguard.check_url(newurl, allow_private=self.allow_private, https_only=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_json(url: str, user: str, password: str, allow_private: bool = False) -> dict:
    netguard.check_url(url, allow_private=allow_private, https_only=True)
    req = urllib.request.Request(
        url, headers={"User-Agent": "KLDataViz/1.0", "Accept": "application/json"})
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {token}")
    opener = urllib.request.build_opener(
        _SameHostRedirect(urllib.parse.urlparse(url).netloc.lower(), allow_private))
    with opener.open(req, timeout=netguard.TIMEOUT_S) as resp:
        data = resp.read(netguard.MAX_BYTES + 1)
    if len(data) > netguard.MAX_BYTES:
        raise ValueError("Réponse DHIS2 trop volumineuse : réduisez les périodes ou les unités d'organisation.")
    return json.loads(data.decode("utf-8"))


def _http_hint(code: int) -> str:
    return {401: "identifiants invalides", 403: "accès refusé (compte en lecture seule ?)",
            404: "endpoint ou paramètres introuvables"}.get(
        code, "vérifiez l'instance et les paramètres")


# --------------------------------------------------------------------------
# Conversion en DataFrame long
# --------------------------------------------------------------------------
def _resolve(meta: dict, code):
    """Résout un code en libellé via metaData.items, sinon renvoie le code brut."""
    if not meta or code not in meta:
        return code
    return meta[code].get("name", code) if isinstance(meta[code], dict) else code


def _period(p):
    """Périodes DHIS2 → format reconnu par AGT-11 : « 202501 » → « 2025-01 », « 20250115 » → « 2025-01-15 »
    (trimestres « 2025Q1 », semaines et années inchangés)."""
    p = "" if p is None else str(p)
    if len(p) == 6 and p.isdigit():
        return f"{p[:4]}-{p[4:]}"
    if len(p) == 8 and p.isdigit():
        return f"{p[:4]}-{p[4:6]}-{p[6:]}"
    return p


def _analytics_to_long(payload: dict) -> pd.DataFrame:
    headers = payload.get("headers", [])
    rows = payload.get("rows", [])
    meta = payload.get("metaData", {}).get("items", {})
    names = [h.get("name") for h in headers]
    i_dx, i_ou, i_pe, i_val = (names.index(n) if n in names else None
                               for n in ("dx", "ou", "pe", "value"))
    out = []
    for row in rows:
        out.append({
            "indicateur": _resolve(meta, row[i_dx]) if i_dx is not None else "",
            "unite_organisation": _resolve(meta, row[i_ou]) if i_ou is not None else "",
            "periode": _period(row[i_pe]) if i_pe is not None else "",
            "valeur": row[i_val] if i_val is not None else None,
        })
    return pd.DataFrame(out, columns=COLS)


def _data_values_to_long(payload: dict) -> pd.DataFrame:
    rows = payload.get("dataValues", [])
    meta = payload.get("metaData", {}).get("items", {})
    out = []
    for r in rows:
        out.append({
            "indicateur": _resolve(meta, r.get("dataElement")),
            "unite_organisation": _resolve(meta, r.get("orgUnit")),
            "periode": _period(r.get("period")),
            "valeur": r.get("value"),
        })
    return pd.DataFrame(out, columns=COLS)


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------
def read_dhis2(uri: str, allow_private: bool = False, creds=None):
    """Lit une source ``dhis2://`` et renvoie ``(df_long, err)`` (comme ``_read_df``).
    ``creds`` = (user, password) pour une lecture d'essai AVANT d'enregistrer quoi que ce soit."""
    try:
        instance, url = parse_uri(uri)
    except ValueError as e:
        return None, str(e)
    creds = creds or load_credentials(instance)
    if creds is None:
        import glob
        cp = _cred_path()
        why = ("le coffre des identifiants a été réinitialisé (clé du serveur changée)"
               if glob.glob(cp + ".illisible-*") or (os.path.isfile(cp) and _unreadable(cp)) else "jamais enregistré")
        return None, (f"Aucun identifiant DHIS2 pour « {instance} » ({why}) : "
                      "reconnectez la source DHIS2 depuis l'écran d'import.")
    user, password = creds
    try:
        payload = _fetch_json(url, user, password, allow_private)
    except netguard.BlockedURL as e:
        return None, str(e)
    except urllib.error.HTTPError as e:
        return None, f"DHIS2 a refusé la requête (HTTP {e.code}) : {_http_hint(e.code)}."
    except urllib.error.URLError as e:
        return None, f"Instance DHIS2 injoignable : {e.reason}"
    except Exception as e:
        return None, f"Lecture DHIS2 impossible : {e}"
    try:
        if isinstance(payload, dict) and "dataValues" in payload:
            df = _data_values_to_long(payload)
        elif isinstance(payload, dict) and "rows" in payload and "headers" in payload:
            df = _analytics_to_long(payload)
        else:
            return None, "Réponse DHIS2 inattendue (ni analytics ni dataValueSets)."
    except Exception as e:
        return None, f"Conversion de la réponse DHIS2 impossible : {e}"
    if df.empty:
        return None, "La requête DHIS2 ne renvoie aucune donnée."
    df.attrs["agrege"] = True  # valeurs déjà agrégées par DHIS2 : une ligne = une unité × une période
    return df, None
