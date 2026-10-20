# -*- coding: utf-8 -*-
"""Sources connectées : actualiser sans réimporter, mêmes indicateurs, sans IA (mode local, fichier fictif)."""
import datetime as dt
import os
import sys
import tempfile

import pandas as pd

from _harness import reset_app_modules


def _setup(monkeypatch):
    reset_app_modules()
    import sources  # noqa: PLC0415
    d = tempfile.mkdtemp(prefix="kldv-src-")
    monkeypatch.setenv("KLDV_SOURCES_DIR", d)  # stockage isolé du test
    src = os.path.join(d, "suivi.csv")
    pd.DataFrame({"region": ["A", "B"], "mois": ["2025-08", "2025-08"], "depistes": [10, 20]}).to_csv(src, index=False)
    return sources, src


def test_actualiser_une_source_connectee(monkeypatch):
    sources, src = _setup(monkeypatch)
    builds = []

    def read_df(path):
        return pd.read_csv(path), None

    def build(df, spec):
        builds.append(spec)
        return {"html": f"<html>{int(df['depistes'].sum())}</html>", "spec": spec}
    s = sources.connect(src, "suivi", {"kpis": [{"nom": "Dépistés"}], "_ia": True}, pd.read_csv(src), "<html>30</html>")
    assert s and s["freq"] == "off"
    r = sources.refresh(s["id"], read_df, build)
    assert r["changed"] is False and not builds                         # rien n'a changé : rien n'est recalculé
    pd.DataFrame({"region": ["A", "B", "A"], "mois": ["2025-08", "2025-08", "2025-09"],
                  "depistes": [10, 20, 5]}).to_csv(src, index=False)
    r = sources.refresh(s["id"], read_df, build)
    assert r["changed"] and r["html"] == "<html>35</html>" and r["rows"] == 3
    assert builds == [{"kpis": [{"nom": "Dépistés"}], "_ia": True}]     # même spec : pas de nouvel appel à l'IA
    pd.DataFrame({"region": ["A"], "depistes": [1], "nouvelle": [2]}).to_csv(src, index=False)
    r = sources.refresh(s["id"], read_df, build)
    assert r["structure_changed"] and "Régénérer" in r["status"]


def test_frequence_et_echeance(monkeypatch):
    sources, src = _setup(monkeypatch)
    s = sources.connect(src, "suivi", {"kpis": []}, pd.read_csv(src), "<html></html>")
    assert sources.due() == []
    s = sources.set_freq(s["id"], "hourly")
    assert s["next_refresh"] and sources.due() == []
    assert sources.due(dt.datetime.now() + dt.timedelta(hours=2)) == [s["id"]]
    sources.delete(s["id"])
    assert sources.list_sources() == []


def test_agt11_reutilise_la_spec_sans_ia():
    reset_app_modules()
    import server  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA"))
    from hermes_da.adapters import dashboard as d  # noqa: PLC0415
    df = pd.DataFrame({"region": ["A", "B"] * 3, "depistes": [1, 2, 3, 4, 5, 6]})
    spec = {"kpis": [{"nom": "Dépistés", "agregation": "somme", "colonne": "depistes"}], "graphiques": [], "_ia": True}

    def interdit(*a, **k):
        raise AssertionError("l'IA ne doit pas être appelée lors d'une actualisation")
    out = d.generate_dashboard(df, "t", llm=interdit, spec=spec)
    assert out["ia"] is True and out["kpis"][0]["valeur"] == 21 and out["spec"]["kpis"][0]["nom"] == "Dépistés"
