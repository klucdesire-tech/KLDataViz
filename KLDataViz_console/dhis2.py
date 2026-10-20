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
    d = os.environ.get("KLDV_DHIS2_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "storage")
    os.makedirs(d, exist_ok=True)
    return d


def _key() -> bytes:
    kp = os.path.join(_storage_dir(), "dhis2.key")
    if os.path.isfile(kp):
        with open(kp, "rb") as f:
            return f.read()
    key = os.urandom(32)
    with open(kp, "wb") as f:
        f.write(key)
    return key


def _stream(key: bytes, iv: bytes, n: int) -> bytes:
    """Keystream déterministe : HMAC-SHA256(key, iv || compteur), sur n octets."""
    blocks = (n + 31) // 32
    return b"".join(
        hmac.new(key, iv + i.to_bytes(8, "big"), hashlib.sha256).digest()
        for i in range(blocks)
    )


def _encrypt(plaintext: bytes) -> bytes:
    key = _key()
    iv = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(plaintext, _stream(key, iv, len(plaintext))))
    tag = hmac.new(key, iv + ct, hashlib.sha256).digest()
    return iv + ct + tag


def _decrypt(blob: bytes) -> bytes:
    key = _key()
    iv, ct, tag = blob[:16], blob[16:-32], blob[-32:]
    if not hmac.compare_digest(tag, hmac.new(key, iv + ct, hashlib.sha256).digest()):
        raise ValueError("Identifiants corrompus ou clé de chiffrement incorrecte.")
    return bytes(a ^ b for a, b in zip(ct, _stream(key, iv, len(ct))))


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
            vault = {}
    vault[instance] = {"user": user, "password": password}
    with open(cp, "wb") as f:
        f.write(_encrypt(json.dumps(vault, ensure_ascii=False).encode("utf-8")))


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
def _fetch_json(url: str, user: str, password: str, allow_private: bool = False) -> dict:
    netguard.check_url(url, allow_private=allow_private, https_only=True)
    req = urllib.request.Request(
        url, headers={"User-Agent": "KLDataViz/1.0", "Accept": "application/json"})
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=netguard.TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
            "periode": row[i_pe] if i_pe is not None else "",
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
            "periode": r.get("period"),
            "valeur": r.get("value"),
        })
    return pd.DataFrame(out, columns=COLS)


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------
def read_dhis2(uri: str, allow_private: bool = False):
    """Lit une source ``dhis2://`` et renvoie ``(df_long, err)`` (comme ``_read_df``)."""
    try:
        instance, url = parse_uri(uri)
    except ValueError as e:
        return None, str(e)
    creds = load_credentials(instance)
    if creds is None:
        return None, (f"Aucun identifiant enregistré pour l'instance DHIS2 « {instance} ». "
                      f"Enregistrez-les d'abord via dhis2.save_credentials(instance, user, motdepasse).")
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
    return df, None
