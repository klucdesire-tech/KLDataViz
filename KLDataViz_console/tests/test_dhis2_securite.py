# -*- coding: utf-8 -*-
"""DHIS2 : coffre par espace, clé hors disque en ligne, pas de redirection vers un autre serveur,
périodes lisibles par AGT-11, connexion depuis l'interface (réponse simulée, aucun appel réseau)."""
import io
import json
import urllib.request

import pytest

from _harness import reset_app_modules

ANALYTICS = {"headers": [{"name": "dx"}, {"name": "ou"}, {"name": "pe"}, {"name": "value"}],
             "rows": [["a1", "o1", "202501", "10"], ["a1", "o1", "202502", "12"]],
             "metaData": {"items": {"a1": {"name": "Dépistés VIH"}, "o1": {"name": "Soubré"}}}}


def test_coffre_par_espace_et_cle_derivee(tmp_path, monkeypatch):
    reset_app_modules()
    import dhis2  # noqa: PLC0415
    monkeypatch.setenv("KLDV_SECRET", "secret-serveur")
    monkeypatch.setenv("KLDV_DHIS2_DIR", str(tmp_path / "espace-a"))
    dhis2.save_credentials("dhis2.test/1", "lecteur", "mdp")
    assert dhis2.load_credentials("dhis2.test/1") == ("lecteur", "mdp")
    assert not (tmp_path / "espace-a" / "dhis2.key").exists()          # en ligne : aucune clé sur le disque
    assert b"mdp" not in (tmp_path / "espace-a" / "dhis2_credentials.enc").read_bytes()
    monkeypatch.setenv("KLDV_DHIS2_DIR", str(tmp_path / "espace-b"))
    assert dhis2.load_credentials("dhis2.test/1") is None               # un autre espace ne voit rien


def test_periodes_et_redirection(monkeypatch):
    reset_app_modules()
    import dhis2  # noqa: PLC0415
    df = dhis2._analytics_to_long(ANALYTICS)
    assert list(df["periode"]) == ["2025-01", "2025-02"] and df["indicateur"][0] == "Dépistés VIH"
    h = dhis2._SameHostRedirect("dhis2.test", allow_private=False)
    with pytest.raises(Exception, match="autre serveur"):
        h.redirect_request(urllib.request.Request("https://dhis2.test/api/x"), None, 302, "", {}, "https://pirate.test/x")


def test_connexion_depuis_l_interface(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    seen = {}

    def fake_fetch(url, user, password, allow_private=False):
        seen.update(url=url, user=user)
        return json.loads(json.dumps(ANALYTICS))
    monkeypatch.setattr(server.dhis2, "_fetch_json", fake_fetch)
    out = server.dhis2_connect({"link": "https://dhis2.test/ci/api/analytics.json?dimension=pe:LAST_12_MONTHS",
                                "user": "lecteur", "password": "mdp"})
    assert out["uri"] == "dhis2://dhis2.test/ci/api/analytics.json?dimension=pe:LAST_12_MONTHS"
    assert out["rows"] == 2 and out["indicateurs"] == 1 and "mdp" not in json.dumps(out)
    assert seen == {"url": "https://dhis2.test/ci/api/analytics.json?dimension=pe:LAST_12_MONTHS", "user": "lecteur"}
    df, err = server._read_df(out["uri"])                               # relu à chaque actualisation
    assert err is None and len(df) == 2
    with pytest.raises(ValueError, match="https://"):
        server.dhis2_connect({"link": "http://dhis2.test/api/analytics", "user": "u", "password": "p"})


def test_dhis2_agrege_sans_compteur_de_patients():
    """Export DHIS2 (format long agrégé) : pas de « Nouveaux patients » = nombre d'unités d'organisation."""
    import os  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    reset_app_modules()
    import server  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA"))
    from hermes_da.adapters import dashboard as d  # noqa: PLC0415
    df = pd.DataFrame([{"indicateur": "ANC 1 Coverage", "periode": p, "unite_organisation": o, "valeur": v}
                       for p, v in (("2025-08", 90), ("2025-09", 95)) for o in ("Bo", "Kenema", "Bombali")])
    spec = {"kpis": [{"nom": "Couverture ANC 1", "agregation": "moyenne", "colonne": "ANC 1 Coverage"}],
            "graphiques": []}
    out = d.generate_dashboard(df, "t", llm=False, spec=spec)
    noms = [g["indicateur"] for g in out["gaps"]]
    assert "Nouveaux patients" not in noms and "Nouveaux enregistrements" not in noms and noms == ["Couverture ANC 1"]
