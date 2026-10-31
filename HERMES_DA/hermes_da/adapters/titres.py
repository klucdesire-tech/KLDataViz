# -*- coding: utf-8 -*-
"""Titres-messages : Adifa’ écrit la phrase, le navigateur y met les chiffres.

Elle ne voit que les noms de colonnes et ne renvoie que des gabarits à variables : aucun chiffre ne vient d'elle, et
chaque titre se recalcule à chaque filtre. Un gabarit refusé laisse la place à la formule du moteur."""
import json
import re
import unicodedata

MAX_TITRE = 110
MAX_JETONS = 8000  # le raisonnement du modèle compte : à 1500, il s'arrête sans avoir écrit sa réponse

# La formule du moteur, seule source des titres : Adifa’ la reformule sans perdre une seule variable.
FORMULES = {
    "courbe": "{mesure} : {dernier} en {periode}, {evolution} par rapport à {precedente}",
    "classement": "{premier} : valeur la plus haute ({valeur}), devant {second} ({valeur2})",
    "anneau": "{premier} représente {part} du total",
    "treemap": "{premier} représente {part} du total",
    "chaleur": "Valeur la plus haute : {groupe} en {periode} ({valeur})",
    "pente": "{bilan} ; plus fort écart : {premier} ({ecart})",
    "passage": "{mesure} {evolution} entre {annee_a} et {annee_b}, principal écart : {premier} ({ecart})",
    "cible": "{bilan} ; ensemble : {taux}",
    "bulles": "{mesure} et {mesure_y} {lien}",
    "multiples": "{premier} : valeur la plus haute en {periode} ({valeur})",
    "histogramme": "Valeurs les plus fréquentes : {de} à {a} ({part})",
}
CIBLE_SEULE = "{taux} de l'objectif réalisé"
SENS = {
    "dernier": "la valeur de la dernière période complète", "evolution": "le sens et l'ampleur de la variation, ex. « en hausse de 3 % »",
    "periode": "la période concernée", "precedente": "la période de comparaison", "premier": "le groupe concerné",
    "valeur": "sa valeur", "second": "le groupe suivant", "valeur2": "sa valeur", "part": "un pourcentage du total", "groupe": "le groupe concerné",
    "bilan": "un constat complet déjà rédigé, à reprendre tel quel", "ecart": "son écart signé", "annee_a": "l'année de départ",
    "annee_b": "l'année d'arrivée", "taux": "le pourcentage de l'objectif", "lien": "le lien entre les deux mesures, verbe compris",
    "de": "le bas de la tranche", "a": "le haut de la tranche",
}
# Mots qui affirmeraient un sens, un jugement ou une quantité : vrais pour un filtre, faux pour un autre.
INTERDITS = re.compile(
    r"\b(?:hauss|baiss|progress|recul|augment|diminu|chut|bond|grimp|flamb|record|meilleur|pire|mauvais|bon\b|bonne|forts?\b|fortes?\b|faible"
    r"|largement|net\b|nette|nettement|excellent|inquiet|alarm|critiq|domin|leader|champion|seul|toujours|jamais|tous\b|tout|aucun|majorit|minorit"
    r"|moins|dernier|derniere|queue|atteint|depass|manqu|retard|insuffis|stable|stagn|excede|ne\b|pas\b"
    r"|moitie|tiers|quarts?\b|demi|double|triple|quadruple|fois|pres\b|presque|environ|trop\b|peu\b|autant|encore|mieux|beaucoup|grande?s?\b|petite?s?\b|gros\b"
    r"|enorme|modeste|seulement|uniquement|deja|souvent|rarement|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|vingt|trente"
    r"|quarante|cinquante|soixante|cent\b|cents\b|mille|million|milliard|pourcent)|\bn'|(?<!\ble )(?<!\bla )\bplus\b")

SYSTEM = """Tu es Adifa’, analyste de données. Tu reçois la formule que le moteur utilise pour le titre de chaque graphique d'un tableau de bord, et tu la reformules en français naturel, comme un analyste qui dit ce qu'il faut retenir.
Tu ne connais pas les chiffres : le moteur les calcule et ils changent à chaque filtre. Les variables entre accolades, par exemple {premier}, restent donc telles quelles.
Réponds en JSON strict, sans aucun texte autour : {"titres":{"0":"...","1":"..."}}.
Règles :
- Garde toutes les variables de la formule, et n'en ajoute aucune.
- N'écris aucun chiffre ni aucune date.
- N'écris aucun sens d'évolution ni jugement (hausse, baisse, fort, faible, meilleur, atteint, seul, aucun, négation) : les variables les portent.
- Une valeur haute n'est pas forcément une bonne nouvelle (décès, ruptures, retards) : pas de mot flatteur pour ces mesures.
- Reprends le vocabulaire métier des colonnes, au bon genre et au bon nombre. Français simple, sans jargon, 90 caractères au plus.
- Si la formule est déjà bonne, renvoie-la telle quelle."""


def formule(carte):
    return CIBLE_SEULE if carte["type"] == "cible" and not carte.get("dimension") else FORMULES[carte["type"]]


def variables(carte):
    """Les variables qu'un gabarit doit reprendre : les noms de mesures, eux, s'écrivent en toutes lettres."""
    return set(re.findall(r"\{(\w+)\}", formule(carte))) - {"mesure", "mesure_y"}


def cle(carte):
    return "|".join(str(carte.get(k) or "") for k in ("type", "mesure", "dimension"))


def _cartes(plan):
    return [c for ligne in plan.get("lignes", []) for c in ligne]


def _plat(texte):
    return "".join(x for x in unicodedata.normalize("NFD", texte.lower()) if unicodedata.category(x) != "Mn")


def valider(gabarit, carte, autres=()):
    """Le gabarit accepté, ou None : variables exactes, aucun chiffre étranger aux colonnes, aucune affirmation en dur.
    `autres` : les colonnes des autres cartes, qu'un titre ne doit pas nommer."""
    if not isinstance(gabarit, str) or carte.get("type") not in FORMULES:
        return None
    t = " ".join(gabarit.split())
    noms = set(re.findall(r"\{(\w+)\}", t))
    reste = _plat(re.sub(r"\{\w+\}", " ", t))
    siennes = [str(carte.get(k) or "") for k in ("mesure", "dimension", "mesure_y", "objectif")]
    connus = set(re.findall(r"\w+", _plat(" ".join(siennes))))
    sans_noms = re.sub(r"\w+", lambda m: "" if m.group(0) in connus else m.group(0), reste)  # une colonne « retards » reste nommable
    if not 8 <= len(t) <= MAX_TITRE or re.search(r"[{}<>]", reste) or INTERDITS.search(sans_noms):
        return None
    if noms != variables(carte):
        return None
    # \w couvre aussi les chiffres non ASCII et les fractions comme « ⅓ »
    if any(m not in connus and any(unicodedata.category(x).startswith("N") for x in m) for m in re.findall(r"\w+", reste)):
        return None
    etrangers = {_plat(a).replace("_", " ") for a in autres} - {_plat(a).replace("_", " ") for a in siennes}
    return None if any(re.search(r"\b" + re.escape(a) + r"\b", reste.replace("_", " ")) for a in etrangers if a) else t


def _mesures(plan):
    return [c[k] for c in _cartes(plan) for k in ("mesure", "mesure_y") if c.get(k)]


def _lire(brut):
    debut, fin = (brut or "").find("{"), (brut or "").rfind("}")
    return json.loads(brut[debut:fin + 1], strict=False) if 0 <= debut < fin else {}


def _en_clair(carte):
    nom = lambda c: str(carte.get(c) or "").replace("_", " ")  # noqa: E731
    return formule(carte).replace("{mesure_y}", nom("mesure_y")).replace("{mesure}", nom("mesure"))


def demander(plan, llm):
    """Les gabarits valides d'Adifa’, par carte. Sans IA ou en cas d'échec : rien, et le moteur formule seul."""
    cartes = _cartes(plan)
    if not llm or not cartes:
        return {}
    demande = [{"id": str(i), "graphique": c["type"], "mesure": c.get("mesure"), "dimension": c.get("dimension"), "deuxieme_mesure": c.get("mesure_y"),
                "formule": _en_clair(c), "variables": {v: SENS[v] for v in sorted(variables(c))}} for i, c in enumerate(cartes)]
    try:
        recu = (_lire(llm([{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": json.dumps(demande, ensure_ascii=False, default=str)}], MAX_JETONS)) or {}).get("titres") or {}
    except Exception:  # noqa: BLE001 - un titre ne doit jamais empêcher la page de se construire
        return {}
    valides = {cle(c): valider(recu.get(str(i)), c, _mesures(plan)) for i, c in enumerate(cartes)}
    return {k: v for k, v in valides.items() if v}


def appliquer(plan, gabarits):
    """Pose la formule et le gabarit sur chaque carte. Gabarits revalidés : ils reviennent aussi d'un tableau enregistré."""
    for c in _cartes(plan):
        c["formule"] = formule(c)
        t = valider((gabarits or {}).get(cle(c)), c, _mesures(plan))
        if t:
            c["titre_ia"] = t
    return plan
