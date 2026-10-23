# -*- coding: utf-8 -*-
"""Jeu d'exemple FICTIF, généré à la volée (« Essayer avec un exemple ») : aucun fichier de données dans le dépôt.

Suivi mensuel de 12 centres de santé (4 districts, 2 régions) sur 18 mois : consultations, CPN1/CPN4,
accouchements assistés, vaccination Penta3, paludisme (TDR réalisés / cas confirmés, saisonnier), rupture de CTA
et population desservie (un stock : ne s'additionne pas d'un mois à l'autre). Le générateur est déterministe : les
mêmes chiffres à chaque fois, ce qui en fait aussi le jeu de référence des tests.
"""
import numpy as np
import pandas as pd

NOM = "exemple_centres_de_sante_fictif.csv"

CENTRES = [  # (centre, district, région, population desservie en 2024)
    ("CSU Abobo Avocatier", "Abobo-Est", "Abidjan", 41000), ("CSU Abobo Baoulé", "Abobo-Est", "Abidjan", 36500),
    ("CSU Anonkoua", "Abobo-Est", "Abidjan", 28800), ("CSU Yopougon Port-Bouët 2", "Yopougon-Est", "Abidjan", 52300),
    ("CSU Yopougon Toits Rouges", "Yopougon-Est", "Abidjan", 39900), ("CSU Niangon", "Yopougon-Est", "Abidjan", 33100),
    ("CSR Sakassou", "Sakassou", "Gbêkê", 18700), ("CSR Toumodi-Sakassou", "Sakassou", "Gbêkê", 12900),
    ("CSR Ayaou-Sran", "Sakassou", "Gbêkê", 9800), ("CSU Bouaké Dar-es-Salam", "Bouaké-Nord-Ouest", "Gbêkê", 30400),
    ("CSU Bouaké Koko", "Bouaké-Nord-Ouest", "Gbêkê", 27600), ("CSR Djébonoua", "Bouaké-Nord-Ouest", "Gbêkê", 14200),
]


def generer() -> pd.DataFrame:
    rng = np.random.default_rng(2026)
    mois = pd.period_range("2024-01", "2025-06", freq="M")
    lignes = []
    for centre, district, region, pop0 in CENTRES:
        rural = centre.startswith("CSR")
        for m in mois:
            pop = round(pop0 * (1.026 if m.year == 2025 else 1.0))           # croissance annuelle, stock
            pluie = 1.9 if m.month in (6, 7, 8, 9, 10) else 1.0              # saison des pluies : paludisme
            consult = int(rng.poisson(pop * (0.034 if rural else 0.028)))
            cpn1 = int(rng.poisson(pop * 0.0031))
            cpn4 = int(min(cpn1, rng.binomial(cpn1, 0.42 if rural else 0.61)))
            accouch = int(rng.binomial(max(cpn1, 1), 0.58 if rural else 0.83))
            penta3 = int(rng.poisson(pop * 0.0029 * (0.86 if rural else 0.95)))
            tdr = int(rng.poisson(consult * 0.23 * pluie))
            cas = int(min(tdr, rng.binomial(tdr, 0.47 if rural else 0.33)))
            rupture = "Oui" if rng.random() < (0.22 if rural else 0.06) else "Non"
            lignes.append({"centre": centre, "district": district, "region": region, "mois": str(m),
                           "population_desservie": pop, "consultations": consult, "cpn1": cpn1, "cpn4": cpn4,
                           "accouchements_assistes": accouch, "enfants_vaccines_penta3": penta3,
                           "tdr_realises": tdr, "cas_paludisme_confirmes": cas, "rupture_stock_cta": rupture})
    return pd.DataFrame(lignes)


def ecrire(dossier: str) -> dict:
    """Écrit l'exemple dans le dossier d'import de l'espace et renvoie {path, name, size}."""
    import os
    p = os.path.join(dossier, NOM)
    generer().to_csv(p, index=False, encoding="utf-8")
    return {"path": p, "name": NOM, "size": os.path.getsize(p)}
