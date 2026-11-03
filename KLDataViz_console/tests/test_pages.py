# -*- coding: utf-8 -*-
"""Chaque élément ciblé par les scripts ($('#id')) doit exister dans la page.
Un élément manquant fait planter tout le script au chargement (cas réel : #adminList)."""
import os
import re

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("page", ["index.html", "landing.html", "admin.html", "login.html"])
def test_elements_cibles_presents(page):
    html = open(os.path.join(BASE, page), encoding="utf-8").read()
    ids = set(re.findall(r'id="([\w-]+)"', html))
    used = set(re.findall(r"\$\('#([\w-]+)'\)", html))
    manquants = sorted(used - ids)
    assert not manquants, f"{page} : éléments utilisés mais absents : {manquants}"


def test_bouton_flottant_du_chat():
    """Le bouton qui rouvre la discussion flotte en bas à droite, hors de la barre du haut, avec un compteur."""
    html = open(os.path.join(BASE, "index.html"), encoding="utf-8").read()
    haut = html[html.index('<div class="top">'):html.index('<div id="annBar"')]
    assert 'id="chatOpen"' not in haut and 'class="fab" id="chatOpen"' in html
    assert re.search(r"\.fab\{[^}]*position:fixed;right:24px", html)
    assert "nonLus(nonLus()+1)" in html and "if(on)nonLus(0)" in html


def test_contenu_sur_toute_la_largeur():
    """Les pages occupent toute la largeur disponible (plus de plafond à 1100 px)."""
    html = open(os.path.join(BASE, "index.html"), encoding="utf-8").read()
    vue = re.search(r"\n\.view\{([^}]*)\}", html).group(1)
    assert "max-width" not in vue


def test_essai_termine_mene_aux_forfaits():
    """Quota épuisé ou essai terminé : « Voir les forfaits » remplace « Réessayer » ; en ligne, pas de « cet ordinateur »."""
    html = open(os.path.join(BASE, "index.html"), encoding="utf-8").read()
    assert 'id="runPlans" hidden' in html and "$('#runPlans').addEventListener('click',()=>voirForfaits())" in html
    assert 'id="quotaBar"' in html and "quotaBar(me);" in html  # bandeau d'épuisement mis à jour avec le compte
    assert "$('#runRetry').hidden=!!a.quota;$('#runPlans').hidden=!a.quota" in html
    assert "$('#runWhere').textContent='Le traitement se fait sur nos serveurs" in html
