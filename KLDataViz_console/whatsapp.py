# -*- coding: utf-8 -*-
"""Envoi par WhatsApp (API WhatsApp Business Cloud de Meta) : rapports planifiés et alertes de surveillance.

Inactif tant que le compte WhatsApp Business n'est pas configuré sur le service :
- WHATSAPP_JETON : jeton d'accès permanent d'un utilisateur système (Meta Business → Utilisateurs système) ;
- WHATSAPP_NUMERO_ID : identifiant du numéro d'envoi (WhatsApp Manager → API → « Phone number ID ») ;
- WHATSAPP_MODELE_RAPPORT : modèle approuvé par Meta pour un rapport. En-tête « Document », corps à 3 variables :
  {{1}} nom du rapport, {{2}} date, {{3}} ce qu'il faut retenir ;
- WHATSAPP_MODELE_ALERTE : modèle approuvé pour une alerte de surveillance, corps à 1 variable : {{1}} le résumé ;
- WHATSAPP_LANGUE (fr par défaut), WHATSAPP_INDICATIF (225 par défaut, pour les numéros écrits sans indicatif),
  WHATSAPP_API_VERSION (v23.0 par défaut).

Pourquoi des modèles : WhatsApp n'autorise un message libre que dans les 24 h qui suivent un message du destinataire.
Un rapport ou une alerte part sans qu'on lui ait écrit, donc par un modèle validé à l'avance par Meta.

Le jeton ne sort jamais du serveur ; seuls l'API de Meta (graph.facebook.com) et le numéro du destinataire reçoivent
le message. Les numéros sont gardés au format international (+225…).
"""
import json
import os
import re
import uuid
import urllib.error
import urllib.request

API = "https://graph.facebook.com"
MAX_VARIABLE = 900          # Meta refuse une variable trop longue ; on garde une marge
_ESPACES = re.compile(r"[ \t ]{2,}")


def _env(nom, defaut=""):
    return (os.environ.get(nom) or defaut).strip()


def actif() -> bool:
    return bool(_env("WHATSAPP_JETON") and _env("WHATSAPP_NUMERO_ID"))


def modele(genre: str) -> str:
    return _env("WHATSAPP_MODELE_RAPPORT" if genre == "rapport" else "WHATSAPP_MODELE_ALERTE")


def pret(genre: str) -> bool:
    """Prêt à envoyer ce genre de message : compte configuré et modèle nommé."""
    return actif() and bool(modele(genre))


def etat() -> dict:
    """Pour l'administration : ce qui est configuré, sans aucun secret."""
    return {"actif": actif(), "rapport": pret("rapport"), "alerte": pret("alerte")}


# ---------------------------------------------------------------- numéros
def est_numero(texte: str) -> bool:
    """Un destinataire sans @ est un numéro de téléphone."""
    return "@" not in str(texte or "")


def normaliser(texte: str) -> str:
    """« +225 07 00 00 00 00 », « 00225… » ou « 07 00 00 00 00 » → « +2250700000000 ». ValueError si invalide."""
    brut = str(texte or "").strip()
    chiffres = re.sub(r"[\s.\-()/ ]", "", brut)
    if chiffres.startswith("00"):
        chiffres = "+" + chiffres[2:]
    if not chiffres.startswith("+"):
        if not chiffres.isdigit():
            raise ValueError(f"Numéro WhatsApp invalide : {brut}")
        # Numéro local (en Côte d'Ivoire, 10 chiffres qui commencent par 0 et le 0 se garde) : indicatif du service
        chiffres = "+" + (_env("WHATSAPP_INDICATIF", "225").lstrip("+") or "225") + chiffres
    if not re.fullmatch(r"\+[1-9]\d{7,14}", chiffres):
        raise ValueError(f"Numéro WhatsApp invalide : {brut} (écrivez-le avec l'indicatif, par exemple +225 07 00 00 00 00)")
    return chiffres


def masque(numero: str) -> str:
    """Pour les statuts et journaux : « +225 •••• 00 »."""
    n = str(numero or "")
    return (n[:4] + " •••• " + n[-2:]) if len(n) > 6 else "••••"


def separer(destinataires) -> tuple:
    """Liste mêlée → (e-mails, numéros WhatsApp normalisés). Les numéros invalides lèvent ValueError."""
    mails, numeros = [], []
    for d in destinataires or []:
        d = str(d or "").strip()
        if not d:
            continue
        if est_numero(d):
            n = normaliser(d)
            if n not in numeros:
                numeros.append(n)
        else:
            mails.append(d)
    return mails, numeros


def decouper(texte: str) -> list:
    """Saisie libre (virgules, points-virgules, retours à la ligne) → destinataires. Les e-mails peuvent aussi être
    séparés par des espaces ; un numéro garde les siens (« +225 07 00 00 00 00 » reste un seul destinataire)."""
    sortie = []
    for morceau in re.split(r"[,;\n]+", str(texte or "")):
        morceau = morceau.strip()
        if not morceau:
            continue
        if "@" in morceau:
            sortie += [x for x in morceau.split() if x]
        else:
            sortie.append(morceau)
    return sortie


# ---------------------------------------------------------------- appels à Meta
def _variable(texte: str) -> str:
    """Meta refuse dans une variable les retours à la ligne, les tabulations et plus de 4 espaces de suite."""
    t = re.sub(r"[\r\n\t]+", " · ", str(texte or "")).strip(" ·")
    t = _ESPACES.sub(" ", t)
    return (t[:MAX_VARIABLE - 1] + "…") if len(t) > MAX_VARIABLE else (t or "—")


def _url(chemin: str) -> str:
    return f"{API}/{_env('WHATSAPP_API_VERSION', 'v23.0')}/{_env('WHATSAPP_NUMERO_ID')}/{chemin}"


CONSEILS = {
    190: "Jeton expiré ou invalide : créez un jeton permanent (utilisateur système) et mettez à jour WHATSAPP_JETON.",
    132001: "Modèle introuvable : vérifiez son nom et sa langue dans WhatsApp Manager (il doit être approuvé).",
    132000: "Le nombre de variables ne correspond pas au modèle approuvé.",
    132012: "Les variables ne correspondent pas au modèle approuvé (en-tête Document attendu pour un rapport ?).",
    131030: "Numéro de test : ajoutez ce destinataire à la liste autorisée dans WhatsApp Manager, ou passez en production.",
    131026: "Message non remis : ce numéro n'a peut-être pas WhatsApp.",
    131042: "Paiement : ajoutez un moyen de paiement au compte WhatsApp Business.",
    131056: "Trop de messages vers ce numéro en peu de temps : réessayez plus tard.",
    130429: "Limite d'envoi de Meta atteinte : réessayez plus tard.",
}


def _erreur(exc: urllib.error.HTTPError) -> RuntimeError:
    try:
        e = json.loads(exc.read().decode() or "{}").get("error") or {}
    except Exception:
        e = {}
    code = e.get("code")
    conseil = CONSEILS.get(code, "")
    # Le message de Meta peut citer le numéro : on garde le code et notre propre explication
    return RuntimeError(f"Envoi WhatsApp refusé par Meta ({exc.code}{', code ' + str(code) if code else ''})."
                        + (" " + conseil if conseil else ""))


def _appel(req, ouvrir=None):
    try:
        with (ouvrir or urllib.request.urlopen)(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise _erreur(exc) from None
    except urllib.error.URLError:
        raise RuntimeError("API WhatsApp de Meta injoignable depuis le serveur : réessayez plus tard.") from None


def _entetes(json_=True):
    h = {"Authorization": "Bearer " + _env("WHATSAPP_JETON")}
    if json_:
        h["Content-Type"] = "application/json"
    return h


def televerser(chemin: str, ouvrir=None) -> str:
    """Dépose un fichier chez Meta (gardé 30 jours) et rend son identifiant, pour l'en-tête « Document »."""
    nom = os.path.basename(chemin)
    types = {".pdf": "application/pdf", ".xlsx":
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    type_ = types.get(os.path.splitext(nom)[1].lower(), "application/octet-stream")
    borne = uuid.uuid4().hex
    with open(chemin, "rb") as f:
        contenu = f.read()
    corps = b"".join([
        f"--{borne}\r\nContent-Disposition: form-data; name=\"messaging_product\"\r\n\r\nwhatsapp\r\n".encode(),
        f"--{borne}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\n{type_}\r\n".encode(),
        f"--{borne}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{nom}\"\r\n"
        f"Content-Type: {type_}\r\n\r\n".encode(), contenu, f"\r\n--{borne}--\r\n".encode()])
    h = dict(_entetes(json_=False), **{"Content-Type": f"multipart/form-data; boundary={borne}"})
    rep = _appel(urllib.request.Request(_url("media"), data=corps, method="POST", headers=h), ouvrir)
    if not rep.get("id"):
        raise RuntimeError("Meta n'a pas rendu d'identifiant pour la pièce jointe.")
    return str(rep["id"])


def envoyer_modele(numero: str, nom_modele: str, variables, document=None, ouvrir=None) -> str:
    """Message à partir d'un modèle approuvé. `document` : (identifiant Meta, nom du fichier) pour l'en-tête."""
    if not actif():
        raise RuntimeError("Envoi WhatsApp non configuré sur ce service (WHATSAPP_JETON, WHATSAPP_NUMERO_ID).")
    if not nom_modele:
        raise RuntimeError("Aucun modèle WhatsApp n'est nommé pour cet envoi.")
    composants = []
    if document:
        composants.append({"type": "header", "parameters": [
            {"type": "document", "document": {"id": document[0], "filename": document[1]}}]})
    if variables:
        composants.append({"type": "body", "parameters": [{"type": "text", "text": _variable(v)} for v in variables]})
    corps = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": numero.lstrip("+"),
             "type": "template", "template": {"name": nom_modele, "language": {"code": _env("WHATSAPP_LANGUE", "fr")},
                                              "components": composants}}
    rep = _appel(urllib.request.Request(_url("messages"), data=json.dumps(corps).encode(), method="POST",
                                        headers=_entetes()), ouvrir)
    messages = rep.get("messages") or [{}]
    return str(messages[0].get("id") or "")


# ---------------------------------------------------------------- usages
FORMATS = (".pdf", ".xlsx")   # un rapport HTML ne passe pas en pièce jointe WhatsApp


def _signaler(motif: str):
    """Un envoi WhatsApp raté devient un incident de surveillance (sans le numéro). Le contrôle « E-mail » n'en
    dépend pas : WhatsApp et Brevo tombent en panne séparément."""
    try:
        import surveillance
        surveillance.signaler("envoi", "WhatsApp : " + str(motif or "échec")[:200])
    except Exception:
        pass


def envoyer_rapport(numeros, nom: str, date: str, retenir: str, fichier=None, ouvrir=None) -> dict:
    """Rapport planifié à des numéros : la pièce jointe n'est déposée qu'une fois chez Meta pour tous.
    Rend {"envoyes": n, "echecs": [(numéro masqué, motif)]} ; ne lève pas pour un seul numéro en échec."""
    if not numeros:
        return {"envoyes": 0, "echecs": []}
    if not pret("rapport"):
        raise RuntimeError("Envoi WhatsApp des rapports non configuré sur ce service (WHATSAPP_MODELE_RAPPORT).")
    document = None
    if fichier and os.path.exists(fichier):
        try:
            document = (televerser(fichier, ouvrir), os.path.basename(fichier))
        except Exception as exc:
            _signaler(str(exc))
            raise
    envoyes, echecs = 0, []
    for n in numeros:
        try:
            envoyer_modele(n, modele("rapport"), [nom, date, retenir], document, ouvrir)
            envoyes += 1
        except Exception as exc:
            echecs.append((masque(n), str(exc)))
    if echecs:
        _signaler(echecs[0][1])
    return {"envoyes": envoyes, "echecs": echecs}


def destinataires_alertes() -> list:
    """Numéros qui reçoivent les alertes de surveillance (KLDV_ALERTES_WHATSAPP, séparés par des virgules)."""
    sortie = []
    for brut in decouper(_env("KLDV_ALERTES_WHATSAPP")):
        try:
            n = normaliser(brut)
        except ValueError:
            continue
        if n not in sortie:
            sortie.append(n)
    return sortie


def envoyer_alerte(resume: str, ouvrir=None) -> dict:
    """Alerte courte aux numéros d'astreinte. Ne note rien dans la surveillance : une panne d'alerte ne doit pas
    s'alimenter elle-même (comme pour l'e-mail)."""
    numeros = destinataires_alertes()
    if not numeros or not pret("alerte"):
        return {"envoyes": 0, "echecs": [], "raison": "WhatsApp non configuré pour les alertes"}
    envoyes, echecs = 0, []
    for n in numeros:
        try:
            envoyer_modele(n, modele("alerte"), [resume], None, ouvrir)
            envoyes += 1
        except Exception as exc:
            echecs.append((masque(n), str(exc)))
    return {"envoyes": envoyes, "echecs": echecs}
