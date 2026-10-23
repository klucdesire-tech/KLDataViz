# -*- coding: utf-8 -*-
"""Extrait les scripts intégrés des pages HTML (et du modèle de tableau de bord) dans des fichiers .js, pour que
l'intégration continue vérifie leur syntaxe avec « node --check » : une seule faute (apostrophe non échappée…)
rendrait toute l'application inutilisable, sans qu'aucun test Python ne le voie.

Usage : python tests/extraire_js.py <dossier_sortie>   (puis : for f in <dossier>/*.js; do node --check "$f"; done)
"""
import os
import re
import sys

ICI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ["index.html", "index_classic.html", "login.html", "admin.html", "landing.html"]


def scripts(html):
    return [m.group(1) for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S | re.I)]


def main(out):
    os.makedirs(out, exist_ok=True)
    n = 0
    for page in PAGES:
        p = os.path.join(ICI, page)
        if not os.path.exists(p):
            continue
        for i, js in enumerate(scripts(open(p, encoding="utf-8").read())):
            open(os.path.join(out, f"{page}.{i}.js"), "w", encoding="utf-8").write(js)
            n += 1
    sys.path.insert(0, os.path.join(ICI, "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import TEMPLATE  # modèle du tableau de bord (marqueurs remplacés)
    tpl = TEMPLATE
    for k in ("__DATA__", "__KPIDEFS__", "__META__", "__FILTERS__", "__FILTERCOLS__"):
        tpl = tpl.replace(k, "{}" if k == "__META__" else "[]")
    tpl = tpl.replace("__CHARTJS__", "")
    for i, js in enumerate(scripts(tpl)):
        open(os.path.join(out, f"dashboard.{i}.js"), "w", encoding="utf-8").write(js)
        n += 1
    print(f"{n} script(s) extrait(s) dans {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "js_extraits")
