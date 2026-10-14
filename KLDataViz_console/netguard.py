# -*- coding: utf-8 -*-
"""Téléchargement d'URL protégé contre la falsification de requêtes côté serveur (SSRF).

En ligne, le serveur refuse les adresses internes (127.0.0.1, 10.x, 169.254.169.254…),
vérifie chaque redirection et limite la taille et la durée du téléchargement.
"""
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
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise BlockedURL(f"Adresse introuvable : {u.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise BlockedURL("Ce lien pointe vers une adresse interne : il est refusé.")


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private, https_only):
        self.allow_private, self.https_only = allow_private, https_only

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.allow_private, self.https_only)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, allow_private: bool = False, https_only: bool = True, max_bytes: int = MAX_BYTES):
    """Renvoie (contenu, content_type). Lève BlockedURL, urllib.error.URLError ou ValueError."""
    check_url(url, allow_private, https_only)
    opener = urllib.request.build_opener(_CheckedRedirect(allow_private, https_only))
    req = urllib.request.Request(url, headers={"User-Agent": "KLDataViz/1.0"})
    with opener.open(req, timeout=TIMEOUT_S) as resp:
        size = resp.headers.get("Content-Length")
        if size and size.isdigit() and int(size) > max_bytes:
            raise ValueError(f"Fichier trop volumineux (plus de {max_bytes // (1024 * 1024)} Mo).")
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"Fichier trop volumineux (plus de {max_bytes // (1024 * 1024)} Mo).")
        return data, (resp.headers.get("Content-Type") or "").lower()
