# -*- coding: utf-8 -*-
"""Courbe d'évolution de plusieurs volumes sur un même graphique (base 100), lissée sans pic inventé. Données fictives.

Consultants (≈ 1 000), dépistés (≈ 100) et positifs (≈ 5) n'ont pas le même ordre de grandeur : sur un même axe,
les positifs resteraient collés à zéro. Chaque courbe est rapportée à sa valeur du premier trimestre bien couvert."""
import json
import os
import re
import shutil
import subprocess
import sys

import pandas as pd
import pytest

from _harness import APP, reset_app_modules

NODE = shutil.which("node")
sans_node = pytest.mark.skipif(NODE is None, reason="Node.js absent")
PERIODES = ["FY25Q3", "FY25Q4", "FY26Q1", "FY26Q2"]

BANC = r"""
const vm = require('vm'), fs = require('fs');
const el = {};
function noeud(id) {
  if (el[id]) return el[id];
  const base = { id, style: {}, innerHTML: '', textContent: '', hidden: false, value: '', dataset: {}, children: [], classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    querySelectorAll() { return []; }, querySelector() { return noeud('q' + Math.random()); }, getBoundingClientRect() { return { height: 0, width: 0 }; } };
  return el[id] = new Proxy(base, { get: (t, k) => k in t ? t[k] : typeof k === 'string' ? () => undefined : undefined });
}
const ctx = { console, document: { getElementById: noeud, createElement: () => noeud('x' + Math.random()), write() {}, body: noeud('body'), querySelectorAll: () => [] },
  matchMedia: () => ({ matches: true }), addEventListener() {}, Math, Date, JSON, parent: { postMessage() {} }, innerWidth: 1400 };
ctx.window = ctx; ctx.self = ctx; ctx.top = ctx;
vm.createContext(ctx);
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
vm.runInContext(scripts[scripts.length - 1], ctx);
const pts = JSON.parse(process.argv[3]);
console.log(JSON.stringify({ page: el.compo ? el.compo.innerHTML : null, cc: vm.runInContext('JSON.stringify(CP_CC)', ctx),
  lisse: pts.map(p => vm.runInContext('cpLisse(' + JSON.stringify(p) + ')', ctx)) }));
"""


def _module():
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters import catalogue, composition, dashboard, fiche  # noqa: PLC0415
    return catalogue, composition, dashboard, fiche


def _donnees():
    """3 centres ; au 1er trimestre, un seul a déclaré (trimestre mal couvert : jamais la base 100)."""
    lignes = []
    for i, p in enumerate(PERIODES):
        for s, centre in enumerate(["Centre A", "Centre B", "Centre C"]):
            if i == 0 and s > 0:
                continue
            lignes.append({"periode": p, "district": f"District {s}", "site": centre,
                           "Nombre de consultants": 1000 + 100 * i + 10 * s, "Nombre de clients dépistés": 100 + 20 * i + s,
                           "Nombre de clients positifs": 5 + (i % 2) + s})
    return pd.DataFrame(lignes)


def test_la_courbe_d_un_volume_emmene_les_autres_volumes():
    catalogue, _, _, fiche = _module()
    df = _donnees()
    props = catalogue.proposer(fiche.lire_fiche(df), df)
    courbes = {p["mesure"]: p.get("ensemble") for p in props if p["type"] == "courbe"}
    assert set(courbes["Nombre de clients dépistés"]) == {"Nombre de consultants", "Nombre de clients positifs"}
    assert all(len(v) <= catalogue.MAX_ENSEMBLE - 1 for v in courbes.values())


@sans_node
def test_page_base_100_au_premier_trimestre_bien_couvert(tmp_path):
    _, composition, dashboard, _ = _module()
    df = _donnees()
    html = dashboard.generate_dashboard(df, "", llm=False, dictionnaire=[], composition=True)["html"]
    meta = json.loads(re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", html, re.S).group(1))
    courbe = next(c for ligne in meta["composition"]["lignes"] for c in ligne if c["type"] == "courbe")
    assert courbe.get("ensemble")
    (tmp_path / "banc.js").write_text(BANC, encoding="utf-8")
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    points = [[[0, 10], [10, 50], [20, 45], [30, 90], [40, 90], [50, 0]], [[0, 5], [10, 5], [20, 80]]]
    r = subprocess.run([NODE, str(tmp_path / "banc.js"), str(tmp_path / "page.html"), json.dumps(points)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[:900]
    s = json.loads(r.stdout)
    assert "Base 100 en FY25Q4" in s["page"]
    # le trimestre mal couvert (1 centre sur 3) n'est pas tracé : son écart viendrait du manque de déclarations
    assert "Non tracé, car moins bien couvert" in s["page"] and "FY25Q3." in s["page"]
    assert "undefined" not in s["page"] and "NaN" not in s["page"]
    cc = json.loads(s["cc"])
    serie = next(x for x in cc if x["P"][0] == "FY25Q4")
    assert serie["P"] == PERIODES[1:]
    attendu = df[df.periode != "FY25Q3"].groupby("periode", sort=False)
    for x in serie["S"]:
        col = next(c for c in df.columns if c.startswith("Nombre") and c.lower().endswith(x["nom"].lower().split()[-1]))
        reel = attendu[col].sum().tolist()
        assert x["reel"] == reel, (x["nom"], x["reel"], reel)  # vraies valeurs au survol et à droite
        assert [round(v, 6) for v in x["vals"]] == [round(100 * v / reel[0], 6) for v in reel]  # base = FY25Q4
    # lissage : chaque segment reste entre ses deux points (pas de pic ni de creux inventé)
    for pts, chemin in zip(points, s["lisse"]):
        segments = re.findall(r"C([-\d. ]+)", chemin)
        assert len(segments) == len(pts) - 1
        for (a, b), seg in zip(zip(pts, pts[1:]), segments):
            v = [float(t) for t in seg.split()]
            for y in (v[1], v[3], v[5]):
                assert min(a[1], b[1]) - 1e-6 <= y <= max(a[1], b[1]) + 1e-6, (a, b, seg)


def test_une_seule_courbe_d_ensemble_dans_la_page():
    """Chaque volume emmène les autres : la page ne doit pas montrer deux fois les mêmes séries."""
    _, composition, dashboard, _ = _module()
    html = dashboard.generate_dashboard(_donnees(), "", llm=False, dictionnaire=[], composition=True)["html"]
    meta = json.loads(re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", html, re.S).group(1))
    courbes = [c for ligne in meta["composition"]["lignes"] for c in ligne if c["type"] == "courbe"]
    series = [{c["mesure"], *(c.get("ensemble") or [])} for c in courbes]
    for i, a in enumerate(series):
        for b in series[i + 1:]:
            assert not (a & b), courbes
