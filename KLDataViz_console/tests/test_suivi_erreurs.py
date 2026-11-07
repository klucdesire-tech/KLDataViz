# -*- coding: utf-8 -*-
"""Sentry et surveillance extérieure : rien ne part sans SENTRY_DSN, et ce qui part ne porte aucune donnée
(personnes fictives, aucun envoi réseau)."""
import os
import sys
import types

import pytest

from _harness import reset_app_modules

ICI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FauxSdk(types.ModuleType):
    """Remplace sentry_sdk : garde les appels au lieu d'envoyer."""

    def __init__(self):
        super().__init__("sentry_sdk")
        self.options, self.exceptions, self.messages, self.tags = None, [], [], {}

    def init(self, **options):
        self.options = options

    def new_scope(self):
        sdk = self

        class Portee:
            def __enter__(self):
                sdk.tags = {}
                return self

            def __exit__(self, *a):
                return False

            def set_tag(self, k, v):
                sdk.tags[k] = v
        return Portee()

    def capture_exception(self, exc):
        self.exceptions.append((exc, dict(self.tags)))

    def capture_message(self, texte, level=None):
        self.messages.append((texte, level, dict(self.tags)))


@pytest.fixture
def S(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_SURVEILLANCE_DIR", str(tmp_path))
    monkeypatch.setenv("SENTRY_DSN", "https://cle-fictive@o0.ingest.example.invalid/1")
    reset_app_modules()
    faux = FauxSdk()
    monkeypatch.setitem(sys.modules, "sentry_sdk", faux)
    import suivi_erreurs
    import surveillance
    assert suivi_erreurs.demarrer(version="abc123")
    surveillance.faux = faux
    surveillance.suivi = suivi_erreurs
    yield surveillance
    suivi_erreurs._etat.update(actif=False, sdk=None)


def _boom(message):
    try:
        raise KeyError(message)
    except KeyError as e:
        return e


def test_options_sans_donnee_personnelle(S):
    o = S.faux.options
    assert o.get("send_default_pii") is not True and o["include_local_variables"] is False
    assert o["max_request_body_size"] == "never" and o["max_breadcrumbs"] == 0
    assert o["default_integrations"] is False and o["auto_enabling_integrations"] is False
    assert o["traces_sample_rate"] == 0.0 and o["before_send"] is S.suivi.nettoyer and o["release"] == "abc123"


def test_erreur_interne_part_avec_sa_pile_mais_sans_jeton(S):
    S.erreur_interne("a1b2c3", "GET", "/t/jeton-secret-123", _boom("Aya Kouassi 07 45 12 33"), espace="org-1")
    exc, tags = S.faux.exceptions[0]
    assert isinstance(exc, KeyError) and tags == {"genre": "erreur", "ref": "a1b2c3", "chemin": "GET /t/…", "espace": "org-1"}
    assert not S.faux.messages  # pas de doublon en message


def test_incident_sans_exception_masque_adresses_et_numeros(S):
    S.signaler("envoi", "Brevo 400 : adresse invalide aya.kouassi@exemple.ci, tel +225 07 45 12 33 90", espace="org-2")
    texte, niveau, tags = S.faux.messages[0]
    assert niveau == "warning" and tags["genre"] == "envoi" and tags["espace"] == "org-2"
    assert "aya.kouassi" not in texte and "07 45" not in texte and "[adresse]" in texte and "[nombre]" in texte


def test_nettoyer_retire_message_variables_et_requete(S):
    event = {"exception": {"values": [{"type": "KeyError", "value": "'Aya Kouassi'",
                                       "stacktrace": {"frames": [{"filename": "server.py", "vars": {"ligne": "Aya"}}]}}]},
             "request": {"data": "Aya"}, "user": {"email": "aya@exemple.ci"}, "breadcrumbs": {"values": [{"message": "Aya"}]},
             "extra": {"x": "Aya"}, "tags": {"ref": "r"}}
    propre = S.suivi.nettoyer(event)
    assert "Aya" not in repr(propre) and propre["exception"]["values"][0]["type"] == "KeyError" and propre["tags"] == {"ref": "r"}


def test_sans_dsn_rien_ne_part(tmp_path, monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    reset_app_modules()
    import suivi_erreurs
    assert suivi_erreurs.demarrer() is False and not suivi_erreurs.actif()
    suivi_erreurs.exception(_boom("x"))  # ne lève pas
    suivi_erreurs.message("ia", "x")


def test_paquet_absent_ne_bloque_pas(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://cle-fictive@o0.ingest.example.invalid/1")
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # import → ImportError
    reset_app_modules()
    import suivi_erreurs
    assert suivi_erreurs.demarrer() is False


def test_vrai_sdk_n_envoie_rien_de_sensible(tmp_path, monkeypatch):
    """Avec le vrai paquet : l'événement préparé pour l'envoi ne contient ni le message ni les variables."""
    sdk = pytest.importorskip("sentry_sdk")
    reset_app_modules()
    import suivi_erreurs
    envoyes = []

    class Transport(sdk.transport.Transport):
        def capture_envelope(self, envelope):
            envoyes.extend(i.payload.json for i in envelope.items if i.payload.json)

    monkeypatch.setenv("SENTRY_DSN", "https://cle-fictive@o0.ingest.example.invalid/1")
    vrai_init = sdk.init
    monkeypatch.setattr(sdk, "init", lambda **o: vrai_init(transport=Transport, **o))
    try:
        assert suivi_erreurs.demarrer()

        # valeurs construites à l'exécution : les lignes de code jointes à la pile ne les contiennent pas
        nom, tel = "".join(["Zo", "é ", "Fictif"]), " ".join(["07", "45", "12", "33"])

        def fonction_metier():
            patient = nom  # noqa: F841 - variable locale qui ne doit pas partir
            raise KeyError(f"{nom} {tel}")
        try:
            fonction_metier()
        except KeyError as e:
            suivi_erreurs.exception(e, ref="r1", chemin="POST /api/chat")
        sdk.flush()
        assert envoyes, "aucun événement préparé"
        brut = repr(envoyes)
        assert nom not in brut and tel not in brut and "KeyError" in brut and "r1" in brut
    finally:
        suivi_erreurs._etat.update(actif=False, sdk=None)
        sdk.get_global_scope().set_client(None)


def test_etat_public_sans_detail_et_mis_en_cache(S, monkeypatch):
    appels = []

    def faux_bilan():
        appels.append(1)
        return {"ok": False, "controles": [{"nom": "Base de données", "ok": False, "detail": "mot de passe refusé pour aya"},
                                           {"nom": "Disque", "ok": True, "detail": "12 Go libres"}],
                "incidents": [{"resume": "Aya"}]}
    monkeypatch.setattr(S, "bilan", faux_bilan)
    S._public.update(t=0.0, r=None)
    r = S.etat_public()
    assert r["ok"] is False and r["en_echec"] == ["Base de données"] and set(r) == {"ok", "en_echec", "verifie"}
    assert "aya" not in repr(r).lower()
    S.etat_public()
    assert len(appels) == 1  # recalculé au plus toutes les 30 secondes


def test_route_et_dependance():
    with open(os.path.join(ICI, "server.py"), encoding="utf-8") as f:
        serveur = f.read()
    assert 'path == "/healthz/complet"' in serveur and "surveillance.etat_public()" in serveur
    assert 'path == "/healthz":' in serveur  # la preuve de vie de Render ne dépend toujours pas de la base
    assert "suivi_erreurs.demarrer()" in serveur
    with open(os.path.join(ICI, "requirements.txt"), encoding="utf-8") as f:
        assert "sentry-sdk==" in f.read()
