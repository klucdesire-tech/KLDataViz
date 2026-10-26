# -*- coding: utf-8 -*-
"""Montants écrits avec leur devise : convertis en nombres, jamais vidés puis complétés par la médiane."""
import pandas as pd

import modeling


def test_montants_avec_devise_convertis():
    s = pd.Series(["1 234 567 FCFA", "700479", "12,5 €", "F CFA 3 000", "$45", "2 000 XOF", None] * 4)
    out, t = modeling._coerce_series(s)
    assert t == "numérique"
    assert out.tolist()[:6] == [1234567.0, 700479.0, 12.5, 3000.0, 45.0, 2000.0] and pd.isna(out.iloc[6])


def test_texte_ordinaire_inchange():
    assert modeling._coerce_series(pd.Series(["Plateau", "Cocody", "Treichville"] * 5))[1] is None
    assert modeling._coerce_series(pd.Series(["F", "M"] * 5))[1] is None   # « F » seul n'est pas une devise


def test_codes_colles_a_un_sigle_restent_du_texte():
    assert modeling._coerce_series(pd.Series(["CFA2023", "XOF-12", "EUR2024", "CFA2025"] * 5))[1] is None
