# -*- coding: utf-8 -*-
"""Syntaxe JavaScript des pages et du modèle de tableau de bord : une apostrophe non échappée suffit à rendre toute
l'application inutilisable (vu en octobre 2026 sur le bouton « Essayer avec un exemple »)."""
import glob
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extraire_js  # noqa: E402


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js absent : vérification faite par la CI")
def test_scripts_des_pages_valides(tmp_path):
    extraire_js.main(str(tmp_path))
    fichiers = glob.glob(str(tmp_path / "*.js"))
    assert len(fichiers) >= 6
    erreurs = []
    for f in fichiers:
        r = subprocess.run(["node", "--check", f], capture_output=True, text=True)
        if r.returncode:
            lignes = r.stderr.strip().splitlines()
            detail = next((x for x in lignes if "Error" in x), lignes[0] if lignes else r.returncode)
            erreurs.append(f"{os.path.basename(f)} : {detail} ({lignes[0] if lignes else ''})")
    assert not erreurs, erreurs
