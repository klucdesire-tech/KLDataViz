# -*- coding: utf-8 -*-
"""Registre décrit : titre et résumé repris de Hermes, changements de version calculés par règles."""
import tempfile

import pandas as pd

from _harness import reset_app_modules


def _load():
    reset_app_modules()
    import server  # noqa: PLC0415
    import registry_meta  # noqa: PLC0415
    return server, registry_meta


def _df(mois, extra=False):
    rows = [{"site": f"S{i}", "mois": m, "file_active": 100 + i} for i in range(3) for m in mois]
    df = pd.DataFrame(rows)
    if extra:
        df["nouveaux_cas"] = 2
    return df


COMP = {"titre": "File active VIH par site et par mois", "mots_cles": ["VIH", "file active", "Nawa"],
        "resume": "Suivi mensuel de la file active.", "grain": "un site pour un mois", "periode": "2025-01 à 2025-02",
        "colonnes": [{"nom": "file_active", "role": "mesure_stock", "sens": "patients suivis"}]}


def test_deux_versions_decrites():
    server, RM = _load()
    with tempfile.TemporaryDirectory() as d:
        m1 = RM.describe(d, "ds-1", 1, _df(["2025-01", "2025-02"]), server._period_series, COMP, "fa.csv")
        assert m1["titre"] == COMP["titre"] and m1["source"] == "hermes" and m1["mots_cles"] == ["VIH", "file active", "Nawa"]
        assert m1["versions"]["1"]["changements"] == ["Première version enregistrée."]
        m2 = RM.describe(d, "ds-1", 2, _df(["2025-01", "2025-02", "2025-03"], extra=True), server._period_series, None, "fa.csv")
        ch = " ".join(m2["versions"]["2"]["changements"])
        assert "+3 ligne(s) (6 → 9)" in ch and "nouveaux_cas" in ch and "2025-03" in ch
        assert m2["titre"] == COMP["titre"]                          # la description de Hermes est conservée
        assert RM.summary(RM.load(d, "ds-1"))["periode"] == COMP["periode"]


def test_sans_hermes_titre_et_periode_par_regles():
    server, RM = _load()
    with tempfile.TemporaryDirectory() as d:
        m = RM.describe(d, "ds-2", 1, _df(["2025-01", "2025-04"]), server._period_series, None, "export.csv")
        assert m["titre"] == "export.csv" and m["periode"] == "2025-01 à 2025-04" and "source" not in m


def test_identifiant_assaini():
    _, RM = _load()
    with tempfile.TemporaryDirectory() as d:
        assert RM.load(d, "../../etc/passwd") == {}                   # aucun chemin hors du dossier
