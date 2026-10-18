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
    # Style « Analytics » : période reconnue (courbe + sélecteur Mois/Trimestre/Année) et graphiques choisis transmis
    assert '"period": "mois"' in out["html"] and '"__p": "2025-01"' in out["html"]
    assert '"type": "bar"' in out["html"] and "janv. 2025 – févr. 2025" in out["html"]


def test_ergonomie_filtres_et_periode_au_jour():
    """Cas « carte carburant » : identifiant, dates d'un seul mois, montants, valeurs vides."""
    import sys  # noqa: PLC0415
    reset_app_modules()
    import server  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import _meta, _period  # noqa: PLC0415
    df = pd.DataFrame({"N°": range(1, 9),
                       "Date": pd.to_datetime(["2024-04-03", "2024-04-05", None, "2024-04-12", "2024-04-16",
                                               "2024-04-19", "2024-04-19", "2024-04-23"]),
                       "Libellés (objet, N° véh)": ["Prise", "Recharge", None, "Prise", "Solde", "Prise", "Recharge", "Prise"],
                       "Solde /carte": ["113215", "165110", None, "93010", "185215", "113215", "93010", "238215"],
                       "Sorties": [20000, None, 35000, 17000, 20000, None, 17000, 35000]})
    pc, ps = _period(df)
    meta = _meta(df, [{"nom": "Sorties", "agregation": "somme", "colonne": "Sorties"}], {"graphiques": []},
                 [], pc, ps)
    assert pc == "Date" and ps.dropna().iloc[0] == "2024-04-03"                       # au jour près
    assert meta["gran"] == "D" and meta["grans"] == ["D", "W", "M"] and meta["range"] == "3 – 23 avr. 2024"
    assert [f["col"] for f in meta["filters"]] == ["Libellés (objet, N° véh)"]        # ni N°, ni date, ni montants
    assert meta["filters"][0]["values"] == ["Prise", "Recharge", "Solde"]              # sans valeur vide


def test_sans_periode_graphiques_par_categorie():
    """Export par structure (pas de date) : barres + anneau par catégorie ; aucun code JS masqué par un commentaire."""
    import sys  # noqa: PLC0415
    reset_app_modules()
    import server  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import TEMPLATE, _meta, _period  # noqa: PLC0415
    df = pd.DataFrame({"structure": ["BUYO", "GUEYO", "MEAGUI", "NAWA", "SOUBRE"], "patients": [300, 150, 620, 1700, 600]})
    pc, ps = _period(df)
    meta = _meta(df, [{"nom": "Patients", "agregation": "somme", "colonne": "patients"}], {"graphiques": []},
                 ["structure"], pc, ps)
    assert pc is None and meta["measure"]["colonne"] == "patients"
    assert "c_cat" in TEMPLATE and "c_catd" in TEMPLATE                    # barres + part du total
    for line in TEMPLATE.splitlines():                                     # régression PR #38 : code après « // »
        if "used['k'+k.nom]=1" in line:
            assert "//" not in line.split("used['k'+k.nom]=1")[0]
