# -*- coding: utf-8 -*-
"""Sauvegardes hors site : chiffrées avant de quitter le serveur, refusées si on les modifie ou les tronque,
une par jour, surveillées. Données fictives, aucun appel réseau (stockage simulé)."""
import datetime as dt
import json
import os
import sys

import pytest

from _harness import reset_app_modules

pytest.importorskip("cryptography")
ICI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FICTIF = "Zoé Fictive, dossier DOS-2025-00042".encode("utf-8")


@pytest.fixture
def D(monkeypatch):
    for n in ("SAUVEGARDE_S3_BUCKET", "SAUVEGARDE_S3_ENDPOINT", "SAUVEGARDE_S3_CLE_ID", "SAUVEGARDE_S3_CLE_SECRETE",
              "SAUVEGARDE_CHIFFREMENT_CLE"):
        monkeypatch.delenv(n, raising=False)
    reset_app_modules()
    import sauvegarde_distante
    monkeypatch.setattr(sauvegarde_distante, "BLOC", 1000)  # plusieurs blocs sans gros fichier
    return sauvegarde_distante


def _fichier(tmp_path, nom, contenu):
    p = tmp_path / nom
    p.write_bytes(contenu)
    return str(p)


@pytest.mark.parametrize("taille", [0, 10, 1000, 3500])
def test_aller_retour(D, tmp_path, taille):
    cle = D.lire_cle(D.nouvelle_cle())
    contenu = (FICTIF * 200)[:taille]
    src = _fichier(tmp_path, "a.dump", contenu)
    D.chiffrer(src, src + ".kldv", cle)
    chiffre = open(src + ".kldv", "rb").read()
    assert chiffre.startswith(D.MAGIC) and (not taille or FICTIF[:10] not in chiffre)
    D.dechiffrer(src + ".kldv", str(tmp_path / "b.dump"), cle)
    assert (tmp_path / "b.dump").read_bytes() == contenu


def test_modifie_tronque_ou_mauvaise_cle_refuse(D, tmp_path):
    cle = D.lire_cle(D.nouvelle_cle())
    src = _fichier(tmp_path, "a.dump", FICTIF * 200)
    D.chiffrer(src, src + ".kldv", cle)
    brut = open(src + ".kldv", "rb").read()
    premier = 16 + 4 + 1000 + 16                      # en-tête + longueur + premier bloc chiffré (tag compris)
    essais = {"modifié": brut[:30] + bytes([brut[30] ^ 1]) + brut[31:],
              "tronqué entre deux blocs": brut[:premier],
              "tronqué en plein bloc": brut[:-5]}
    for nom, octets in essais.items():
        p = tmp_path / f"{nom}.kldv"
        p.write_bytes(octets)
        with pytest.raises(D.Erreur):
            D.dechiffrer(str(p), str(tmp_path / "x"), cle)
    with pytest.raises(D.Erreur, match="mauvaise clé"):
        D.dechiffrer(src + ".kldv", str(tmp_path / "x"), D.lire_cle(D.nouvelle_cle()))
    with pytest.raises(D.Erreur, match="pas une sauvegarde"):
        D.dechiffrer(src, str(tmp_path / "x"), cle)


def test_reglages(D, monkeypatch):
    assert D.config() is None                                     # pas de compartiment : rien n'est envoyé
    monkeypatch.setenv("SAUVEGARDE_S3_BUCKET", "kldv-sauvegardes")
    with pytest.raises(D.Erreur, match="SAUVEGARDE_CHIFFREMENT_CLE"):  # jamais d'envoi en clair
        D.config()
    for n, v in (("SAUVEGARDE_S3_ENDPOINT", "https://compte.r2.example.invalid"), ("SAUVEGARDE_S3_CLE_ID", "id"),
                 ("SAUVEGARDE_S3_CLE_SECRETE", "secret"), ("SAUVEGARDE_CHIFFREMENT_CLE", "trop-court")):
        monkeypatch.setenv(n, v)
    with pytest.raises(D.Erreur, match="base64|32 octets"):
        D.config()
    monkeypatch.setenv("SAUVEGARDE_CHIFFREMENT_CLE", D.nouvelle_cle())
    assert D.config()["bucket"] == "kldv-sauvegardes" and len(D.config()["cle"]) == 32


class Stockage:
    """Compartiment S3 simulé : garde ce qu'on lui envoie."""

    def __init__(self, deja=()):
        self.objets = {k: b"" for k in deja}

    def upload_file(self, chemin, bucket, cle):
        self.objets[cle] = open(chemin, "rb").read()

    def get_paginator(self, nom):
        objets = self.objets
        return type("P", (), {"paginate": staticmethod(lambda **k: [{"Contents": [{"Key": c} for c in sorted(objets)]}])})()

    def delete_object(self, Bucket, Key):
        del self.objets[Key]


def test_envoi_chiffre_et_rotation(D, tmp_path):
    cfg = {"bucket": "b", "cle": D.lire_cle(D.nouvelle_cle())}
    anciens = [f"kldataviz/base_202601{j:02d}_010000.dump.kldv" for j in range(1, 5)]
    s = Stockage(anciens)
    dump = _fichier(tmp_path, "base_20261107_020000.dump", FICTIF * 50)
    envoyes = D.envoyer([dump], cfg, garder=3, client=s, journal=lambda *_: None)
    assert envoyes == ["kldataviz/base_20261107_020000.dump.kldv"]
    assert FICTIF not in s.objets[envoyes[0]] and s.objets[envoyes[0]].startswith(D.MAGIC)
    assert not os.path.exists(dump + ".kldv")                     # aucun fichier chiffré oublié sur le disque
    assert sorted(s.objets) == sorted(anciens[-2:] + envoyes)      # les 3 plus récentes gardées


@pytest.fixture
def script(tmp_path, monkeypatch, D):
    monkeypatch.setenv("KLDV_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data" / "tenants").mkdir(parents=True)
    (tmp_path / "data" / "tenants" / "note.txt").write_bytes(FICTIF)
    sys.path.insert(0, os.path.join(ICI, "scripts"))
    for m in ("sauvegarde", "tenancy"):
        sys.modules.pop(m, None)
    import sauvegarde
    stock = Stockage()
    monkeypatch.setattr(D, "_client", lambda cfg: stock)
    sauvegarde.stock = stock
    return sauvegarde


def _etat(dossier):
    return json.loads((dossier / ".etat.json").read_text(encoding="utf-8"))


def test_script_envoie_et_note_le_resultat(script, D, tmp_path, monkeypatch):
    sortie = tmp_path / "out"
    assert script.main(["--sans-base", "--dossier", str(sortie)]) == 0
    assert _etat(sortie)["ok"] and _etat(sortie)["distant"] == "non configurée"
    for n, v in (("SAUVEGARDE_S3_BUCKET", "b"), ("SAUVEGARDE_S3_ENDPOINT", "https://x.example.invalid"),
                 ("SAUVEGARDE_S3_CLE_ID", "id"), ("SAUVEGARDE_S3_CLE_SECRETE", "s")):
        monkeypatch.setenv(n, v)
    assert script.main(["--sans-base", "--dossier", str(sortie)]) == 5  # clé de chiffrement absente : rien ne part
    assert not script.stock.objets and _etat(sortie)["ok"] is False
    cle = D.nouvelle_cle()
    monkeypatch.setenv("SAUVEGARDE_CHIFFREMENT_CLE", cle)
    assert script.main(["--sans-base", "--dossier", str(sortie)]) == 0
    assert _etat(sortie)["distant"] == "envoyée" and len(script.stock.objets) == 1
    (nom, chiffre), = script.stock.objets.items()
    assert FICTIF not in chiffre
    # restauration : la copie distante se déchiffre avec la clé posée dans Render
    (tmp_path / "copie.kldv").write_bytes(chiffre)
    assert script.main(["--dechiffrer", str(tmp_path / "copie.kldv"), str(tmp_path / "copie.tar.gz")]) == 0
    import tarfile
    with tarfile.open(tmp_path / "copie.tar.gz") as t:
        assert t.extractfile("tenants/note.txt").read() == FICTIF


def test_script_nouvelle_cle(script, D, capsys):
    assert script.main(["--nouvelle-cle"]) == 0
    assert len(D.lire_cle(capsys.readouterr().out.strip())) == 32


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KLDV_SURVEILLANCE_DIR", str(tmp_path))
    reset_app_modules()
    import server
    (tmp_path / "sauvegardes").mkdir(exist_ok=True)
    monkeypatch.setattr(server.T, "DATA_DIR", str(tmp_path))
    return server


def _poser_etat(tmp_path, ok=True, heures=1, distant="envoyée"):
    date = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=heures)).isoformat(timespec="seconds")
    (tmp_path / "sauvegardes" / ".etat.json").write_text(json.dumps({"date": date, "ok": ok, "distant": distant}), encoding="utf-8")


def test_une_par_jour_la_nuit(srv, tmp_path):
    nuit = dt.datetime(2026, 11, 8, 2, 0, tzinfo=dt.timezone.utc)
    midi = dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc)
    assert srv._sauvegarde_due(None, midi)                       # jamais faite : tout de suite
    _poser_etat(tmp_path, heures=21)
    assert srv._sauvegarde_due(srv._sauvegarde_etat(), nuit) and not srv._sauvegarde_due(srv._sauvegarde_etat(), midi)
    _poser_etat(tmp_path, heures=2)
    assert not srv._sauvegarde_due(srv._sauvegarde_etat(), nuit)
    _poser_etat(tmp_path, heures=31)
    assert srv._sauvegarde_due(srv._sauvegarde_etat(), midi)     # rattrapage après une nuit sans service


def test_controle_sante(srv, tmp_path, monkeypatch):
    assert srv._sante_sauvegarde()[0] is None                    # non configurée : ni bon ni en échec
    monkeypatch.setenv("SAUVEGARDE_S3_BUCKET", "b")
    _poser_etat(tmp_path, heures=2)
    assert srv._sante_sauvegarde()[0] is True
    _poser_etat(tmp_path, heures=2, ok=False, distant="échec")
    assert srv._sante_sauvegarde() == (False, srv._sante_sauvegarde()[1]) and "échec" in srv._sante_sauvegarde()[1]
    _poser_etat(tmp_path, heures=40)
    assert srv._sante_sauvegarde()[0] is False


def test_liste_sans_fichier_d_etat_et_une_seule_a_la_fois(srv, tmp_path):
    _poser_etat(tmp_path)
    (tmp_path / "sauvegardes" / "base_20261107_020000.dump").write_bytes(b"x")
    assert [s["nom"] for s in srv._sauvegardes_liste()] == ["base_20261107_020000.dump"]
    assert srv._SAUVEGARDE_LOCK.acquire(blocking=False)
    try:
        if os.path.isfile(os.path.join(srv.BASE, "scripts", "sauvegarde.py")):
            assert srv._sauvegarde_lancer() == {"error": "une sauvegarde est déjà en cours."}
    finally:
        srv._SAUVEGARDE_LOCK.release()
