# -*- coding: utf-8 -*-
"""Titres-messages : le moteur écrit la phrase et calcule les chiffres, Adifa’ donne leur nom métier aux colonnes.

Une phrase libre peut devenir fausse après un filtre, et aucune liste de mots ne l'empêche. Un nom ne dit rien des
valeurs : c'est donc tout ce qu'on lui demande, à partir des seuls noms de colonnes."""
import json
import re
import unicodedata

MAX_LIBELLE = 45
MAX_MOTS = 6
MAX_JETONS = 8000  # le raisonnement du modèle compte : à 1500, il s'arrête sans avoir écrit sa réponse

# La seule source des titres. Les variables sont calculées par la page à chaque filtre.
FORMULES = {
    "courbe": "{mesure} : {dernier} en {periode}, {evolution} par rapport à {precedente}",
    "classement": "{mesure} : valeur la plus haute pour {premier} ({valeur}), devant {second} ({valeur2})",
    "anneau": "{mesure} : {premier} représente {part} du total",
    "treemap": "{mesure} : {premier} représente {part} du total",
    "chaleur": "{mesure} : valeur la plus haute pour {groupe} en {periode} ({valeur})",
    "pente": "{mesure} : {bilan}, plus fort écart pour {premier} ({ecart})",
    "passage": "{mesure} : {evolution} entre {annee_a} et {annee_b}, principal écart pour {premier} ({ecart})",
    "cible": "{mesure} : {bilan}, ensemble à {taux}",
    "bulles": "{mesure} et {mesure_y} {lien}",
    "comparee": "{mesure} : valeur la plus haute pour {premier} en {periode} ({valeur})",
    "histogramme": "{mesure} : valeurs les plus fréquentes de {de} à {a} ({part})",
}
CIBLE_SEULE = "{mesure} : {taux} de l'objectif"

# Le nom devient le sujet du titre : ni sens, ni jugement, ni unité. Les mots de la colonne restent permis (« retards », « part_marche »).
INTERDITS = re.compile(
    r"\b(?:hauss|baiss|progress|recul|augment|diminu|chut|croiss|decroiss|bond|grimp|flamb|record|meilleur|pire|mauvais|bon\b|bonne"
    r"|forts?\b|fortes?\b|faibles?\b|nets?\b|nettes?\b|largement|excellent|inquiet|alarm|critiq|tres\b|trop\b|peu\b|plus\b|moins\b"
    r"|double|triple|fois\b|environ|presque|moitie|tiers\b|quarts?\b|demi|dizaine|douzaine|centaine|millier|mille\b|million|milliard"
    r"|pourcent|parts?\b|par\b)")

SYSTEM = """Tu es Adifa’, analyste de données. Tu reçois des noms de colonnes d'un fichier. Pour chacune, donne le nom qu'un analyste écrirait dans le titre d'un graphique.
Réponds en JSON strict, sans aucun texte autour : {"libelles":{"nom_de_colonne":"nom clair"}}.
Règles :
- Un groupe nominal court, six mots au plus, sans article, sans verbe, sans ponctuation.
- En minuscules, sauf les sigles et les noms propres.
- Développe un sigle seulement si son sens est certain dans ce domaine ; sinon, garde-le tel quel.
- Aucun chiffre, aucun jugement, aucune idée d'évolution.
- Si le nom est déjà clair, renvoie-le tel quel."""


def formule(carte):
    return CIBLE_SEULE if carte["type"] == "cible" and not carte.get("dimension") else FORMULES[carte["type"]]


def _cartes(plan):
    return [c for ligne in plan.get("lignes", []) for c in ligne]


def _colonnes(plan):
    return list(dict.fromkeys(c[k] for c in _cartes(plan) for k in ("mesure", "mesure_y", "dimension") if c.get(k)))


def _plat(texte):
    return "".join(x for x in unicodedata.normalize("NFD", texte.lower()) if unicodedata.category(x) != "Mn")


def valider(libelle, colonne):
    """Le nom accepté, ou None : un groupe nominal court, sans chiffre étranger à la colonne ni idée de sens."""
    if not isinstance(libelle, str):
        return None
    t = " ".join(libelle.split())
    mots = re.findall(r"\w+", _plat(t))
    connus = set(re.findall(r"[^\W_]+", _plat(str(colonne))))
    if not 2 <= len(t) <= MAX_LIBELLE or len(t.split()) > MAX_MOTS or re.search(r"[{}<>\"«»:;!?.,/()=+%]", t):
        return None
    reste = " ".join(m for m in mots if m not in connus)
    if INTERDITS.search(reste) or any(unicodedata.category(x).startswith("N") for x in reste):
        return None
    return t


def _lire(brut):
    debut, fin = (brut or "").find("{"), (brut or "").rfind("}")
    return json.loads(brut[debut:fin + 1], strict=False) if 0 <= debut < fin else {}


def _valides(libelles, colonnes):
    if not isinstance(libelles, dict):
        return {}
    noms = {str(c): valider(libelles.get(str(c)), c) for c in colonnes}
    return {k: v for k, v in noms.items() if v}


def demander(plan, llm):
    """Les noms métier valides, par colonne. Sans IA ou en cas d'échec : rien, et les titres gardent les noms du fichier."""
    colonnes = _colonnes(plan)
    if not llm or not colonnes:
        return {}
    try:
        recu = _lire(llm([{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": json.dumps([str(c) for c in colonnes], ensure_ascii=False)}], MAX_JETONS))
    except Exception:  # noqa: BLE001 - un nom ne doit jamais empêcher la page de se construire
        return {}
    return _valides(recu.get("libelles") if isinstance(recu, dict) else None, colonnes)


def appliquer(plan, libelles):
    """Pose les formules sur les cartes et les noms sur le plan, revalidés : ils reviennent aussi d'un tableau enregistré."""
    for c in _cartes(plan):
        c["formule"] = formule(c)
    plan["libelles"] = _valides(libelles, _colonnes(plan))
    return plan
