# -*- coding: utf-8 -*-
"""Fournisseur d'IA choisi par réglage (DeepSeek ou Claude) et appel à Claude, sans réseau : le client Anthropic est
remplacé par un faux qui enregistre la requête et renvoie une réponse préparée. Aucune clé réelle n'est utilisée."""
import types

import anthropic
import httpx2 as httpx
import pytest

from _harness import reset_app_modules


class _Faux:
    """Faux client : `reponses` est une liste de réponses ou d'exceptions, consommées dans l'ordre."""

    def __init__(self, reponses):
        self.reponses, self.requetes = list(reponses), []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requetes.append(kw)
        r = self.reponses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _msg(texte, stop="end_turn"):
    return types.SimpleNamespace(stop_reason=stop, content=[types.SimpleNamespace(type="text", text=texte)])


def _erreur(code):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIStatusError("erreur", response=httpx.Response(code, request=req), body=None)


@pytest.fixture
def server(monkeypatch, tmp_path):
    for k in ("KLDV_LLM_PROVIDER", "ANTHROPIC_API_KEY", "KLDV_ANTHROPIC_MODEL", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    reset_app_modules()
    import server as s  # noqa: PLC0415
    s._IA_PANNE.update(echecs=0, jusqua=0.0)
    monkeypatch.setattr(s.time, "sleep", lambda _x: None)
    monkeypatch.setattr(s, "BASE", str(tmp_path))  # pas de llm_config.json local : seuls les réglages du test comptent
    return s


def _brancher(monkeypatch, reponses):
    faux = _Faux(reponses)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: faux)
    return faux


def test_choix_du_fournisseur(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    cfg = server._llm_config()
    assert cfg["provider"] == "anthropic" and cfg["model"] == "claude-opus-5-5"
    monkeypatch.setenv("KLDV_ANTHROPIC_MODEL", "claude-sonnet-5-5")
    assert server._llm_config()["model"] == "claude-sonnet-5-5"
    monkeypatch.setenv("KLDV_LLM_PROVIDER", "deepseek")  # le réglage explicite l'emporte
    assert (server._llm_config() or {}).get("provider") != "anthropic"


def test_appel_claude_system_effort_et_texte(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    faux = _brancher(monkeypatch, [_msg("Réponse de Claude")])
    out = server._llm_call([{"role": "system", "content": "Tu es Hermes."}, {"role": "user", "content": "Bonjour"}],
                           500, model="deepseek-flash", effort="high")
    assert out == "Réponse de Claude"
    r = faux.requetes[0]
    assert r["model"] == "claude-opus-5-5"                      # un nom de modèle DeepSeek n'est jamais envoyé à Claude
    assert r["system"].startswith("Tu es Hermes.") and server.GARDE_DONNEES in r["system"]
    assert r["messages"] == [{"role": "user", "content": "Bonjour"}]
    assert r["output_config"] == {"effort": "high"} and r["max_tokens"] >= 1024
    assert r["fallbacks"] == "default" and r["betas"] == ["server-side-fallback-2026-07-01"]
    assert "temperature" not in r                               # refusé par les modèles récents


def test_refus_compte_sans_reessai_et_panne_reessayee(server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    faux = _brancher(monkeypatch, [_erreur(402)])
    assert server._llm_call([{"role": "user", "content": "x"}], attempts=3) is None and len(faux.requetes) == 1
    faux = _brancher(monkeypatch, [_erreur(529), _msg("ok après réessai")])
    assert server._llm_call([{"role": "user", "content": "x"}], attempts=3) == "ok après réessai" and len(faux.requetes) == 2
    faux = _brancher(monkeypatch, [_msg("", stop="refusal")])
    assert server._llm_call([{"role": "user", "content": "x"}], attempts=3) is None and len(faux.requetes) == 1


def test_sans_cle_pas_d_appel(server, monkeypatch):
    monkeypatch.setenv("KLDV_LLM_PROVIDER", "anthropic")
    faux = _brancher(monkeypatch, [_msg("ne doit pas servir")])
    assert server._llm_call([{"role": "user", "content": "x"}]) is None and not faux.requetes


def _cloud(server, monkeypatch):
    import cloud  # noqa: PLC0415 - chargé seulement en ligne par server ; branché ici pour le test
    monkeypatch.setattr(server, "cloud", cloud, raising=False)
    return cloud


def test_reglage_de_l_administration_prioritaire(server, monkeypatch):
    _cloud(server, monkeypatch)
    monkeypatch.setattr(server.T, "CLOUD", True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "cle-de-test-2")
    monkeypatch.setattr(server.cloud, "ia_settings", lambda: {"provider": "deepseek", "model": "deepseek-flash"})
    cfg = server._llm_config()
    assert cfg["provider"] == "deepseek" and cfg["model"] == "deepseek-flash"
    monkeypatch.setattr(server.cloud, "ia_settings", lambda: {"provider": "anthropic", "model": "claude-sonnet-5-5"})
    assert server._llm_config()["model"] == "claude-sonnet-5-5"
    monkeypatch.setattr(server.cloud, "ia_settings", lambda: {"provider": "auto"})
    assert server._llm_config()["provider"] == "anthropic"           # automatique : Claude dès que sa clé existe


def test_enregistrement_controle_et_page_publique(server, monkeypatch):
    cloud = _cloud(server, monkeypatch)
    ecrits = []
    monkeypatch.setattr(cloud, "_admin", lambda ctx: None)
    monkeypatch.setattr(cloud, "q", lambda sql, args=None, **kw: ecrits.append(args) or [])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        cloud.admin_ia_save(None, {"provider": "anthropic"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    with pytest.raises(ValueError, match="modèle"):
        cloud.admin_ia_save(None, {"provider": "anthropic", "model": "deepseek-flash"})
    cloud.admin_ia_save(None, {"provider": "anthropic", "model": "claude-sonnet-5-5"})
    assert ("ia_provider", '"anthropic"') in ecrits and ("ia_model", '"claude-sonnet-5-5"') in ecrits
    monkeypatch.setattr(cloud, "_all_settings", lambda: {"hero_title": "T", "ia_provider": "anthropic"})
    assert cloud.site_settings() == {"hero_title": "T"}                 # le réglage IA n'est jamais public
    assert cloud.admin_ia(None)["cles"]["anthropic"] is True


def test_relecture_hermes_modele_deepseek_impose_et_erreurs(server, monkeypatch):
    _cloud(server, monkeypatch)
    monkeypatch.setattr(server.T, "CLOUD", True)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "cle-de-test")
    monkeypatch.setattr(server.cloud, "ia_settings", lambda: {"provider": "deepseek", "model": "deepseek-v4-pro"})
    envoye = {}

    class _R:
        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'
    monkeypatch.setattr(server.urllib.request, "urlopen",
                        lambda req, timeout=0: envoye.setdefault("body", req.data) and _R())
    assert server._llm_call([{"role": "user", "content": "x"}], model="deepseek-flash") == "ok"
    assert b'"deepseek-v4-pro"' in envoye["body"]              # le choix de l'administration s'impose
    monkeypatch.setattr(server.cloud, "ia_settings", lambda: {"provider": "anthropic"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
    monkeypatch.setattr(server, "_claude_call", lambda *a: (_ for _ in ()).throw(ValueError("inattendu")))
    assert server._llm_call([{"role": "user", "content": "x"}], attempts=2) is None   # jamais d'exception
    monkeypatch.setattr(server, "_claude_call", lambda *a: (_ for _ in ()).throw(ImportError("anthropic")))
    assert server._llm_call([{"role": "user", "content": "x"}], attempts=2) is None
