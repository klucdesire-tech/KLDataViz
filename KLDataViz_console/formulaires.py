# -*- coding: utf-8 -*-
"""Connecteur des formulaires de collecte : KoboToolbox et ODK Central (sources connectées, comme DHIS2).

Les ONG, projets et programmes de santé collectent sur le terrain avec ces outils ; ici les réponses arrivent
directement, sans export manuel, et restent actualisables (↻ Actualiser relit le formulaire).

URI de source (ce qui est enregistré, jamais d'identifiant dedans) :
    kobo://<serveur>/<uid du formulaire>                ex. kobo://kf.kobotoolbox.org/aBcD3fGh5iJk7LmN9pQr
    odk://<serveur>/<n° de projet>/<id du formulaire>    ex. odk://central.exemple.org/3/enquete_menage

Authentification : jeton d'API KoboToolbox (« Token … ») ; compte ODK Central (e-mail + mot de passe, en Basic).
Ils vont dans le coffre chiffré de l'espace (le même que DHIS2), sous « kobo:<serveur> » ou « odk:<serveur> ».

Ce qui n'est jamais gardé : qui a soumis (`_submitted_by`, `submitterName`…), identifiants techniques et métadonnées
du formulaire, pièces jointes. Les groupes répétés (listes) ne tiennent pas en une ligne : ils sont écartés, et la
réponse le dit. Appels https seulement, vers le serveur déclaré (netguard : anti-SSRF, redirection vers un autre
serveur refusée : le jeton partirait ailleurs)."""
from __future__ import annotations

import base64
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

import dhis2
import netguard

KOBO, ODK = "kobo://", "odk://"
PAGE = 1000            # réponses par page
MAX_REPONSES = 200_000
DATE_SOUMISSION = "date_soumission"
# Métadonnées sans intérêt pour l'analyse, ou qui désignent une personne (l'enquêteur, le compte qui a soumis)
_SYSTEME_KOBO = {"_id", "_uuid", "_xform_id_string", "_status", "_submitted_by", "_attachments", "_geolocation",
                 "_notes", "_tags", "_validation_status", "_bamboo_dataset_id", "__version__", "_version_",
                 "formhub/uuid", "meta/instanceID", "meta/rootUuid", "meta/deprecatedID", "meta/instanceName"}
_SYSTEME_ODK = {"__id", "meta", "instanceID", "__system"}
# Métadonnées XLSForm qui identifient l'appareil ou l'enquêteur : jamais gardées, ni dans Kobo ni dans ODK
_APPAREIL = {"deviceid", "imei", "username", "phonenumber", "simserial", "subscriberid"}


def est_formulaire(path: str) -> bool:
    return str(path or "").startswith((KOBO, ODK))


def nom_source(path: str) -> str:
    """Nom lisible d'une source de formulaire (pour le registre et les fichiers)."""
    morceaux = path.split("://", 1)[1].split("/")
    return ("kobo_" if path.startswith(KOBO) else "odk_") + re.sub(r"[^A-Za-z0-9_-]+", "_", morceaux[-1])[:40]


# --------------------------------------------------------------------------
# Liens collés par l'utilisateur → URI
# --------------------------------------------------------------------------
def depuis_lien(genre: str, lien: str) -> str:
    """Un lien copié dans le navigateur (page du formulaire, ou adresse de l'API) → URI kobo:// ou odk://."""
    lien = str(lien or "").strip()
    if not lien.startswith("https://"):
        raise ValueError("Le lien doit commencer par https://")
    u = urllib.parse.urlparse(lien)
    hote, tout = u.netloc.lower(), u.path + "#" + (u.fragment or "")
    if not hote:
        raise ValueError("Lien illisible : copiez l'adresse complète de la page du formulaire.")
    if genre == "kobo":
        m = re.search(r"(?:/forms/|/assets/)([A-Za-z0-9]{10,40})", tout)
        if not m:
            raise ValueError("Formulaire KoboToolbox introuvable dans ce lien : ouvrez le formulaire dans Kobo "
                             "(page « Résumé » ou « Données ») et copiez l'adresse de la page.")
        return f"{KOBO}{hote}/{m.group(1)}"
    if genre == "odk":
        m = re.search(r"/projects/(\d+)/forms/([^/#?.]+)", tout)
        if not m:
            raise ValueError("Formulaire ODK Central introuvable dans ce lien : ouvrez le formulaire dans ODK Central "
                             "et copiez l'adresse de la page (…/projects/<n°>/forms/<formulaire>).")
        return f"{ODK}{hote}/{m.group(1)}/{urllib.parse.unquote(m.group(2))}"
    raise ValueError("Type de formulaire inconnu (kobo ou odk).")


def analyser_uri(uri: str):
    """URI → (genre, serveur, url de la première page, clé du coffre)."""
    if uri.startswith(KOBO):
        parts = uri[len(KOBO):].split("/")
        if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9]{10,40}", parts[1] or ""):
            raise ValueError("Source KoboToolbox attendue au format kobo://serveur/uid")
        hote, uid = parts
        return "kobo", hote, f"https://{hote}/api/v2/assets/{uid}/data/?format=json&limit={PAGE}", f"kobo:{hote}"
    if uri.startswith(ODK):
        parts = uri[len(ODK):].split("/")
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2]:
            raise ValueError("Source ODK Central attendue au format odk://serveur/projet/formulaire")
        hote, projet, form = parts
        return ("odk", hote, f"https://{hote}/v1/projects/{projet}/forms/{urllib.parse.quote(form, safe='')}.svc/"
                f"Submissions?%24top={PAGE}", f"odk:{hote}")
    raise ValueError("Source de formulaire attendue (kobo:// ou odk://).")


# --------------------------------------------------------------------------
# Appels
# --------------------------------------------------------------------------
def _entete(genre, creds):
    if genre == "kobo":
        return f"Token {creds[1]}"
    return "Basic " + base64.b64encode(f"{creds[0]}:{creds[1]}".encode("utf-8")).decode("ascii")


def _lire_page(url, hote, entete, allow_private):
    if urllib.parse.urlparse(url).netloc.lower() != hote:  # « next » d'un serveur détourné : le jeton n'y part pas
        raise netguard.BlockedURL("Page suivante sur un autre serveur : refusée.")
    netguard.check_url(url, allow_private=allow_private, https_only=True)
    req = urllib.request.Request(url, headers={"User-Agent": "KLDataViz/1.0", "Accept": "application/json",
                                               "Authorization": entete})
    opener = urllib.request.build_opener(dhis2._SameHostRedirect(hote, allow_private), *netguard.handlers(allow_private))
    with opener.open(req, timeout=netguard.TIMEOUT_S) as resp:
        data = resp.read(netguard.MAX_BYTES + 1)
    if len(data) > netguard.MAX_BYTES:
        raise ValueError("Réponse trop volumineuse.")
    return json.loads(data.decode("utf-8"))


def _toutes(genre, hote, url, entete, allow_private):
    lignes, pages = [], 0
    while url and len(lignes) < MAX_REPONSES:
        page = _lire_page(url, hote, entete, allow_private)
        pages += 1
        if genre == "kobo":
            lignes += page.get("results") or []
            url = page.get("next")
        else:
            lignes += page.get("value") or []
            url = page.get("@odata.nextLink")
        if pages > MAX_REPONSES // PAGE + 1:
            break
    return lignes[:MAX_REPONSES]


# --------------------------------------------------------------------------
# Réponses → tableau
# --------------------------------------------------------------------------
def _aplatir(objet, prefixe="", out=None):
    out = {} if out is None else out
    for cle, val in objet.items():
        nom = f"{prefixe}{cle}"
        if isinstance(val, dict):
            _aplatir(val, nom + "/", out)
        else:
            out[nom] = val
    return out


def _court(colonnes):
    """« groupe/sous_groupe/question » → « question » quand le nom court est unique ; sinon le chemin, joint par « _ »."""
    derniers = [c.split("/")[-1] for c in colonnes]
    return {c: (d if derniers.count(d) == 1 else c.replace("/", "_")) for c, d in zip(colonnes, derniers)}


def en_tableau(genre, reponses):
    """Réponses brutes → (DataFrame, colonnes écartées car répétées). Une ligne par réponse."""
    lignes, repetes = [], set()
    for r in reponses:
        if not isinstance(r, dict):
            continue
        plat = {}
        if genre == "kobo":
            for cle, val in r.items():
                if cle in _SYSTEME_KOBO or cle in _APPAREIL or (cle.startswith("_") and cle != "_submission_time"):
                    continue
                if isinstance(val, list):
                    repetes.add(cle.split("/")[-1])
                    continue
                plat[DATE_SOUMISSION if cle == "_submission_time" else cle] = val
        else:
            soumis = (r.get("__system") or {}).get("submissionDate")
            for cle, val in _aplatir({k: v for k, v in r.items() if k not in _SYSTEME_ODK}).items():
                if "@odata" in cle or cle in _APPAREIL:
                    continue
                if isinstance(val, list):
                    repetes.add(cle.split("/")[-1])
                    continue
                plat[cle] = val
            if soumis:
                plat[DATE_SOUMISSION] = soumis
        lignes.append(plat)
    df = pd.DataFrame(lignes)
    if df.empty:
        return df, sorted(repetes)
    df = df.rename(columns=_court([c for c in df.columns]))
    for c in df.columns:  # nombres saisis comme texte par le formulaire (« 12 », « 3.5 ») : en nombres
        if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):  # pandas 3 : type « str », plus « object »
            v = pd.to_numeric(df[c], errors="coerce")
            if v.notna().sum() and v.notna().sum() == df[c].notna().sum():
                df[c] = v
    if DATE_SOUMISSION in df.columns:  # la date de soumission en tête : la période se lit d'abord là
        df = df[[DATE_SOUMISSION] + [c for c in df.columns if c != DATE_SOUMISSION]]
    return df, sorted(repetes)


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------
def lire(uri: str, allow_private: bool = False, creds=None):
    """Lit une source de formulaire → (DataFrame, erreur), comme `_read_df`. `creds` : essai avant enregistrement."""
    try:
        genre, hote, url, cle = analyser_uri(uri)
    except ValueError as e:
        return None, str(e)
    nom = "KoboToolbox" if genre == "kobo" else "ODK Central"
    creds = creds or dhis2.load_credentials(cle)
    if creds is None:
        return None, f"Aucun accès enregistré pour {nom} ({hote}) : reconnectez le formulaire depuis l'écran d'import."
    try:
        reponses = _toutes(genre, hote, url, _entete(genre, creds), allow_private)
    except netguard.BlockedURL as e:
        return None, str(e)
    except urllib.error.HTTPError as e:
        motif = {401: "jeton ou identifiants invalides", 403: "accès refusé à ce formulaire",
                 404: "formulaire introuvable (vérifiez le lien)"}.get(e.code, "réessayez plus tard")
        return None, f"{nom} a refusé la requête (HTTP {e.code}) : {motif}."
    except urllib.error.URLError:  # le détail système (« getaddrinfo failed ») n'aide pas l'utilisateur
        return None, f"Serveur {nom} injoignable : vérifiez l'adresse dans le lien, puis réessayez."
    except Exception as e:  # noqa: BLE001 — le détail part dans les journaux, pas à l'écran
        sys.stderr.write(f"[formulaires] lecture {nom} impossible : {type(e).__name__}\n")
        return None, (f"Lecture {nom} impossible : réponse inattendue du serveur. Réessayez ; si cela continue, "
                      "vérifiez le lien du formulaire.")
    df, repetes = en_tableau(genre, reponses)
    if df.empty:
        return None, f"Le formulaire {nom} n'a encore aucune réponse."
    if repetes:
        df.attrs["repetes_ecartes"] = repetes
    df.attrs["agrege"] = False  # une ligne = une réponse
    return df, None


def connecter(genre: str, lien: str, utilisateur: str, secret: str, allow_private: bool = False):
    """Lien + accès → essai de lecture, puis accès enregistrés (chiffrés) seulement s'il réussit."""
    uri = depuis_lien(genre, lien)
    _, hote, _, cle = analyser_uri(uri)
    if not secret or (genre == "odk" and not utilisateur):
        raise ValueError("Indiquez le jeton d'API KoboToolbox." if genre == "kobo"
                         else "Indiquez l'e-mail et le mot de passe du compte ODK Central.")
    creds = (utilisateur or "jeton", secret)
    df, err = lire(uri, allow_private=allow_private, creds=creds)
    if err:
        return {"error": err}
    dhis2.save_credentials(cle, *creds)
    return {"uri": uri, "name": nom_source(uri), "rows": int(len(df)), "colonnes": int(df.shape[1]),
            "repetes_ecartes": df.attrs.get("repetes_ecartes", [])}
