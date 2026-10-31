# -*- coding: utf-8 -*-
"""Filet de sécurité de la refonte du tableau de bord : six formes de fichiers fictifs et le résultat actuel, figé.

`references_tableau.json` garde les indicateurs et graphiques produits sans IA. Un écart fait échouer le test : soit
c'est une régression, soit le changement est voulu et se régénère avec KLDV_MAJ_REFERENCE=1, à relire dans la PR."""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from _harness import APP, reset_app_modules

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jeux_reference as J  # noqa: E402

REFERENCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "references_tableau.json")


def _tableau(jeu):
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import generate_dashboard  # noqa: PLC0415
    return generate_dashboard(J.JEUX[jeu](), "", llm=False, dictionnaire=[])


def _meta(html):
    m = re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", html, re.S)
    assert m, "META introuvable dans la page"
    return json.loads(m.group(1))


def _empreinte(res):
    meta = _meta(res["html"])
    return {"kpis": [{k: x.get(k) for k in ("nom", "agregation", "colonne", "dimension", "valeur")} for x in res["kpis"]],
            "graphiques": [[c.get("colonne"), c.get("type")] for c in meta.get("charts", [])],
            "mesure": (meta.get("measure") or {}).get("colonne"), "periode": meta.get("period"), "pile": meta.get("stack"),
            "filtres": [f.get("col") for f in meta.get("filters", [])], "agrege": bool(meta.get("agrege"))}


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_deterministe_et_conforme(jeu):
    reset_app_modules()
    a, b = J.JEUX[jeu](), J.JEUX[jeu]()
    assert a.equals(b)
    assert jeu in J.ATTENDU and len(a) > 0


def test_aucune_donnee_reelle():
    """Seules des valeurs inventées ici : pas de nom de patient, de téléphone ni d'identifiant national."""
    reset_app_modules()
    for jeu, f in J.JEUX.items():
        cols = " ".join(map(str, f().columns)).lower()
        assert not re.search(r"patient|nom_|prenom|telephone|contact|cni", cols), jeu


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_page_generee(jeu):
    res = _tableau(jeu)
    assert res["ia"] is False and "<html" in res["html"].lower()
    assert "__DATA__" not in res["html"] and "__META__" not in res["html"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js absent : vérification faite par la CI")
@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_javascript_valide(jeu, tmp_path):
    scripts = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", _tableau(jeu)["html"], re.S)
    f = tmp_path / "page.js"
    f.write_text("\n;\n".join(scripts), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:400]


@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_resultat_inchange(jeu):
    actuel = json.loads(json.dumps(_empreinte(_tableau(jeu)), ensure_ascii=False, default=str))
    if os.environ.get("KLDV_MAJ_REFERENCE"):
        base = json.load(open(REFERENCE, encoding="utf-8")) if os.path.exists(REFERENCE) else {}
        base[jeu] = actuel
        with open(REFERENCE, "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
    with open(REFERENCE, encoding="utf-8") as f:
        assert actuel == json.load(f)[jeu]
