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
const sortie = { page: el.compo ? el.compo.innerHTML : null, row2: el.row2 ? el.row2.style.display : null, kpis: el.kpis ? el.kpis.innerHTML : '', gaps: el.gaps ? el.gaps.innerHTML : '' };
const filtres = JSON.parse(process.argv[3] || '{}');
if (Object.keys(filtres).length) {
  for (const k in filtres) vm.runInContext('state[' + JSON.stringify(k) + ']=' + JSON.stringify(filtres[k]), ctx);
  vm.runInContext('render()', ctx);
  sortie.filtre = el.compo ? el.compo.innerHTML : null;
  sortie.kpis_filtre = el.kpis ? el.kpis.innerHTML : '';
  sortie.gaps_filtre = el.gaps ? el.gaps.innerHTML : '';
}
console.log(JSON.stringify(sortie));
"""


@pytest.fixture
def gen(monkeypatch):
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters import composition  # noqa: PLC0415
    from hermes_da.adapters.dashboard import generate_dashboard  # noqa: PLC0415
    monkeypatch.setattr(composition, "CHOIX", "modele")  # le dessin de chaque type, pas le choix (test_composition)
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
def test_peu_de_groupes_barres_jumelees(gen, tmp_path):
    """Cinq régions : chaque nom et chaque valeur se lisent, sans nuage de points perdu dans le vide."""
    regions = (("Nawa", 900, 18000), ("Gôh", 800, 15000), ("San-Pédro", 700, 13500), ("Haut-Sassandra", 650, 12000), ("Indénié", 500, 9000))
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "region": r, "tonnage": t, "valeur": v} for m in range(1, 7) for r, t, v in regions])
    page = re.sub(r"\s", " ", executer(tmp_path, gen(df, composition=True)["html"])["page"])
    assert 'class="cp-ju"' in page and all(f">{r}</span><div><em>" in page for r, _, _ in regions)
    assert page.index(">Nawa</span><div><em>") < page.index(">Indénié</span><div><em>")
    assert "Par région" in page and "Chaque point" not in page


def _jumelles(lignes):
    return pd.DataFrame([{"mois": f"2025-{m:02d}", "region": r, "zone": z, "tonnage": t, "valeur": v} for m in range(1, 7) for r, z, t, v in lignes])


@sans_node
def test_filtre_laissant_deux_groupes_garde_ses_barres(gen, tmp_path):
    df = _jumelles([("Nawa", "N", 900, 18000), ("Gôh", "N", 800, 15000), ("San-Pédro", "S", 700, 13500), ("Haut", "S", 650, 12000), ("Bas", "S", 500, 9000)])
    s = executer(tmp_path, gen(df, composition=True)["html"], {"zone": "N"})
    assert 'class="cp-ju"' in s["page"] and s["filtre"].count('class="cp-jl"') == 2


@sans_node
def test_barres_jumelees_avec_negatifs_reperent_le_zero(gen, tmp_path):
    df = _jumelles([("Nawa", "N", 900, -18000), ("Gôh", "N", 800, 15000), ("San-Pédro", "S", -700, 13500), ("Haut", "S", 650, 12000), ("Bas", "S", 500, -9000)])
    page = executer(tmp_path, gen(df, composition=True)["html"])["page"]
    carte = page[page.index('class="cp-ju"'):]
    assert carte.count('<u style="left:') >= 10


@sans_node
def test_borne_sur_la_grille_sans_pas_de_vide(gen, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "centre": f"C{i:02d}", "zone": "AB"[i % 2], "tonnage": 3000 + i * 66, "valeur": 9000 + i * 310} for m in range(1, 7) for i in range(14)])
    page = re.sub(r"\s", " ", executer(tmp_path, gen(df, composition=True)["html"])["page"])
    carte = page[page.index("Chaque point"):]
    ticks = re.findall(r'y="\d+" text-anchor="(?:start|middle|end)" font-size="12" fill="#52667A">([^<]+)</text>', carte[:carte.index("</svg>")])
    assert ticks and min(int(t.replace(" ", "")) for t in ticks if t.replace(" ", "").isdigit()) >= 18000


@sans_node
def test_graduations_sans_melange_d_unites(gen, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "ventes": 1000 + m * 1500 * (i + 1)} for m in range(1, 9) for i, z in enumerate("ABCD")])
    page = re.sub(r"\s", " ", executer(tmp_path, gen(df, composition=True)["html"])["page"])
    carte = page[page.index('class="cp-cc"'):page.index("</svg>", page.index('class="cp-cc"'))]
    ticks = re.findall(r'text-anchor="end" font-size="12" fill="#52667A">([^<]+)</text>', carte)
    chiffres = [t for t in ticks if re.match(r"^[\d ]+k?$", t) and t != "0"]
    assert len({t.endswith("k") for t in chiffres}) == 1


@sans_node
def test_graduations_sans_melange_k_et_m(gen, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "ventes": 100000 + m * 30000} for m in range(1, 9) for z in "ABCD"])
    page = re.sub(r"\s", " ", executer(tmp_path, gen(df, composition=True)["html"])["page"])
    ticks = re.findall(r'text-anchor="end" font-size="12" fill="#52667A">([^<]+)</text>', page[:page.index("</svg>")])
    assert ticks and not (any(t.endswith("k") for t in ticks) and any(t.endswith("M") for t in ticks))


@sans_node
def test_groupes_tous_egaux_le_disent(gen, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "centre": f"C{i:02d}", "tonnage": 500, "valeur": 9000} for m in range(1, 7) for i in range(14)])
    assert "mêmes valeurs" in executer(tmp_path, gen(df, composition=True)["html"])["page"]


@sans_node
def test_beaucoup_de_groupes_nuage_gradue(gen, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "centre": f"C{i:02d}", "zone": "AB"[i % 2], "tonnage": 500 + i * 13, "valeur": 9000 + i * 310} for m in range(1, 7) for i in range(14)])
    page = re.sub(r"\s", " ", executer(tmp_path, gen(df, composition=True)["html"])["page"])
    assert "Chaque point" in page and "Axes limités aux valeurs du fichier." in page
    carte = page[page.index("Chaque point"):]
    assert len(re.findall(r'font-size="12" fill="#52667A">[^<]+</text>', carte[:carte.index("</svg>")])) >= 6


@sans_node
def test_indicateurs_replies_par_defaut(gen, tmp_path):
    g = executer(tmp_path, gen(J.JEUX["sante"](), composition=True)["html"])["gaps"]
    assert 'aria-expanded="false"' in g and '<div id="cp-i-corps" hidden>' in g and ">Afficher<" in g
    assert "indicateurs ·" in g and "sv-grid" not in g and "sv-sp" not in g and g.count('class="cp-i-l"') >= 3


@sans_node
def test_page_classique_garde_son_panneau(gen, tmp_path):
    g = executer(tmp_path, gen(J.JEUX["sante"]())["html"])["gaps"]
    assert "sv-grid" in g and "cp-i-bt" not in g


def _mensuel(colonne, montee=True):
    return pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "patients": 10, colonne: 100 + (m if montee else 9 - m) * 20} for m in range(1, 9) for z in "ABC"])


def _ligne(g, nom):
    return next(c for c in g.split('class="cp-i-l" role="row"') if nom in c)


@sans_node
@pytest.mark.parametrize("colonne", ["tonnage", "taux_insatisfaction", "taux_non_conformite", "taux_conformite"])
def test_indicateurs_sans_sens_connu_restent_neutres(gen, tmp_path, colonne):
    """Le moteur ne sait pas si cette hausse est bonne : aucun vert ni rouge, surtout pas sur une dégradation."""
    g = executer(tmp_path, gen(_mensuel(colonne), composition=True)["html"])["gaps"]
    assert 'class="cp-i-l" role="row"' in g and "cp-i-mal" not in g and "cp-i-bon" not in g


@sans_node
def test_indicateurs_hausse_defavorable_rouge(gen, tmp_path):
    g = executer(tmp_path, gen(_mensuel("deces"), composition=True)["html"])["gaps"]
    assert "cp-i-mal" in _ligne(g, "eces") and "cp-i-bon" not in g


@sans_node
def test_indicateurs_baisse_favorable_verte(gen, tmp_path):
    g = executer(tmp_path, gen(_mensuel("deces", montee=False), composition=True)["html"])["gaps"]
    assert "cp-i-bon" in _ligne(g, "eces") and "cp-i-mal" not in g


@sans_node
def test_resume_replie_compte_comme_le_tableau(gen, tmp_path):
    """Un écart affiché « −0,8 % » n'est pas compté comme stable."""
    g = re.sub(r"\s", " ", executer(tmp_path, gen(J.JEUX["sante"](), composition=True)["html"])["gaps"])
    affiches = re.findall(r'role="cell">(▲|▼) ', g)
    assert affiches and f"{affiches.count('▲')} en hausse" in g and f"{affiches.count('▼')} en baisse" in g


@sans_node
def test_indicateurs_tableau_accessible(gen, tmp_path):
    g = executer(tmp_path, gen(J.JEUX["sante"](), composition=True)["html"])["gaps"]
    assert 'role="table"' in g and g.count('role="columnheader"') == 4 and g.count('role="cell"') >= 12


@sans_node
def test_indicateurs_gros_fichier_annoncent_qu_ils_ne_suivent_pas_le_filtre(gen, tmp_path, monkeypatch):
    from hermes_da.adapters import cube  # noqa: PLC0415
    monkeypatch.setattr(cube, "SEUIL", 1)
    res = gen(J.JEUX["sante"](), composition=True)
    s = executer(tmp_path, res["html"], {"district": "Sakassou"})
    assert "ne suit pas les filtres" in s["gaps_filtre"] and "ne suit pas les filtres" not in s["gaps"]


def _parts(valeurs, **autres):
    return pd.DataFrame([dict({"mois": f"2025-{m:02d}", "zone": z, "ventes": v}, **autres) for m in range(1, 7) for z, v in valeurs])


def _couleur_legende(page, nom):
    return re.search(r'<li><i style="background:(#[0-9A-F]{6})"></i><span>' + nom + "<", page).group(1)


@sans_node
def test_anneau_garde_ses_couleurs_apres_filtre(gen, tmp_path):
    """Couches bébé est dernier en valeur mais premier par son nom : sa couleur ne suit pas le rang."""
    s = executer(tmp_path, gen(J.JEUX["ventes"](), composition=True)["html"], {"zone": "Sud"})
    assert _couleur_legende(s["page"], "Couches bébé") == _couleur_legende(s["filtre"], "Couches bébé") == "#2A78D6"


def _carte(page, titre):
    debut = page.rindex("<article", 0, page.index(titre))
    return page[debut:page.index("</article>", debut)]


@sans_node
def test_anneau_remplace_par_des_barres_si_les_parts_sont_proches(gen, tmp_path):
    """Le titre annonce une part du total : les barres doivent la montrer aussi."""
    df = J.JEUX["ventes"]()
    ecarte = executer(tmp_path, gen(df, composition=True)["html"])["page"]
    assert 'class="cp-do"' in _carte(ecarte, "Part de chaque produit")
    rang = {p: i for i, p in enumerate(sorted(df["produit"].unique()))}
    df["marge"] = df["chiffre_affaires"] = df["produit"].map(lambda p: 1000 + rang[p] * 5)
    carte = _carte(executer(tmp_path, gen(df, composition=True)["html"])["page"], "Part de chaque produit")
    assert 'class="cp-do"' not in carte and 'class="cp-bt"' in carte and carte.count(" %</b>") == 5 and "du total" in carte


@sans_node
def test_treemap_une_seule_couleur(gen, tmp_path):
    page = executer(tmp_path, gen(J.JEUX["sante"](), composition=True)["html"])["page"]
    carte = page[page.index('class="cp-tm"'):]
    assert len(set(re.findall(r"background:(#[0-9A-Fa-f]{6});color", carte[:carte.index("</article>")]))) == 1


def _luminance(h):
    canaux = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    a, b, c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in canaux]
    return 0.2126 * a + 0.7152 * b + 0.0722 * c


@sans_node
def test_carte_de_chaleur_texte_lisible_partout(gen, tmp_path):
    page = executer(tmp_path, gen(J.JEUX["sante"](), composition=True)["html"])["page"]
    cases = set(re.findall(r'class="cp-cl"[^>]*style="background:(#[0-9A-F]{6});color:(#[0-9A-Fa-f]{3,6})"', page))
    assert len(cases) >= 3
    for fond, encre in cases:
        encre = "#FFFFFF" if encre == "#fff" else encre
        a, b = sorted((_luminance(fond), _luminance(encre)), reverse=True)
        assert (a + 0.05) / (b + 0.05) >= 4.5, (fond, encre)


@sans_node
def test_regroupement_au_dessus_des_cartes(gen, tmp_path):
    page = executer(tmp_path, gen(J.JEUX["sante"](), composition=True)["html"])["page"]
    assert page.startswith('<div class="cp-outils">') and "Regrouper par" in page
    assert '<div class="seg"' not in page[page.index("<article"):]


@sans_node
def test_pas_de_regroupement_sans_temps(gen, tmp_path):
    assert "cp-outils" not in executer(tmp_path, gen(J.JEUX["sans_dates"](), composition=True)["html"])["page"]


def _annuel(ecart=0.0001):
    regions = ("Abidjan", "Bélier", "Gbêkê", "Haut-Sassandra", "Poro", "San-Pédro", "Tonkpi")
    return pd.DataFrame([{"region": r, "annee": a, "naissances": 100 + a - 2015 + i * 5, "deces": 10 + i, "part_population_region_pct": 4.17 + (a - 2015) * ecart * (1 if i % 2 else -1)} for a in range(2015, 2025) for i, r in enumerate(regions)])


@sans_node
def test_donnees_annuelles_sans_note_sur_un_mois(gen, tmp_path):
    page = re.sub(r"\s", " ", executer(tmp_path, gen(_annuel(), composition=True)["html"])["page"])
    assert "(1 mois)" not in page and "Même période" not in page


@sans_node
def test_tres_petit_ecart_jamais_ecrit_zero(gen, tmp_path):
    page = re.sub(r"\s", " ", executer(tmp_path, gen(_annuel(), composition=True)["html"])["page"])
    assert "(−0)" not in page and "(+0)" not in page


@sans_node
def test_pente_distingue_les_valeurs_proches(gen, tmp_path):
    page = re.sub(r"\s", " ", executer(tmp_path, gen(_annuel(), composition=True)["html"])["page"])
    carte = page[page.index("en hausse, plus fort écart"):]
    droites = re.findall(r'font-weight="600">([^<]+)</tspan>', carte[:carte.index("</svg>")])
    assert len(droites) == 7 and len(set(droites)) > 1


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


def test_ouverte_par_defaut_et_retour_arriere(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    monkeypatch.delenv("KLDV_COMPOSITION", raising=False)
    assert server._composition_active() is True      # ouverte à tous par défaut
    monkeypatch.setenv("KLDV_COMPOSITION", "1")
    assert server._composition_active() is True
    monkeypatch.setenv("KLDV_COMPOSITION", "0")      # retour arrière : réservée aux administrateurs
    monkeypatch.setattr(server.T, "CLOUD", True)
    monkeypatch.setattr(server.T, "current", lambda: type("C", (), {"is_admin": False})())
    assert server._composition_active() is False
    monkeypatch.setattr(server.T, "current", lambda: type("C", (), {"is_admin": True})())
    assert server._composition_active() is True


def _partiel(jours):
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import _partial_month  # noqa: PLC0415
    return _partial_month(pd.Series(jours))


@pytest.mark.parametrize("jour", ["01", "15"])
def test_fichier_mensuel_n_a_pas_de_mois_incomplet(jour):
    """Une date par mois : le dernier mois est complet, même daté du 1er."""
    assert _partiel([f"2025-{m:02d}-{jour}" for m in range(1, 13)]) is None


def test_arret_en_cours_de_mois_detecte():
    jours = pd.date_range("2025-01-01", "2025-06-20").strftime("%Y-%m-%d").tolist()
    assert _partiel(jours) == "2025-06"


def test_arret_dans_les_derniers_jours_conserve_le_mois():
    assert _partiel(pd.date_range("2025-01-01", "2025-06-28").strftime("%Y-%m-%d").tolist()) is None


def test_saisie_hebdomadaire_arretee_tot_detectee():
    assert _partiel(pd.date_range("2025-01-03", "2025-06-12", freq="7D").strftime("%Y-%m-%d").tolist()) == "2025-06"


def test_un_seul_mois_sans_comparaison_reste_incomplet():
    assert _partiel([f"2025-06-{j:02d}" for j in range(1, 11)]) == "2025-06"


@sans_node
def test_fichier_date_du_premier_garde_son_dernier_mois_dans_la_page(gen, tmp_path):
    df = pd.DataFrame([{"date": f"2025-{m:02d}-01", "zone": z, "ventes": 100 + m * 10} for m in range(1, 13) for z in "ABC"])
    page = re.sub(r"\s", " ", executer(tmp_path, gen(df, composition=True)["html"])["page"])
    assert "est incomplet" not in page and "déc. 25" in page
