# -*- coding: utf-8 -*-
"""Fichiers ENTIÈREMENT FICTIFS qui reproduisent les pièges des vrais fichiers de terrain, pour le banc des chiffres.

Chacun a déjà fait afficher un chiffre faux, ou y aurait conduit :
- stock_jour : chaque site déclare sa file active un jour différent du mois ;
- trous : mesures vides, une zone qui n'a pas encore déclaré le dernier mois, taux de 0 à 1, « Oui » et « oui » ;
- negatifs : résultats nets positifs et négatifs ;
- gros : plus de 6 000 lignes, la page passe en résumé exact ;
- communes : 55 groupes, plus que ce qu'un classement affiche ;
- libelles : un même lieu écrit avec des espaces en trop ou une autre casse ;
- totaux : une ligne « Total » par mois sous les districts, comme dans un export Excel.
Déterministes : mêmes valeurs à chaque exécution."""
import numpy as np
import pandas as pd

MOIS = [str(m) for m in pd.period_range("2024-01", "2025-05", freq="M")]   # finit en milieu de trimestre


def stock_jour():
    rng = np.random.default_rng(41)
    return pd.DataFrame([{"date": f"{m}-{5 + 6 * i:02d}", "site": site, "file_active": 300 + 40 * i + int(rng.integers(0, 20)),
                          "nouveaux": int(rng.integers(5, 30))}
                         for m in MOIS for i, site in enumerate(["Site Nord", "Site Sud", "Site Est", "Site Ouest"])])


def trous():
    rng = np.random.default_rng(42)
    lignes = []
    for m in MOIS:
        for z in ["Zone A", "Zone B", "Zone C", "Zone D", "Zone E", "Zone F"]:
            if z == "Zone C" and m == MOIS[-1]:
                continue
            v = int(rng.integers(50, 150))
            lignes.append({"mois": m, "zone": z, "visites": v if rng.random() > .08 else None,
                           "taux_couverture": round(float(rng.uniform(.4, .95)), 3),
                           "satisfait": rng.choice(["Oui", "Non", "oui", "", None], p=[.5, .25, .1, .1, .05])})
    return pd.DataFrame(lignes)


def negatifs():
    rng = np.random.default_rng(43)
    return pd.DataFrame([{"mois": m, "agence": ag, "resultat_net": int(rng.integers(-400, 600)), "charges": int(rng.integers(800, 1500))}
                         for m in MOIS for ag in ["Agence 1", "Agence 2", "Agence 3", "Agence 4", "Agence 5"]])


def gros():
    rng = np.random.default_rng(44)
    mois = [str(p) for p in pd.period_range("2023-01", "2025-05", freq="M")]
    n = len(mois) * 3 * 8 * 12
    return pd.DataFrame({"mois": np.repeat(mois, 3 * 8 * 12),
                         "zone": np.tile(np.repeat(["Nord", "Sud", "Centre"], 8 * 12), len(mois)),
                         "produit": np.tile(np.repeat(["Riz", "Huile", "Savon", "Lait", "Sucre", "Farine", "Thé", "Café"], 12), len(mois) * 3),
                         "client": np.tile([f"Client {c:02d}" for c in range(12)], len(mois) * 3 * 8),
                         "montant": rng.integers(1000, 9000, n), "paye": rng.choice(["Oui", "Non"], n, p=[.8, .2])})


def communes():
    rng = np.random.default_rng(45)
    return pd.DataFrame([{"mois": m, "commune": f"Commune {i:02d}", "naissances": int(rng.integers(10, 400))}
                         for m in MOIS[-6:] for i in range(55)])


def libelles():
    rng = np.random.default_rng(46)
    ecritures = {"Abidjan": ["Abidjan", "Abidjan ", "ABIDJAN"], "Bouaké": ["Bouaké", " Bouaké"], "Daloa": ["Daloa", "daloa"],
                 "Korhogo": ["Korhogo"]}
    lignes = []
    for m in MOIS[-8:]:
        for ville, formes in ecritures.items():
            for j in range(3):
                lignes.append({"mois": m, "ville": formes[(j + int(m[5:])) % len(formes)], "cas": int(rng.integers(20, 90))})
    return pd.DataFrame(lignes)


def totaux():
    rng = np.random.default_rng(47)
    lignes = []
    for m in MOIS[-10:]:
        bloc = [{"mois": m, "district": d, "depistes": int(rng.integers(80, 200)), "positifs": int(rng.integers(2, 12))}
                for d in ["Abobo", "Cocody", "Yopougon", "Treichville"]]
        lignes += bloc + [{"mois": m, "district": "Total", "depistes": sum(x["depistes"] for x in bloc),
                           "positifs": sum(x["positifs"] for x in bloc)}]
    return pd.DataFrame(lignes)


JEUX = {"stock_jour": stock_jour, "trous": trous, "negatifs": negatifs, "gros": gros, "communes": communes, "libelles": libelles,
        "totaux": totaux}
