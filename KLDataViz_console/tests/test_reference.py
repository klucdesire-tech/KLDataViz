# -*- coding: utf-8 -*-
"""Jeu de référence : l'exemple fictif (12 centres de santé × 18 mois) est recalculé indépendamment avec pandas,
et chaque chiffre affiché par KLDataViz doit être identique. Toute régression de calcul fait échouer ces tests."""
import os
import sys
import tempfile
import threading

import pandas as pd
import pytest

from _harness import call, reset_app_modules, start


@pytest.fixture()
def env(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    import exemple  # noqa: PLC0415
    monkeypatch.setattr(server, "_llm_config", lambda: None)  # règles seules : aucun appel réseau
    sys.path.insert(0, os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA"))
    from hermes_da import config as hconfig  # noqa: PLC0415
    monkeypatch.setattr(hconfig, "CONSOLE_DIR", type(hconfig.CONSOLE_DIR)(tempfile.mkdtemp()))
    info = exemple.ecrire(tempfile.mkdtemp())
    return server, exemple.generer(), info["path"]


def test_exemple_deterministe():
    reset_app_modules()
    import exemple  # noqa: PLC0415
    a, b = exemple.generer(), exemple.generer()
    assert a.equals(b) and len(a) == 12 * 18
    assert (a["cpn4"] <= a["cpn1"]).all() and (a["cas_paludisme_confirmes"] <= a["tdr_realises"]).all()


def test_indicateurs_du_tableau_de_bord(env):
    server, df, path = env
    data, _ = server._read_df(path)
    k = {x["colonne"]: x for x in server.build_dashboard(data)["kpis"]}
    dernier = df[df["mois"] == df["mois"].max()]
    assert k["consultations"]["valeur"] == df["consultations"].sum()
    assert k["consultations"]["nom"] == "Total : consultations"
    assert k["cpn1"]["nom"] == "Total : CPN1" and k["cpn1"]["valeur"] == df["cpn1"].sum()
    assert k["population_desservie"]["valeur"] == dernier["population_desservie"].sum()      # stock : dernier mois
    assert k["population_desservie"]["nom"] == "Population desservie (juin 2025)"
    assert k["rupture_stock_cta"]["valeur"] == round((df["rupture_stock_cta"] == "Oui").mean(), 4)


def test_graphiques_de_l_analyse(env):
    server, df, path = env
    a = server.analyze(path)
    ch = {c["title"]: c for c in a["smart_charts"]}
    par_district = df.groupby("district")["consultations"].sum().sort_values(ascending=False)
    rk = ch["Consultations par district"]
    assert rk["labels"][0] == par_district.index[0] and rk["values"][0][0] == par_district.iloc[0]
    tr = next(c for c in a["smart_charts"] if c["kind"] == "trend")
    assert tr["labels"] == sorted(df["mois"].unique()) and "incomplet" not in tr["subtitle"]  # mois complets
    assert tr["values"][0] == df.groupby("mois")["consultations"].sum().tolist()
    taux = (df["rupture_stock_cta"] == "Oui").groupby(df["district"]).mean().mul(100).sort_values(ascending=False)
    t = ch["Taux « rupture stock CTA » par district"]
    assert t["labels"][0] == taux.index[0] and abs(t["values"][0][0] - taux.iloc[0]) < 1e-3
    assert a["libelles"]["tdr_realises"] == "TDR réalisés"


def test_bouton_exemple(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    import tenancy as T  # noqa: PLC0415
    d = tempfile.mkdtemp()
    monkeypatch.setattr(T, "uploads_dir", lambda: d)  # jamais dans le vrai dossier d'import
    httpd, base = start(server)
    try:
        code, d = call(base, "/api/exemple", {})
        assert code == 200 and d["name"].endswith("fictif.csv") and os.path.exists(d["path"])
    finally:
        httpd.shutdown()


def test_serveur_occupe_plutot_que_sature(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    import tenancy as T  # noqa: PLC0415
    monkeypatch.setattr(server, "_HEAVY", threading.BoundedSemaphore(1))
    monkeypatch.setattr(server, "_HEAVY_WAIT", 0.1)
    assert server._HEAVY.acquire()  # un traitement lourd est déjà en cours
    try:
        with pytest.raises(T.Unavailable):
            server.analyze("x.csv")
    finally:
        server._HEAVY.release()


def test_registre_de_visites_garde_ses_nouveaux_patients():
    """Relecture de Hermes : un patient vu une fois par mois n'est pas un « rapport par centre »."""
    reset_app_modules()
    import server  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import generate_dashboard  # noqa: PLC0415
    mois = [f"2025-{m:02d}" for m in range(1, 7)]
    df = pd.DataFrame([{"id_patient": f"P{p}", "mois": m, "sexe": "F" if p % 2 else "M", "cd4": 300 + p,
                        "cv_supprimee": "Oui" if p % 3 else "Non"} for p in range(40) for m in mois])
    gaps = generate_dashboard(df, "t", llm=False).get("gaps") or []
    assert any(g["indicateur"].startswith("Nouveaux") for g in gaps)  # compteur de lignes conservé
