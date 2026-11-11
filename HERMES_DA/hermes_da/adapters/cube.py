# -*- coding: utf-8 -*-
"""Résumé exact des gros fichiers : tout ce que la page doit recalculer, sans une seule ligne brute.

Un échantillon fausse les totaux. Ici, chaque case porte la somme, l'effectif, le minimum et le maximum de toutes ses
lignes, et la page additionne les cases retenues par les filtres. Seule la médiane ne se recompose pas."""
import pandas as pd

SEUIL = 6000       # au-delà, la page ne peut plus embarquer les lignes
MAX_CASES = 15000  # au-delà, la page deviendrait trop lourde à ouvrir
OUI = {"oui", "yes", "true", "vrai", "o", "y", "positif", "positive", "1"}
NON = {"non", "no", "false", "faux", "n", "négatif", "negatif", "négative", "negative", "0"}
VIDES = {"", "nan", "none", "null", "na", "n/a"}


def _cartes(plan):
    """Les cartes et les graphiques qu'on peut leur substituer : le résumé doit contenir de quoi dessiner chacun."""
    return [v for ligne in plan["lignes"] for c in ligne for v in [c] + list(c.get("variantes") or [])[1:]]


def echelle_taux(serie):
    """Le facteur qui met un taux calculé côté serveur à l'échelle de la page : celle-ci affiche de 0 à 100."""
    texte = _texte(serie.dropna())
    if len(texte) and texte.isin(OUI | NON).all():
        return 100
    v = pd.to_numeric(serie, errors="coerce").dropna()
    return 100 if len(v) and v.max() <= 1 else 1


def _uniques(valeurs, colonnes):
    return [c for c in dict.fromkeys(valeurs) if c in colonnes]


def _texte(serie):
    return serie.astype(str).str.strip().str.lower()


def _indicateurs(data, kpi_defs):
    """Colonnes de comptage et de taux des tuiles, par ligne : elles s'additionnent comme les mesures."""
    sortie = {}
    for k in kpi_defs:
        c, a = k.get("colonne"), str(k.get("agregation") or "").lower()
        if c not in data.columns or a not in ("compte", "count", "taux", "rate", "pct", "proportion"):
            continue
        texte = _texte(data[c])
        presents = ~texte.isin(VIDES) & data[c].notna()
        sortie[f"{c}__n"] = presents.astype(int)
        v = pd.to_numeric(data[c], errors="coerce")
        if presents.any() and v[presents].notna().all() and v[presents].isin([0, 1]).all():
            sortie[f"{c}__oui"] = (v == 1).astype(int)
        elif presents.any() and texte[presents].isin(OUI | NON).all():
            sortie[f"{c}__oui"] = texte.isin(OUI).astype(int)
    return sortie


def resumer(data, plan, kpi_defs, filtres):
    """Les cases et les réglages du résumé, ou None si le fichier reste trop éclaté même simplifié."""
    cartes = _cartes(plan)
    objectifs = [c for c in cartes if c.get("objectif")]
    mesures = _uniques([c.get(k) for c in cartes for k in ("mesure", "mesure_y", "objectif")]
                       + [m for c in cartes for m in c.get("ensemble") or []]
                       + [k.get("colonne") for k in kpi_defs if str(k.get("agregation") or "").lower() not in ("compte", "count")], data.columns)
    fixes = _uniques([c.get("dimension") for c in cartes] + [k.get("dimension") for k in kpi_defs]
                     + [(k.get("hors") or {}).get("colonne") for k in kpi_defs], data.columns)
    mesures = [m for m in mesures if m not in fixes and pd.to_numeric(data[m], errors="coerce").notna().any()]
    voulus = _uniques(filtres, data.columns)
    retires = [c for c in voulus if c in mesures]  # un filtre sur une mesure n'a pas de case à lui
    gardes = [c for c in voulus if c not in mesures and c not in fixes]
    t = data[fixes + gardes].copy()
    periode = "__p" in data.columns
    if periode:
        t["__p"] = data["__p"]
    # Chaque objectif sépare les lignes où mesure et objectif coexistent : la page ne compare que celles-là.
    etats = []
    for i, c in enumerate(objectifs):
        etats.append(f"__c{i}")
        t[etats[-1]] = pd.to_numeric(data[c["mesure"]], errors="coerce").notna().astype(int) * 2 + pd.to_numeric(data[c["objectif"]], errors="coerce").notna().astype(int)

    def cles():
        return fixes + gardes + etats + (["__p"] if periode else [])

    mensuel = False
    while len(t[cles()].drop_duplicates()) > MAX_CASES:
        if periode and not mensuel and t["__p"].astype(str).str.len().max() > 7:
            t["__p"], mensuel = t["__p"].astype(str).str[:7].where(t["__p"].notna()), True
        elif gardes:
            retires.append(max(gardes, key=lambda c: t[c].nunique(dropna=False)))
            gardes.remove(retires[-1])
        else:
            return None
    nombres = pd.DataFrame({m: pd.to_numeric(data[m], errors="coerce") for m in mesures}, index=data.index)
    comptes = pd.DataFrame(_indicateurs(data, kpi_defs), index=data.index)
    groupe = cles()
    cadre = pd.concat([t[groupe], nombres, comptes], axis=1)
    g = cadre.groupby(groupe, dropna=False, sort=False) if groupe else cadre.groupby(lambda _: 0)
    morceaux = [g.size().rename("__n")]
    if mesures:
        morceaux += [g[mesures].sum(min_count=1), g[mesures].count().add_suffix("__v"), g[mesures].min().add_suffix("__mi"), g[mesures].max().add_suffix("__ma")]
    if len(comptes.columns):
        morceaux.append(g[list(comptes.columns)].sum())
    cases = pd.concat(morceaux, axis=1).reset_index()
    if not groupe:
        cases = cases.drop(columns=[c for c in cases.columns if c == "index"])
    return {"cases": cases, "retires": [str(c) for c in retires], "mensuel": mensuel}
