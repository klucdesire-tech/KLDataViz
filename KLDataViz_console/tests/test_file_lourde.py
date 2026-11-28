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


def _autre_calcul_pendant(server, duree):
    """Pendant l'attente de l'IA, un autre calcul prend le seul jeton et le garde `duree` secondes."""
    pris, rendu = threading.Event(), {}

    def autre():
        server._HEAVY.acquire()
        pris.set()
        threading.Event().wait(duree)
        rendu["t"] = __import__("time").monotonic()
        server._HEAVY.release()

    threading.Thread(target=autre, daemon=True).start()
    assert pris.wait(5)
    return rendu


def test_apres_l_ia_le_calcul_attend_son_jeton_jamais_sans(env):
    """Relecture de Hermes (28/11) : la reprise échouait en « occupé », avalé par les replis de l'IA, et le calcul
    continuait sans jeton. Désormais il attend le jeton, même au-delà de KLDV_HEAVY_WAIT."""
    import time
    server, _, _, _ = env
    etat = {}

    def ia():
        etat["rendu"] = _autre_calcul_pendant(server, 0.5)  # plus long que _HEAVY_WAIT (0,2 s)
        return "réponse"

    with server._jeton_lourd():
        assert server._hors_jeton(ia)() == "réponse"
        etat["repris"] = time.monotonic()
        assert _libres(server) == 0 and server._heavy_depth.n == 1  # le calcul tient bien son jeton
    assert etat["repris"] >= etat["rendu"]["t"]  # il a attendu que l'autre calcul rende le sien
    assert _libres(server) == 1  # rien rendu deux fois (BoundedSemaphore aurait levé)


def test_tableau_de_bord_reel_sans_jeton_vole(env, monkeypatch):
    """Le vrai chemin (build_dashboard + replis de l'IA qui avalent les exceptions) : la suite du calcul a attendu."""
    server, _, df, _ = env
    etats, rendus = [], []
    libere = []

    def fausse_ia(messages, max_tokens=8000, **k):
        if not rendus:
            rendus.append(_autre_calcul_pendant(server, 0.5))
        return None

    monkeypatch.setattr(server, "_llm_config", lambda: {"provider": "deepseek", "api_key": "fictive"})
    monkeypatch.setattr(server, "_llm_call", fausse_ia)
    monkeypatch.setattr(server, "_LIBERER_APRES_S", 0)
    monkeypatch.setattr(server, "_rendre_memoire",
                        lambda: libere.append((_libres(server), getattr(server._heavy_depth, "n", 0))))
    res = server.build_dashboard(df)
    assert rendus, "le tableau de bord n'a pas appelé l'IA : le test ne vérifie rien"
    assert "<html" in res["html"].lower()
    assert libere == [(0, 1)]  # fin du calcul : jeton tenu (le vol est fini), pas « 0 libre et profondeur 0 »
    assert _libres(server) == 1


def test_ia_en_panne_le_jeton_revient(env, monkeypatch):
    server, _, df, _ = env

    def ia_en_panne(messages, max_tokens=8000, **k):
        raise RuntimeError("fournisseur en panne")

    monkeypatch.setattr(server, "_llm_config", lambda: {"provider": "deepseek", "api_key": "fictive"})
    monkeypatch.setattr(server, "_llm_call", ia_en_panne)
    assert "<html" in server.build_dashboard(df)["html"].lower()  # repli par règles
    assert _libres(server) == 1


def test_le_tableau_attend_vraiment_son_tour(env, monkeypatch):
    """Un autre calcul finit pendant l'attente : le tableau obtient le jeton et se calcule (pas de 503)."""
    server, _, df, _ = env
    monkeypatch.setattr(server, "_HEAVY_WAIT", 5)
    _autre_calcul_pendant(server, 0.4)
    assert "<html" in server.build_dashboard(df)["html"].lower()
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


def test_memoire_rendue_apres_chaque_calcul_lourd(env, monkeypatch):
    """Arrêts du 18 et du 28/11 : la mémoire ne redescendait pas après un gros calcul."""
    server, _, df, _ = env
    appels = []
    monkeypatch.setattr(server, "_LIBERER_APRES_S", 0)
    monkeypatch.setattr(server, "_rendre_memoire", lambda: appels.append(_libres(server)))
    server.build_dashboard(df)
    assert appels == [0]  # une fois, jeton encore tenu : le calcul suivant démarre sur une mémoire rendue
    with server._jeton_lourd():
        server.build_dashboard(df)  # imbriqué : pas de libération au milieu du calcul englobant
        assert appels == [0]
    assert appels == [0, 0]
    assert _libres(server) == 1


def test_calcul_court_sans_liberation(env, monkeypatch):
    """Une étape brève (code de la discussion) ne paie pas un gc.collect()."""
    server, _, _, _ = env
    appels = []
    monkeypatch.setattr(server, "_rendre_memoire", lambda: appels.append(1))
    with server._jeton_lourd():
        pass
    assert appels == []


def test_actualisation_occupee_reportee_pas_en_erreur(env, monkeypatch):
    server, T, _, _ = env
    import sources  # noqa: PLC0415
    statuts = {}
    monkeypatch.setattr(sources, "_update", lambda sid, upd: statuts.__setitem__(sid, upd["status"]))

    def occupe(sid, read_df, build):
        raise T.Unavailable("occupé")

    def casse(sid, read_df, build):
        raise ValueError("colonne disparue")

    monkeypatch.setattr(sources, "refresh", occupe)
    sources._actualiser("a", None, None)
    monkeypatch.setattr(sources, "refresh", casse)
    sources._actualiser("b", None, None)
    assert statuts["a"].startswith("Reporté")
    assert statuts["b"] == "Erreur : colonne disparue"


def test_retrait_annule_si_le_tableau_ne_peut_pas_etre_recalcule(env, monkeypatch):
    server, T, df, _ = env
    import sources  # noqa: PLC0415
    ajout = {"id": "g1", "code": "x", "titre": "Graphique"}
    remis = []
    monkeypatch.setattr(sources, "spec_de", lambda sid: {sources.CLE_AJOUTS: [ajout]})
    monkeypatch.setattr(sources, "retirer_graphique", lambda sid, aid: [])
    monkeypatch.setattr(sources, "ajouter_graphique", lambda sid, a: remis.append(a))
    monkeypatch.setattr(sources, "get", lambda sid, with_html=True: {"path": "fictif.csv"})
    monkeypatch.setattr(server, "_read_df", lambda p: (df, None))

    def occupe(*a, **k):
        raise T.Unavailable("occupé")

    monkeypatch.setattr(server, "build_dashboard", occupe)
    with pytest.raises(T.Unavailable):
        server.retirer_du_tableau("s1", "g1")
    assert remis == [ajout]  # le graphique reste, comme le tableau affiché


def test_rendre_memoire_ne_casse_jamais_la_reponse(env, monkeypatch):
    server, _, _, _ = env

    def en_panne(_):
        raise OSError("malloc_trim indisponible")

    monkeypatch.setattr(server, "_MALLOC_TRIM", en_panne)
    server._rendre_memoire()  # aucune exception


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="glibc : service en ligne (Linux) seulement")
def test_malloc_trim_trouve_sur_linux(env):
    server, _, _, _ = env
    assert server._MALLOC_TRIM is not None
    assert server._MALLOC_TRIM(0) in (0, 1)
