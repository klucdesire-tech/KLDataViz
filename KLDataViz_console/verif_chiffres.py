# -*- coding: utf-8 -*-
"""Vérification des chiffres d'une réponse de l'IA (anti-hallucination).

Chaque nombre « significatif » cité dans la réponse doit se retrouver dans les sources de vérité : résultats des
calculs réellement exécutés (bac à sable) et chiffres fournis à l'IA (résumé de l'analyse). Sont admis aussi ce
qu'une rédaction honnête en dérive : pourcentage ↔ proportion, écart, somme ou rapport de deux chiffres sources,
arrondis (tolérance relative) et écritures « 1,3 k » / « 2 M ».

On ne censure pas : un chiffre introuvable est SIGNALÉ à l'utilisateur, la réponse reste affichée.
"""
import re

_NB = re.compile(r"(?<![\w-])(-?\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?)(\s?(?:k|K|M|Md)\b)?")
_MULT = {"k": 1e3, "K": 1e3, "M": 1e6, "Md": 1e9}


_DATES = re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b|\b\d{4}-\d{2}(?:-\d{2})?\b")


def nombres(texte: str) -> list:
    """Nombres d'un texte français : « 1 234,5 », « 12,5 % », « 1,3 M », « 0.42 » → flottants (années, dates exclues)."""
    return [x for x, _ in nombres_precis(texte)]


def nombres_precis(texte: str) -> list:
    """Comme `nombres`, avec la marge d'arrondi de l'écriture : « 35,1 » → ±0,05 ; « 181 k » → ±500 ; « 63 469 » → ±0,5 ;
    « 63 000 » → ±500 (un nombre rond est un arrondi)."""
    out = []
    for m in _NB.finditer(_DATES.sub(" ", str(texte or ""))):
        brut = re.sub(r"[   ]", "", m.group(1))
        if brut.count(",") == 1 and brut.count(".") == 0:
            brut = brut.replace(",", ".")
        elif "," in brut and "." in brut:  # 1,234.5 (format anglais)
            brut = brut.replace(",", "")
        try:
            x = float(brut)
        except ValueError:
            continue
        unite = (m.group(2) or "").strip()
        dec = len(brut.split(".")[1]) if "." in brut else 0
        marge = 0.5 * 10 ** -dec
        if not dec and abs(x) >= 10000:                 # « 63 000 » : arrondi au millier (ou plus)
            zeros = len(brut) - len(brut.rstrip("0"))
            if zeros >= 3:
                marge = 0.5 * 10 ** zeros
        if unite in _MULT:
            x *= _MULT[unite]
            marge *= _MULT[unite]
        out.append((x, marge))
    return out


def _significatif(x: float) -> bool:
    if x == int(x) and 1900 <= x <= 2100:  # une année
        return False
    if x == int(x) and abs(x) < 10:  # « 3 districts », « 5 points »
        return False
    return True


def sources(*textes) -> set:
    """Chiffres de référence bruts extraits des résultats de calcul et du contexte."""
    vals = set()
    for t in textes:
        vals.update(nombres(t))
    return vals


def _proche(x, ref, marge=None):
    """Égal à l'arrondi près. Sans marge connue, l'ancienne tolérance (1,5 %) ; avec elle, l'arrondi de l'écriture et
    un millième pour les arrondis des chiffres sources eux-mêmes. Une tolérance de 1,5 % laissait passer toute erreur :
    mesuré sur un banc à réponse connue, 0 erreur sur 24 repérée (un total faux de 7 %, un chiffre doublé…)."""
    if marge is None:
        return abs(x - ref) <= max(0.5, abs(ref) * 0.015)
    return abs(x - ref) <= marge + abs(ref) * 1e-6 + 1e-9


def verifier(reponse: str, refs: set, max_pairs: int = 120) -> list:
    """Nombres significatifs de la réponse introuvables dans les sources, même après dérivation simple."""
    brut = sorted({v for v in refs if v == v and abs(v) < 1e15})
    # proportion ↔ pourcentage (0,125 ↔ 12,5 %)
    base = brut + [v * 100 for v in brut] + [v / 100 for v in brut]
    # Ce qu'une rédaction honnête dérive de DEUX chiffres, et seulement cela : avec toutes les dérivations de toutes
    # les paires, des milliers de valeurs couvraient presque n'importe quel nombre (banc à réponse connue : 0 erreur
    # sur 24 repérée).
    gros = sorted(brut, key=lambda v: -abs(v))[:max_pairs]
    totaux = gros[:10]                       # une part se calcule sur un total : l'un des plus grands chiffres
    derives = set()
    for i, a in enumerate(gros):
        for b in gros[i + 1:]:
            if a and b and 0.1 <= abs(a / b) <= 10:                 # même ordre de grandeur
                derives.update((a - b, b - a, a + b))
            if a and b and 0.5 <= abs(a / b) <= 2:                  # quantités comparables (deux années, deux sites)
                derives.update((a / b, b / a, (a / b - 1) * 100, (b / a - 1) * 100))   # rapport, variation
    for t in totaux:
        if t:
            derives.update(v / t * 100 for v in gros if abs(v) <= abs(t))           # part d'un total
            derives.update(t / v for v in gros if v and abs(v) <= abs(t))          # « 1 sur X », « X fois plus »
    # écart en points entre deux pourcentages (« hausse de 6,4 points ») : proportions (0–1) et pourcentages (0–100)
    pcts = sorted({v * 100 for v in brut if 0 < v <= 1} | {v for v in brut if 0 < v <= 100})[:max_pairs]
    for i, a in enumerate(pcts):
        for b in pcts[i + 1:]:
            derives.update((a - b, b - a))
    tous = base + list(derives)
    douteux = []
    for x, marge in nombres_precis(reponse):
        if not _significatif(x):
            continue
        if not any(_proche(x, r, marge) for r in tous):
            douteux.append(x)
    return douteux


def note(douteux: list) -> str:
    """Avertissement ajouté à la réponse (format nombre français)."""
    if not douteux:
        return ""
    f = [(f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}").replace(",", " ").replace(".", ",")
         for v in douteux[:4]]
    return ("\n\n⚠ Vérification : " + ", ".join(f) + (" n'a" if len(f) == 1 else " n'ont")
            + " pas été retrouvé" + ("" if len(f) == 1 else "s") + " dans les calculs effectués. À vérifier avant de diffuser.")
