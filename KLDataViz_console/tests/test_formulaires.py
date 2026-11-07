# -*- coding: utf-8 -*-
"""Connecteur KoboToolbox / ODK Central : réponses FICTIVES, serveur simulé, aucun appel réseau."""
import base64
import io
import json
import os
import urllib.error

import pytest

from _harness import reset_app_modules

ICI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UID = "aBcD3fGh5iJk7LmN9pQr"


@pytest.fixture
def F(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_DHIS2_DIR", str(tmp_path))   # coffre chiffré isolé
    reset_app_modules()
    import formulaires
    formulaires.appels = []

    def serveur(pages):
        class Rep:
            def __init__(self, b):
                self.b = b

            def read(self, n=-1):
                return self.b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Ouvreur:
            def open(self, req, timeout=None):
                formulaires.appels.append((req.full_url, req.get_header("Authorization")))
                rep = pages[req.full_url]
                if isinstance(rep, int):
                    raise urllib.error.HTTPError(req.full_url, rep, "x", {}, io.BytesIO(b""))
                return Rep(json.dumps(rep).encode())
        monkeypatch.setattr(formulaires.urllib.request, "build_opener", lambda *a: Ouvreur())
    formulaires.serveur = serveur
    return formulaires


def test_liens_colles_par_l_utilisateur(F):
    assert F.depuis_lien("kobo", f"https://kf.kobotoolbox.org/#/forms/{UID}/summary") == f"kobo://kf.kobotoolbox.org/{UID}"
    assert F.depuis_lien("kobo", f"https://eu.kobotoolbox.org/api/v2/assets/{UID}/data/") == f"kobo://eu.kobotoolbox.org/{UID}"
    assert F.depuis_lien("odk", "https://central.exemple.org/#/projects/3/forms/enquete_menage/submissions") \
        == "odk://central.exemple.org/3/enquete_menage"
    assert F.depuis_lien("odk", "https://central.exemple.org/v1/projects/3/forms/enquete_menage.svc/Submissions") \
        == "odk://central.exemple.org/3/enquete_menage"
    for genre, lien in (("kobo", f"http://kf.kobotoolbox.org/#/forms/{UID}"), ("kobo", "https://kf.kobotoolbox.org/#/projects"),
                        ("odk", "https://central.exemple.org/#/projects/3")):
        with pytest.raises(ValueError):
            F.depuis_lien(genre, lien)
    with pytest.raises(ValueError):
        F.analyser_uri("kobo://kf.kobotoolbox.org/../../admin")


def _kobo():
    p1 = f"https://kf.kobotoolbox.org/api/v2/assets/{UID}/data/?format=json&limit=1000"
    p2 = f"https://kf.kobotoolbox.org/api/v2/assets/{UID}/data/?format=json&limit=1000&start=1000"
    rep = lambda i: {"_id": i, "_uuid": "u", "_submitted_by": "enqueteur_fictif", "username": "agent7", "deviceid": "imei:000",
                     "meta/instanceID": "uuid:x", "_submission_time": f"2026-0{i}-15T10:00:00", "_attachments": [],
                     "identification/district": "Bouaké" if i % 2 else "Korhogo", "identification/age": str(20 + i),
                     "sante/age": "oui", "menage/membres": [{"m/nom": "Zoé Fictive"}]}
    return {p1: {"next": p2, "results": [rep(1), rep(2)]}, p2: {"next": None, "results": [rep(3)]}}


def test_kobo_toutes_les_pages_sans_identite(F):
    F.serveur(_kobo())
    df, err = F.lire(f"kobo://kf.kobotoolbox.org/{UID}", allow_private=True, creds=("jeton", "jeton-fictif"))
    assert err is None and len(df) == 3 and df.attrs["agrege"] is False
    assert all(auth == "Token jeton-fictif" for _, auth in F.appels) and len(F.appels) == 2
    assert list(df.columns)[0] == "date_soumission" and "district" in df.columns
    assert {"identification_age", "sante_age"} <= set(df.columns)          # même nom court : chemin complet gardé
    assert df["identification_age"].tolist() == [21, 22, 23]                # nombres saisis en texte → nombres
    texte = df.to_csv()
    for absent in ("enqueteur_fictif", "agent7", "imei", "uuid:x", "Zoé Fictive"):
        assert absent not in texte
    assert df.attrs["repetes_ecartes"] == ["membres"]


def test_page_suivante_sur_un_autre_serveur_refusee(F):
    p1 = f"https://kf.kobotoolbox.org/api/v2/assets/{UID}/data/?format=json&limit=1000"
    F.serveur({p1: {"next": "https://pirate.example.invalid/vol", "results": [{"q": 1}]}})
    df, err = F.lire(f"kobo://kf.kobotoolbox.org/{UID}", allow_private=True, creds=("jeton", "jeton-fictif"))
    assert df is None and "autre serveur" in err
    assert all("pirate" not in url for url, _ in F.appels)                 # le jeton n'y est jamais parti


def test_odk_groupes_aplatis_et_soumetteur_ecarte(F):
    p1 = "https://central.exemple.org/v1/projects/3/forms/enquete_menage.svc/Submissions?%24top=1000"
    p2 = "https://central.exemple.org/v1/projects/3/forms/enquete_menage.svc/Submissions?%24top=1000&%24skip=1000"
    rep = lambda i: {"__id": f"uuid:{i}", "__system": {"submissionDate": f"2026-10-0{i}T08:00:00Z", "submitterName": "Agent Fictif"},
                     "meta": {"instanceID": "uuid:x"}, "deviceid": "imei:1", "localisation": {"region": "Gbêkê", "village": "V" + str(i)},
                     "nb_enfants": i, "enfants@odata.navigationLink": "Submissions('x')/enfants", "enfants": [{"prenom": "Zoé"}]}
    F.serveur({p1: {"value": [rep(1)], "@odata.nextLink": p2}, p2: {"value": [rep(2)]}})
    df, err = F.lire("odk://central.exemple.org/3/enquete_menage", allow_private=True, creds=("lecteur@exemple.ci", "mdp-fictif"))
    assert err is None and len(df) == 2
    attendu = "Basic " + base64.b64encode(b"lecteur@exemple.ci:mdp-fictif").decode()
    assert all(auth == attendu for _, auth in F.appels)
    assert set(df.columns) == {"date_soumission", "region", "village", "nb_enfants"}
    assert "Agent Fictif" not in df.to_csv() and df.attrs["repetes_ecartes"] == ["enfants"]


def test_erreurs_lisibles(F):
    F.serveur({f"https://kf.kobotoolbox.org/api/v2/assets/{UID}/data/?format=json&limit=1000": 401})
    df, err = F.lire(f"kobo://kf.kobotoolbox.org/{UID}", allow_private=True, creds=("jeton", "mauvais"))
    assert df is None and "HTTP 401" in err and "jeton" in err
    df, err = F.lire(f"kobo://kf.kobotoolbox.org/{UID}", allow_private=True)
    assert df is None and "reconnectez" in err                               # aucun accès enregistré

    class Injoignable:
        def open(self, req, timeout=None):
            raise urllib.error.URLError(OSError(11001, "getaddrinfo failed"))
    F.urllib.request.build_opener = lambda *a: Injoignable()
    df, err = F.lire(f"kobo://kf.kobotoolbox.org/{UID}", allow_private=True, creds=("jeton", "x"))
    assert "injoignable" in err and "getaddrinfo" not in err and "Errno" not in err

    class Bizarre:  # réponse qui n'est pas du JSON : aucun nom d'exception à l'écran
        def open(self, req, timeout=None):
            raise KeyError("results")
    F.urllib.request.build_opener = lambda *a: Bizarre()
    df, err = F.lire(f"kobo://kf.kobotoolbox.org/{UID}", allow_private=True, creds=("jeton", "x"))
    assert "réponse inattendue" in err and "KeyError" not in err


def test_connexion_enregistre_l_acces_chiffre_seulement_si_l_essai_reussit(F, tmp_path):
    lien = f"https://kf.kobotoolbox.org/#/forms/{UID}/summary"
    F.serveur({f"https://kf.kobotoolbox.org/api/v2/assets/{UID}/data/?format=json&limit=1000": 401})
    assert "error" in F.connecter("kobo", lien, "", "mauvais-jeton", allow_private=True)
    assert F.dhis2.load_credentials("kobo:kf.kobotoolbox.org") is None
    F.serveur(_kobo())
    r = F.connecter("kobo", lien, "", "jeton-fictif-123", allow_private=True)
    assert r["uri"] == f"kobo://kf.kobotoolbox.org/{UID}" and r["rows"] == 3 and r["name"] == f"kobo_{UID}"
    assert F.dhis2.load_credentials("kobo:kf.kobotoolbox.org") == ("jeton", "jeton-fictif-123")
    coffre = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    assert b"jeton-fictif-123" not in coffre                                # jamais en clair sur le disque
    df, err = F.lire(r["uri"], allow_private=True)                          # relecture (↻ Actualiser) avec l'accès gardé
    assert err is None and len(df) == 3


def test_branchement_serveur(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_DHIS2_DIR", str(tmp_path))
    reset_app_modules()
    import server
    import sources
    lu = []
    monkeypatch.setattr(server.formulaires, "lire", lambda uri, allow_private=False: (lu.append(uri), (None, "simulé"))[1])
    assert server._read_df_raw(f"kobo://kf.kobotoolbox.org/{UID}") == (None, "simulé") and lu
    assert server.source_label("odk://central.exemple.org/3/enquete_menage") == "odk_enquete_menage"
    assert sources.connectable(f"kobo://kf.kobotoolbox.org/{UID}") and sources.connectable("odk://c.exemple.org/1/f")
    with open(os.path.join(ICI, "index.html"), encoding="utf-8") as f:
        page = f.read()
    assert 'data-src="form"' in page and "/api/formulaires/connect" in page and 'id="formCard"' in page
    with open(os.path.join(ICI, "server.py"), encoding="utf-8") as f:
        assert 'path == "/api/formulaires/connect"' in f.read()
