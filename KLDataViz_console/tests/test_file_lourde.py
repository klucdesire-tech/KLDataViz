# -*- coding: utf-8 -*-
"""File des calculs lourds (test de charge du 13/11) : le tableau de bord prend un des 2 jetons comme l'analyse,
le rend pendant l'attente de l'IA, « occupé » reste un 503, et aucun jeton n'est rendu deux fois."""
import os
import sys
import tempfile
import threading

import pytest

from _harness import call, reset_app_modules, start


@pytest.fixture()
def env(monkeypatch):
    os.environ.pop("KLDV_MODE", None)
    reset_app_modules()
    import server  # noqa: PLC0415
    import exemple  # noqa: PLC0415
    import tenancy as T  # noqa: PLC0415
    monkeypatch.setattr(server, "_llm_config", lambda: None)  # règles seules : aucun appel réseau
    sys.path.insert(0, os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA"))
    from hermes_da import config as hconfig  # noqa: PLC0415
    monkeypatch.setattr(hconfig, "CONSOLE_DIR", type(hconfig.CONSOLE_DIR)(tempfile.mkdtemp()))
    monkeypatch.setattr(server, "_HEAVY", threading.BoundedSemaphore(1))
    monkeypatch.setattr(server, "_HEAVY_WAIT", 0.2)
    info = exemple.ecrire(tempfile.mkdtemp())
    return server, T, exemple.generer(), info["path"]


def _libres(server):
    return server._HEAVY._value


def test_tableau_de_bord_attend_son_tour(env):
    server, T, df, _ = env
    assert server._HEAVY.acquire()  # un autre calcul lourd est en cours
    try:
        with pytest.raises(T.Unavailable):
            server.build_dashboard(df)
    finally:
        server._HEAVY.release()
    res = server.build_dashboard(df)  # le jeton revenu, le tableau se calcule
    assert "<html" in res["html"].lower()
    assert _libres(server) == 1  # et le jeton est rendu


def test_pas_de_second_jeton_dans_un_calcul_lourd(env):
    """Lien public (_share_refresh) et actualisation : déjà sous jeton, build_dashboard ne se bloque pas lui-même."""
    server, _, df, _ = env
    with server._jeton_lourd(attente=0):
        assert _libres(server) == 0
        assert "<html" in server.build_dashboard(df)["html"].lower()
        assert _libres(server) == 0
    assert _libres(server) == 1


def test_ia_appelee_sans_tenir_de_jeton(env, monkeypatch):
    server, _, df, _ = env
    vus = []

    def fausse_ia(messages, max_tokens=8000, **k):
        vus.append(_libres(server))  # jetons libres pendant l'attente de l'IA
        return None  # pas de réponse : le tableau garde ses noms par règles

    monkeypatch.setattr(server, "_llm_config", lambda: {"provider": "deepseek", "api_key": "fictive"})
    monkeypatch.setattr(server, "_llm_call", fausse_ia)
    res = server.build_dashboard(df)
    assert vus, "le tableau de bord n'a pas appelé l'IA : le test ne vérifie rien"
    assert set(vus) == {1}  # le seul jeton était libre pendant chaque appel
    assert "<html" in res["html"].lower()
    assert _libres(server) == 1


def test_jeton_non_repris_apres_l_ia_rien_rendu_deux_fois(env):
    server, T, _, _ = env
    pris, fin = threading.Event(), threading.Event()

    def autre_calcul():
        server._HEAVY.acquire()
        pris.set()
        fin.wait(5)
        server._HEAVY.release()

    def ia():
        threading.Thread(target=autre_calcul, daemon=True).start()
        assert pris.wait(5)  # pendant l'attente de l'IA, un autre calcul prend le jeton et le garde
        return "réponse"

    with pytest.raises(T.Unavailable):
        with server._jeton_lourd():
            server._hors_jeton(ia)()
    # sortie du bloc sans ValueError (BoundedSemaphore) : le jeton de l'autre calcul n'a pas été rendu à sa place
    assert _libres(server) == 0
    fin.set()
    for _ in range(50):
        if _libres(server) == 1:
            break
        threading.Event().wait(0.05)
    assert _libres(server) == 1


def test_hors_jeton_sans_jeton_tenu_appel_direct(env):
    server, _, _, _ = env
    assert server._hors_jeton(lambda x: x * 2)(21) == 42
    assert _libres(server) == 1


def test_route_tableau_de_bord_occupe_en_503(env):
    server, _, _, chemin = env
    httpd, url = start(server)
    assert server._HEAVY.acquire()
    try:
        statut, rep = call(url, "/api/dashboard", {"file_path": chemin})
        assert statut == 503 and rep.get("unavailable") is True
    finally:
        server._HEAVY.release()
        httpd.shutdown()


@pytest.mark.parametrize("valeur, attendu", [(None, 30), ("", 30), ("abc", 30), ("30.0", 30), (" 40 ", 40),
                                             ("0", 1), ("-5", 1), ("100000", 100000)])
def test_limite_par_minute_mal_saisie(env, valeur, attendu):
    server, _, _, _ = env
    assert server._lire_limite(valeur) == attendu
