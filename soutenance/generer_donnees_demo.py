# -*- coding: utf-8 -*-
"""Jeux de données de DÉMONSTRATION pour la soutenance — entièrement FICTIFS.

Aucune donnée réelle (TA-MOH ou autre) : noms de régions/districts publics, valeurs tirées au hasard
avec une graine fixe (mêmes fichiers à chaque exécution). Usage :

    python soutenance/generer_donnees_demo.py

Produit dans soutenance/donnees_demo/ :
  1. demo_dhis2_vih_mensuel.xlsx   — export DHIS2 « long » (Indicateur, Période, Région, District, Valeur)
  2. demo_patients_vih.xlsx        — une ligne par patient (dates, sexe, âge, délais, CD4, CV, statut)
  3. demo_pivot_districts.xlsx     — export « pivot » : une ligne par district et par mois, une colonne par indicateur
"""
from __future__ import annotations

import os
import random

import numpy as np
import pandas as pd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "donnees_demo")
REGIONS = {
    "Abidjan 2": ["Cocody-Bingerville", "Koumassi", "Port-Bouët-Vridi"],
    "Gbêkê": ["Bouaké Nord-Est", "Bouaké Sud", "Sakassou"],
    "San-Pédro": ["San-Pédro", "Tabou"],
    "Nawa": ["Soubré", "Buyo", "Méagui", "Guéyo"],
    "Poro": ["Korhogo 1", "Korhogo 2", "Sinématiali"],
}
MOIS_FR = ["Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août", "Septembre", "Octobre",
           "Novembre", "Décembre"]


def dhis2_long(rng: np.random.Generator) -> pd.DataFrame:
    """Cascade VIH cohérente par district et par mois (janv.–sept. 2025)."""
    rows = []
    for region, districts in REGIONS.items():
        for district in districts:
            base = rng.integers(400, 2200)
            for m in range(1, 10):
                saison = 1 + 0.08 * np.sin(m / 2)
                depistes = int(base * saison * rng.uniform(0.9, 1.1))
                positifs = int(depistes * rng.uniform(0.025, 0.045))
                inities = int(positifs * rng.uniform(0.82, 0.97))
                cv_recue = int(base * 0.6 * rng.uniform(0.85, 1.05))
                cv_supp = int(cv_recue * rng.uniform(0.86, 0.95))
                cv_1000 = cv_recue - cv_supp
                deces = int(rng.poisson(max(1, positifs * 0.04)))
                periode = f"{MOIS_FR[m - 1]} 2025"
                for ind, val in [
                    ("Nombre de personnes dépistées pour le VIH et ayant reçu leur résultat", depistes),
                    ("Nombre de personnes dépistées séropositives au VIH", positifs),
                    ("Nombre de PVVIH nouvellement initiées sous TARV", inities),
                    ("Nombre de PVVIH avec résultat de charge virale reçu", cv_recue),
                    ("Nombre de PVVIH avec charge virale supprimée (<1000 copies/mL)", cv_supp),
                    ("Nombre de PVVIH avec charge virale ≥1000 copies/mL", cv_1000),
                    ("Nombre de décès parmi les PVVIH", deces),
                ]:
                    rows.append({"Indicateur": ind, "Période": periode, "Région": region, "District": district,
                                 "Valeur": val})
    return pd.DataFrame(rows)


def patients(rng: np.random.Generator, n: int = 1500) -> pd.DataFrame:
    """Une ligne par patient. Le dernier mois s'arrête le 14 (mois incomplet, pour la démo)."""
    random.seed(42)
    start, end = pd.Timestamp("2024-01-01"), pd.Timestamp("2025-11-14")
    span = (end - start).days
    tranches = ["15-24", "25-34", "35-44", "45-54", "55+"]
    statuts = ["Actif"] * 84 + ["Perdu de vue"] * 9 + ["Transféré sortant"] * 4 + ["Décédé"] * 3
    data = []
    for i in range(n):
        region = random.choice(list(REGIONS))
        district = random.choice(REGIONS[region])
        dep = start + pd.Timedelta(days=int(rng.integers(0, span)))
        delai = int(rng.choice([0, 0, 0, 0, 0, 0, 1, 2, 3, 5, 7, 10, 14, 21, 35, 60]))
        init = min(dep + pd.Timedelta(days=delai), end)
        cd4 = int(np.clip(rng.normal(390, 160), 5, 1200))
        cv = int(10 ** rng.normal(1.6, 1.2)) if rng.random() < 0.88 else int(10 ** rng.uniform(3, 6))
        data.append({
            "id_patient": f"PAT-{i + 1:05d}",
            "date_depistage": dep.date(),
            "date_initiation_arv": init.date(),
            "sexe": "F" if rng.random() < 0.62 else "M",
            "tranche_age": random.choice(tranches),
            "region": region,
            "district": district,
            "delai_initiation_jours": delai,
            "initiation_meme_jour": "Oui" if delai == 0 else "Non",
            "cd4_initial": cd4,
            "tpt_demarre": "Oui" if rng.random() < 0.77 else "Non",
            "resultat_cv_copies": cv,
            "cv_supprimee": "Oui" if cv < 1000 else "Non",
            "statut_actuel": random.choice(statuts),
        })
    df = pd.DataFrame(data).sort_values("date_initiation_arv").reset_index(drop=True)
    # Défaut de qualité volontaire (pour montrer sa détection) : 22 % des CD4 remplacés par la médiane
    idx = df.sample(frac=0.22, random_state=7).index
    df.loc[idx, "cd4_initial"] = int(df["cd4_initial"].median())
    return df


def pivot_districts(long: pd.DataFrame) -> pd.DataFrame:
    """Format « pivot » DHIS2 (deux derniers mois) : organisationunitname × periodname, une colonne par indicateur."""
    last = long[long["Période"].isin(["Août 2025", "Septembre 2025"])]
    wide = last.pivot_table(index=["District", "Période"], columns="Indicateur", values="Valeur", aggfunc="sum").reset_index()
    wide = wide.rename(columns={"District": "organisationunitname", "Période": "periodname"})
    wide.columns.name = None
    return wide


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    rng = np.random.default_rng(2025)
    long = dhis2_long(rng)
    files = {
        "demo_dhis2_vih_mensuel.xlsx": long,
        "demo_patients_vih.xlsx": patients(rng),
        "demo_pivot_districts.xlsx": pivot_districts(long),
    }
    for name, df in files.items():
        df.to_excel(os.path.join(OUT, name), index=False)
        print(f"{name:32s} {len(df):5d} lignes × {df.shape[1]} colonnes")


if __name__ == "__main__":
    main()
