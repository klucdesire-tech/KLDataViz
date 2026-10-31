# -*- coding: utf-8 -*-
"""Six jeux de données ENTIÈREMENT FICTIFS, un par forme de fichier que le tableau de bord doit savoir traiter.

Déterministes : mêmes valeurs à chaque exécution. `ATTENDU` décrit ce qu'une lecture correcte doit y trouver,
écrit à la main pour servir de référence indépendante du code qui sera testé."""
import numpy as np
import pandas as pd

MOIS = [str(m) for m in pd.period_range("2024-01", "2025-12", freq="M")]


def sante():
    import exemple
    return exemple.generer()


def ventes():
    rng = np.random.default_rng(2031)
    produits = [("Riz 25 kg", "Alimentaire", 4200, .14), ("Huile 5 L", "Alimentaire", 3100, .16),
                ("Savon carton", "Hygiène", 1700, .31), ("Couches bébé", "Hygiène", 900, .34), ("Eau pack", "Boissons", 1300, .24)]
    lignes = []
    for m in MOIS:
        for z, part in (("Nord", .55), ("Sud", .45)):
            for p, c, base, marge in produits:
                ca = round(base * part * rng.normal(1, .06) * (1.1 if m >= "2025" else 1))
                lignes.append({"mois": m, "zone": z, "produit": p, "categorie": c, "chiffre_affaires": ca,
                               "marge": round(ca * marge), "objectif": round(base * part * 1.05)})
    return pd.DataFrame(lignes)


def dhis2_long():
    rng = np.random.default_rng(2032)
    lignes = []
    for ind, base in (("HTS_TST", 400), ("HTS_TST_POS", 30), ("TX_NEW", 28), ("TX_CURR", 1500)):
        for ou in ("Soubré", "Méagui", "Buyo"):
            for m in MOIS[:12]:
                lignes.append({"indicateur": ind, "periode": m, "unite_organisation": ou, "valeur": int(rng.poisson(base))})
    return pd.DataFrame(lignes)


def formulaire():
    rng = np.random.default_rng(2033)
    lignes = []
    for ind in ("Dépistés", "Positifs"):
        for sexe in ("Femmes", "Hommes"):
            for age in ("15-24", "25-49", "50+"):
                lignes.append({"indicateur": ind, "sexe": sexe, "tranche_age": age,
                               **{s: int(rng.integers(5, 90)) for s in ("Site A", "Site B", "Site C")}})
    return pd.DataFrame(lignes)


def simple():
    return pd.DataFrame({"categorie": ["Loyer", "Salaires", "Transport", "Fournitures", "Énergie", "Divers"],
                         "montant": [450000, 1250000, 180000, 95000, 210000, 60000]})


def sans_dates():
    rng = np.random.default_rng(2034)
    n = 240
    return pd.DataFrame({"id_repondant": [f"R{i:04d}" for i in range(n)],
                         "region": rng.choice(["Abidjan", "Bouaké", "Daloa", "Korhogo"], n),
                         "sexe": rng.choice(["Femme", "Homme"], n), "age": rng.integers(18, 70, n),
                         "satisfaction": rng.integers(1, 6, n), "recommande": rng.choice(["Oui", "Non"], n, p=[.7, .3])})


JEUX = {"sante": sante, "ventes": ventes, "dhis2_long": dhis2_long, "formulaire": formulaire, "simple": simple,
        "sans_dates": sans_dates}

ATTENDU = {
    "sante": {"lignes": 216, "periode": "mois", "lieux": ["region", "district", "centre"], "objectif": None,
              "mesures": ["consultations", "cpn1", "cpn4", "accouchements_assistes", "enfants_vaccines_penta3", "tdr_realises",
                          "cas_paludisme_confirmes"], "stocks": ["population_desservie"], "oui_non": ["rupture_stock_cta"]},
    "ventes": {"lignes": 240, "periode": "mois", "lieux": ["zone"], "objectif": "objectif",
               "mesures": ["chiffre_affaires", "marge"], "stocks": [], "oui_non": []},
    "dhis2_long": {"lignes": 144, "periode": "periode", "lieux": ["unite_organisation"], "objectif": None,
                   "mesures": ["valeur"], "stocks": [], "oui_non": []},
    "formulaire": {"lignes": 12, "periode": None, "lieux": [], "objectif": None, "mesures": ["Site A", "Site B", "Site C"],
                   "stocks": [], "oui_non": []},
    "simple": {"lignes": 6, "periode": None, "lieux": [], "objectif": None, "mesures": ["montant"], "stocks": [], "oui_non": []},
    "sans_dates": {"lignes": 240, "periode": None, "lieux": ["region"], "objectif": None, "mesures": ["satisfaction"],
                   "stocks": [], "oui_non": ["recommande"]},
}
