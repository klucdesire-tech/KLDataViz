# -*- coding: utf-8 -*-
"""Banc à réponse connue de la vérification automatique des chiffres d'Adifa’ (`verif_chiffres.py`), sans appel à l'IA.

Sources : ce que le bac à sable aurait calculé sur un fichier fictif (totaux, moyennes, sommes par district, moyennes
par région). Réponses : des phrases justes, puis les mêmes avec une erreur glissée (total faux de 10 % ou de 7 %,
chiffre doublé, chiffres inversés, part fausse de 12 points, moyenne fausse de 25 %).

Avant la PR qui a créé ce banc : 0 erreur sur 24 repérée (tolérance de 1,5 % et dérivations de toutes les paires).
Les erreurs encore invisibles sont des parts fausses (« 47,1 % du total ») : sans savoir quel total va avec quel
chiffre, un pourcentage à une décimale tombe trop souvent sur une part possible. La relecture par l'IA, qui connaît la
question, couvre ce cas ; ce banc garde la vérification automatique honnête sur le reste."""
import os
import sys

import pytest

from _harness import reset_app_modules

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jeux_reference as J  # noqa: E402

MESURES = ["consultations", "cpn1", "tdr_realises", "cas_paludisme_confirmes"]


def fr(x, dec=0):
    return f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")


def _faits(df):
    lignes = []
    for c in MESURES + ["cpn4", "accouchements_assistes"]:
        lignes.append(f"total {c} = {df[c].sum()}")
        lignes.append(f"moyenne {c} = {round(df[c].mean(), 2)}")
        lignes += [f"{c} {g} = {v}" for g, v in df.groupby("district")[c].sum().items()]
        lignes += [f"moyenne {c} {g} = {v}" for g, v in df.groupby("region")[c].mean().round(2).items()]
    return "\n".join(lignes)


def _reponses(df):
    justes, fausses = [], []
    for c in MESURES:
        g, tot = df.groupby("district")[c].sum(), df[c].sum()
        haut = g.idxmax()
        part = g[haut] / tot * 100
        justes += [f"Au total, {fr(tot)} {c}.", f"{haut} arrive en tête avec {fr(g[haut])} ({fr(part, 1)} % du total).",
                   f"La moyenne mensuelle par centre est de {fr(df[c].mean(), 1)}.",
                   f"{haut} dépasse le deuxième de {fr(g.sort_values().iloc[-1] - g.sort_values().iloc[-2])}."]
        fausses += [("total +10 %", f"Au total, {fr(tot * 1.10)} {c}."), ("total -7 %", f"Au total, {fr(tot * 0.93)} {c}."),
                    ("doublé", f"{haut} arrive en tête avec {fr(g[haut] * 2)}."),
                    ("moyenne +25 %", f"La moyenne mensuelle par centre est de {fr(df[c].mean() * 1.25, 1)}."),
                    ("part +12 points", f"{haut} représente {fr(part + 12, 1)} % du total.")]
    return justes, fausses


@pytest.fixture(scope="module")
def banc():
    reset_app_modules()
    import verif_chiffres as V
    df = J.sante()
    return V, V.sources(_faits(df)), *_reponses(df)


def test_aucune_fausse_alerte_sur_les_reponses_justes(banc):
    V, refs, justes, _ = banc
    alertes = [(t, V.verifier(t, refs)) for t in justes if V.verifier(t, refs)]
    assert not alertes, alertes


@pytest.mark.parametrize("genre", ["total +10 %", "total -7 %", "doublé", "moyenne +25 %"])
def test_erreur_glissee_reperee(banc, genre):
    V, refs, _, fausses = banc
    vues = [t for g, t in fausses if g == genre and V.verifier(t, refs)]
    assert len(vues) == len(MESURES), f"{genre} : {len(vues)} sur {len(MESURES)} repérées"


def test_taux_de_detection_global(banc):
    """Garde-fou : la vérification ne doit plus jamais redevenir complaisante (0 sur 24 avant ce banc)."""
    V, refs, _, fausses = banc
    vues = sum(1 for _, t in fausses if V.verifier(t, refs))
    assert vues >= 16, f"{vues} erreurs repérées sur {len(fausses)}"
