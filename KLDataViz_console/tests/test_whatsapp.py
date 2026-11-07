# -*- coding: utf-8 -*-
"""WhatsApp : numéros, modèles, pièce jointe, rapports planifiés et alertes. Aucun appel réseau (Meta simulée)."""
import io
import json
import urllib.error

import pytest

from _harness import reset_app_modules

NUMERO_ID = "1234567890"


class _Rep(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Meta:
    """Faux graph.facebook.com : garde chaque requête, rend un identifiant ou une erreur."""

    def __init__(self, refus=None):
        self.requetes, self.refus = [], refus or {}

    def __call__(self, req, timeout=None):
        corps = req.data or b""
        self.requetes.append({"url": req.full_url, "entetes": dict(req.header_items()), "corps": corps})
        if req.full_url.endswith("/media"):
            return _Rep(json.dumps({"id": "media-42"}).encode())
        doc = json.loads(corps)
        if doc["to"] in self.refus:
            code = self.refus[doc["to"]]
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                         io.BytesIO(json.dumps({"error": {"code": code, "message": f"refusé {doc['to']}"}}).encode()))
        return _Rep(json.dumps({"messages": [{"id": "wamid.1"}]}).encode())

    def messages(self):
        return [json.loads(r["corps"]) for r in self.requetes if r["url"].endswith("/messages")]


@pytest.fixture
def W(monkeypatch, tmp_path):
    for k in ("WHATSAPP_JETON", "WHATSAPP_NUMERO_ID", "WHATSAPP_MODELE_RAPPORT", "WHATSAPP_MODELE_ALERTE",
              "KLDV_ALERTES_WHATSAPP", "WHATSAPP_INDICATIF", "WHATSAPP_LANGUE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KLDV_SURVEILLANCE_DIR", str(tmp_path))
    reset_app_modules()
    import whatsapp
    return whatsapp


def _configurer(monkeypatch):
    monkeypatch.setenv("WHATSAPP_JETON", "jeton-de-test")
    monkeypatch.setenv("WHATSAPP_NUMERO_ID", NUMERO_ID)
    monkeypatch.setenv("WHATSAPP_MODELE_RAPPORT", "rapport_kldataviz")
    monkeypatch.setenv("WHATSAPP_MODELE_ALERTE", "alerte_kldataviz")


def test_numeros_normalises(W):
    assert W.normaliser("+225 07 00 00 00 00") == "+2250700000000"
    assert W.normaliser("00225 05.00.00.00.00") == "+2250500000000"
    assert W.normaliser("07 00 00 00 00") == "+2250700000000"   # indicatif du service, le 0 se garde
    assert W.normaliser("+33 6 12 34 56 78") == "+33612345678"
    for faux in ("abc", "+0123", "12", "+225 07 00 00 00 00 00 00 00 00"):
        with pytest.raises(ValueError):
            W.normaliser(faux)
    assert W.masque("+2250700000012") == "+225 •••• 12"


def test_saisie_melee_emails_et_numeros(W):
    saisie = "direction@exemple.ci dg@exemple.ci, +225 07 00 00 00 00 ; 05 00 00 00 00\n+225 0700000000"
    mails, numeros = W.separer(W.decouper(saisie))
    assert mails == ["direction@exemple.ci", "dg@exemple.ci"]
    assert numeros == ["+2250700000000", "+2250500000000"]   # doublon retiré


def test_inactif_sans_configuration(W):
    assert not W.actif() and W.etat() == {"actif": False, "rapport": False, "alerte": False}
    with pytest.raises(RuntimeError, match="non configuré"):
        W.envoyer_modele("+2250700000000", "x", ["a"])
    assert W.envoyer_alerte("x")["envoyes"] == 0


def test_rapport_avec_piece_jointe_deposee_une_seule_fois(W, monkeypatch, tmp_path):
    _configurer(monkeypatch)
    f = tmp_path / "rapport.pdf"
    f.write_bytes(b"%PDF-1.4 fictif")
    meta = Meta()
    r = W.envoyer_rapport(["+2250700000000", "+2250500000000"], "Ventes", "01/11/2026",
                          "Hausse de 4 %.\nDeux régions en retard.", str(f), ouvrir=meta)
    assert r == {"envoyes": 2, "echecs": []}
    medias = [x for x in meta.requetes if x["url"].endswith("/media")]
    assert len(medias) == 1 and b"%PDF-1.4 fictif" in medias[0]["corps"]
    assert medias[0]["url"] == f"https://graph.facebook.com/v23.0/{NUMERO_ID}/media"
    assert all(x["entetes"]["Authorization"] == "Bearer jeton-de-test" for x in meta.requetes)
    m = meta.messages()[0]
    assert m["to"] == "2250700000000" and m["type"] == "template"
    assert m["template"]["name"] == "rapport_kldataviz" and m["template"]["language"] == {"code": "fr"}
    entete, corps = m["template"]["components"]
    assert entete["parameters"][0]["document"] == {"id": "media-42", "filename": "rapport.pdf"}
    textes = [p["text"] for p in corps["parameters"]]
    assert textes[:2] == ["Ventes", "01/11/2026"] and "\n" not in textes[2]   # Meta refuse les retours à la ligne


def test_un_numero_refuse_n_arrete_pas_les_autres(W, monkeypatch, tmp_path):
    _configurer(monkeypatch)
    meta = Meta(refus={"2250500000099": 131030})
    r = W.envoyer_rapport(["+2250500000099", "+2250700000000"], "Ventes", "01/11/2026", "—", None, ouvrir=meta)
    assert r["envoyes"] == 1 and len(r["echecs"]) == 1
    numero, motif = r["echecs"][0]
    assert numero == "+225 •••• 99" and "liste autorisée" in motif
    assert "2250500000099" not in motif   # le message de Meta, qui cite le numéro, n'est pas repris
    import surveillance
    assert any("WhatsApp" in i["resume"] for i in surveillance._incidents)


def test_jeton_expire_explique(W, monkeypatch):
    _configurer(monkeypatch)
    meta = Meta(refus={"2250700000000": 190})
    with pytest.raises(RuntimeError, match="WHATSAPP_JETON"):
        W.envoyer_modele("+2250700000000", "alerte_kldataviz", ["x"], ouvrir=meta)


def test_planification_accepte_les_numeros_si_whatsapp_actif(W, monkeypatch):
    import studio
    monkeypatch.setattr(studio, "_save_schedules", lambda items: None)
    monkeypatch.setattr(studio, "list_schedules", lambda: [])
    demande = {"source": "ventes.csv", "format": "pdf", "to": "dg@exemple.ci, +225 07 00 00 00 00"}
    with pytest.raises(ValueError, match="pas encore activé"):
        studio.create_schedule(demande)
    _configurer(monkeypatch)
    job = studio.create_schedule(demande)
    assert job["to"] == ["dg@exemple.ci", "+2250700000000"]
    with pytest.raises(ValueError, match="PDF ou Excel"):
        studio.create_schedule(dict(demande, format="html"))
    with pytest.raises(ValueError, match="Numéro WhatsApp invalide"):
        studio.create_schedule(dict(demande, to="+12"))
    assert studio.create_schedule(dict(demande, format="html", to="dg@exemple.ci"))["to"] == ["dg@exemple.ci"]


def test_rapport_planifie_par_email_et_whatsapp(W, monkeypatch, tmp_path):
    import studio
    import whatsapp
    _configurer(monkeypatch)
    mails, wa = [], []
    monkeypatch.setattr(studio, "send_mail", lambda dest, sujet, corps, piece=None: mails.append(dest))
    monkeypatch.setattr(whatsapp, "envoyer_rapport",
                        lambda numeros, nom, date, retenir, fichier: wa.append((numeros, nom, retenir)) or
                        {"envoyes": len(numeros), "echecs": []})
    job = {"id": "j1", "name": "Ventes", "to": ["dg@exemple.ci", "+2250700000000"]}
    statut = studio.envoyer_rapport(job, {"rows": 3}, {"resume": "Hausse de 4 %."}, str(tmp_path / "r.pdf"),
                                    __import__("datetime").datetime(2026, 11, 1))
    assert mails == [["dg@exemple.ci"]] and wa == [(["+2250700000000"], "Ventes", "Hausse de 4 %.")]
    assert statut == "Envoyé : e-mail à 1 adresse(s) ; WhatsApp à 1 numéro(s)"

    def panne(*a, **k):
        raise RuntimeError("Brevo injoignable")
    monkeypatch.setattr(studio, "send_mail", panne)
    statut = studio.envoyer_rapport(job, {"rows": 3}, None, "r.pdf", __import__("datetime").datetime(2026, 11, 1))
    assert statut.startswith("Envoyé : e-mail non envoyé : Brevo injoignable ; WhatsApp à 1")
    assert studio.envoyer_rapport(dict(job, to=[]), {}, None, "r.pdf", None) == "Rapport créé"


def test_alerte_whatsapp_meme_si_brevo_tombe(W, monkeypatch):
    monkeypatch.setenv("KLDV_ALERTES", "admin@exemple.ci")
    import surveillance
    resumes = []
    surveillance.signaler("ia", "DeepSeek : délai dépassé (cle-secrete-xyz)")

    def brevo_en_panne(*a, **k):
        raise RuntimeError("Brevo injoignable")
    r = surveillance.alerter(force=True, envoyer=brevo_en_panne,
                             envoyer_wa=lambda resume: resumes.append(resume) or {"envoyes": 1, "echecs": []})
    assert r["envoye"] and r["whatsapp"] == 1
    assert resumes == ["1 incident : Intelligence artificielle. Détail dans l'e-mail et la page Admin."]
    assert "cle-secrete" not in resumes[0]   # seulement le genre et le nombre


def test_essai_dit_combien_de_numeros(W, monkeypatch):
    _configurer(monkeypatch)
    monkeypatch.setenv("KLDV_ALERTES", "admin@exemple.ci")
    monkeypatch.setenv("KLDV_ALERTES_WHATSAPP", "+225 07 00 00 00 00, pas-un-numero, 05 00 00 00 00")
    import cloud
    import surveillance
    import whatsapp
    assert whatsapp.destinataires_alertes() == ["+2250700000000", "+2250500000000"]
    monkeypatch.setattr(cloud, "send_mail", lambda *a, **k: None)
    r = surveillance.essai(envoyer_wa=lambda resume: {"envoyes": 2, "echecs": []})
    assert r["envoye"] and r["whatsapp"] == {"envoyes": 2, "erreur": ""}


def test_branchements(W):
    import os
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lire = lambda n: open(os.path.join(app, n), encoding="utf-8").read()
    html = lire("index.html")
    assert 'id="scTo" autocomplete="off" placeholder="direction@exemple.ci, +225' in html
    assert 'id="ssTo" autocomplete="off"' in html and 'type="email" id="ssTo"' not in html
    assert "Meta (WhatsApp Business)" in lire("confidentialite.html")
    assert "r.whatsapp" in lire("admin.html")
