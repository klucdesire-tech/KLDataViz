# -*- coding: utf-8 -*-
"""Connexion à la base : quelques essais pour un refus passager du pooler, mais une attente totale bornée
(horloge simulée, aucune vraie connexion)."""
import psycopg
import pytest

from _harness import reset_app_modules


@pytest.fixture()
def base(monkeypatch):
    reset_app_modules()
    import cloud  # noqa: PLC0415
    horloge = {"t": 0.0, "essais": [], "pannes": []}
    monkeypatch.setattr(cloud, "DATABASE_URL", "postgresql://u:p@hote-fictif:6543/postgres")
    monkeypatch.setattr(cloud.time, "monotonic", lambda: horloge["t"])
    monkeypatch.setattr(cloud.time, "sleep", lambda s: horloge.__setitem__("t", horloge["t"] + s))

    def connect(dsn, **kw):
        horloge["essais"].append(kw["connect_timeout"])
        panne = horloge["pannes"].pop(0) if horloge["pannes"] else None
        if panne == "delai":      # l'hôte ne répond pas : tout le délai de l'essai est consommé
            horloge["t"] += kw["connect_timeout"]
            raise psycopg.OperationalError("connection timeout expired")
        if panne == "refus":      # le pooler refuse tout de suite
            horloge["t"] += 0.05
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        if panne == "mot_de_passe":
            raise psycopg.OperationalError('FATAL:  password authentication failed for user "u"')
        return "connexion"
    monkeypatch.setattr(psycopg, "connect", connect)
    cloud.horloge = horloge
    return cloud


def test_refus_passager_puis_succes(base):
    base.horloge["pannes"] = ["refus", "refus"]
    assert base.db() == "connexion" and len(base.horloge["essais"]) == 3
    assert base.horloge["t"] < 2  # deux courtes pauses, pas d'attente inutile


def test_vraie_panne_attente_bornee(base):
    base.horloge["pannes"] = ["delai"] * 5
    with pytest.raises(base.Unavailable, match="injoignable"):
        base.db()
    # avant : 3 × 10 s + pauses ≈ 31 s ; maintenant le budget arrête les essais
    assert base.horloge["t"] <= 8 and base.horloge["essais"] == [4, 3]


def test_refus_repetes_trois_essais_puis_indisponible(base):
    base.horloge["pannes"] = ["refus"] * 5
    with pytest.raises(base.Unavailable):
        base.db()
    assert len(base.horloge["essais"]) == 3


def test_mot_de_passe_refuse_pas_de_nouvel_essai(base):
    base.horloge["pannes"] = ["mot_de_passe"] * 3
    with pytest.raises(base.Unavailable):
        base.db()
    assert len(base.horloge["essais"]) == 1 and base.horloge["t"] == 0
