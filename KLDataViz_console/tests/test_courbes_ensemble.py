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
const sortie = { page: el.compo ? el.compo.innerHTML : null, cc: vm.runInContext('JSON.stringify(CP_CC)', ctx),
  lisse: pts.map(p => vm.runInContext('cpLisse(' + JSON.stringify(p) + ')', ctx)) };
const filtres = JSON.parse(process.argv[4] || '{}');
if (Object.keys(filtres).length) {
  for (const k in filtres) vm.runInContext('state[' + JSON.stringify(k) + ']=' + JSON.stringify(filtres[k]), ctx);
  vm.runInContext('render()', ctx);
  sortie.filtre = el.compo ? el.compo.innerHTML : null;
}
sortie.evals = JSON.parse(process.argv[5] || '[]').map(e => vm.runInContext(e, ctx));
console.log(JSON.stringify(sortie));
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


def _par_sexe():
    lignes = []
    for i, p in enumerate(PERIODES):
        for s, centre in enumerate(["Centre A", "Centre B", "Centre C"]):
            for k, sexe in enumerate(["Féminin", "Masculin"]):
                lignes.append({"periode": p, "district": f"District {s}", "site": centre, "sexe": sexe,
                               "Nombre de consultants": 300 + 400 * k + 10 * i + s, "Nombre de clients dépistés": 70 - 40 * k + i,
                               "Nombre de clients positifs": 4 - 2 * k + (i % 2)})
    return pd.DataFrame(lignes)


def test_repartition_par_sexe_en_bandes_verticales_par_indicateur(tmp_path):
    """Deux sexes, trois volumes : une carte « Part de chaque sexe », un groupe de bandes par indicateur (= 100 %)."""
    catalogue, _, dashboard, fiche = _module()
    df = _par_sexe()
    props = catalogue.proposer(fiche.lire_fiche(df), df)
    parts = [p for p in props if p["type"] == "anneau" and p["dimension"] == "sexe"]
    assert parts and all(len(p.get("ensemble") or []) == 2 for p in parts)
    if NODE is None:
        return
    html = dashboard.generate_dashboard(df, "", llm=False, dictionnaire=[], composition=True)["html"]
    meta = json.loads(re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", html, re.S).group(1))
    cartes = [c for ligne in meta["composition"]["lignes"] for c in ligne if c["type"] == "anneau" and c.get("ensemble")]
    assert len(cartes) <= 1  # jamais deux cartes de parts qui montrent les mêmes séries
    if not cartes:
        return
    (tmp_path / "banc.js").write_text(BANC, encoding="utf-8")
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "banc.js"), str(tmp_path / "page.html"), "[]"], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[:900]
    page = json.loads(r.stdout)["page"]
    assert "chaque groupe de bandes = 100 %" in page
    vus = {}
    for nom, sexe, val, pct in re.findall(r"<rect [^>]*><title>([^<·]+) · (Féminin|Masculin) : ([^(<]+) \(([\d,]+) %\)</title>", page):
        vus.setdefault(nom.strip(), {})[sexe] = (int(re.sub(r"\D", "", val)), float(pct.replace(",", ".")))
    assert len(vus) == 3
    colonnes = {"Consultants": "Nombre de consultants", "Clients dépistés": "Nombre de clients dépistés",
                "Clients positifs": "Nombre de clients positifs"}
    for nom, parts_sexe in vus.items():
        attendu = df.groupby("sexe")[colonnes[nom]].sum()
        assert {s: v for s, (v, _) in parts_sexe.items()} == attendu.to_dict(), nom
        assert abs(sum(p for _, p in parts_sexe.values()) - 100) <= 0.15, nom  # chaque groupe fait 100 %


def _jeu_filtres():
    """2 régions, 2 sexes ; « éligibles » déclaré au total seulement (sans sexe), comme dans un rapport réel."""
    lignes = []
    for i, p in enumerate(PERIODES):
        for r, region in enumerate(["Région Est", "Région Ouest"]):
            for s in range(2):
                site = f"Centre {r}{s}"
                for k, sexe in enumerate(["Féminin", "Masculin"]):
                    lignes.append({"periode": p, "region": region, "district": f"District {r}{s}", "site": site, "sexe": sexe,
                                   "Nombre de consultants": 200 + 50 * k + 10 * i + r, "Nombre de clients dépistés": 40 - 10 * k + i})
                lignes.append({"periode": p, "region": region, "district": f"District {r}{s}", "site": site, "sexe": None,
                               "Nombre de clients éligibles": 90 + i + r})
    return pd.DataFrame(lignes)


@sans_node
def test_filtre_qui_vide_une_carte_dit_pourquoi_ou_trace_le_lieu_retenu(tmp_path):
    _, _, dashboard, _ = _module()
    df = _jeu_filtres()
    html = dashboard.generate_dashboard(df, "", llm=False, dictionnaire=[], composition=True)["html"]
    carte = lambda **k: json.dumps(dict({"span": 6, "agregation": "somme"}, **k), ensure_ascii=False)  # noqa: E731
    evals = [
        # comparaison par région, une seule région retenue : l'évolution de cette région
        "CP_DESSINS.comparee(" + carte(type="comparee", mesure="Nombre de consultants", dimension="region") + ",filtered())",
        # parts par sexe, un seul sexe retenu : la carte dit quel filtre retirer
        "CP_DESSINS.anneau(" + carte(type="anneau", mesure="Nombre de consultants", dimension="sexe",
                                     ensemble=["Nombre de clients dépistés"]) + ",filtered())",
    ]
    (tmp_path / "banc.js").write_text(BANC, encoding="utf-8")
    (tmp_path / "page.html").write_text(html, encoding="utf-8")

    def lancer(filtres, exprs):
        r = subprocess.run([NODE, str(tmp_path / "banc.js"), str(tmp_path / "page.html"), "[]", json.dumps(filtres),
                            json.dumps(exprs)], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, r.stderr[:900]
        return json.loads(r.stdout)["evals"]

    region, _ = lancer({"region": "Région Est"}, evals)
    assert "· Région Est" in region and "cp-vide" not in region
    attendu = df[df.region == "Région Est"].groupby("periode", sort=False)["Nombre de consultants"].sum()
    points = {p: int(re.sub(r"\D", "", v)) for p, v in re.findall(r"<title>(FY\d\dQ\d) : ([^<]+)</title>", region)}
    assert points == {p: int(v) for p, v in attendu.items()}  # la courbe de la r\u00e9gion retenue, valeur par valeur
    _, parts = lancer({"sexe": "Féminin"}, evals)
    assert "ne garde qu’une seule valeur" in parts
    # un indicateur sans ventilation par sexe : la carte le dit au lieu d'un « pas assez de données » muet
    vide = lancer({"sexe": "Féminin"}, ["CP_CARTE=" + carte(type="classement", mesure="Nombre de clients éligibles", dimension="district")
                                        + ";CP_DESSINS.classement(CP_CARTE,filtered())"])[0]
    assert "n’est pas ventilé par" in vide and "retirez le filtre" in vide


def _declarants():
    """Consultants : 3 sites chaque trimestre. Positifs : 1 seul site au 1er trimestre, puis 3 (sondes de la relecture).
    Dépistés : 3 sites, sauf au 3e trimestre (1 seul). Sexe : les positifs ne sont jamais renseignés pour les hommes."""
    lignes = []
    for i, p in enumerate(PERIODES):
        for s in range(3):
            for k, sexe in enumerate(["Féminin", "Masculin"]):
                lignes.append({"periode": p, "district": f"District {s}", "site": f"Centre {s}", "sexe": sexe,
                               "Nombre de consultants": 100 + 10 * i + s,
                               "Nombre de clients dépistés": (20 + i) if (i != 2 or s == 0) else None,
                               "Nombre de clients positifs": (2 + i) if (k == 0 and (i > 0 or s == 0)) else None})
    return pd.DataFrame(lignes)


@sans_node
def test_chaque_serie_controlee_sur_ses_propres_declarations(tmp_path):
    _, _, dashboard, _ = _module()
    df = _declarants()
    html = dashboard.generate_dashboard(df, "", llm=False, dictionnaire=[], composition=True)["html"]
    carte = lambda **k: json.dumps(dict({"span": 8, "agregation": "somme"}, **k), ensure_ascii=False)  # noqa: E731
    courbe = carte(type="courbe", mesure="Nombre de consultants", ensemble=["Nombre de clients dépistés", "Nombre de clients positifs"])
    parts = carte(type="anneau", mesure="Nombre de consultants", dimension="sexe", ensemble=["Nombre de clients dépistés", "Nombre de clients positifs"])
    (tmp_path / "banc.js").write_text(BANC, encoding="utf-8")
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    exprs = ["CP_DESSINS.courbe(" + courbe + ",filtered())", "CP_DESSINS.anneau(" + parts + ",filtered())",
             "JSON.stringify(cpIndices(filtered()," + courbe + "))", "cpMessage(" + courbe + ",filtered())"]
    r = subprocess.run([NODE, str(tmp_path / "banc.js"), str(tmp_path / "page.html"), "[]", "{}", json.dumps(exprs)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[:900]
    dessin, bandes, indices, titre = json.loads(r.stdout)["evals"]
    Z = json.loads(indices)
    noms = [x["d"] for x in Z["S"]]
    # positifs : 1 déclaration sur 3 au trimestre de base → jamais tracés en « hausse de 275 % »
    assert "Nombre de clients positifs" not in noms and "Non tracé, car moins déclaré" in dessin
    # dépistés : le 3e trimestre (1 site sur 3) n'est pas tracé, et la note le dit
    dep = next(x for x in Z["S"] if x["d"] == "Nombre de clients dépistés")
    assert dep["vals"][2] is None and "Points non tracés" in dessin and "FY26Q1" in dessin
    # parts par sexe : les positifs non renseignés pour les hommes ne deviennent pas « Féminin 100 % » en silence
    assert "Non renseigné" in bandes and "Masculin" in bandes.split("Non renseigné")[1]
    # titre : la comparaison, pas la seule mesure de tête
    assert titre.startswith("En ") and "base 100 en" in titre
