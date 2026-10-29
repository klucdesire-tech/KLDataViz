# -*- coding: utf-8 -*-
"""Base PostgreSQL d'un client, version en ligne : connexion rendue sûre (adresse publique épinglée, SSL,
lecture seule, délais), paramètres dangereux refusés, mot de passe masqué dans les métadonnées."""
import socket

import pytest

import netguard
from ingestion import readers


def _dns(monkeypatch, ip):
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda host, port, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))])


def test_connexion_publique_renforcee(monkeypatch):
    from psycopg.conninfo import conninfo_to_dict
    _dns(monkeypatch, "93.184.216.34")
    d = conninfo_to_dict(netguard.safe_pg_conninfo("postgresql://lecteur:s3cret@db.exemple.com:6543/ventes"))
    assert d["host"] == "db.exemple.com" and d["hostaddr"] == "93.184.216.34" and d["port"] == "6543"
    assert d["user"] == "lecteur" and d["password"] == "s3cret" and d["dbname"] == "ventes"
    assert d["sslmode"] == "require" and d["connect_timeout"] == "10"
    assert "default_transaction_read_only=on" in d["options"] and "statement_timeout=60000" in d["options"]
    # sslmode=disable demandé : forcé à require ; verify-full : autorités de certification du système
    assert conninfo_to_dict(netguard.safe_pg_conninfo("host=db.exemple.com dbname=v sslmode=disable"))["sslmode"] == "require"
    v = conninfo_to_dict(netguard.safe_pg_conninfo("host=db.exemple.com dbname=v sslmode=verify-full"))
    assert v["sslmode"] == "verify-full" and v["sslrootcert"] == "system"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1"])
def test_adresses_internes_refusees(monkeypatch, ip):
    _dns(monkeypatch, ip)
    with pytest.raises(netguard.BlockedURL):
        netguard.safe_pg_conninfo("postgresql://u:p@base.piege.test/db")


@pytest.mark.parametrize("dsn", [
    "postgresql://u:p@/db",                                   # pas d'hôte (socket local)
    "host=/var/run/postgresql dbname=db",                     # socket Unix
    "host=a.exemple.com,b.exemple.com dbname=db",             # plusieurs hôtes
    "host=db.exemple.com hostaddr=127.0.0.1 dbname=db",       # adresse imposée par le client
    "host=db.exemple.com dbname=db options='-c default_transaction_read_only=off'",
    "host=db.exemple.com dbname=db passfile=/etc/passwd",
    "host=db.exemple.com dbname=db service=interne",
    "host=db.exemple.com dbname=db sslrootcert=/etc/ssl/x.pem",
    "host=db.exemple.com port=99999 dbname=db",
    "ceci n'est pas une connexion",
])
def test_parametres_refuses(monkeypatch, dsn):
    _dns(monkeypatch, "93.184.216.34")
    with pytest.raises(netguard.BlockedURL):
        netguard.safe_pg_conninfo(dsn)


def test_mot_de_passe_masque_dans_les_metadonnees():
    assert "s3cret" not in readers._mask_dsn("postgresql://lecteur:s3cret@db.exemple.com/ventes")
    assert "s3cret" not in readers._mask_dsn("host=db.exemple.com password=s3cret dbname=v")
