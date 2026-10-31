# -*- coding: utf-8 -*-
"""Composition d'un tableau de bord : choisit un modèle, jusqu'à 8 graphiques, et leur place sur 12 colonnes.

Part des propositions du catalogue et n'en invente aucune. Déterministe : à fichier et vedettes égaux, page égale.
Chaque ligne de la grille fait exactement 12 colonnes, sur toutes les pages, quel que soit le nombre de cartes."""
from .catalogue import proposer

MAX_CARTES = 8
MESURES_CLES = 3     # au-delà, la page s'éparpille ; en deçà, elle répète la même mesure
LARGEUR = 12

# Ordre de lecture de chaque modèle : la question la plus utile d'abord. « a|b » : le premier disponible.
MODELES = {
    "pilotage": ["cible", "passage", "treemap|anneau", "courbe", "bulles", "comparee", "classement", "chaleur"],
    "territoire": ["courbe", "classement", "chaleur", "pente", "passage", "treemap|anneau", "comparee", "bulles"],
    "evolution": ["courbe", "chaleur", "comparee", "treemap|anneau", "classement", "histogramme"],
    "repartition": ["classement", "treemap|anneau", "bulles", "histogramme", "classement"],
}
# Largeur souhaitée : le temps se lit à l'horizontale, un classement tient en colonne.
LARGEURS = {"courbe": 8, "chaleur": 8, "comparee": 6, "passage": 6, "treemap": 7, "bulles": 5, "classement": 4, "anneau": 4,
            "pente": 4, "cible": 4, "histogramme": 4}
# En dessous, la carte n'est plus lisible : elle passe à la ligne.
MINIMA = {"courbe": 6, "chaleur": 7, "comparee": 5, "passage": 5, "treemap": 5}
MINIMUM = 4
# Ces graphiques répondent à la même question sur une même mesure et une même dimension : un seul à la fois.
MEME_REPONSE = {"classement", "anneau", "treemap"}


def choisir_modele(fiche):
    c = fiche["capacites"]
    if c["objectif"]:
        return "pilotage"
    if c["evolution"] and c["geographie"]:
        return "territoire"
    if c["evolution"]:
        return "evolution"
    return "repartition"


def _vedettes(fiche, props, df, vedettes=()):
    """Mesure et dimension principales. La mesure liée à un objectif prime, puis le choix d'Adifa’, puis la première colonne additive."""
    cible = next((p["mesure"] for p in props if p["type"] == "cible"), None)
    mesures = [p["mesure"] for p in props if p.get("mesure")]
    choisie = next((v for v in vedettes if v in mesures), None)
    mesure = cible or choisie or (fiche["volumes"] or [None])[0] or (mesures[0] if mesures else None)
    dims = list(dict.fromkeys(p["dimension"] for p in props if p.get("dimension")))
    # Dans une hiérarchie, le niveau intermédiaire : ni deux régions, ni trente centres.
    dimension = next((d for d in dims if d in fiche["lieux"][1:]), None) if len(fiche["lieux"]) > 2 else None
    lisible = next((d for d in dims if 3 <= df[d].nunique(dropna=True) <= 12), None)  # deux zones ne structurent pas une page
    return mesure, dimension or lisible or (dims[0] if dims else None)


def _mesures_cles(fiche, mesure):
    autres = [c for c in fiche["volumes"] + fiche["taux"] + fiche["moyennes"] if c != mesure]
    return ([mesure] if mesure else []) + autres[:MESURES_CLES - 1]


def _rang(p, cles, dimension, deja):
    """Les deux premières cartes posent la mesure principale, les suivantes en montrent d'autres."""
    m = p.get("mesure")
    usage = deja["usages"].get(m, 0) if len(deja["paires"]) >= 2 else 0
    return (m not in cles, usage, cles.index(m) if m in cles else 9, p.get("dimension") != dimension,
            (m, p.get("dimension")) in deja["paires"])


def _doublon(p, choisis):
    for c in choisis:
        memes = p.get("mesure") == c.get("mesure") and p.get("dimension") == c.get("dimension")
        if memes and (p["type"] == c["type"] or {p["type"], c["type"]} <= MEME_REPONSE):
            return True
    return False


def selectionner(fiche, props, modele, df, vedettes=()):
    mesure, dimension = _vedettes(fiche, props, df, vedettes)
    cles = _mesures_cles(fiche, mesure)
    deja = {"usages": {}, "paires": set()}
    choisis = []
    for case in MODELES[modele]:
        if len(choisis) >= MAX_CARTES:
            break
        for genre in case.split("|"):
            permis = [p for p in props if p["type"] == genre and not _doublon(p, choisis)]
            if permis:
                p = min(permis, key=lambda x: _rang(x, cles, dimension, deja))
                choisis.append(p)
                deja["usages"][p.get("mesure")] = deja["usages"].get(p.get("mesure"), 0) + 1
                deja["paires"].add((p.get("mesure"), p.get("dimension")))
                break
    return choisis, mesure, dimension


def _etaler(ligne, reste):
    """Partage la place restante à parts égales : deux cartes seules font 6 et 6, pas 4 et 8."""
    i = 0
    while reste > 0:
        ligne[i % len(ligne)]["span"] += 1
        reste -= 1
        i += 1


def placer(cartes):
    """Range les cartes en lignes de 12 colonnes. Une carte se réduit jusqu'à son minimum avant de passer à la ligne."""
    lignes, ligne, reste = [], [], LARGEUR
    for c in cartes:
        voulue, minimum = LARGEURS[c["type"]], MINIMA.get(c["type"], MINIMUM)
        if ligne and min(voulue, reste) < minimum:
            _etaler(ligne, reste)
            lignes.append(ligne)
            ligne, reste = [], LARGEUR
        span = min(voulue, reste)
        ligne.append(dict(c, span=span))
        reste -= span
    if ligne:
        _etaler(ligne, reste)
        lignes.append(ligne)
    return lignes


def composer(fiche, df, vedettes=(), exclus=()):
    """Le plan de la page : modèle, vedettes et lignes de cartes. Sans proposition, une page vide et explicite.
    `vedettes` : mesures proposées par Adifa’, la plus importante d'abord ; seules celles qui ont un graphique comptent.
    `exclus` : graphiques que la page ne pourra pas dessiner."""
    props = [p for p in proposer(fiche, df) if p["type"] not in exclus]
    modele = choisir_modele(fiche)
    cartes, mesure, dimension = selectionner(fiche, props, modele, df, vedettes)
    return {"version": 2, "modele": modele, "mesure": mesure, "dimension": dimension, "lignes": placer(cartes), "cartes": len(cartes)}
