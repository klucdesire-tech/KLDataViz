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
        if unite in _MULT:
            x *= _MULT[unite]
        out.append(x)
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


def _proche(x, ref):
    return abs(x - ref) <= max(0.5, abs(ref) * 0.015)


def verifier(reponse: str, refs: set, max_pairs: int = 120) -> list:
    """Nombres significatifs de la réponse introuvables dans les sources, même après dérivation simple."""
    brut = sorted({v for v in refs if v == v and abs(v) < 1e15})
    # proportion ↔ pourcentage (0,125 ↔ 12,5 %)
    base = brut + [v * 100 for v in brut] + [v / 100 for v in brut]
    # écarts, sommes et rapports de DEUX chiffres bruts (jamais de leurs formes ×100 : tout deviendrait « explicable »)
    gros = sorted(brut, key=lambda v: -abs(v))[:max_pairs]
    derives = set()
    for i, a in enumerate(gros):
        for b in gros[i + 1:]:
            derives.update((a - b, b - a, a + b))
            if b:
                derives.update((a / b, a / b * 100, (a / b - 1) * 100))   # rapport, part, variation (« 22 % de plus »)
            if a:
                derives.update((b / a, b / a * 100, (b / a - 1) * 100))
    # écart en points entre deux pourcentages (« hausse de 6,4 points ») : proportions (0–1) et pourcentages (0–100)
    pcts = sorted({v * 100 for v in brut if 0 < v <= 1} | {v for v in brut if 0 < v <= 100})[:max_pairs]
    for i, a in enumerate(pcts):
        for b in pcts[i + 1:]:
            derives.update((a - b, b - a))
    tous = base + list(derives)
    douteux = []
    for x in nombres(reponse):
        if not _significatif(x):
            continue
        if not any(_proche(x, r) for r in tous):
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
