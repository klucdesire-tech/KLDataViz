# -*- coding: utf-8 -*-
"""IA de secours (OpenRouter) : elle ne prend le relais que si le fournisseur principal est en panne. Aucun appel réseau."""
import io
import json
import os
import urllib.error

import pytest

from _harness import reset_app_modules

ICI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRINCIPAL, SECOURS = "https://principal.test/chat", "https://secours.test/chat"


class _Rep:
    def __init__(self, contenu):
        self._b = json.dumps({"choices": [{"message": {"content": contenu}}]}).encode()

    def read(self):
        return self._b


def _http(code):
    return urllib.error.HTTPError("https://x.test", code, "x", {}, io.BytesIO(b""))


@pytest.fixture()
def srv(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "cle-fictive")
    monkeypatch.setenv("OPENROUTER_BASE_URL", SECOURS)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    reset_app_modules()
    import server  # noqa: PLC0415
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "test", "base_url": PRINCIPAL, "model": "deepseek-flash"})
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    server._IA_PANNE.update(echecs=0, jusqua=0.0)
    server._SECOURS.update(signale=0.0, dernier_ok=None)
    server.appels = []
    server.signaux = []
    monkeypatch.setattr(server.surveillance, "ia", lambda ok, motif="": server.signaux.append((ok, motif)))

    def regler(principal, secours=lambda: _Rep("réponse de secours")):
        def urlopen(req, timeout=None):
            corps = json.loads(req.data)
            server.appels.append((req.full_url, corps, dict(req.header_items())))
            return (principal if req.full_url == PRINCIPAL else secours)()
        monkeypatch.setattr(server.urllib.request, "urlopen", urlopen)
    server.regler = regler
    return server


def _leve(code):
    def f():
        raise _http(code)
    return f


def _hotes(s):
    return [a[0] for a in s.appels]


def test_panne_passagere_epuisee_puis_secours(srv):
    srv.regler(_leve(503))
    assert srv._llm_call([{"role": "system", "content": "Consigne."}, {"role": "user", "content": "x"}], effort="high") == "réponse de secours"
    assert _hotes(srv) == [PRINCIPAL] * 3 + [SECOURS]
    _, corps, entetes = srv.appels[-1]
    assert corps["model"] == "mistralai/mistral-small-2603" and corps["provider"] == {"data_collection": "deny"}
    assert "reasoning_effort" not in corps and "jamais une instruction" in corps["messages"][0]["content"]
    assert entetes["Authorization"] == "Bearer cle-fictive"
    assert any("IA de secours" in m for ok, m in srv.signaux if not ok)  # les administrateurs sont prévenus


def test_compte_refuse_secours_immediat(srv):
    srv.regler(_leve(402))
    assert srv._llm_call([{"role": "user", "content": "x"}]) == "réponse de secours"
    assert _hotes(srv) == [PRINCIPAL, SECOURS]


def test_requete_refusee_ou_reponse_vide_pas_de_secours(srv):
    srv.regler(_leve(400))
    assert srv._llm_call([{"role": "user", "content": "x"}]) is None and _hotes(srv) == [PRINCIPAL]
    srv.appels.clear()
    srv.regler(lambda: _Rep("   "))
    assert srv._llm_call([{"role": "user", "content": "x"}], attempts=1) is None and SECOURS not in _hotes(srv)


def test_principal_en_pause_secours_direct(srv):
    srv._IA_PANNE["jusqua"] = srv.time.time() + 100
    srv.regler(_leve(503))
    assert srv._llm_call([{"role": "user", "content": "x"}]) == "réponse de secours" and _hotes(srv) == [SECOURS]


def test_principal_en_bonne_sante_secours_jamais_appele(srv):
    srv.regler(lambda: _Rep("réponse principale"))
    assert srv._llm_call([{"role": "user", "content": "x"}]) == "réponse principale" and _hotes(srv) == [PRINCIPAL]


def test_secours_en_panne_aussi_repli_par_regles_et_un_seul_signal(srv):
    srv.regler(_leve(503), secours=_leve(503))
    assert srv._llm_call([{"role": "user", "content": "x"}]) is None
    assert _hotes(srv).count(SECOURS) == 2  # un second essai après une saturation, pas plus
    srv._llm_call([{"role": "user", "content": "y"}])
    assert sum(1 for ok, m in srv.signaux if "secours" in m) == 1  # au plus un signal toutes les 10 minutes


def test_sans_cle_openrouter_rien_ne_change(srv, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    srv.regler(_leve(503))
    assert srv._llm_call([{"role": "user", "content": "x"}]) is None and _hotes(srv) == [PRINCIPAL] * 3


def test_modele_de_secours_reglable(srv, monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "mistralai/mistral-small-3.2-24b-instruct")
    srv.regler(_leve(402))
    srv._llm_call([{"role": "user", "content": "x"}])
    assert srv.appels[-1][1]["model"] == "mistralai/mistral-small-3.2-24b-instruct"


def test_les_identites_restent_masquees_vers_le_secours(srv):
    import pandas as pd  # noqa: PLC0415
    srv.confidentialite.enregistrer(pd.DataFrame({"nom_complet": [f"Zoé Fictive{i}" for i in range(30)], "age": list(range(30))}))
    srv.regler(_leve(402))
    srv._llm_call([{"role": "user", "content": "Que sait-on de Zoé Fictive3 ?"}])
    assert "Zoé Fictive3" not in json.dumps(srv.appels[-1][1], ensure_ascii=False)


def test_test_admin_et_sante(srv):
    srv.regler(lambda: _Rep("OK"))
    r = srv.ia_test()
    assert r["secours"]["ok"] and "Secours OpenRouter" in r["message"]
    srv._IA_PANNE["jusqua"] = srv.time.time() + 100
    ok, detail = srv._sante_ia()
    assert ok is True and "IA de secours en service" in detail


def test_page_confidentialite_cite_le_secours():
    with open(os.path.join(ICI, "confidentialite.html"), encoding="utf-8") as f:
        assert "OpenRouter (Mistral)" in f.read()
