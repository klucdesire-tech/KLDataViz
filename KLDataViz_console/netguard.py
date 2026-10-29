# -*- coding: utf-8 -*-
"""Téléchargement d'URL protégé contre la falsification de requêtes côté serveur (SSRF).

En ligne, le serveur refuse les adresses internes (127.0.0.1, 10.x, 169.254.169.254…),
vérifie chaque redirection et limite la taille et la durée du téléchargement.
"""
import http.client
import ipaddress
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
        if not ip.is_global or ip.is_multicast:
            raise BlockedURL("Ce lien pointe vers une adresse interne : il est refusé.")
    return list(dict.fromkeys(info[4][0] for info in infos))


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
    perso = re.match(r"(/personal/[^/]+)/", u.path)
    if not (ident and perso):
        return data, ctype  # page sans fichier (lien expiré, accès restreint) : l'appelant explique
    lien = f"{u.scheme}://{u.netloc}{perso.group(1)}/_layouts/15/download.aspx?UniqueId={ident.group(1)}&Translate=false"
    check_url(lien, allow_private, https_only)
    with opener.open(urllib.request.Request(lien, headers=entetes), timeout=TIMEOUT_S) as resp:
        return _lire(resp, max_bytes)
