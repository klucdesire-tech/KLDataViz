# -*- coding: utf-8 -*-
"""Téléchargement d'URL protégé contre la falsification de requêtes côté serveur (SSRF).

En ligne, le serveur refuse les adresses internes (127.0.0.1, 10.x, 169.254.169.254…),
vérifie chaque redirection et limite la taille et la durée du téléchargement.
"""
import http.client
import ipaddress
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 50 * 1024 * 1024
TIMEOUT_S = 60


class BlockedURL(ValueError):
    pass


def check_url(url: str, allow_private: bool = False, https_only: bool = True) -> None:
    u = urllib.parse.urlparse(url)
    if u.scheme not in (("https",) if https_only else ("http", "https")):
        raise BlockedURL("Seuls les liens https:// sont acceptés.")
    if not u.hostname:
        raise BlockedURL("Lien invalide.")
    if allow_private:
        return
    public_ips(u.hostname, u.port or 443)


def public_ips(host: str, port: int) -> list:
    """Résout `host` et renvoie ses adresses (toutes publiques) ; lève BlockedURL si l'une est interne."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise BlockedURL(f"Adresse introuvable : {host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast or _ipv4_embarquee(ip):
            raise BlockedURL("Ce lien pointe vers une adresse interne : il est refusé.")
    return list(dict.fromkeys(info[4][0] for info in infos))


_IPV6_DOUTEUSES = tuple(ipaddress.ip_network(n) for n in (
    "64:ff9b::/96", "64:ff9b:1::/48",  # NAT64
    "::/96",                           # « IPv4-compatible » (::7f00:1 = 127.0.0.1), obsolète
    "::ffff:0:0:0/96",                 # SIIT (::ffff:0:a.b.c.d)
    "fec0::/10",                       # site-local, obsolète
))


def _ipv4_embarquee(ip) -> bool:
    """IPv6 qui transporte une adresse IPv4 (::ffff:a.b.c.d, 6to4, NAT64, IPv4-compatible, SIIT) ou plage obsolète
    (site-local) : refusée, l'adresse cachée peut être interne."""
    return ip.version == 6 and bool(ip.ipv4_mapped or ip.sixtofour or any(ip in n for n in _IPV6_DOUTEUSES))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Se connecte à l'adresse VÉRIFIÉE, pas à une nouvelle résolution du nom : un DNS qui répond une adresse
    publique au contrôle puis 127.0.0.1 / 169.254.169.254 à la connexion (« DNS rebinding ») est sans effet.
    Le certificat reste vérifié pour le nom d'hôte."""

    def connect(self):
        if self._tunnel_host:  # proxy HTTPS : la cible est contrôlée par check_url, le proxy fait la connexion
            return super().connect()
        err = None
        for ip in public_ips(self.host, self.port):  # comme create_connection : chaque adresse vérifiée, tour à tour
            try:
                sock = socket.create_connection((ip, self.port), self.timeout, self.source_address)
                break
            except OSError as exc:
                err = exc
        else:
            raise err or OSError(f"connexion impossible : {self.host}")
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req)


def handlers(allow_private: bool) -> list:
    """Gestionnaires à ajouter à un opener urllib : connexion épinglée en ligne (adresses internes refusées)."""
    return [] if allow_private else [PinnedHTTPSHandler()]


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private, https_only):
        self.allow_private, self.https_only = allow_private, https_only

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.allow_private, self.https_only)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _lire(resp, max_bytes: int):
    size = resp.headers.get("Content-Length")
    if size and size.isdigit() and int(size) > max_bytes:
        raise ValueError(f"Fichier trop volumineux (plus de {max_bytes // (1024 * 1024)} Mo).")
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"Fichier trop volumineux (plus de {max_bytes // (1024 * 1024)} Mo).")
    return data, (resp.headers.get("Content-Type") or "").lower()


def fetch(url: str, allow_private: bool = False, https_only: bool = True, max_bytes: int = MAX_BYTES):
    """Renvoie (contenu, content_type). Lève BlockedURL, urllib.error.URLError ou ValueError."""
    check_url(url, allow_private, https_only)
    opener = urllib.request.build_opener(_CheckedRedirect(allow_private, https_only), *handlers(allow_private))
    req = urllib.request.Request(url, headers={"User-Agent": "KLDataViz/1.0"})
    with opener.open(req, timeout=TIMEOUT_S) as resp:
        return _lire(resp, max_bytes)


NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")


def fetch_onedrive(url: str, allow_private: bool = False, https_only: bool = True, max_bytes: int = MAX_BYTES):
    """Lien de partage OneDrive personnel (1drv.ms, onedrive.live.com), y compris le nouveau format
    « 1drv.ms/x/c/… » : l'ancienne API anonyme api.onedrive.com répond désormais 401.

    Comme un navigateur : le lien est ouvert (cookie de partage déposé au passage), la page d'aperçu donne
    l'identifiant du fichier (« sourcedoc={…} »), puis download.aspx?UniqueId=… renvoie le fichier lui-même.
    Chaque étape et chaque redirection passent par les mêmes contrôles que fetch (adresses internes refusées,
    connexion épinglée, taille limitée)."""
    import http.cookiejar
    import re

    check_url(url, allow_private, https_only)
    opener = urllib.request.build_opener(_CheckedRedirect(allow_private, https_only),
                                         urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                         *handlers(allow_private))
    entetes = {"User-Agent": NAVIGATEUR}
    with opener.open(urllib.request.Request(url, headers=entetes), timeout=TIMEOUT_S) as resp:
        final = resp.geturl()
        data, ctype = _lire(resp, max_bytes)
    if "text/html" not in ctype:
        return data, ctype  # le lien donnait déjà le fichier
    page = data.decode("utf-8", "replace")
    ident = (re.search(r"sourcedoc=(?:%7B|\{)([0-9a-fA-F-]{36})", final, re.I)
             or re.search(r"download\.aspx\?UniqueId=([0-9a-fA-F-]{36})", page))
    u = urllib.parse.urlparse(final)
    perso = re.match(r"(/personal/[A-Za-z0-9_.-]+)/", u.path)
    hote = (u.hostname or "").lower()
    microsoft = allow_private or hote == "onedrive.live.com" or hote.endswith((".sharepoint.com", ".microsoftpersonalcontent.com"))
    if not (ident and perso and microsoft):
        return data, ctype  # page sans fichier (lien expiré, accès restreint) : l'appelant explique
    hote_port = hote + (f":{u.port}" if u.port else "")  # jamais d'identifiants « user@ » repris de l'URL
    lien = f"{u.scheme}://{hote_port}{perso.group(1)}/_layouts/15/download.aspx?UniqueId={ident.group(1)}&Translate=false"
    check_url(lien, allow_private, https_only)
    with opener.open(urllib.request.Request(lien, headers=entetes), timeout=TIMEOUT_S) as resp:
        return _lire(resp, max_bytes)


PG_KEYS = {"host", "port", "dbname", "user", "password", "sslmode", "application_name"}
PG_STATEMENT_TIMEOUT_MS = 60_000


def safe_pg_conninfo(dsn: str) -> str:
    """Connexion PostgreSQL saisie par un client (version en ligne), rendue sûre pour le serveur :

    - seuls les paramètres usuels sont acceptés (pas de `hostaddr`, `options`, `passfile`, `service`, certificats…) ;
    - un seul hôte, joignable sur Internet : adresses internes refusées, et la connexion est épinglée sur
      l'adresse vérifiée (`hostaddr`), comme pour les téléchargements (pas de « DNS rebinding ») ;
    - chiffrement obligatoire (`sslmode=require`, ou `verify-full` avec les autorités du système si demandé) ;
    - session en lecture seule, délai de connexion de 10 s et de requête de 60 s ; un serveur qui se tait est
      abandonné (keepalives, tcp_user_timeout) ;
    - AUCUN identifiant du serveur KLDataViz ne peut partir chez le client : utilisateur, base et mot de passe
      sont toujours explicites (un service PGSERVICE ne peut donc plus les fournir), et PGPASSWORD, le fichier
      .pgpass et un certificat client (PGSSLCERT) sont neutralisés.
    Lève BlockedURL avec un message lisible."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    try:
        params = {k: v for k, v in conninfo_to_dict(dsn).items() if v not in (None, "")}
    except Exception:
        raise BlockedURL("Chaîne de connexion PostgreSQL invalide (format attendu : hôte, "
                         "utilisateur, base et mot de passe explicites).")
    refuses = sorted(set(params) - PG_KEYS)
    if refuses:
        raise BlockedURL("Paramètre de connexion non autorisé en ligne : " + ", ".join(refuses) + ".")
    host = str(params.get("host") or "").strip()
    if not host or host.startswith("/") or "," in host:
        raise BlockedURL("Indiquez l'adresse Internet d'un seul serveur PostgreSQL (pas localhost ni un socket local).")
    if not params.get("user") or not params.get("dbname"):
        raise BlockedURL("Indiquez l'utilisateur et la base dans la connexion (format : postgresql://, puis "
                         "utilisateur, mot de passe, serveur, port et base).")
    port = str(params.get("port") or "5432")
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise BlockedURL("Port PostgreSQL invalide.")
    ip = public_ips(host, int(port))[0]  # refuse 127.0.0.1, 10.x, 192.168.x, 169.254.169.254…
    if params.get("sslmode") in ("verify-ca", "verify-full"):
        params["sslrootcert"] = "system"
    else:
        params["sslmode"] = "require"
    params.update(host=host, hostaddr=ip, port=port, connect_timeout="10",
                  options=f"-c default_transaction_read_only=on -c statement_timeout={PG_STATEMENT_TIMEOUT_MS}",
                  # rien de l'environnement du serveur : ni mot de passe (PGPASSWORD, .pgpass), ni service, ni certificat
                  # (un service PGSERVICE ne complète que les paramètres absents : tous ceux qui comptent sont fixés ici ;
                  # « service='' » n'est pas possible, libpq le cherche et échoue)
                  password=params.get("password", ""), passfile=os.devnull, sslcertmode="disable",
                  gssencmode="disable",
                  # mot de passe seulement : ni Kerberos/SSPI (jeton du poste ou du serveur), ni authentification
                  # « trust » silencieuse qu'un faux serveur accepterait pour cacher une session inattendue
                  require_auth="scram-sha-256,md5,password",
                  keepalives="1", keepalives_idle="30", keepalives_interval="10", keepalives_count="3",
                  tcp_user_timeout=str(PG_STATEMENT_TIMEOUT_MS + 30_000))
    return make_conninfo(**params)
