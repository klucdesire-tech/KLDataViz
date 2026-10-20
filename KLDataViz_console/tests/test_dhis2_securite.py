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
    df.attrs["agrege"] = True  # posé par le connecteur DHIS2
    spec = {"kpis": [{"nom": "Couverture ANC 1", "agregation": "moyenne", "colonne": "ANC 1 Coverage"},
                     {"nom": "Total ANC 1", "agregation": "somme", "colonne": "ANC 1 Coverage"}], "graphiques": []}
    out = d.generate_dashboard(df, "t", llm=False, spec=spec)
    g = {x["indicateur"]: x for x in out["gaps"]}
    assert "Nouveaux patients" not in g and set(g) == {"Couverture ANC 1", "Total ANC 1"}
    assert g["Total ANC 1"]["derniere"] == 285 and round(g["Total ANC 1"]["pct"], 2) == 5.56   # somme du mois comparée
    # Vrai fichier patient au format long, un seul indicateur, sans drapeau : jamais pivoté, compteur conservé
    pat = pd.DataFrame({"indicateur": ["Poids"] * 4, "periode": ["2025-08", "2025-08", "2025-09", "2025-09"],
                        "valeur": [60, 72, 65, 70]})
    assert d._long_to_wide(pat)[1] is False


def test_coffre_illisible_mis_de_cote_et_essai_avant_enregistrement(tmp_path, monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    dh = server.dhis2
    monkeypatch.setenv("KLDV_SECRET", "ancienne-cle")
    dh.save_credentials("a.test", "u", "p")
    monkeypatch.setenv("KLDV_SECRET", "nouvelle-cle")                   # clé changée : coffre illisible
    assert dh.load_credentials("a.test") is None
    dh.save_credentials("b.test", "u2", "p2")
    import glob, os  # noqa: E401,PLC0415
    assert glob.glob(os.path.join(dh._storage_dir(), "dhis2_credentials.enc.illisible-*"))  # pas écrasé : mis de côté
    monkeypatch.setattr(dh, "_fetch_json", lambda *a, **k: (_ for _ in ()).throw(
        __import__("urllib.error").error.HTTPError("u", 401, "x", {}, None)))
    out = server.dhis2_connect({"link": "https://c.test/api/analytics.json?x=1", "user": "u", "password": "faux"})
    assert "401" in out["error"] and dh.load_credentials("c.test") is None   # mauvais mot de passe : rien d'enregistré


def test_isolation_des_espaces_en_mode_en_ligne(tmp_path, monkeypatch):
    """Vrai chemin tenancy (KLDV_MODE=cloud, deux T.use) — pas seulement la variable d'environnement."""
    import uuid  # noqa: PLC0415
    monkeypatch.delenv("KLDV_DHIS2_DIR", raising=False)
    monkeypatch.setenv("KLDV_MODE", "cloud")
    monkeypatch.setenv("KLDV_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KLDV_SECRET", "secret-serveur")
    try:
        reset_app_modules()
        import dhis2  # noqa: PLC0415
        import tenancy as T  # noqa: PLC0415
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        with T.use(T.Ctx(key="a", org_id=a)):
            dhis2.save_credentials("dhis2.test", "lecteur", "mdp")
            assert dhis2.load_credentials("dhis2.test") == ("lecteur", "mdp")
            assert dhis2._storage_dir().startswith(str(tmp_path / "tenants" / a))
        with T.use(T.Ctx(key="b", org_id=b)):
            assert dhis2.load_credentials("dhis2.test") is None             # l'espace B ne voit rien
        assert not list(tmp_path.rglob("dhis2.key"))                        # aucune clé sur le disque
    finally:
        monkeypatch.undo()
        reset_app_modules()                                                 # les tests suivants repartent en local


def test_drapeau_agrege_survit_aux_corrections(tmp_path, monkeypatch):
    """Scénario mesuré par Hermes : DHIS2 à 1 indicateur → corrections appliquées (CSV nettoyé) → tableau de bord.
    Le drapeau « agrégé » doit survivre à l'aller-retour CSV : écarts sur les vrais KPI, pas « Nouveaux patients »."""
    reset_app_modules()
    import server  # noqa: PLC0415
    one = {"headers": ANALYTICS["headers"], "metaData": ANALYTICS["metaData"],
           "rows": [["a1", o, p, v] for p, v in (("202508", "90"), ("202509", "95")) for o in ("o1", "o2", "o3")]}
    monkeypatch.setattr(server.dhis2, "_fetch_json", lambda *a, **k: json.loads(json.dumps(one)))
    monkeypatch.setattr(server.T.Ctx, "root", property(lambda self: str(tmp_path)))
    server.dhis2_connect({"link": "https://d.test/api/analytics.json?x=1", "user": "u", "password": "p"})
    out = server.clean("dhis2://d.test/api/analytics.json?x=1")
    cleaned = out.get("cleaned_path") or out.get("path") or next(
        str(f) for f in tmp_path.rglob("*_nettoye.csv"))
    df, err = server._read_df(cleaned)
    assert err is None and df.attrs.get("agrege") is True                 # réattaché grâce au témoin .agrege
    spec = {"kpis": [{"nom": "Total dépistés", "agregation": "somme", "colonne": "Dépistés VIH"}], "graphiques": []}
    noms = [g["indicateur"] for g in server.build_dashboard(df, spec)["gaps"]]
    assert noms == ["Total dépistés"]
    df2 = df.copy()
    df2.attrs = {}                                                        # témoin perdu : la signature suffit
    assert [g["indicateur"] for g in server.build_dashboard(df2, spec)["gaps"]] == ["Total dépistés"]


def test_message_coffre_reinitialise(tmp_path, monkeypatch):
    reset_app_modules()
    import dhis2  # noqa: PLC0415
    monkeypatch.setenv("KLDV_SECRET", "k1")
    dhis2.save_credentials("x.test", "u", "p")
    monkeypatch.setenv("KLDV_SECRET", "k2")
    df, err = dhis2.read_dhis2("dhis2://x.test/api/analytics.json?a=1")
    assert df is None and "réinitialisé" in err and "reconnectez" in err
