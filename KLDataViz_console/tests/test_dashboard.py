# -*- coding: utf-8 -*-
"""Tableau de bord (agent AGT-11 de HERMES_DA) appelé par la console : avec l'IA de la console, ou par règles."""
import json
import os
import tempfile

import pandas as pd

from _harness import reset_app_modules


def _src(d):
    p = os.path.join(d, "fa.csv")
    pd.DataFrame([{"district": dd, "mois": m, "file_active": 100 + i * 10 + j}
                  for i, dd in enumerate(["Soubré", "Méagui", "Buyo"]) for j, m in enumerate(["2025-01", "2025-02"])]).to_csv(p, index=False)
    return p


def test_tableau_de_bord_par_regles(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    monkeypatch.setattr(server, "_llm_config", lambda: None)  # pas d'IA : repli par règles, aucun appel réseau
    with tempfile.TemporaryDirectory() as d:
        # Comme en ligne : pas de llm_config.json à côté de la console (sinon AGT-11 l'utiliserait en local)
        import sys  # noqa: PLC0415
        sys.path.insert(0, os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA"))
        from hermes_da import config as hconfig  # noqa: PLC0415
        monkeypatch.setattr(hconfig, "CONSOLE_DIR", type(hconfig.CONSOLE_DIR)(d))
        out = server.do_dashboard(_src(d))
        assert "error" not in out and out["ia"] is False and "<html" in out["html"].lower()


def test_tableau_de_bord_avec_l_ia_de_la_console(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    seen = []

    def fake_llm(messages, max_tokens, **kw):
        seen.append(messages)
        return json.dumps({"kpis": [{"nom": "File active", "agregation": "somme", "colonne": "file_active",
                                     "dimension": "district", "justification": "volume suivi"}],
                           "graphiques": [{"colonne": "district", "type": "bar", "justification": "comparaison"}]})
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "test"})
    monkeypatch.setattr(server, "_llm_call", fake_llm)
    with tempfile.TemporaryDirectory() as d:
        out = server.do_dashboard(_src(d))
    assert out["ia"] is True and seen                       # la clé vient de la console, pas de llm_config.json
    assert out["kpis"][0]["nom"] == "File active" and out["kpis"][0]["valeur"] == 663  # calculée sur les données
