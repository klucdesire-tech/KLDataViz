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
    assert conninfo_to_dict(netguard.safe_pg_conninfo("host=db.exemple.com user=u dbname=v sslmode=disable"))["sslmode"] == "require"
    v = conninfo_to_dict(netguard.safe_pg_conninfo("host=db.exemple.com user=u dbname=v sslmode=verify-full"))
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
    "host=db.exemple.com user=u dbname=db options='-c default_transaction_read_only=off'",
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


def _faux_postgres():
    """Faux serveur PostgreSQL local : lit le message de démarrage (utilisateur, base), demande le mot de passe
    en clair, et note ce que le client envoie. Sert à vérifier ce qui partirait chez un serveur hostile."""
    import struct
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    vu = {}

    def servir():
        c, _ = srv.accept()
        c.settimeout(5)
        try:
            n = struct.unpack("!I", c.recv(4))[0]
            corps = c.recv(n - 4)
            champs = corps[4:].split(b"\0")
            vu.update({champs[i].decode(): champs[i + 1].decode() for i in range(0, len(champs) - 1, 2) if champs[i]})
            c.sendall(b"R" + struct.pack("!II", 8, 3))  # AuthenticationCleartextPassword
            t = c.recv(1)
            if t == b"p":
                n = struct.unpack("!I", c.recv(4))[0]
                vu["password_recu"] = c.recv(n - 4).rstrip(b"\0").decode()
        except OSError:
            pass
        finally:
            c.close()
            srv.close()

    threading.Thread(target=servir, daemon=True).start()
    return srv.getsockname()[1], vu


@pytest.mark.parametrize("mdp_saisi", [None, "à moi"])
def test_aucun_identifiant_du_serveur_ne_part(monkeypatch, tmp_path, mdp_saisi):
    """PGPASSWORD, PGSERVICE, .pgpass du serveur : jamais envoyés à la base du client (relecture de Hermes, PR 113)."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    pgpass = tmp_path / "pgpass"
    pgpass.write_text("*:*:*:*:SECRET_PGPASS\n", encoding="utf-8")
    monkeypatch.setenv("PGPASSWORD", "SECRET_ENV")
    monkeypatch.setenv("PGPASSFILE", str(pgpass))
    service = tmp_path / "pg_service.conf"
    service.write_text("[interne]\nuser=postgres\npassword=SECRET_SERVICE\ndbname=kldataviz\n", encoding="utf-8")
    monkeypatch.setenv("PGSERVICEFILE", str(service))
    monkeypatch.setenv("PGSERVICE", "interne")
    _dns(monkeypatch, "93.184.216.34")
    dsn = "postgresql://client@db.exemple.com/ventes" if mdp_saisi is None else \
        f"postgresql://client:{mdp_saisi.replace(' ', '%20')}@db.exemple.com/ventes"
    d = conninfo_to_dict(netguard.safe_pg_conninfo(dsn))
    port, vu = _faux_postgres()
    d.update(hostaddr="127.0.0.1", port=str(port), sslmode="disable")  # le faux serveur n'a pas de TLS
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(make_conninfo(**d)).close()
    assert vu.get("user") == "client" and vu.get("database") == "ventes"
    assert "SECRET" not in vu.get("password_recu", "")
    assert vu.get("password_recu", "") == (mdp_saisi or "")


def test_utilisateur_et_base_obligatoires(monkeypatch):
    _dns(monkeypatch, "93.184.216.34")
    monkeypatch.setenv("PGUSER", "postgres")
    monkeypatch.setenv("PGDATABASE", "kldataviz")
    for dsn in ("postgresql://db.exemple.com/", "postgresql://client@db.exemple.com/", "host=db.exemple.com dbname=v"):
        with pytest.raises(netguard.BlockedURL):
            netguard.safe_pg_conninfo(dsn)


def test_mot_de_passe_avec_espace_ou_apostrophe_masque(monkeypatch):
    _dns(monkeypatch, "93.184.216.34")
    from ingestion.store import _mask
    for mdp in ("Tres Secret9", "xQ'yQ zQ", "aW=bW cW"):
        from psycopg.conninfo import make_conninfo
        brut = make_conninfo(host="db.exemple.com", user="u", dbname="v", password=mdp)
        for morceau in mdp.split():
            assert morceau not in _mask(brut).replace("password=***", "")


@pytest.mark.parametrize("ip", ["::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::1"])
def test_ipv6_qui_cache_une_ipv4_refusee(monkeypatch, ip):
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda host, port, **k: [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0))])
    with pytest.raises(netguard.BlockedURL):
        netguard.safe_pg_conninfo("postgresql://u:p@base.piege.test/db")


def test_identite_du_jeu_sans_mot_de_passe_ni_ip(monkeypatch):
    from ingestion import agent
    _dns(monkeypatch, "93.184.216.34")
    a = netguard.safe_pg_conninfo("postgresql://lecteur:un@db.exemple.com/ventes")
    _dns(monkeypatch, "93.184.216.35")
    b = netguard.safe_pg_conninfo("postgresql://lecteur:deux@db.exemple.com/ventes")
    assert agent._identite_pg(a) == agent._identite_pg(b) == "postgresql://lecteur@db.exemple.com:5432/ventes"
    assert agent._identite_pg("postgresql://u:p@localhost/db") is None  # local : identité inchangée
