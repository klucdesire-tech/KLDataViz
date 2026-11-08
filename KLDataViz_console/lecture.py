# -*- coding: utf-8 -*-
"""Lecture fidèle des valeurs d'un fichier, à son ouverture, pour tous les parcours (analyse, tableau de bord,
sources connectées, rapports planifiés, Adifa’).

- Nombres écrits en texte : « 1 234,5 », « 12 500 FCFA », « 1.234,5 », « 45,2 % » deviennent des nombres. Sans cela,
  ces valeurs ne comptaient dans aucun total ni aucun graphique, sauf si l'utilisateur appliquait la correction
  proposée à l'écran « Vérifier ». Les valeurs qui ne sont pas des nombres (« trois cents ») sont écartées des calculs,
  et notées avec leur ligne pour le bilan qualité, qui les signale.
- Ordre du jour et du mois dans les dates « 03/04/2025 » : jour en premier (usage français), sauf si le fichier prouve
  le contraire (« 04/23/2025 » : 23 ne peut pas être un mois). Si le fichier mélange les deux ordres, il le dit.

Rien n'est inventé : une valeur illisible devient vide, jamais une estimation. `df.attrs["lecture"]` dit ce qui a été
converti, pour l'écran « Vérifier » et le bilan qualité."""
import re

import numpy as np
import pandas as pd

ATTENTES = {"", "-", "--", "—", "n/a", "na", "nd", "n.d.", "?", ".", "néant", "neant", "#n/a", "#ref!", "#div/0!",
            "#value!", "non renseigné", "non renseigne", "inconnu", "x", "nan", "none", "null"}
DEVISE = re.compile(r"^(?:(?:f\s?cfa|cfa|xof|eur|usd)\s+|[€$]\s*)|(?:\s+(?:f\s?cfa|cfa|xof|eur|usd)|\s*[€$%])$", re.I)
# Preuve qu'une colonne contient des nombres écrits « à la main » : séparateur de milliers, virgule décimale, devise
PREUVE = re.compile(r"^[+-]?\d{1,3}([   .]\d{3})+,\d+$|^[+-]?\d{1,3}([   ]\d{3})+$|^[+-]?\d+,\d+$")
SEUIL = 0.95             # part des valeurs remplies qui doivent se lire comme des nombres
# Colonnes de codes et de numéros : jamais des quantités, même écrites comme des nombres (relecture de la PR 198)
NOM_CODE = re.compile(r"t[ée]l[ée]?phone|^tel(_|\b)|portable|mobile|contact|whatsapp|fax|code|matricule|identifiant|"
                      r"(^|_)id($|_)|cni|passeport|num[ée]ro|^n°|^no_|postal|ifu|rccm|imei|iban", re.I)
ZERO_INITIAL = re.compile(r"^[+-]?0[\d\s]")   # « 071 234 567 », « 0 123 456 », « 007 » : jamais un montant
DATE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(\s|$)")
MAX_EXEMPLES = 20


def _texte(v):
    t = str(v).replace(" ", " ").replace(" ", " ").strip()
    return DEVISE.sub("", t).strip()


def _nombre(v, virgule=True):
    """« 1 234,5 » → 1234.5 ; « 12,5 » → 12.5 ; None si ce n'est pas un nombre. Avec la virgule décimale (`virgule`),
    le point sépare les milliers (« 1.234,5 ») ; sans elle, « 1.234 » reste un nombre décimal (usage anglais)."""
    t = _texte(v).replace(" ", "")
    if virgule:
        t = t.replace(".", "").replace(",", ".")
    try:
        x = float(t)
    except ValueError:
        return None
    return x if np.isfinite(x) else None


def nombres(df):
    """Colonnes de nombres écrits en texte → nombres. Rend le tableau (copie si une colonne change) et la liste des
    conversions : colonne, nombre de valeurs converties, un exemple, et les valeurs écartées avec leur ligne."""
    conversions, sortie = [], None
    for c in df.columns:
        s = df[c]
        if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
            continue
        if NOM_CODE.search(str(c)):
            continue
        rempli = s[s.notna()]
        texte = rempli.astype(str).str.strip()
        utile = texte[~texte.str.lower().isin(ATTENTES)]
        if len(utile) < 2:
            continue
        propre = utile.map(_texte)
        if propre.str.match(ZERO_INITIAL).any():
            continue  # zéro initial : téléphones, codes ; le convertir ferait perdre le zéro et en ferait une mesure
        if not propre.str.match(PREUVE).any() and not utile.map(lambda v: bool(DEVISE.search(str(v).strip()))).any():
            continue  # rien d'écrit « à la main » : des codes (« 007 ») ou du vrai texte, laissés tels quels
        virgule = bool(propre.str.contains(r"\d,\d").any())
        valeurs = utile.map(lambda v: _nombre(v, virgule))
        lus = valeurs.notna()
        if lus.mean() < SEUIL:
            continue
        nouveau = pd.Series(np.nan, index=s.index, dtype=float)
        nouveau[valeurs[lus].index] = valeurs[lus].astype(float)
        if (nouveau.dropna() % 1 == 0).all() and nouveau.notna().all():
            nouveau = nouveau.astype("int64")
        ecartees = [(int(df.index.get_loc(i)) + 2, str(texte[i])) for i in valeurs[~lus].index[:MAX_EXEMPLES]]
        # « NA », « inconnu », « - » : des valeurs qui veulent dire « pas de donnée » ; devenues vides, elles restent
        # notées (avec leur ligne) pour que le bilan qualité les signale comme telles
        attente = texte[texte.str.lower().isin(ATTENTES) & (texte != "")]
        attentes = [(int(df.index.get_loc(i)) + 2, str(attente[i])) for i in attente.index[:MAX_EXEMPLES]]
        # Exemple de conversion pris parmi les valeurs CONVERTIES : une valeur écartée (« trois cents ») n'a pas de
        # nombre à montrer (relecture de la PR 198 : un exemple vide faisait tomber tout le bilan qualité)
        converties = [str(v) for v, x in zip(utile, valeurs) if pd.notna(x)]
        avant = next((v for v in converties if v.strip() != str(_nombre(v, virgule))), converties[0])
        if sortie is None:
            sortie = df.copy()
            sortie.attrs = dict(df.attrs)
        sortie[c] = nouveau
        conversions.append({"colonne": str(c), "convertis": int(lus.sum()), "exemple": [avant, _nombre(avant, virgule)],
                            "ecartees": int((~lus).sum()), "exemples_ecartes": ecartees,
                            "attentes": int(len(attente)), "exemples_attentes": attentes,
                            # toutes les positions (pas seulement les exemples) : le bilan ne compte aucune d'elles
                            # une seconde fois comme case vide
                            "positions_non_lues": [int(df.index.get_loc(i)) for i in
                                                   list(valeurs[~lus].index) + list(attente.index)]})
    return (df if sortie is None else sortie), conversions


def ordre_dates(valeurs):
    """Ordre du jour et du mois dans des dates « 03/04/2025 » : « jour » (usage français), « mois » si le fichier le
    prouve (une valeur comme 04/23/2025), « melange » si les deux ordres sont prouvés, None sans date de ce type."""
    t = pd.Series(list(valeurs)).dropna().astype(str)
    m = t.str.extract(DATE)
    m = m.dropna(subset=[0, 1])
    if m.empty:
        return None
    a, b = m[0].astype(int), m[1].astype(int)
    jour_prouve, mois_prouve = (a > 12).any(), (b > 12).any()
    if jour_prouve and mois_prouve:
        return "melange"
    return "mois" if mois_prouve else "jour"


def lire(df):
    """Point d'entrée à l'ouverture d'un fichier : nombres convertis, conversions notées dans `attrs["lecture"]`."""
    df2, conversions = nombres(df)
    if conversions:
        df2.attrs["lecture"] = conversions
    return df2
