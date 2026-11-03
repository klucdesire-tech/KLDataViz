# -*- coding: utf-8 -*-
"""Pseudonymisation des données personnelles avant tout envoi à l'IA.

Tout appel à l'IA passe par `server._llm_call` : les valeurs des colonnes qui identifient une personne (nom, téléphone,
e-mail, adresse, numéro de dossier ou de patient, date de naissance, texte libre) y sont remplacées par des jetons
⟦P12⟧, et la réponse est remise en clair au retour. L'utilisateur voit ses données ; le fournisseur d'IA, jamais.

Les valeurs sont enregistrées à la lecture d'un fichier, pour l'utilisateur courant et non pour la requête : un résumé
d'analyse envoyé plus tard, depuis une autre requête, contient encore les valeurs les plus fréquentes de chaque colonne.

Ce qui part encore : les noms de colonnes, les catégories (district, sexe, tranche d'âge), les nombres et les dates
qui n'identifient personne. Un identifiant numérique de moins de 6 chiffres n'est pas masqué : il se confondrait
avec un effectif ou un montant.
"""
import re
import threading
import unicodedata

import tenancy as T

JETON = re.compile(r"⟦P(\d+)⟧")
MAX_VALEURS = 200_000
MIN_CHIFFRES = 6

# Mots qui désignent à eux seuls une donnée personnelle
_IDENTIFIANT = re.compile(
    r"(^| )(nom|noms|prenom|prenoms|name|surname|firstname|lastname|fullname|telephone|tel|phone|mobile|portable|gsm|"
    r"whatsapp|email|mail|courriel|adresse|address|domicile|cni|passeport|passport|piece identite|matricule|code patient|"
    r"id patient|patient id|numero patient|num dossier|numero dossier|nir|nin|secu|naissance|date de naissance|dob|"
    r"birth|birthdate|gps|latitude|longitude|coordonnees)( |$)")
# Mots qui ne désignent une personne que si la colonne en compte beaucoup : « client_fidelite » est un Oui/Non
_IDENTIFIANT_FAIBLE = re.compile(r"(^| )(patient|patients|beneficiaire|client|clients|contact|identifiant|dossier|personne|agent)( |$)")
_DATE = re.compile(r"^\d{4}[-/.]\d{1,2}([-/.]\d{1,2})?([ T].*)?$|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$|^\d{4}\s*[-/]\s*\d{4}$")
# « nom du district », « nom_site » : un lieu ou un objet, pas une personne
_PAS_UNE_PERSONNE = re.compile(
    r"(^| )(district|site|region|centre|etablissement|structure|formation|fs|ville|commune|village|localite|agence|"
    r"magasin|produit|article|indicateur|programme|service|projet|ecole|classe|direction|departement|poste|categorie|"
    r"partenaire|bailleur|fournisseur|banque|zone|pays|province|prefecture|sous prefecture|ministere|hopital|clinique|"
    r"pharmacie|laboratoire|entreprise|societe|organisation|ong|cooperative)( |$)")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_TELEPHONE = re.compile(r"^\+?[\d][\d .\-]{7,}$")
_NOM_PERSONNE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ'’\-]+( [A-Za-zÀ-ÖØ-öø-ÿ'’\-]+){1,3}$")
_verrou = threading.Lock()


def _norme(texte) -> str:
    t = unicodedata.normalize("NFKD", str(texte)).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def colonnes_identifiantes(df) -> list:
    """Colonnes dont les valeurs désignent une personne, par leur nom ou par leur contenu."""
    import pandas as pd
    out = []
    n = len(df)
    for c in df.columns:
        nom = _norme(c)
        s = df[c].dropna()
        if s.empty:
            continue
        if _IDENTIFIANT.search(nom) and not _PAS_UNE_PERSONNE.search(nom):
            out.append(c)
            continue
        if pd.api.types.is_bool_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
            continue
        distincts = s.nunique()
        if (_IDENTIFIANT_FAIBLE.search(nom) and not _PAS_UNE_PERSONNE.search(nom) and distincts >= 20 and distincts >= .3 * n
                and not pd.api.types.is_float_dtype(s)):
            out.append(c)  # un patient, un client par ligne : son nom ou son code
            continue
        if pd.api.types.is_numeric_dtype(s):
            continue
        texte = s.astype(str).str.strip()
        echantillon = texte.head(2000)
        telephone = lambda v: bool(_TELEPHONE.match(v)) and not _DATE.match(v) and sum(ch.isdigit() for ch in v) >= 8  # noqa: E731
        if echantillon.map(lambda v: bool(_EMAIL.match(v))).mean() > .3 or echantillon.map(telephone).mean() > .5:
            out.append(c)
            continue
        if distincts >= 20 and distincts >= .5 * n and echantillon.map(lambda v: bool(_NOM_PERSONNE.match(v))).mean() > .7:
            out.append(c)  # une personne par ligne : des noms et prénoms
            continue
        if echantillon.str.len().mean() > 40 and distincts >= 20 and distincts >= .3 * n:
            out.append(c)  # texte libre : commentaires, observations, où des noms se glissent ; pas un libellé répété
    return out


def _formes(v) -> set:
    """Écritures d'une valeur telle qu'elle peut réapparaître dans un texte envoyé à l'IA."""
    import pandas as pd
    if isinstance(v, (pd.Timestamp,)):
        return {v.strftime("%Y-%m-%d"), v.strftime("%d/%m/%Y"), str(v)}
    if isinstance(v, float) and v == int(v):
        v = int(v)
    s = str(v).strip()
    if not s:
        return set()
    chiffres = sum(ch.isdigit() for ch in s)
    if s.replace(" ", "").replace("+", "").replace(".", "").replace("-", "").isdigit() and chiffres < MIN_CHIFFRES:
        return set()  # « 12 » serait masqué partout : un effectif, un âge, un montant
    if len(s) < 3:
        return set()
    formes = {s}
    if s.isdigit() and s.startswith("0"):
        formes.add(s.lstrip("0"))  # 0707… lu comme nombre : 707…
    return formes


def _registre() -> dict:
    st = T.state()
    with _verrou:
        return st.setdefault("pseudonymes", {"vers_jeton": {}, "vers_valeur": {}, "motif": None})


def enregistrer(df) -> int:
    """Enregistre les valeurs identifiantes d'un tableau pour l'utilisateur courant. Renvoie le nombre de colonnes concernées."""
    if df is None:
        return 0
    try:
        colonnes = colonnes_identifiantes(df)
    except Exception:
        return 0
    if not colonnes:
        return 0
    reg = _registre()
    with _verrou:
        for c in colonnes:
            for v in df[c].dropna().unique()[:MAX_VALEURS]:
                for f in _formes(v):
                    if f not in reg["vers_jeton"] and len(reg["vers_jeton"]) < MAX_VALEURS:
                        jeton = f"⟦P{len(reg['vers_valeur']) + 1}⟧"
                        reg["vers_jeton"][f], reg["vers_valeur"][jeton] = jeton, f
        reg["motif"] = None  # à recompiler
    return len(colonnes)


def _motif(reg):
    if reg["motif"] is None and reg["vers_jeton"]:
        valeurs = sorted(reg["vers_jeton"], key=len, reverse=True)
        reg["motif"] = re.compile(r"(?<![\w])(?:" + "|".join(map(re.escape, valeurs)) + r")(?![\w])")
    return reg["motif"]


def masquer(texte):
    """Remplace les valeurs identifiantes connues par leur jeton."""
    if not isinstance(texte, str) or not texte:
        return texte
    reg = _registre()
    with _verrou:
        motif = _motif(reg)
        if motif is None:
            return texte
        return motif.sub(lambda m: reg["vers_jeton"][m.group(0)], texte)


def demasquer(texte):
    """Remet en clair les jetons d'une réponse de l'IA, pour l'utilisateur."""
    if not isinstance(texte, str) or "⟦P" not in texte:
        return texte
    reg = _registre()
    return JETON.sub(lambda m: reg["vers_valeur"].get(m.group(0), m.group(0)), texte)


def oublier():
    """Vide le registre de l'utilisateur courant (tâche planifiée qui change d'espace)."""
    T.state().pop("pseudonymes", None)
