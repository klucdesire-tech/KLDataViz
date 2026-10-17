# -*- coding: utf-8 -*-
"""Corrections de Hermes : avis sur les règles, recettes validées sur les données, application avant les règles."""
import json
import os
import tempfile

import pandas as pd

from _harness import reset_app_modules


def _load():
    reset_app_modules()
    import server  # noqa: PLC0415
    import hermes_clean  # noqa: PLC0415
    return server, hermes_clean


def _df():
    return pd.DataFrame({
        "code_site": ["0101", "0102", "0103", "0104", "0105", "TOTAL"],
        "district": ["Soubré", "SOUBRE", "soubre ", "Méagui", "Meagui", "Total"],
        "nouveaux_cas": [3, None, 5, 2, 4, 14],
        "taux_cv": ["85", "-", "92", "140", "88", "90"],
    })


ANSWER = {
    "avis": [{"id": "imput:nouveaux_cas", "verdict": "deconseille", "raison": "un vide peut signifier aucun cas"},
             {"id": "id_inexistant", "verdict": "deconseille", "raison": "x"}],
    "corrections": [
        {"type": "harmoniser", "colonne": "district", "titre": "Harmoniser les districts",
         "correspondances": {"SOUBRE": "Soubré", "soubre": "Soubré", "Meagui": "Méagui"}},
        {"type": "remplacer", "colonne": "taux_cv", "valeurs": ["-"], "par": None, "titre": "Tiret = vide"},
        {"type": "exclure_lignes", "colonne": "district", "valeurs": ["Total"], "titre": "Ligne de total"},
        {"type": "plage", "colonne": "taux_cv", "min": 0, "max": 100, "titre": "Taux impossibles"},
        {"type": "texte", "colonne": "code_site", "titre": "Codes sites en texte"},
        {"type": "harmoniser", "colonne": "colonne_inventee", "correspondances": {"a": "b"}},
    ],
}


def test_avis_et_recettes_validees():
    server, H = _load()
    seen = []

    def llm(messages, max_tokens):
        seen.append(json.loads(messages[1]["content"]))
        return json.dumps(ANSWER)

    rule_items = [{"id": "imput:nouveaux_cas", "correction": "compléter 1 vide par la médiane"}]
    r = H.run(_df(), rule_items, "f.csv", llm, server._parse_llm_json)
    assert r["avis"] == [{"id": "imput:nouveaux_cas", "verdict": "deconseille", "raison": "un vide peut signifier aucun cas"}]
    types = [c["type"] for c in r["corrections"]]
    assert types == ["harmoniser", "remplacer", "exclure_lignes", "plage", "texte"]   # colonne inventée écartée
    assert any("colonne_inventee" in e for e in r["rejetees"])
    harm = r["corrections"][0]
    assert harm["correspondances"] == {"SOUBRE": "Soubré", "soubre": "Soubré", "Meagui": "Méagui"} and harm["impact"] == 3
    prof = seen[0]["colonnes"][1]
    assert ["SOUBRE", "Soubré", "soubre"] in prof["variantes_proches"]          # repérées par le moteur
    assert seen[0]["colonnes"][3]["valeurs_suspectes"] == {"-": 1}


def test_clean_avec_corrections_de_hermes():
    server, H = _load()
    corr, _ = H.validate(_df(), ANSWER["corrections"][:5])
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "f.csv")
        _df().to_csv(src, index=False)
        out = server.clean(src, skip=["imput:nouveaux_cas"], hermes=corr)  # taux_cv vidé par Hermes : pas recomplété
        assert "error" not in out and out["rows"] == 5                               # ligne Total retirée
        df = pd.read_csv(out["cleaned_path"], dtype=str)
        assert set(df["district"]) == {"Soubré", "Méagui"}
        assert df["code_site"].tolist()[0] == "0101"                                 # code gardé en texte
        assert df["taux_cv"].isna().sum() == 2                                       # « - » et 140 → vides
        assert [c["id"] for c in out["rapport"]["hermes"]] == ["h:0", "h:1", "h:2", "h:3", "h:4"]


def test_sans_ia():
    server, H = _load()
    assert H.run(_df(), [], "f.csv", None, server._parse_llm_json)["ia"] is False
