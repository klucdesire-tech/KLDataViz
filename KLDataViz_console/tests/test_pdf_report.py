# -*- coding: utf-8 -*-
"""Rapport PDF détaillé : contenu, lignes de cumul écartées, mois non éclatés en semaines."""
import os
import tempfile

from _harness import reset_app_modules


def _load():
    reset_app_modules()
    import server  # noqa: PLC0415
    import pdf_report  # noqa: PLC0415
    return server, pdf_report


def test_rapport_pdf_detaille():
    server, pdf_report = _load()
    lines = ["site;mois;depistes;positifs"]
    for i, s in enumerate(["CSU Alpha", "CSR Beta", "HG Gamma", "CSR Delta"]):
        for m in ["2026-01", "2026-02", "2026-03", "2026-04"]:
            lines.append(f"{s};{m};{100 + 20 * i};{3 + i}")
    lines.append("Total;2026-04;9999;99")
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "sites.csv")
        with open(src, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        r = server.analyze(src)
        trend = [c for c in r["smart_charts"] if c["kind"] == "trend"]
        assert trend and trend[0]["labels"][0] == "2026-01"  # mois, pas semaines

        # La ligne « Total » est repérée par l'analyse et absente des graphiques
        assert r["lignes_cumul"] == {"lignes": 1, "libelles": ["Total"]}
        assert all("Total" not in c.get("labels", []) for c in r["smart_charts"])
        charts, totals = pdf_report.drop_totals([{"kind": "ranking", "type": "bar", "labels": ["A", "B", "Total"],
                                                  "values": [[1, 2, 3]], "ref": {"value": 2}}])
        assert totals == ["Total"] and charts[0]["labels"] == ["A", "B"] and charts[0]["ref"]["value"] == 1.5

        out = os.path.join(d, "rapport.pdf")
        pdf_report.build(dict(r, _insights=[{"tag": "Tendance", "severity": "ok", "text": "Hausse de **20 %**."}]), out)
        data = open(out, "rb").read()
        assert data[:4] == b"%PDF" and len(data) > 10_000


def test_interpretation_classement():
    _, pdf_report = _load()
    txt = pdf_report.interpret_chart({"kind": "ranking", "type": "bar", "labels": ["A", "B", "C", "D"],
                                      "values": [[50, 30, 15, 5]], "ref": {"value": 25}})
    assert "A</b> arrive en tête" in txt and "50,0 %" in txt and "1 à 10" in txt
