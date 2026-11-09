# -*- coding: utf-8 -*-
"""Informations collées dans un texte : période, tranche d'âge, sexe.

Une source (DHIS2, Kobo, rapport Excel de district…) colle souvent plusieurs informations dans un même titre de colonne
(« SIG - Nombre de clients dépistés positif Janvier - Juin 2026 15-19 ans ») ou dans une même cellule
(« Masculin, 15-24 ans »). Ce module les reconnaît par des règles sûres et rend le reste (l'indicateur). Il ne devine
pas : un morceau qu'aucune règle ne reconnaît reste dans l'indicateur, tel quel."""
from __future__ import annotations

import re

DIMENSIONS = ("periode", "sexe", "tranche_age")
NOMS = {"indicateur": "Indicateur", "periode": "Période", "sexe": "Sexe", "tranche_age": "Tranche d'âge"}

_MOIS = (r"(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre|"
         r"janv|f[ée]vr?|avr|juil|sept?|oct|nov|d[ée]c|"
         r"january|february|march|april|may|june|july|august|september|october|november|december|"
         r"jan|feb|mar|apr|jun|jul|aug|sep)")
_AN = r"(?:19|20)\d{2}"
PERIODE = re.compile(
    rf"\b{_MOIS}\.?\s*(?:[-–]|à|au|to)\s*{_MOIS}\.?\s+{_AN}\b"            # Janvier - Juin 2026
    rf"|\b{_MOIS}\.?\s+{_AN}\b"                                            # Mars 2026, janv. 2026
    rf"|\b(?:trimestre|semestre)\s*[1-4]\s*{_AN}\b"                        # Trimestre 1 2026
    rf"|\b(?:T|Q|S)[1-4]\s*[-/ ]?\s*{_AN}\b|\b{_AN}\s*[-/ ]?\s*(?:T|Q|S)[1-4]\b"  # T1 2026, 2026Q1, 2026S1
    rf"|\b(?:semaine\s+)?{_AN}W\d{{1,2}}\b"                                # 2026W05 (semaine DHIS2)
    rf"|\b{_AN}(?:0[1-9]|1[0-2])\b"                                        # 202601 (mois DHIS2)
    rf"|\b{_AN}-(?:0[1-9]|1[0-2])\b"                                       # 2026-01
    rf"|(?<![\d-]){_AN}(?![\d-])",                                         # 2026 seul
    re.I)
TRANCHE_AGE = re.compile(
    r"(?:<|≤|moins\s+de)\s*\d{1,3}\s*(?:ans?|mois|years?|y)\b"                     # <1 an, moins de 5 ans, <15y
    r"|\b\d{1,3}\s*(?:[-–]|à)\s*\d{1,3}\s*(?:ans?|mois|years?|y)\b"                # 15-19 ans, 0-11 mois, 15-24y
    r"|\b\d{1,3}\s*(?:ans?|years?|y)?\s*(?:et\s+plus|\+|and\s+over|ou\s+plus)"     # 50 ans et plus, 50+
    r"|\b(?:plus\s+de|>|≥)\s*\d{1,3}\s*(?:ans?|years?|y)\b",                       # plus de 49 ans
    re.I)
# « Femme enceinte / allaitante » est une population, pas un sexe : laissée dans l'indicateur.
SEXE = re.compile(r"\b(?:masculin|f[ée]minin|hommes?|femmes?(?!\s+(?:enceintes?|allaitantes?))|"
                  r"gar[cç]ons?|filles?|males?|females?)\b", re.I)
_REGLES = (("tranche_age", TRANCHE_AGE), ("periode", PERIODE), ("sexe", SEXE))
_BORDS = " -–,:;|/_"


def decouper(texte) -> tuple[str, dict]:
    """Texte → (reste, {dimension: valeur}). Le reste, nettoyé, est l'indicateur (ou "" s'il ne reste rien)."""
    reste, trouve = str(texte), {}
    for nom, rx in _REGLES:
        m = rx.search(reste)
        if m:
            trouve[nom] = " ".join(m.group(0).split()).strip(_BORDS)
            reste = reste[:m.start()] + " " + reste[m.end():]
    reste = re.sub(r"\(\s*\)", " ", reste)
    reste = re.sub(r"\s*([-–,:;|/])\s*(?=[-–,:;|/]|$)", " ", " ".join(reste.split()))   # séparateurs orphelins
    return " ".join(reste.split()).strip(_BORDS), trouve
