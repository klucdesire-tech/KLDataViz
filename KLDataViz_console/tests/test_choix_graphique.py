# -*- coding: utf-8 -*-
"""Menu « Type de graphique » des cartes (données fictives) : seuls les graphiques que le catalogue permet pour les
mêmes données, et le choix redessine la carte sans changer sa largeur."""
import json
import os
import re
import subprocess
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_courbes_ensemble import BANC, NODE, _module, sans_node  # noqa: E402


def _donnees():
    lignes = []
    for i, p in enumerate(["FY25Q3", "FY25Q4", "FY26Q1", "FY26Q2", "FY26Q3", "FY26Q4"]):
        for d, district in enumerate(["District A", "District B", "District C", "District D"]):
            lignes.append({"periode": p, "district": district, "Nombre de consultants": 100 + 15 * i + 30 * d,
                           "Taux de positivité": 2.0 + d / 2 + i / 10})
    return pd.DataFrame(lignes)


def _meta(html):
    return json.loads(re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", html, re.S).group(1))


def test_un_taux_ne_propose_ni_anneau_ni_mosaique():
    """Un taux ne se partage pas en parts d'un total : jamais proposé en anneau ni en mosaïque."""
    _, _, dashboard, _ = _module()
    meta = _meta(dashboard.generate_dashboard(_donnees(), "", llm=False, dictionnaire=[], composition=True)["html"])
    cartes = [c for ligne in meta["composition"]["lignes"] for c in ligne]
    for c in cartes:
        types = [v["type"] for v in c.get("variantes") or []]
        if c.get("mesure") == "Taux de positivité":
            assert not {"anneau", "treemap", "passage"} & set(types), c
        for v in c.get("variantes") or []:  # même mesure, même découpage, une formule de titre
            assert v["mesure"] == c["mesure"] and v.get("dimension") == c.get("dimension") and v.get("formule")


@sans_node
def test_menu_change_le_graphique_et_garde_la_largeur(tmp_path):
    _, _, dashboard, _ = _module()
    html = dashboard.generate_dashboard(_donnees(), "", llm=False, dictionnaire=[], composition=True)["html"]
    meta = _meta(html)
    cartes = [c for ligne in meta["composition"]["lignes"] for c in ligne]
    i, c = next((i, c) for i, c in enumerate(cartes) if c.get("variantes"))
    j, autre = next((j, v) for j, v in enumerate(c["variantes"]) if v["type"] != c["type"])
    (tmp_path / "banc.js").write_text(BANC, encoding="utf-8")
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    exprs = ["(renderCompo(filtered()),document.getElementById('compo').innerHTML)",
             f"(cpChoisir({i},{j},false),renderCompo(filtered()),document.getElementById('compo').innerHTML)",
             "JSON.stringify(cpCartes().map(function(P){var c=META.composition.lignes[P.l][P.k];return [c.type,c.span]}))"]
    r = subprocess.run([NODE, str(tmp_path / "banc.js"), str(tmp_path / "page.html"), "[]", "{}", json.dumps(exprs)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[:900]
    avant, apres, etat = json.loads(r.stdout)["evals"]
    # le menu de la carte liste exactement ses graphiques permis, le sien sélectionné
    menu = re.search(r'<select class="cp-type" data-i="%d"[^>]*>(.*?)</select>' % i, avant).group(1)
    assert len(re.findall(r"<option ", menu)) == len(c["variantes"]) and 'selected>' in menu
    # après le choix : la carte a le nouveau type, la même largeur, et la page se dessine sans erreur
    assert json.loads(etat)[i] == [autre["type"], c["span"]]
    assert "undefined" not in apres and "NaN" not in apres and "pas pu être dessiné" not in apres
    menu2 = re.search(r'<select class="cp-type" data-i="%d"[^>]*>(.*?)</select>' % i, apres).group(1)
    assert re.search(r'<option value="%d" selected>' % j, menu2)


@sans_node
def test_choix_garde_par_la_console_et_relu_par_type(tmp_path):
    """Dans la console, le tableau est un cadre isolé sans stockage : le choix part vers la console (type, pas index)
    et revient à l'ouverture ; un type inconnu est ignoré."""
    _, _, dashboard, _ = _module()
    html = dashboard.generate_dashboard(_donnees(), "", llm=False, dictionnaire=[], composition=True)["html"]
    cartes = [c for ligne in _meta(html)["composition"]["lignes"] for c in ligne]
    i, c = next((i, c) for i, c in enumerate(cartes) if c.get("variantes"))
    j, autre = next((j, v) for j, v in enumerate(c["variantes"]) if v["type"] != c["type"])
    (tmp_path / "banc.js").write_text(BANC, encoding="utf-8")
    (tmp_path / "page.html").write_text(html, encoding="utf-8")
    exprs = [
        # envoi vers la console au choix de l'utilisateur
        f"(function(){{var m=[];parent.postMessage=function(x){{m.push(x)}};cpChoisir({i},{j},true);return JSON.stringify(m)}})()",
        # relecture : la console renvoie {clé: type} ; on repart de la carte d'origine
        f"(function(){{cpChoisir({i},0,false);CP_CHOIX_EXT={{}};CP_CHOIX_EXT[cpCleChoix(cpCartes().length&&META.composition.lignes[cpCartes()[{i}].l][cpCartes()[{i}].k],{i})]={json.dumps(autre['type'])};"
        f"CP_CHOIX_LUS=false;cpLireChoix();var P=cpCartes()[{i}];return META.composition.lignes[P.l][P.k].type}})()",
        # un type inconnu de la carte : rien ne change
        f"(function(){{cpChoisir({i},0,false);var P=cpCartes()[{i}],c=META.composition.lignes[P.l][P.k];CP_CHOIX_EXT={{}};CP_CHOIX_EXT[cpCleChoix(c,{i})]='inconnu';"
        f"CP_CHOIX_LUS=false;cpLireChoix();return META.composition.lignes[P.l][P.k].type}})()",
    ]
    r = subprocess.run([NODE, str(tmp_path / "banc.js"), str(tmp_path / "page.html"), "[]", "{}", json.dumps(exprs)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[:900]
    envoyes, relu, inconnu = json.loads(r.stdout)["evals"]
    msg = json.loads(envoyes)
    assert msg and msg[0]["kldvChoixGraphique"]["type"] == autre["type"] and "srcdoc" not in msg[0]["kldvChoixGraphique"]["cle"]
    assert relu == autre["type"] and inconnu == c["type"]


def test_colonnes_des_variantes_nommees_et_resume_sans_doublon():
    reset = _module()
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "HERMES_DA"))
    from hermes_da.adapters import cube, titres  # noqa: PLC0415
    carte = {"type": "classement", "mesure": "a", "dimension": "d"}
    carte["variantes"] = [dict(carte), {"type": "bulles", "mesure": "a", "mesure_y": "b", "dimension": "d"}]
    plan = {"lignes": [[carte]]}
    assert "b" in titres._colonnes(plan)  # la mesure en ordonnée du nuage de points proposé
    assert [c["type"] for c in cube._cartes(plan)] == ["classement", "bulles"]  # la carte une seule fois
    del reset
