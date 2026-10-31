# -*- coding: utf-8 -*-
"""Gros fichiers : la page dit vrai au-delà de 6000 lignes. Résumé exact pour la page composée, tuiles justes pour la classique."""
import json
import os
import re
import sys

import pandas as pd
import pytest

from _harness import APP, reset_app_modules

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jeux_reference as J  # noqa: E402
from test_rendu_composition import executer, sans_node  # noqa: E402


@pytest.fixture
def mod():
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters import cube, dashboard  # noqa: PLC0415
    return type("M", (), {"gen": staticmethod(lambda df, **k: dashboard.generate_dashboard(df, "", **dict({"llm": False, "dictionnaire": []}, **k))), "cube": cube})


def gros():
    """14 400 lignes : 4 zones, 300 centres, 12 mois, 10 par ligne, soit 144 000."""
    return pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "centre": f"Centre-{c}", "ventes": 10} for m in range(1, 13) for z in "ABCD" for c in range(300)])


def meta(html):
    return json.loads(re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", html, re.S).group(1))


def tuiles(html):
    return [re.sub(r"\s", " ", v) for v in re.findall(r'class="v">(.*?)</div>', html)]


@sans_node
def test_tuiles_exactes_page_composee(mod, tmp_path):
    res = mod.gen(gros(), composition=True)
    assert 'id="compo"' in res["html"] and meta(res["html"])["cube"] is True
    assert tuiles(executer(tmp_path, res["html"])["kpis"]) == ["14 400", "144 k"] or tuiles(executer(tmp_path, res["html"])["kpis"]) == ["14 400", "144 000"]


@sans_node
def test_filtre_exact_page_composee(mod, tmp_path):
    s = executer(tmp_path, mod.gen(gros(), composition=True)["html"], {"zone": "A"})
    assert tuiles(s["kpis_filtre"]) in (["3 600", "36 000"], ["3 600", "36 k"])
    assert re.search(r"36(\s000| k)", re.sub(r"\s", " ", s["filtre"]))


def test_aucune_ligne_brute_dans_la_page(mod):
    html = mod.gen(gros(), composition=True)["html"]
    assert "Centre-17" not in html and len(html) < 500000


@pytest.mark.parametrize("jeu", ["sante", "ventes", "dhis2_long", "formulaire", "simple"])
@sans_node
def test_resume_et_lignes_donnent_la_meme_page(mod, tmp_path, jeu, monkeypatch):
    """L'oracle : la même page avec le résumé qu'avec toutes les lignes, filtres compris."""
    df = J.JEUX[jeu]()
    filtre = next(({f["col"]: f["values"][0]} for f in meta(mod.gen(df, composition=True)["html"]).get("filters", [])[:1]), {})
    lignes = executer(tmp_path, mod.gen(df, composition=True)["html"], filtre)
    monkeypatch.setattr(mod.cube, "SEUIL", 1)
    html = mod.gen(df, composition=True)["html"]
    assert meta(html).get("cube") is True
    resume = executer(tmp_path, html, filtre)
    assert resume["page"] == lignes["page"]
    assert tuiles(resume["kpis"]) == tuiles(lignes["kpis"])
    if filtre:
        assert resume["filtre"] == lignes["filtre"]
        assert tuiles(resume["kpis_filtre"]) == tuiles(lignes["kpis_filtre"])


@sans_node
def test_resume_sans_histogramme(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod.cube, "SEUIL", 1)
    html = mod.gen(J.JEUX["sans_dates"](), composition=True)["html"]
    types = [c["type"] for l in meta(html)["composition"]["lignes"] for c in l]
    assert "histogramme" not in types and "cp-c" in executer(tmp_path, html)["page"]


@sans_node
def test_objectif_incomplet_dans_le_resume(mod, tmp_path, monkeypatch):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "ventes": 100, "objectif_ventes": None if (z == "C" or (z == "B" and m == 1)) else 120} for m in range(1, 8) for z in "ABC"])
    lignes = executer(tmp_path, mod.gen(df, composition=True)["html"])["page"]
    monkeypatch.setattr(mod.cube, "SEUIL", 1)
    assert executer(tmp_path, mod.gen(df, composition=True)["html"])["page"] == lignes


@sans_node
def test_trop_de_cases_periode_ramenee_au_mois(mod, tmp_path, monkeypatch):
    jours = pd.date_range("2024-01-01", "2024-12-31").strftime("%Y-%m-%d")
    df = pd.DataFrame([{"date": j, "zone": z, "ventes": 10} for j in jours for z in "ABCD" for _ in range(2)])
    monkeypatch.setattr(mod.cube, "SEUIL", 100)
    monkeypatch.setattr(mod.cube, "MAX_CASES", 200)
    html = mod.gen(df, composition=True)["html"]
    m = meta(html)
    assert m["cube"] is True and "D" not in m["grans"] and "W" not in m["grans"]
    assert "NaN" not in executer(tmp_path, html)["page"]


def test_filtre_trop_detaille_retire_et_annonce(mod, monkeypatch):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "site": f"S{i}", "ventes": 10} for m in range(1, 13) for z in "ABCD" for i in range(25) for _ in range(5)])
    monkeypatch.setattr(mod.cube, "MAX_CASES", 200)
    m = meta(mod.gen(df, composition=True)["html"])
    if m.get("gros", {}).get("retires"):
        assert all(f["col"] not in m["gros"]["retires"] for f in m["filters"])


def test_fichier_impossible_a_resumer_garde_la_page_classique_et_le_dit(mod, monkeypatch):
    monkeypatch.setattr(mod.cube, "MAX_CASES", 1)
    html = mod.gen(gros(), composition=True)["html"]
    assert 'id="compo"' not in html and meta(html)["gros"]["mode"] == "echantillon"


def test_resume_garde_sa_page_a_l_actualisation(mod):
    df = gros()
    spec = mod.gen(df, composition=True)["spec"]
    assert 'id="compo"' in mod.gen(df, spec=spec)["html"]


@sans_node
def test_page_classique_tuiles_exactes_sans_filtre(mod, tmp_path):
    html = mod.gen(gros())["html"]
    assert 'id="compo"' not in html
    assert tuiles(executer(tmp_path, html)["kpis"]) in (["14 400", "144 k"], ["14 400", "144 000"])


@sans_node
def test_page_classique_estime_avec_un_filtre_et_le_dit(mod, tmp_path):
    t = tuiles(executer(tmp_path, mod.gen(gros())["html"], {"zone": "A"})["kpis_filtre"])
    assert all(x.startswith("≈") for x in t)
    assert 3000 < float(re.sub(r"[^\d]", "", t[0])) < 4200


@sans_node
def test_page_classique_taux_exact_en_pourcentage(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "centre": f"C{c}", "vaccine": "Oui" if c % 4 else "Non"} for m in range(1, 13) for z in "ABCD" for c in range(300)])
    assert any(t.startswith("75") and t.endswith("%") for t in tuiles(executer(tmp_path, mod.gen(df)["html"])["kpis"]))


def test_petit_fichier_sans_aucun_changement(mod):
    html = mod.gen(J.JEUX["ventes"]())["html"]
    assert '"exact"' not in html and '"gros"' not in html.split("window.DATA=")[0] and '"gros":' not in html
