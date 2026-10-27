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


def test_lecture_hallucinee_remplacee():
    """L'IA cite un nombre absent des vraies valeurs → la lecture déterministe (fidèle) la remplace."""
    server, HC = _load()
    calls = []

    def llm(messages, max_tokens):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(SPECS)
        return json.dumps({"lectures": [{"index": 0, "lecture": "La file active passe de 100 à 999."}], "synthese": ""})

    r = HC.run(_df(), "fa.csv", [], llm, server._parse_llm_json, server._period_series)
    evo = r["charts"][0]
    assert "999" not in evo["lecture"]          # le chiffre inventé a disparu
    assert "750" in evo["lecture"]              # remplacé par la lecture déterministe (vraies valeurs)


def test_lecture_fidele_conservee():
    """Une lecture aux chiffres cohérents est conservée telle quelle."""
    server, HC = _load()
    calls = []

    def llm(messages, max_tokens):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(SPECS)
        # valeurs du fichier _df : file active = 750 → 768 sur les 4 mois
        return json.dumps({"lectures": [{"index": 0, "lecture": "La file active progresse de 750 à 768."}], "synthese": ""})

    r = HC.run(_df(), "fa.csv", [], llm, server._parse_llm_json, server._period_series)
    assert "750" in r["charts"][0]["lecture"]   # lecture fidèle conservée


def test_verification_lecture_ecritures_et_derives():
    """Écritures « 12 k » / « 1 800 » et écarts/variations reconnus ; somme ou valeur inventée refusée."""
    _, HC = _load()
    c = {"kind": "trend", "labels": ["2025-01", "2025-02", "2025-03", "2025-04"],
         "values": [[12000, 12500, 13100, 13800]], "seriesNames": ["Consultations"]}
    for ok in ("Les consultations passent de 12 k à 13,8 k.",
               "Elles augmentent de 1 800 entre janvier et avril (+15 %).",
               "Hausse de 700 consultations en avril."):
        assert HC._lecture_est_fidele(c, ok), ok
    for faux in ("Les consultations atteignent 25 000 en avril.",   # somme de deux mois, pas une valeur
                 "Les consultations atteignent 14 500."):
        assert not HC._lecture_est_fidele(c, faux), faux


def test_lecture_deterministe_exacte_et_alignee():
    """Chiffres exacts (pas « 14 k » pour 13 800), libellés alignés malgré les valeurs manquantes, départ à 0."""
    _, HC = _load()
    t = HC._reading_fallback({"kind": "trend", "labels": ["2025-01", "2025-04"],
                              "values": [[12000, 13800]], "seriesNames": ["Consultations"]})
    assert "13 800" in t and "14 k" not in t
    t = HC._reading_fallback({"kind": "trend", "labels": ["a", "b", "c", "d"],
                              "values": [[None, 10, 50, 20]], "seriesNames": ["X"]})
    assert "entre b et d" in t and "pic est en c (50)" in t and "plus bas en b (10)" in t
    t = HC._reading_fallback({"kind": "trend", "labels": ["a", "b"], "values": [[0, 40]], "seriesNames": ["X"]})
    assert t.startswith("X passe de 0 à 40") and "stable" not in t
    t = HC._reading_fallback({"kind": "ranking", "labels": ["A", "B"], "values": [[1499, 1020]]})
    assert "1 499" in t and "1 020" in t


def test_sens_d_evolution_contredit():
    """Une lecture qui annonce une baisse sur une série qui ne fait que monter (ou l'inverse) est rejetée ;
    hausse et baisse mêlées (creux ponctuel) restent acceptées ; « montant » n'est pas une hausse."""
    _, HC = _load()
    monte = {"kind": "trend", "values": [[12000, 12500, 13100, 13800]]}
    creux = {"kind": "trend", "values": [[12000, 11500, 13100, 13800]]}
    descend = {"kind": "trend", "values": [[100, 90, 80]]}
    assert HC._sens_contredit(monte, "Baisse de 1 800 consultations entre janvier et avril.")
    assert not HC._sens_contredit(monte, "Les consultations progressent de 1 800.")
    assert not HC._sens_contredit(creux, "Les consultations baissent en février.")
    assert not HC._sens_contredit(creux, "Après un recul en février, elles progressent.")
    assert HC._sens_contredit(descend, "Le nombre de cas augmente.")
    assert not HC._sens_contredit(descend, "Le montant total diminue.")
    assert not HC._sens_contredit(monte, "Le plus bas est en janvier, le pic en avril.")
    assert not HC._sens_contredit({"kind": "ranking", "values": [[3, 1]]}, "Baisse")
