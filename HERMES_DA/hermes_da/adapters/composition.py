# -*- coding: utf-8 -*-
"""Composition d'un tableau de bord : choisit un modèle, jusqu'à 8 graphiques, et leur place sur 12 colonnes.

Part des propositions du catalogue et n'en invente aucune. Le modèle fixe la première carte (la question de base :
l'objectif, l'évolution ou le classement) ; les suivantes sont celles que les données rendent parlantes (`interet`),
la plus parlante d'abord. Deux fichiers de même forme n'ont donc plus la même page. Déterministe : à fichier et
vedettes égaux, page égale. Chaque ligne de la grille fait exactement 12 colonnes, quel que soit le nombre de cartes."""
from . import interet
from .catalogue import proposer

MAX_CARTES = 8
MIN_CARTES = 3       # une page qui n'a rien de saillant montre quand même l'essentiel
PLANCHER = 0.1       # sous cette note, même pour compléter : le graphique ne dirait rien
# Pour compléter une page sans rien de saillant : des vues neutres, jamais un graphique qui suggère un motif absent
SOCLE = {"courbe", "classement", "anneau", "treemap", "histogramme"}
MAX_PAR_TYPE = 2     # au-delà, la page répète la même question
# « interet » : les graphiques que les données rendent parlants. « modele » : l'ordre fixe de chaque modèle, gardé pour
# les tests de dessin, qui ont besoin d'obtenir à coup sûr un type de graphique donné sur un petit fichier.
CHOIX = "interet"
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
        # Courbes en base 100, parts par indicateur : chaque volume emmène les autres. Deux de ces cartes (même type,
        # même dimension) traceraient les mêmes séries.
        if p["type"] == c["type"] and p["type"] in ("courbe", "anneau") and p.get("dimension") == c.get("dimension") \
                and (p.get("ensemble") or c.get("ensemble")) \
                and ({p.get("mesure")} | set(p.get("ensemble") or [])) & ({c.get("mesure")} | set(c.get("ensemble") or [])):
            return True
        memes = p.get("mesure") == c.get("mesure") and p.get("dimension") == c.get("dimension")
        if memes and (p["type"] == c["type"] or {p["type"], c["type"]} <= MEME_REPONSE):
            return True
    return False


def _ordre(modele, genre):
    """Place du type dans l'ordre de lecture du modèle : départage deux graphiques aussi parlants."""
    for i, case in enumerate(MODELES[modele]):
        if genre in case.split("|"):
            return i
    return len(MODELES[modele])


def _valeur(p, note, cles, dimension, deja):
    """Intérêt de la carte sur cette page : sa note, moins ce qui ferait doublon avec les cartes déjà posées."""
    m = p.get("mesure")
    v = note
    v -= 0.12 * deja["usages"].get(m, 0)                   # une autre mesure plutôt que la même encore
    v -= 0.08 if m not in cles else 0.0                    # les mesures principales d'abord
    v -= 0.05 if p.get("dimension") not in (None, dimension) else 0.0
    v -= 0.2 if (p["type"], p.get("dimension")) in deja["vues"] else 0.0
    v -= 0.6 * deja["types"].get(p["type"], 0)            # une autre question plutôt que le même graphique encore
    return v


def _selon_modele(fiche, props, modele, df, vedettes=()):
    """Ordre fixe du modèle : chaque case prend la première proposition disponible."""
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


def selectionner(fiche, props, modele, df, vedettes=()):
    if CHOIX == "modele":
        return _selon_modele(fiche, props, modele, df, vedettes)
    mesure, dimension = _vedettes(fiche, props, df, vedettes)
    cles = _mesures_cles(fiche, mesure)
    notes = dict(zip(map(id, props), interet.noter(props, fiche, df)))
    deja = {"usages": {}, "paires": set(), "types": {}, "vues": set()}
    choisis = []

    def poser(p):
        choisis.append(p)
        deja["types"][p["type"]] = deja["types"].get(p["type"], 0) + 1
        deja["vues"].add((p["type"], p.get("dimension")))
        deja["usages"][p.get("mesure")] = deja["usages"].get(p.get("mesure"), 0) + 1
        deja["paires"].add((p.get("mesure"), p.get("dimension")))

    # 1. La question de base du modèle, même si les données y sont peu contrastées
    for genre in MODELES[modele][0].split("|"):
        permis = [p for p in props if p["type"] == genre]
        if permis:
            poser(min(permis, key=lambda x: _rang(x, cles, dimension, deja)))
            break
    # 2. Puis les graphiques les plus parlants, en variant les questions
    for plancher in (interet.SEUIL, PLANCHER):
        while len(choisis) < (MAX_CARTES if plancher == interet.SEUIL else MIN_CARTES):
            permis = [(i, p) for i, p in enumerate(props) if p not in choisis and not _doublon(p, choisis)
                      and deja["types"].get(p["type"], 0) < MAX_PAR_TYPE
                      and (plancher == interet.SEUIL or p["type"] in SOCLE)
                      and notes[id(p)] >= plancher and _valeur(p, notes[id(p)], cles, dimension, deja) >= plancher - 0.3]
            if not permis:
                break
            _, p = max(permis, key=lambda ip: (round(_valeur(ip[1], notes[id(ip[1])], cles, dimension, deja), 3),
                                               -_ordre(modele, ip[1]["type"]), -ip[0]))
            poser(p)
    return choisis, mesure, dimension


def _etaler(ligne, reste):
    """Partage la place restante à parts égales : deux cartes seules font 6 et 6, pas 4 et 8."""
    i = 0
    while reste > 0:
        ligne[i % len(ligne)]["span"] += 1
        reste -= 1
        i += 1


def _mini(c):
    return MINIMA.get(c["type"], MINIMUM)


def _loger(ligne, c):
    """Largeur que la carte peut prendre dans la ligne, en resserrant les autres jusqu'à leur minimum ; None si impossible."""
    reste = LARGEUR - sum(x["span"] for x in ligne)
    jeu = sum(x["span"] - _mini(x) for x in ligne)
    if reste + jeu < _mini(c):
        return None
    return max(_mini(c), min(LARGEURS[c["type"]], reste))


def _resserrer(ligne, besoin):
    """Libère `besoin` colonnes, en prenant d'abord aux cartes qui ont le plus de marge."""
    while besoin > 0:
        x = max(ligne, key=lambda y: (y["span"] - _mini(y), -ligne.index(y)))
        x["span"] -= 1
        besoin -= 1


def placer(cartes, regard=2):
    """Range les cartes en lignes de 12 colonnes. Une carte se réduit jusqu'à son minimum avant de passer à la ligne ;
    si la suivante ne loge pas, l'une des `regard` cartes d'après peut prendre sa place dans la ligne."""
    file, lignes = list(cartes), []
    while file:
        c = file.pop(0)
        ligne = [dict(c, span=min(LARGEURS[c["type"]], LARGEUR))]
        while file:
            for i, suivante in enumerate(file[:1 + regard]):
                span = _loger(ligne, suivante)
                if span is not None:
                    _resserrer(ligne, span - (LARGEUR - sum(x["span"] for x in ligne)))
                    ligne.append(dict(suivante, span=span))
                    file.pop(i)
                    break
            else:
                break
        _etaler(ligne, LARGEUR - sum(x["span"] for x in ligne))
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
