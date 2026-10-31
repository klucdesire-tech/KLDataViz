# -*- coding: utf-8 -*-
"""Pages composées : la page exécutée sous Node, filtres compris, et l'ancienne page strictement inchangée."""
import json
import os
import re
import shutil
import subprocess
import sys

import pandas as pd
import pytest

from _harness import APP, reset_app_modules

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jeux_reference as J  # noqa: E402

NODE = shutil.which("node")
sans_node = pytest.mark.skipif(NODE is None, reason="Node.js absent")

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
  matchMedia: () => ({ matches: true }), addEventListener() {}, Math, Date, JSON, parent: { postMessage() {} } };
ctx.window = ctx; ctx.self = ctx; ctx.top = ctx;
vm.createContext(ctx);
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
vm.runInContext(scripts[scripts.length - 1], ctx);
const sortie = { page: el.compo ? el.compo.innerHTML : null, row2: el.row2 ? el.row2.style.display : null, kpis: el.kpis ? el.kpis.innerHTML : '' };
const filtres = JSON.parse(process.argv[3] || '{}');
if (Object.keys(filtres).length) {
  for (const k in filtres) vm.runInContext('state[' + JSON.stringify(k) + ']=' + JSON.stringify(filtres[k]), ctx);
  vm.runInContext('render()', ctx);
  sortie.filtre = el.compo ? el.compo.innerHTML : null;
  sortie.kpis_filtre = el.kpis ? el.kpis.innerHTML : '';
}
console.log(JSON.stringify(sortie));
"""


@pytest.fixture
def gen():
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import generate_dashboard  # noqa: PLC0415
    return lambda df, **k: generate_dashboard(df, "", llm=False, dictionnaire=[], **k)


def executer(tmp_path, html, filtres=None):
    (tmp_path / "banc.js").write_text(BANC, encoding="utf-8")
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "banc.js"), str(tmp_path / "page.html"), json.dumps(filtres or {})],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[:900]
    return json.loads(r.stdout)


def test_sans_composition_la_page_ne_change_pas(gen):
    html = gen(J.JEUX["ventes"]())["html"]
    assert "__COMPO" not in html and 'id="compo"' not in html and "cp-c" not in html and "renderCompo(rows)" in html
    assert "function renderCompo" not in html


def test_choix_garde_dans_la_spec(gen):
    assert gen(J.JEUX["ventes"](), composition=True)["spec"]["composition"] is True
    assert "composition" not in gen(J.JEUX["ventes"]())["spec"]


def test_actualisation_garde_sa_page(gen):
    df = J.JEUX["ventes"]()
    spec = gen(df, composition=True)["spec"]
    assert 'id="compo"' in gen(df, spec=spec)["html"]


def test_ancienne_spec_reste_ancienne_page(gen):
    df = J.JEUX["ventes"]()
    assert 'id="compo"' not in gen(df, spec=gen(df)["spec"])["html"]


@sans_node
@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_page_executee_sans_erreur(gen, tmp_path, jeu):
    res = gen(J.JEUX[jeu](), composition=True)
    plan = re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", res["html"], re.S)
    m = json.loads(plan.group(1))["composition"]
    s = executer(tmp_path, res["html"])
    assert s["page"].count('class="cp-c"') == m["cartes"] and s["row2"] == "none"
    assert "undefined" not in s["page"] and "NaN" not in s["page"] and "null" not in s["page"]


@sans_node
def test_somme_affichee_exacte(gen, tmp_path):
    df = pd.DataFrame({"mois": [f"2025-{m:02d}" for m in range(1, 7)] * 2, "zone": ["A"] * 6 + ["B"] * 6, "ventes": [10] * 12})
    assert ">20<" in executer(tmp_path, gen(df, composition=True)["html"])["page"]


@sans_node
def test_chaleur_respecte_la_moyenne(gen, tmp_path):
    """Le défaut de la première version : deux centres à 80 % ne font pas 160 %."""
    lignes = [{"mois": f"2025-{m:02d}", "district": d, "centre": c, "taux_couverture": 80.0}
              for m in range(1, 8) for d in ("A", "B") for c in ("x", "y")]
    page = executer(tmp_path, gen(pd.DataFrame(lignes), composition=True)["html"])["page"]
    assert 'class="cp-cl"' in page and "160" not in page and ">80<" in page


@sans_node
def test_valeur_manquante_jamais_zero(gen, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "ventes": 50 + m} for m in range(1, 8) for z in ("A", "B", "C") if not (z == "B" and m == 3)])
    page = executer(tmp_path, gen(df, composition=True)["html"])["page"]
    assert "B, mars 25 : —" in page


@sans_node
def test_filtre_met_tout_a_jour_sans_carte_vide_muette(gen, tmp_path):
    res = gen(J.JEUX["sante"](), composition=True)
    s = executer(tmp_path, res["html"], {"district": "Sakassou"})
    assert s["filtre"] != s["page"] and "undefined" not in s["filtre"] and "NaN" not in s["filtre"]
    assert s["filtre"].count('class="cp-c"') == s["page"].count('class="cp-c"')
    assert "Choisissez plusieurs" in s["filtre"]


@sans_node
def test_comparaison_sur_les_mois_communs(gen, tmp_path):
    """12 mois en 2024, 6 en 2025 : seuls janvier à juin se comparent."""
    df = pd.DataFrame([{"mois": f"{a}-{m:02d}", "zone": z, "ventes": 100} for a, n in ((2024, 12), (2025, 6)) for m in range(1, n + 1) for z in "ABC"])
    page = executer(tmp_path, gen(df, composition=True)["html"])["page"]
    assert "(6 mois)" in page and "+900" not in page


@sans_node
def test_textes_echappes(gen, tmp_path):
    df = pd.DataFrame({"zone": ["<b>A</b>", "B", "C", "D"], "ventes": [1, 2, 3, 4]})
    page = executer(tmp_path, gen(df, composition=True)["html"])["page"]
    assert "<b>A</b>" not in page and "&lt;b&gt;A" in page


@sans_node
def test_passage_compte_les_groupes_d_une_seule_annee(gen, tmp_path):
    """Un produit lancé en 2025 fait partie du total : sinon la dernière colonne ment."""
    df = pd.DataFrame([{"mois": f"{a}-{m:02d}", "produit": p, "ventes": 100, "objectif_ventes": 110} for a in (2024, 2025) for m in range(1, 13)
                       for p in ("A", "B", "C") + (("Nouveau",) if a == 2025 else ())])
    page = executer(tmp_path, gen(df, composition=True)["html"])["page"]
    assert "Ce qui explique" in page and re.search(r"\+1\s200<", page)


@sans_node
def test_objectif_suit_l_agregation_de_la_mesure(gen, tmp_path):
    """Un taux moyen de 80 pour un objectif de 90 fait 88,9 % : additionner l'objectif donnerait une fraction absurde."""
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "taux_couverture": 80.0, "objectif_couverture": 90.0}
                       for m in range(1, 8) for z in "AB"])
    page = executer(tmp_path, gen(df, composition=True)["html"])["page"]
    assert "88,9 %" in page


@sans_node
def test_valeurs_negatives_restent_visibles(gen, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "resultat": -50 + m * 12, "ventes": 200 + m} for m in range(1, 10) for z in "ABC"])
    page = executer(tmp_path, gen(df, composition=True)["html"])["page"]
    assert "n&#39;a pas pu" not in page and "n'a pas pu" not in page and "NaN" not in page


@sans_node
def test_mesure_toute_negative_sans_echelle_inventee(gen, tmp_path):
    """-50, -40, -10 : l'axe s'arrête à 0 et la meilleure agence garde la barre la plus courte, posée contre le zéro."""
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "agence": a, "resultat": v} for m in range(1, 13) for a, v in (("A", -50), ("B", -40), ("C", -10))])
    page = executer(tmp_path, gen(df, composition=True)["html"])["page"]
    assert "jusqu'à 1." not in page and ">1</text>" not in page
    assert "margin-left:80.0%;width:20.0%" in page and "margin-left:0.0%;width:100.0%" in page


@sans_node
def test_note_sans_double_point(gen, tmp_path):
    df = pd.DataFrame([{"date": f"2025-{m:02d}-{j:02d}", "zone": z, "ventes": 10 + j} for m in range(1, 5) for j in (1, 8, 15, 22) for z in "AB"])
    res = gen(df, composition=True)["html"].replace("window.META=", "window.META_=").replace("var state={},gran=(META.gran||'M')", "var META=window.META_;META.gran='W';var state={},gran='W'")
    page = executer(tmp_path, res)["page"]
    assert "sem." in page and ".." not in re.sub(r"<[^>]+>", " ", page)


@sans_node
def test_objectif_incomplet_est_annonce(gen, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "ventes": 100, "objectif_ventes": None if (z == "C" or (z == "B" and m == 1)) else 120}
                       for m in range(1, 8) for z in "ABC"])
    page = executer(tmp_path, gen(df, composition=True)["html"])["page"]
    assert "8 sans objectif écartées" in page and "Sans objectif : C." in page


@sans_node
def test_carte_en_erreur_n_efface_pas_la_page(gen, tmp_path):
    html = gen(J.JEUX["ventes"](), composition=True)["html"].replace("function cpCourbe(rows,c){", "function cpCourbe(rows,c){throw 1;")
    page = executer(tmp_path, html)["page"]
    assert "pas pu être dessiné" in page and page.count('class="cp-c"') == 8


def test_echec_de_la_composition_garde_la_page_classique(gen, monkeypatch):
    from hermes_da.adapters import composition as compo  # noqa: PLC0415
    monkeypatch.setattr(compo, "composer", lambda *a: 1 / 0)
    res = gen(J.JEUX["ventes"](), composition=True)
    assert 'id="compo"' not in res["html"] and "composition" not in res["spec"]


def test_reserve_aux_administrateurs(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    monkeypatch.delenv("KLDV_COMPOSITION", raising=False)
    assert server._composition_active() is False
    monkeypatch.setenv("KLDV_COMPOSITION", "1")
    assert server._composition_active() is True
    monkeypatch.delenv("KLDV_COMPOSITION")
    monkeypatch.setattr(server.T, "CLOUD", True)
    monkeypatch.setattr(server.T, "current", lambda: type("C", (), {"is_admin": False})())
    assert server._composition_active() is False
    monkeypatch.setattr(server.T, "current", lambda: type("C", (), {"is_admin": True})())
    assert server._composition_active() is True
