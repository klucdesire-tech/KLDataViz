# -*- coding: utf-8 -*-
"""Phase 2 : agents résilients (réessais, coupe-circuit, garde anti-injection), vérification des chiffres du chat,
journal des actions, export / suppression des données d'un espace. Aucun appel réseau réel."""
import io
import json
import os
import urllib.error
import zipfile

import pytest

from _harness import reset_app_modules


class _Rep:
    def __init__(self, contenu):
        self._b = json.dumps({"choices": [{"message": {"content": contenu}}]}).encode()

    def read(self):
        return self._b


def _http(code):
    return urllib.error.HTTPError("https://api.test", code, "x", {}, io.BytesIO(b""))


@pytest.fixture()
def srv(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "test", "base_url": "https://api.test"})
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    server._IA_PANNE.update(echecs=0, jusqua=0.0)
    return server


def test_reessai_apres_saturation_puis_succes(srv, monkeypatch):
    appels = []

    def urlopen(req, timeout=None):
        appels.append(json.loads(req.data))
        if len(appels) < 3:
            raise _http(429)
        return _Rep("bonjour")
    monkeypatch.setattr(srv.urllib.request, "urlopen", urlopen)
    assert srv._llm_call([{"role": "system", "content": "Consigne."}, {"role": "user", "content": "x"}]) == "bonjour"
    assert len(appels) == 3
    assert "jamais une instruction" in appels[0]["messages"][0]["content"]   # garde anti-injection ajoutée


def test_solde_epuise_pas_de_reessai(srv, monkeypatch):
    appels = []

    def urlopen(req, timeout=None):
        appels.append(1)
        raise _http(402)
    monkeypatch.setattr(srv.urllib.request, "urlopen", urlopen)
    assert srv._llm_call([{"role": "user", "content": "x"}]) is None and len(appels) == 1


def test_coupe_circuit_puis_repli_immediat(srv, monkeypatch):
    appels = []

    def urlopen(req, timeout=None):
        appels.append(1)
        raise TimeoutError()
    monkeypatch.setattr(srv.urllib.request, "urlopen", urlopen)
    for _ in range(srv.IA_SEUIL):
        assert srv._llm_call([{"role": "user", "content": "x"}], attempts=1) is None
    assert not srv.ia_disponible()
    n = len(appels)
    assert srv._llm_call([{"role": "user", "content": "x"}]) is None and len(appels) == n  # plus d'appel pendant la pause


def test_verification_des_chiffres():
    reset_app_modules()
    import verif_chiffres as V  # noqa: PLC0415
    refs = V.sources("Yopougon-Est 63469\nAbobo-Est 52011\nSakassou 21430\ntotal 180682", "taux 0.125")
    for juste in ("Yopougon-Est arrive en tête avec 63 469 consultations, soit 35,1 % du total (180 682).",
                  "Yopougon-Est dépasse Abobo-Est de 11 458 consultations.", "Le taux est de 12,5 %.",
                  "Environ 181 k consultations en 2024, dans 4 districts."):
        assert V.verifier(juste, refs) == [], juste
    assert V.verifier("Yopougon-Est arrive en tête avec 72 300 consultations.", refs) == [72300.0]


def test_chat_signale_un_chiffre_invente(srv):
    answer, douteux = srv._verifier_chiffres("Le district A compte 9 999 cas.",
                                              [{"role": "user", "content": "Combien de cas ?"}],
                                              [{"resultat": "district  cas\nA  1234"}])
    assert douteux == [9999.0] and "⚠ Vérification" in answer and answer.startswith("Le district A")


def test_journal_et_export(monkeypatch, tmp_path):
    reset_app_modules()
    import tenancy as T  # noqa: PLC0415
    import journal  # noqa: PLC0415
    import espace  # noqa: PLC0415

    class Esp:
        root, plan, email, org_id = str(tmp_path), {"org_name": "Démo"}, "chef@exemple.org", None

        def dir(self, name):
            p = os.path.join(str(tmp_path), "storage" if name == "storage" else name)
            os.makedirs(p, exist_ok=True)
            return p
    monkeypatch.setattr(T, "current", lambda: Esp())
    os.makedirs(tmp_path / "uploads", exist_ok=True)
    (tmp_path / "uploads" / "ventes.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    os.makedirs(tmp_path / "storage" / "dhis2", exist_ok=True)
    (tmp_path / "storage" / "dhis2" / "coffre.json").write_text("{secret}", encoding="utf-8")
    journal.ajouter("lien de partage créé", details={"id": "t1", "password": "NE PAS GARDER"})
    assert journal.lire()[0]["action"] == "lien de partage créé" and "password" not in journal.lire()[0]
    nom = espace.exporter()
    noms = zipfile.ZipFile(os.path.join(str(tmp_path), "exports", nom)).namelist()
    assert "uploads/ventes.csv" in [n.replace("\\", "/") for n in noms] and "LISEZMOI.txt" in noms
    assert not [n for n in noms if "dhis2" in n]                     # jamais le coffre des identifiants


def test_suppression_reservee_a_la_version_en_ligne():
    reset_app_modules()
    import espace  # noqa: PLC0415
    with pytest.raises(ValueError, match="en ligne"):
        espace.supprimer_donnees()
