# -*- coding: utf-8 -*-
"""Intérêt d'un graphique : ce que les données y montrent vraiment, de 0 (rien à voir) à 1 (message fort).

Le catalogue dit ce qui est *possible* ; ici on mesure ce qui est *parlant*, pour que deux fichiers de même forme
n'aient pas la même page. Chaque note vient d'un calcul simple et déterministe sur les données agrégées
comme la page les montrera (somme d'un volume, moyenne d'un taux, dernière valeur d'un stock) :

- classement : écart entre les groupes ;
- anneau, treemap : concentration du total sur quelques groupes ;
- courbe : ampleur des variations dans le temps ;
- chaleur : moments et lieux qui se distinguent (et pas seulement un niveau différent par lieu) ;
- courbe comparée : des lieux qui n'évoluent pas de la même façon ;
- pente : un classement qui change d'une année à l'autre ;
- passage : une variation nette du total entre deux années ;
- bulles : deux mesures qui vont vraiment ensemble ;
- cible : toujours utile (un objectif est fourni) ; histogramme : complément modeste.
Rien n'est envoyé à l'IA : les calculs restent exacts et la page reste la même à fichier égal."""
import math

import pandas as pd

SEUIL = 0.25  # en dessous, le graphique n'apprend presque rien : il ne prend pas de place sur la page
COUVERTURE = 0.6  # part des cases lieu × mois renseignées : en dessous, la grille est à moitié vide


def _borne(x):
    return 0.0 if x is None or not math.isfinite(x) else max(0.0, min(1.0, float(x)))


class Mesures:
    """Agrégats partagés entre les notes d'une même page (chaque couple mesure × groupe n'est calculé qu'une fois)."""

    def __init__(self, fiche, df):
        self.f, self.df, self._cache = fiche, df, {}
        per = fiche.get("periode") or {}
        self.periode = per.get("colonne")
        if self.periode and self.periode in df.columns:
            cle = df[self.periode].astype(str)
            self.mois = cle.str[:4] if per.get("unite") == "annee" else cle.str[:7]
            self.annee = cle.str[:4]
            self.mois_num = cle.str[5:7]  # le mois lu sur la période brute : `mois` est tronqué à l'année sur un fichier annuel
        else:
            self.mois = self.annee = self.mois_num = None

    def valeurs(self, c):
        return pd.to_numeric(self.df[c], errors="coerce")

    def cle(self, nom):
        """Colonne de regroupement ; « @mois » : la période de la page."""
        return self.mois if nom == "@mois" else self.df[nom]

    def agreger(self, c, noms, agregation):
        cle = (c, agregation) + tuple(noms)
        if cle not in self._cache:
            cles = [self.cle(n) for n in noms]
            g = self.valeurs(c).groupby(cles if len(cles) > 1 else cles[0], dropna=True)
            r = g.sum(min_count=1) if agregation == "somme" else g.last() if agregation == "dernier" else g.mean()
            self._cache[cle] = r.dropna()
        return self._cache[cle]


def _cv(s):
    s = s.dropna()
    m = float(s.abs().mean()) if len(s) else 0.0
    return float(s.std(ddof=0)) / m if m else 0.0


def _classement(M, p):
    s = M.agreger(p["mesure"], (p["dimension"],), p["agregation"])
    if len(s) < 2:
        return 0.0
    # écart relatif entre le premier et le dernier : 0 si tous se valent, 1 dès que le premier fait ~2,5 fois le dernier
    haut, bas = float(s.max()), float(s.min())
    return _borne((haut - bas) / (abs(haut) or 1) / 0.6)


def _parts(M, p):
    s = M.agreger(p["mesure"], (p["dimension"],), "somme")
    s = s[s > 0]
    n = len(s)
    if n < 2 or s.sum() <= 0:
        return 0.0
    parts = (s / s.sum()).sort_values(ascending=False)
    k = max(1, round(n * 0.2))  # les 20 % premiers groupes
    attendu = k / n
    # concentration : ce que pèsent les premiers au-delà d'un partage égal
    return _borne((float(parts.iloc[:k].sum()) - attendu) / (1 - attendu) * 1.8 + 0.15)


def _courbe(M, p):
    if M.mois is None:
        return 0.0
    s = M.agreger(p["mesure"], ("@mois",), p["agregation"]).sort_index()
    if len(s) < 3:
        return 0.0
    # variation d'un point au suivant, et écart entre début et fin
    pas = float((s.diff().abs() / s.abs().mean()).mean()) if s.abs().mean() else 0.0
    return _borne(0.45 + _cv(s) * 1.2 + pas * 0.5)


def _tableau(M, p):
    t = M.agreger(p["mesure"], (p["dimension"], "@mois"), p["agregation"]).unstack()
    return t.dropna(how="all").dropna(axis=1, how="all")


def _chaleur(M, p):
    if M.mois is None:
        return 0.0
    t = _tableau(M, p)
    if t.shape[0] < 2 or t.shape[1] < 3:
        return 0.0
    # Un regroupement lié au temps (campagne, exercice) n'occupe que ses propres mois : la grille serait à moitié vide
    couverture = float(t.notna().to_numpy().mean())
    if couverture < COUVERTURE:
        return 0.0
    t = t.apply(lambda ligne: ligne.fillna(ligne.mean()), axis=1)
    v = t.to_numpy(dtype=float)
    moy = float(abs(pd.Series(v.ravel()).mean())) or 1.0
    ligne = t.mean(axis=1).to_numpy()[:, None]
    colonne = t.mean(axis=0).to_numpy()[None, :]
    reste = v - ligne - colonne + float(pd.Series(v.ravel()).mean())
    croise = float(pd.Series(reste.ravel()).std(ddof=0)) / moy   # un lieu qui se distingue à un moment précis
    temps = float(pd.Series(colonne.ravel()).std(ddof=0)) / moy  # des mois qui se distinguent pour tous (saison, rupture)
    return _borne(croise * 2.5 + temps * 2.0)


def _comparee(M, p):
    if M.mois is None:
        return 0.0
    t = _tableau(M, p).T  # une colonne par lieu
    if float(t.notna().to_numpy().mean()) < COUVERTURE:  # des lieux qui ne partagent pas la même période
        return 0.0
    t = t.loc[:, t.notna().sum() >= 3]
    if t.shape[1] < 2 or t.shape[0] < 3:
        return 0.0
    # tendance de chaque lieu : moyenne du dernier tiers de la période contre celle du premier tiers (le bruit s'efface)
    tiers = max(1, len(t) // 3)
    debut, fin = t.iloc[:tiers].mean(), t.iloc[-tiers:].mean()
    evol = ((fin - debut) / debut.abs().where(debut != 0)).dropna()
    if len(evol) < 2:
        return 0.0
    ecart = float(evol.max() - evol.min())
    sens = 1.0 if (evol > 0.05).any() and (evol < -0.05).any() else 0.0  # certains montent pendant que d'autres baissent
    return _borne(ecart * 2.0 + sens * 0.3)


def _deux_annees(M, p):
    per = M.f.get("periode") or {}
    if M.annee is None or not per.get("comparable_n1"):
        return None
    annees = sorted(M.annee.dropna().unique())
    if len(annees) < 2:
        return None
    a, b = annees[-2], annees[-1]
    communs = set(per.get("mois_communs") or [])
    garde = M.mois_num.isin(communs) if communs and M.mois_num is not None else pd.Series(True, index=M.df.index)
    d = M.df[p["dimension"]]
    t = M.valeurs(p["mesure"]).where(garde).groupby([d, M.annee])
    t = (t.sum(min_count=1) if p["agregation"] == "somme" else t.mean()).unstack()
    if a not in t.columns or b not in t.columns:
        return None
    return t[[a, b]].dropna()


def _pente(M, p):
    t = _deux_annees(M, p)
    if t is None or len(t) < 2:
        return 0.0
    a, b = t.columns
    va, vb = t[a].to_numpy(dtype=float), t[b].to_numpy(dtype=float)
    n, niveau = len(t), float(abs(t.to_numpy(dtype=float)).mean()) or 1.0
    net = 0.05 * niveau  # deux lieux à moins de 5 % l'un de l'autre ne se « dépassent » pas : c'est du bruit
    inversions = sum(1 for i in range(n) for j in range(i + 1, n)
                     if abs(va[i] - va[j]) > net and abs(vb[i] - vb[j]) > net and (va[i] - va[j]) * (vb[i] - vb[j]) < 0)
    inversions /= n * (n - 1) / 2
    evol = ((t[b] - t[a]) / t[a].abs().where(t[a] != 0)).dropna()
    ecart = float(evol.max() - evol.min()) if len(evol) > 1 else 0.0  # certains montent, d'autres baissent
    return _borne(inversions * 2.0 + (ecart - 0.06) * 2.0)


def _passage(M, p):
    t = _deux_annees(M, p)
    if t is None or t.empty:
        return 0.0
    a, b = t.columns
    avant, apres = float(t[a].sum()), float(t[b].sum())
    if not avant:
        return 0.0
    variation = abs(apres - avant) / abs(avant)  # 5 % : à peine ; 25 % : un vrai sujet
    return _borne((variation - 0.03) * 4)


def _bulles(M, p):
    x = M.agreger(p["mesure"], (p["dimension"],), p["agregation"])
    y = M.agreger(p["mesure_y"], (p["dimension"],), p["agregation_y"])
    xy = pd.concat([x, y], axis=1, join="inner").dropna()
    if len(xy) < 4 or xy.iloc[:, 0].nunique() < 2 or xy.iloc[:, 1].nunique() < 2:
        return 0.0
    r = float(xy.iloc[:, 0].rank().corr(xy.iloc[:, 1].rank()))  # Spearman, sans dépendre de scipy
    if not math.isfinite(r):
        return 0.0
    taille = 0.6 if p.get("nature") == "volume" and p.get("nature_y") == "volume" else 1.0
    return _borne((abs(r) - 0.3) / 0.6 * taille)


NOTES = {"classement": _classement, "anneau": _parts, "treemap": _parts, "courbe": _courbe, "chaleur": _chaleur,
         "comparee": _comparee, "pente": _pente, "passage": _passage, "bulles": _bulles,
         "cible": lambda M, p: 1.0, "histogramme": lambda M, p: 0.3}


def noter(props, fiche, df):
    """La note de chaque proposition, dans le même ordre. Une erreur de calcul vaut 0 : le graphique n'est pas retenu."""
    M = Mesures(fiche, df)
    notes = []
    for p in props:
        try:
            notes.append(round(_borne(NOTES[p["type"]](M, p)), 3))
        except Exception:  # noqa: BLE001 - une donnée inattendue écarte une carte, jamais la page
            notes.append(0.0)
    return notes
