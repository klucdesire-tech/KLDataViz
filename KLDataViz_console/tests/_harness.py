# -*- coding: utf-8 -*-
"""Démarre server.Handler sur un port libre, dans le processus de test."""
import json
import os
import socketserver
import sys
import threading
import urllib.error
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)


def reset_app_modules():
    """Oublie les modules de la console déjà chargés : le mode (local/cloud) est lu à l'import,
    et test_local.py / test_cloud.py tournent dans le même processus pytest."""
    for name, mod in list(sys.modules.items()):
        f = os.path.abspath(getattr(mod, "__file__", None) or "")
        if f.startswith(APP + os.sep) and not any(os.sep + d + os.sep in f for d in (".venv", "tests")):
            del sys.modules[name]


def start(server_module):
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), server_module.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def call(base, path, body=None, token=None, raw=False):
    req = urllib.request.Request(base + path, method="GET" if body is None else "POST",
                                 data=None if body is None else json.dumps(body).encode())
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data or b"null"))
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data)
        except Exception:
            return e.code, data
