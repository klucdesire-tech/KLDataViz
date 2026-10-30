# -*- coding: utf-8 -*-
"""Client de l'API de paiement CinetPay v1 (https://api.cinetpay.co), sans dépendance.

Le client paie sur la page hébergée par CinetPay : KLDataViz ne voit jamais un numéro de carte ni un code
mobile money. Une clé « sk_test_ » vise le bac à sable, une clé « sk_live_ » la production.
L'accès se fait par pays (CINETPAY_COUNTRY) : un jeton JWT obtenu avec la clé et le mot de passe d'API.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

URL_TEST = "https://api.cinetpay.net"
URL_PROD = "https://api.cinetpay.co"
TIMEOUT = 30
JETON_TTL = 23 * 3600          # valable 24 h côté CinetPay
JETON_EXPIRE = {1002, 1003}    # INVALID_TOKEN, EXPIRED_TOKEN
CODES_OK = {200, 100, 2001, 2002}
ETATS_TRANSACTION = {"SUCCESS", "INITIATED", "PENDING", "FAILED", "EXPIRED", "INSUFFICIENT_BALANCE", "USER_NOT_FOUND",
                     "USER_IS_BLOCKED", "NOT_ALLOWED", "OTP_ERROR", "OTP_EXPIRED", "NOT_FOUND"}
MONTANT_MIN, MONTANT_MAX = 100, 2_500_000

_jeton = (0.0, "")
_verrou = threading.Lock()


class PaiementError(Exception):
    """Échec côté CinetPay. Le message ne contient jamais d'identifiant."""


def _cle():
    return os.environ.get("CINETPAY_API_KEY", "").strip()


def _mot_de_passe():
    return os.environ.get("CINETPAY_API_PASSWORD", "").strip()


def pret() -> bool:
    return bool(_cle() and _mot_de_passe())


def mode_test() -> bool:
    return not _cle().startswith("sk_live_")


def mode() -> str:
    return "test" if mode_test() else "live"


def _base() -> str:
    return URL_TEST if mode_test() else URL_PROD


def _http(method, path, body=None, jeton=None):
    """Appel HTTP brut : renvoie (statut, réponse JSON). Remplacé par un faux prestataire dans les tests."""
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "KLDataViz/1.0"}
    if jeton:
        headers["Authorization"] = "Bearer " + jeton
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_base() + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            statut, brut = r.status, r.read()
    except urllib.error.HTTPError as e:
        statut, brut = e.code, e.read()
    except Exception as e:
        raise PaiementError(f"CinetPay injoignable ({type(e).__name__}).") from None
    try:
        return statut, json.loads(brut.decode("utf-8"))
    except Exception:
        raise PaiementError(f"Réponse illisible de CinetPay (HTTP {statut}).") from None


def _connexion(forcer=False) -> str:
    global _jeton
    with _verrou:
        if not forcer and _jeton[0] > time.time():
            return _jeton[1]
        if not pret():
            raise PaiementError("Paiement non configuré.")
        statut, r = _http("POST", "/v1/oauth/login", {"api_key": _cle(), "api_password": _mot_de_passe()})
        jeton = r.get("access_token") if isinstance(r, dict) else None
        if not jeton:
            raise PaiementError(f"Connexion à CinetPay refusée (HTTP {statut}).")
        _jeton = (time.time() + JETON_TTL, jeton)
        return jeton


def _erreur(statut, r) -> bool:
    return statut >= 400 or (r.get("code") is not None and r.get("code") not in CODES_OK)


def _appel(method, path, body=None, etats=False) -> dict:
    statut, r = _http(method, path, body, _connexion())
    if isinstance(r, dict) and (r.get("code") in JETON_EXPIRE or r.get("status") == "EXPIRED_TOKEN"):
        statut, r = _http(method, path, body, _connexion(forcer=True))
    if not isinstance(r, dict):
        raise PaiementError(f"Réponse inattendue de CinetPay (HTTP {statut}).")
    if etats and r.get("status") in ETATS_TRANSACTION:  # un paiement refusé est une réponse, pas une panne
        return r
    if _erreur(statut, r):
        raise PaiementError(f"CinetPay refuse l'opération : {r.get('status') or statut} "
                            f"({str(r.get('description') or r.get('message') or '')[:160]}).")
    return r


def initier(transaction, montant, devise, libelle, email, prenom, nom, url_succes, url_echec, url_notification,
            langue="fr") -> dict:
    """Crée le paiement. Renvoie payment_url (page hébergée), notify_token et transaction_id."""
    if not MONTANT_MIN <= int(montant) <= MONTANT_MAX:
        raise PaiementError(f"Montant hors limites ({MONTANT_MIN} à {MONTANT_MAX}).")
    r = _appel("POST", "/v1/payment", {
        "currency": devise, "merchant_transaction_id": transaction, "amount": int(montant), "lang": langue,
        "designation": libelle[:255], "client_email": email, "client_first_name": prenom[:255],
        "client_last_name": nom[:255], "success_url": url_succes, "failed_url": url_echec,
        "notify_url": url_notification, "channel": "PUSH"})
    url = str(r.get("payment_url") or "")
    if not url.startswith("https://"):
        raise PaiementError("CinetPay n'a pas renvoyé de page de paiement.")
    return {"payment_url": url, "notify_token": str(r.get("notify_token") or ""),
            "transaction_id": str(r.get("transaction_id") or ""), "payment_token": str(r.get("payment_token") or "")}


def statut(transaction) -> dict:
    """État réel du paiement, lu chez CinetPay : la seule source qui active un forfait."""
    return _appel("GET", "/v1/payment/" + urllib.parse.quote(str(transaction), safe=""), etats=True)
