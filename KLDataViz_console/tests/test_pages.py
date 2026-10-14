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
