# -*- coding: utf-8 -*-
"""Graphiques choisis par Hermes : recettes exécutées et vérifiées, lectures reprises, repli sans IA."""
import json
import os
import tempfile

import pandas as pd

from _harness import reset_app_modules


def _load():
    reset_app_modules()
    import server  # noqa: PLC0415
    import hermes_charts  # noqa: PLC0415
    return server, hermes_charts


def _df():
    mois = ["Décembre 2024", "Janvier 2025", "Février 2025", "Mars 2025"]
    rows = [{"site": f"Site {i}", "district": "Méagui" if i % 2 else "Soubré", "periode": m, "file_active": 100 + 10 * i + j}
            for i in range(6) for j, m in enumerate(mois)]
    return pd.DataFrame(rows)


SPECS = {"graphiques": [
    {"type": "evolution", "titre": "File active par mois", "question": "La file active progresse-t-elle ?",
     "mesure": "file_active", "agregation": "somme", "axe": "periode", "serie": None, "filtre": None},
    {"type": "classement", "titre": "File active par site", "mesure": "file_active", "agregation": "somme", "axe": "site"},
    {"type": "classement", "titre": "Colonne inventée", "mesure": "inconnue", "agregation": "somme", "axe": "site"},
    {"type": "repartition", "titre": "Part par district", "mesure": "file_active", "agregation": "somme", "axe": "district"},
]}


def test_recettes_executees_et_lectures():
    server, HC = _load()
    calls = []

    def llm(messages, max_tokens):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(SPECS)
        return json.dumps({"lectures": [{"index": 0, "lecture": "La file active **augmente**."}], "synthese": "Hausse régulière."})

    r = HC.run(_df(), "fa.csv", [], llm, server._parse_llm_json, server._period_series)
    assert r["source"] == "hermes" and len(r["charts"]) == 3          # la recette à colonne inventée est écartée
    assert any("inconnue" in e for e in r["erreurs"])
    evo = r["charts"][0]
    assert evo["kind"] == "trend" and evo["labels"] == ["2024-12", "2025-01", "2025-02", "2025-03"]  # ordre du temps
    assert evo["lecture"] == "La file active **augmente**." and r["synthese"] == "Hausse régulière."
    assert r["charts"][1]["ref"]["label"] == "Moyenne" and r["charts"][2]["kind"] == "share"
    assert json.loads(calls[0][1]["content"])["lignes"] == 24  # Hermes reçoit le profil, pas les lignes


def test_repli_sans_ia():
    server, HC = _load()
    base = [{"kind": "ranking", "type": "bar", "title": "auto", "labels": ["a", "b"], "values": [[1, 2]]}]
    r = HC.run(_df(), "fa.csv", base, None, server._parse_llm_json, server._period_series)
    assert r["ia"] is False and r["charts"] == base


def test_route_tache_de_fond(monkeypatch):
    server, _ = _load()
    monkeypatch.setattr(server, "_llm_config", lambda: None)  # pas d'appel réseau pendant les tests
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "fa.csv")
        _df().to_csv(src, index=False)
        out = server.hermes_charts(src)
        assert out["ia"] is False and out["charts"]  # sans IA : graphiques automatiques


def test_stock_derniere_periode():
    server, HC = _load()
    g = {"type": "classement", "titre": "File active par site", "mesure": "file_active", "agregation": "derniere", "axe": "site"}
    c = HC.build_one(_df(), g, server._period_series)
    # dernière période = mars 2025 (j = 3) : 100 + 10 * i + 3, jamais la somme des 4 mois
    assert c["labels"][0] == "Site 5" and c["values"][0][0] == 153 and "2025-03" in c["subtitle"]


def test_lecture_par_regles_des_graphiques_automatiques():
    server, _ = _load()
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "fa.csv")
        _df().to_csv(src, index=False)
        charts = server.analyze(src)["smart_charts"]
        assert charts and all(c.get("lecture_auto") for c in charts)
        assert "<b>" not in charts[0]["lecture_auto"] and "**" in charts[0]["lecture_auto"]  # gras en markdown pour l'écran
        rank = next(c for c in charts if c["kind"] == "ranking")
        assert rank["subtitle"].startswith("Dernière période (2025-03)")              # file active = stock
        assert rank["values"][0][0] == 153                                             # Site 5 en mars, pas la somme


def test_tous_les_graphiques_sont_interpretes():
    """Chaque graphique généré a une lecture, même si l'IA n'en renvoie aucune (fallback déterministe)."""
    server, HC = _load()
    calls = []

    def llm(messages, max_tokens):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(SPECS)
        return json.dumps({"lectures": [], "synthese": ""})  # l'IA n'interprète rien

    r = HC.run(_df(), "fa.csv", [], llm, server._parse_llm_json, server._period_series)
    assert len(r["charts"]) == 3
    assert all(c.get("lecture") for c in r["charts"])  # aucun graphique sans lecture
    assert "tête" in r["charts"][1]["lecture"] or "arrive en tête" in r["charts"][1]["lecture"]
