# -*- coding: utf-8 -*-
"""Suivi des erreurs dans Sentry, actif seulement si la variable SENTRY_DSN est posée (Render).

Ce qui part chez Sentry : le type de l'exception, la pile d'appels (fichiers et lignes de NOTRE code), la référence de
l'incident, la route sans jeton, le genre d'incident et l'identifiant de l'espace. Ce qui ne part jamais :
- le message de l'exception (il peut citer une valeur d'un fichier, comme dans `surveillance.py`) ;
- les variables locales, le corps des requêtes, les en-têtes, les cookies, l'utilisateur, les miettes de journal.
Aucune capture automatique : seuls les incidents relevés par `surveillance.py` partent, déjà décrits sans donnée.
Sans SENTRY_DSN, ou sans le paquet `sentry_sdk`, tout cela ne fait rien et ne lève jamais."""
import os
import re
import sys

_etat = {"actif": False, "sdk": None}
CHAMPS_INTERDITS = ("request", "user", "breadcrumbs", "extra", "contexts", "logentry", "modules")
SANS_MESSAGE = "(message retiré : il peut contenir des données)"


def sans_donnees(texte):
    """Un message d'incident peut citer une adresse (refus d'envoi) ou un numéro : remplacés avant l'envoi."""
    t = re.sub(r"[^@\s<>\"']+@[^@\s<>\"']+\.[A-Za-z]{2,}", "[adresse]", str(texte or ""))
    return re.sub(r"\+?\d[\d .-]{6,}\d", "[nombre]", t)[:300]


def nettoyer(event, hint=None):
    """Dernier filtre avant l'envoi : retire tout ce qui pourrait porter une donnée d'un client."""
    try:
        for cle in CHAMPS_INTERDITS:
            event.pop(cle, None)
        for exc in (event.get("exception") or {}).get("values") or []:
            if exc.get("value"):
                exc["value"] = SANS_MESSAGE
            for frame in (exc.get("stacktrace") or {}).get("frames") or []:
                frame.pop("vars", None)
        for fil in (event.get("threads") or {}).get("values") or []:
            for frame in (fil.get("stacktrace") or {}).get("frames") or []:
                frame.pop("vars", None)
        return event
    except Exception:  # noqa: BLE001 - dans le doute, rien ne part
        return None


def demarrer(environnement="production", version=None):
    """Branche Sentry si SENTRY_DSN est posée. Renvoie True si le suivi est actif."""
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        sys.stderr.write("[suivi] SENTRY_DSN posée mais le paquet sentry-sdk est absent : suivi inactif.\n")
        return False
    try:
        sentry_sdk.init(dsn=dsn, environment=os.environ.get("KLDV_ENVIRONNEMENT") or environnement,
                        release=version or os.environ.get("RENDER_GIT_COMMIT") or None,
                        # données personnelles : non envoyées par défaut (option send_default_pii, dépréciée en 2.72),
                        # et `nettoyer` retire de toute façon utilisateur, requête et message
                        include_local_variables=False, max_request_body_size="never",
                        max_breadcrumbs=0, traces_sample_rate=0.0, attach_stacktrace=False,
                        default_integrations=False, auto_enabling_integrations=False,
                        before_send=nettoyer)
    except Exception as exc:  # noqa: BLE001 - une DSN invalide ne doit pas empêcher le service de démarrer
        sys.stderr.write(f"[suivi] Sentry non démarré : {type(exc).__name__}\n")
        return False
    _etat.update(actif=True, sdk=sentry_sdk)
    sys.stderr.write("[suivi] Sentry actif.\n")
    return True


def actif():
    return _etat["actif"]


def _etiquettes(scope, genre, ref, chemin, espace):
    scope.set_tag("genre", genre)
    if ref:
        scope.set_tag("ref", ref)
    if chemin:
        scope.set_tag("chemin", chemin[:120])
    if espace:
        scope.set_tag("espace", str(espace)[:64])


def exception(exc, genre="erreur", ref="", chemin="", espace=""):
    """Une exception attrapée par le serveur ou une tâche de fond."""
    if not _etat["actif"]:
        return
    try:
        sdk = _etat["sdk"]
        with sdk.new_scope() as scope:
            _etiquettes(scope, genre, ref, chemin, espace)
            sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001 - le suivi ne casse jamais ce qu'il suit
        pass


def message(genre, resume, ref="", espace=""):
    """Un incident sans exception (IA en panne, e-mail non envoyé, contrôle de santé en échec)."""
    if not _etat["actif"]:
        return
    try:
        sdk = _etat["sdk"]
        with sdk.new_scope() as scope:
            _etiquettes(scope, genre, ref, "", espace)
            sdk.capture_message(sans_donnees(resume), level="warning")
    except Exception:  # noqa: BLE001
        pass
