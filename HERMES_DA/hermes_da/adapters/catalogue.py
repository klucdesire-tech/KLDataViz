# -*- coding: utf-8 -*-
"""Catalogue de graphiques : ce que chacun exige, et ce qui l'interdit.

`proposer(fiche, df)` liste tout ce que les données permettent honnêtement de montrer, sans rien calculer pour l'affichage
et sans choisir la mise en page : ce choix revient à l'étape suivante. Une règle absente vaut interdiction."""
import pandas as pd

MAX_PARTS = 5        # au-delà, un anneau ne se lit plus : treemap ou classement
MAX_TREEMAP = 40
MAX_CLASSEMENT = 30
MAX_LIGNES_CHALEUR = 25
MIN_COLONNES_CHALEUR = 6
MAX_PENTES = 8
MIN_POINTS_COURBE = 3
MIN_VALEURS_HISTO = 20
MIN_DISTINCT_HISTO = 8
MIN_POINTS_BULLES = 5
MAX_POINTS_BULLES = 60
MAX_COMPAREES = 6  # au-delà, les courbes superposées ne se suivent plus
RATIO_OBJECTIF = (0.5, 2.0)

GRAPHIQUES = {
    "courbe": "Comment ça évolue dans le temps ?",
    "comparee": "L'évolution est-elle la même partout ?",
    "chaleur": "Quand et où se concentrent les valeurs ?",
    "classement": "Qui arrive en tête, qui ferme la marche ?",
    "anneau": "Comment le total se partage-t-il entre peu de groupes ?",
    "treemap": "Comment le total se partage-t-il entre beaucoup de groupes ?",
    "pente": "Qui progresse, qui recule entre deux périodes ?",
    "passage": "Qu'est-ce qui explique la variation du total ?",
    "cible": "Où en est-on par rapport à l'objectif ?",
    "bulles": "Deux mesures vont-elles ensemble ?",
    "histogramme": "Comment les valeurs se répartissent-elles ?",
}


def agregation(nature):
    """Comment regrouper une mesure. Un taux ou une moyenne ne s'additionne jamais, un stock ne se cumule pas dans le temps."""
    return {"volume": "somme", "moyenne": "moyenne", "taux": "moyenne", "stock": "dernier"}[nature]


def _mesures(f):
    return ([(c, "volume") for c in f["volumes"]] + [(c, "taux") for c in f["taux"]]
            + [(c, "moyenne") for c in f["moyennes"]] + [(c, "stock") for c in f["stocks"]])


def _groupes(f, df):
    """Lieux puis catégories, avec leur nombre de valeurs."""
    return [(c, int(df[c].nunique(dropna=True))) for c in f["lieux"] + f["categories"]]


def _sans_negatif(df, c):
    v = pd.to_numeric(df[c], errors="coerce")
    return bool(v.notna().any()) and bool((v.dropna() >= 0).all()) and float(v.sum()) > 0


def relier_objectif(f, df):
    """Associe chaque objectif à sa mesure : par le nom, sinon par l'ordre de grandeur si un seul volume convient."""
    liens = {}
    for o in f["objectifs"]:
        mots = {m for m in str(o).lower().replace("_", " ").split() if len(m) > 3 and m not in ("objectif", "cible", "target", "budget")}
        par_nom = [c for c, _ in _mesures(f) if mots & set(str(c).lower().replace("_", " ").split())]
        if len(par_nom) == 1:
            liens[o] = par_nom[0]
            continue
        total = pd.to_numeric(df[o], errors="coerce").sum()
        proches = [c for c in f["volumes"] if total > 0 and RATIO_OBJECTIF[0] <= pd.to_numeric(df[c], errors="coerce").sum() / total <= RATIO_OBJECTIF[1]]
        if len(proches) == 1:
            liens[o] = proches[0]
    return liens


def _prop(genre, mesure=None, nature=None, **extra):
    p = {"type": genre, "question": GRAPHIQUES[genre], "mesure": mesure}
    if nature:
        p.update(nature=nature, agregation=agregation(nature))
    p.update(extra)
    return p


def _groupes_renseignes(df, mesure, groupe):
    """Groupes qui ont vraiment une valeur : une recette n'existe que sur les lignes de type recette."""
    v = pd.to_numeric(df[mesure], errors="coerce")
    return int((v.groupby(df[groupe]).sum(min_count=1).fillna(0) != 0).sum())


def proposer(fiche, df):
    """Tous les graphiques permis par le fichier, sans doublon, du plus simple au plus spécialisé."""
    f, sortie = fiche, []
    if df.empty:
        return sortie
    per = f["periode"]
    mesures, groupes = _mesures(f), _groupes(f, df)
    for c, nature in mesures:
        if per and per["valeurs"] >= MIN_POINTS_COURBE:
            sortie.append(_prop("courbe", c, nature, periode=per["colonne"]))
        for g, n in groupes:
            if _groupes_renseignes(df, c, g) < 2:  # une carte que la page ne pourrait pas remplir
                continue
            if per and per["valeurs"] >= MIN_COLONNES_CHALEUR and 2 <= n <= MAX_LIGNES_CHALEUR and nature != "stock":
                sortie.append(_prop("chaleur", c, nature, periode=per["colonne"], dimension=g))
            if per and per["valeurs"] >= MIN_POINTS_COURBE and 2 <= n <= MAX_COMPAREES:
                sortie.append(_prop("comparee", c, nature, periode=per["colonne"], dimension=g))
            if 3 <= n <= MAX_CLASSEMENT:
                sortie.append(_prop("classement", c, nature, dimension=g))
            if per and per["comparable_n1"] and 2 <= n <= MAX_PENTES and nature != "stock":
                sortie.append(_prop("pente", c, nature, periode=per["colonne"], dimension=g, mois_communs=per["mois_communs"]))
            if nature == "volume" and _sans_negatif(df, c):  # seul un volume positif se partage en parts d'un total
                if 3 <= n <= MAX_PARTS:  # deux parts se disent en un chiffre, pas en anneau
                    sortie.append(_prop("anneau", c, nature, dimension=g))
                elif MAX_PARTS < n <= MAX_TREEMAP:
                    sortie.append(_prop("treemap", c, nature, dimension=g))
                if per and per["comparable_n1"] and 2 <= n <= MAX_PENTES:
                    sortie.append(_prop("passage", c, nature, periode=per["colonne"], dimension=g, mois_communs=per["mois_communs"]))
        v = pd.to_numeric(df[c], errors="coerce").dropna()
        if nature == "moyenne" and not f["agrege"] and len(v) >= MIN_VALEURS_HISTO and v.nunique() >= MIN_DISTINCT_HISTO:
            sortie.append(_prop("histogramme", c, nature))
    for o, c in relier_objectif(f, df).items():
        sortie.append(_prop("cible", c, dict((m, n) for m, n in mesures)[c], objectif=o,
                            dimension=next((g for g, n in groupes if 2 <= n <= MAX_CLASSEMENT), None)))
    for g, n in groupes:
        if MIN_POINTS_BULLES <= n <= MAX_POINTS_BULLES and len(mesures) >= 2:
            sortie.append(_prop("bulles", mesures[0][0], mesures[0][1], mesure_y=mesures[1][0], nature_y=mesures[1][1],
                                agregation_y=agregation(mesures[1][1]), dimension=g))
    return sortie


def types_proposes(fiche, df):
    return sorted({p["type"] for p in proposer(fiche, df)})
