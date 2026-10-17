# -*- coding: utf-8 -*-
"""Rapports planifiés commentés : instantané, écarts avec l'envoi précédent, commentaire Hermes ou règles."""
import json
import os
import tempfile

import pandas as pd

from _harness import reset_app_modules


def _load():
    reset_app_modules()
    import server  # noqa: PLC0415
    import hermes_report  # noqa: PLC0415
    return server, hermes_report


def _csv(path, n_mois, bump=0):
    mois = ["2025-01", "2025-02", "2025-03", "2025-04"][:n_mois]
    rows = [{"district": d, "mois": m, "nouveaux_cas": 10 + 5 * i + j + (bump if d == "Buyo" else 0)}
            for i, d in enumerate(["Soubré", "Méagui", "Buyo"]) for j, m in enumerate(mois)]
    pd.DataFrame(rows).to_csv(path, index=False)


def test_deux_envois_successifs():
    server, HR = _load()
    with tempfile.TemporaryDirectory() as d:
        src, snaps = os.path.join(d, "cas.csv"), os.path.join(d, "snap")
        os.makedirs(snaps)
        _csv(src, 3)
        first = HR.run("job1", server.analyze(src), snaps)
        assert first["depuis"] is None and "Premier envoi" in first["resume"] and first["ia"] is False
        _csv(src, 4, bump=20)                                   # un mois de plus, Buyo en forte hausse
        second = HR.run("job1", server.analyze(src), snaps)
        assert second["depuis"] and second["evolutions"]
        assert any("Buyo" in e for e in second["evolutions"])


def test_commentaire_hermes_et_email():
    server, HR = _load()
    seen = []

    def llm(messages, max_tokens):
        seen.append(json.loads(messages[1]["content"]))
        return json.dumps({"resume": "Les cas **augmentent**.", "evolutions": ["Buyo : 30 → 50."], "alertes": ["Hausse à Buyo."]})

    prev = {"date": "2026-10-01T07:00:00", "lignes": 9, "moyennes": {"x": 10}, "graphiques": []}
    cur = {"date": "2026-11-01T07:00:00", "lignes": 12, "moyennes": {"x": 12}, "graphiques": []}
    ev = HR.comment(prev, cur, HR.compare(prev, cur), llm, server._parse_llm_json)
    assert ev["ia"] and ev["alertes"] == ["Hausse à Buyo."]
    assert seen[0]["ecarts"][0]["sujet"] == "lignes"        # Hermes reçoit les écarts calculés
    txt = HR.email_text("Cas VIH", {"rows": 12, "completeness_pct": 100}, ev)
    assert "Points d'attention" in txt and "Hausse à Buyo." in txt


def test_pdf_avec_evolution():
    server, HR = _load()
    import pdf_report  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "cas.csv")
        _csv(src, 3)
        r = dict(server.analyze(src), _evolution={"resume": "Hausse.", "evolutions": ["A"], "alertes": ["B"],
                                                   "ia": True, "depuis": "2026-10-01T07:00:00"})
        out = os.path.join(d, "r.pdf")
        pdf_report.build(r, out)
        assert open(out, "rb").read()[:4] == b"%PDF"
