# -*- coding: utf-8 -*-
"""Script de test de charge (scripts/test_charge.py) : il mesure juste et refuse le site en ligne sans accord explicite."""
import importlib.util
import os

import pytest

os.environ.pop("KLDV_MODE", None)
from _harness import reset_app_modules, start  # noqa: E402
reset_app_modules()
import server  # noqa: E402

_CHEMIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "test_charge.py")
_spec = importlib.util.spec_from_file_location("test_charge_script", _CHEMIN)
charge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(charge)


@pytest.fixture(scope="module")
def base():
    limite = server._LIMITE_CALCULS
    server._LIMITE_CALCULS = 100000  # ne dépend pas des demandes déjà faites par d'autres fichiers de test
    httpd, url = start(server)
    yield url
    httpd.shutdown()
    server._LIMITE_CALCULS = limite


def test_pages_toutes_reussies(base):
    r = charge.main(["--url", base, "--scenario", "pages", "--utilisateurs", "3", "--duree", "2",
                     "--montee", "0.3", "--pause", "0.1"])
    assert r["actions"]
    for nom, a in r["actions"].items():
        assert nom.startswith("page /")
        assert a["total"] > 0 and a["réussies"] == a["total"], (nom, a)
        assert a["médiane s"] is not None and a["médiane s"] <= a["95e centile s"] <= a["maximum s"]


def test_calculs_lourds_sur_l_exemple_fictif(base):
    r = charge.main(["--url", base, "--scenario", "lourd", "--utilisateurs", "1", "--duree", "1", "--pause", "0"])
    assert r["actions"]["préparer l'exemple"]["réussies"] == 1
    assert r["actions"]["analyse"]["réussies"] >= 1
    assert r["actions"]["analyse"]["erreurs métier"] == 0
    assert r["actions"]["analyse"]["429 trop de demandes"] == 0


def test_resume_compte_chaque_issue():
    m = charge.Mesures()
    for statut, nature in ((200, None), (200, None), (429, None), (503, None), (500, None), (299, "erreur métier : x"),
                           (0, "délai dépassé")):
        m.noter("analyse", statut, 1.0, nature)
    a = charge.resumer(m, 60)["analyse"]
    assert (a["total"], a["réussies"], a["429 trop de demandes"], a["503 occupé"], a["autres erreurs HTTP"],
            a["erreurs métier"], a["sans réponse"]) == (7, 2, 1, 1, 1, 1, 1)
    assert a["réussies par minute"] == 2.0
    assert a["détail des échecs"] == {"HTTP 429": 1, "HTTP 503": 1, "HTTP 500": 1, "erreur métier : x": 1,
                                      "délai dépassé": 1}


def test_site_en_ligne_refuse_sans_accord(monkeypatch):
    monkeypatch.delenv("KLDV_JETON", raising=False)
    with pytest.raises(SystemExit):
        charge.main(["--url", "https://exemple.invalid", "--duree", "1"])
    with pytest.raises(SystemExit):  # calculs lourds en ligne : jeton obligatoire
        charge.main(["--url", "https://exemple.invalid", "--oui-production", "--scenario", "lourd", "--duree", "1"])
