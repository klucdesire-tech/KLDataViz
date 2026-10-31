# -*- coding: utf-8 -*-
"""Titres-messages : les chiffres viennent du moteur et suivent les filtres ; Adifa’ ne fournit que la phrase."""
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
    from hermes_da.adapters import composition, dashboard, fiche, titres  # noqa: PLC0415
    return type("M", (), {"gen": staticmethod(lambda df, **k: dashboard.generate_dashboard(df, "", **dict({"llm": False, "dictionnaire": [], "composition": True}, **k))),
                          "T": titres, "C": composition, "F": fiche})


def titres_de(page):
    return [re.sub(r"\s", " ", t).replace("&#39;", "'") for t in re.findall(r"<h2>(.*?)</h2>", page)]


def ia(gabarits=None, kpis=None, capture=None):
    """Une Adifa’ factice : répond au choix des KPI, puis aux titres."""
    def appel(messages, n):
        if capture is not None:
            capture.append(messages)
        if "reformules" in messages[0]["content"]:
            return json.dumps({"titres": gabarits or {}}, ensure_ascii=False)
        return json.dumps({"kpis": kpis or [{"nom": "x", "agregation": "somme", "colonne": "marge"}], "graphiques": []})
    return appel


@pytest.mark.parametrize("texte", [
    "{premier} réalise le plus de ventes avec {valeur}, devant {second} ({valeur2})",
    "Chez {premier}, {valeur} de chiffre d'affaires, devant {second} ({valeur2})",
])
def test_gabarit_accepte(mod, texte):
    assert mod.T.valider(texte, {"type": "classement", "mesure": "ventes"}) == texte


@pytest.mark.parametrize("texte,raison", [
    ("{premier} vend {valeur}, soit 40 % du total, devant {second} ({valeur2})", "un chiffre écrit en dur"),
    ("{premier} progresse avec {valeur}, devant {second} ({valeur2})", "un sens écrit en dur"),
    ("{premier} domine nettement avec {valeur}, devant {second} ({valeur2})", "un jugement"),
    ("{premier} n'est pas en tête : {valeur}, devant {second} ({valeur2})", "une négation"),
    ("{premier} arrive en tête avec {valeur}", "une variable de la formule perdue"),
    ("{premier} et {inconnue} : {valeur}, devant {second} ({valeur2})", "une variable inconnue"),
    ("<b>{premier}</b> : {valeur}, devant {second} ({valeur2})", "du code"),
    ("{premier} : {valeur} { devant {second} ({valeur2})", "une accolade seule"),
    (None, "pas un texte"),
])
def test_gabarit_refuse(mod, texte, raison):
    assert mod.T.valider(texte, {"type": "classement", "mesure": "ventes"}) is None, raison


def test_chiffre_d_un_nom_de_colonne_admis(mod):
    assert mod.T.valider("{premier} réalise {valeur} de CPN1, devant {second} ({valeur2})", {"type": "classement", "mesure": "cpn1"})


def test_seuls_les_noms_de_colonnes_partent_vers_l_ia(mod):
    capture, df = [], J.JEUX["ventes"]()
    mod.gen(df, llm=ia(capture=capture))
    envoi = next(m[1]["content"] for m in capture if "reformules" in m[0]["content"])
    assert "Couches bébé" not in envoi and "Nord" not in envoi
    assert not any(str(v) in envoi for v in df["chiffre_affaires"].head(30))


def test_adifa_recoit_la_formule_du_moteur(mod):
    capture = []
    mod.gen(J.JEUX["ventes"](), llm=ia(capture=capture))
    envoi = json.loads(next(m[1]["content"] for m in capture if "reformules" in m[0]["content"]))
    assert all("{" in c["formule"] and c["variables"] for c in envoi)
    assert any("marge" in c["formule"] and "{mesure}" not in c["formule"] for c in envoi if c["graphique"] == "courbe")


def test_objectif_sans_groupe_a_sa_propre_formule(mod):
    assert mod.T.variables({"type": "cible"}) == {"taux"}
    assert mod.T.variables({"type": "cible", "dimension": "zone"}) == {"bilan", "taux"}


def test_ia_en_panne_le_moteur_formule_seul(mod):
    def appel(messages, n):
        if "reformules" in messages[0]["content"]:
            raise RuntimeError("panne")
        return json.dumps({"kpis": [{"nom": "x", "agregation": "somme", "colonne": "marge"}], "graphiques": []})
    res = mod.gen(J.JEUX["ventes"](), llm=appel)
    assert 'id="compo"' in res["html"] and res["spec"]["titres"] == {}


def test_sans_ia_aucun_appel_ni_titre_ia(mod):
    res = mod.gen(J.JEUX["ventes"]())
    assert res["spec"]["titres"] == {} and '"titre_ia"' not in res["html"]


def test_vedette_choisie_par_adifa(mod):
    df = J.JEUX["sante"]()
    fiche = mod.F.lire_fiche(df)
    assert mod.C.composer(fiche, df)["mesure"] == "consultations"
    assert mod.C.composer(fiche, df, vedettes=["inconnue", "cpn4"])["mesure"] == "cpn4"
    plan = mod.C.composer(fiche, df, vedettes=["cpn4"])
    assert plan["lignes"][0][0]["mesure"] == "cpn4"


def test_objectif_prime_sur_le_choix_d_adifa(mod):
    df = J.JEUX["ventes"]()
    assert mod.C.composer(mod.F.lire_fiche(df), df, vedettes=["marge"])["mesure"] == "chiffre_affaires"


def test_kpi_d_adifa_devient_la_vedette(mod):
    res = mod.gen(J.JEUX["sante"](), llm=ia(kpis=[{"nom": "Effectif", "agregation": "compte", "colonne": "consultations"}, {"nom": "CPN4", "agregation": "somme", "colonne": "cpn4"}]))
    m = json.loads(re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", res["html"], re.S).group(1))
    assert m["composition"]["mesure"] == "cpn4"


def test_titres_gardes_a_l_actualisation_sans_ia(mod):
    df = J.JEUX["ventes"]()
    gab = {str(i): "{premier} pèse {part} de cette marge" for i in range(8)}
    spec = mod.gen(df, llm=ia(gabarits=gab))["spec"]
    assert list(spec["titres"].values()) == ["{premier} pèse {part} de cette marge"]

    def interdit(*a):
        raise AssertionError("l'actualisation ne doit pas appeler l'IA")
    assert "de cette marge" in mod.gen(df, spec=spec, llm=interdit, composition=False)["html"]


def test_gabarit_altere_dans_l_enregistrement_ignore(mod):
    df = J.JEUX["ventes"]()
    spec = mod.gen(df, llm=ia(gabarits={str(i): "{premier} pèse {part} de cette marge" for i in range(8)}))["spec"]
    spec["titres"] = {k: "{premier} pèse 99 % de <script>" for k in spec["titres"]}
    assert "99 %" not in mod.gen(df, spec=spec)["html"]


@sans_node
@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_titres_sans_trou(mod, tmp_path, jeu):
    page = executer(tmp_path, mod.gen(J.JEUX[jeu]())["html"])["page"]
    for t in titres_de(page):
        assert "{" not in t and "undefined" not in t and "NaN" not in t and "null" not in t


@sans_node
def test_classement_exact_et_recalcule_apres_filtre(mod, tmp_path):
    df = J.JEUX["sante"]()
    res = mod.gen(df)
    s = executer(tmp_path, res["html"], {"region": "Abidjan"})
    tous = df.groupby("district")["consultations"].sum().sort_values(ascending=False)
    t = next(x for x in titres_de(s["page"]) if "valeur la plus haute (" in x)
    assert t.startswith(tous.index[0]) and f"{tous.iloc[0]:,}".replace(",", " ") in t
    filtre = df[df["region"] == "Abidjan"].groupby("district")["consultations"].sum().sort_values(ascending=False)
    t2 = next(x for x in titres_de(s["filtre"]) if "valeur la plus haute (" in x)
    assert t2.startswith(filtre.index[0]) and tous.index[-1] not in t2


@sans_node
def test_gabarit_d_adifa_rempli_par_le_moteur(mod, tmp_path):
    df = pd.DataFrame([{"zone": z, "ventes": v} for z, v in (("A", 100), ("B", 300), ("C", 50), ("D", 80))])
    res = mod.gen(df, llm=lambda m, n: json.dumps({"titres": {"0": "{premier} réalise {valeur} de ventes, devant {second} ({valeur2})"}}) if "reformules" in m[0]["content"]
                  else json.dumps({"kpis": [{"nom": "x", "agregation": "somme", "colonne": "ventes"}], "graphiques": []}))
    assert "B réalise 300 de ventes, devant A (100)" in titres_de(executer(tmp_path, res["html"])["page"])


@sans_node
def test_courbe_ignore_le_mois_incomplet(mod, tmp_path):
    """Données arrêtées au 3 juillet : juillet n'est pas une chute de 90 %."""
    jours = pd.date_range("2025-01-01", "2025-07-03").strftime("%Y-%m-%d")
    df = pd.DataFrame([{"date": j, "zone": z, "ventes": 10} for j in jours for z in "ABC"])
    t = titres_de(executer(tmp_path, mod.gen(df)["html"])["page"])[0]
    assert "juil." not in t and "en baisse de 9" not in t and "juin 25" in t


@sans_node
def test_annee_incomplete_jamais_comparee(mod, tmp_path):
    """Janvier à juin 2025 contre douze mois de 2024 : la vue par année ne doit pas annoncer une chute de 50 %."""
    df = pd.DataFrame([{"mois": f"{a}-{m:02d}", "zone": z, "ventes": 100} for a, n in ((2023, 12), (2024, 12), (2025, 6)) for m in range(1, n + 1) for z in "ABC"])
    html = mod.gen(df)["html"].replace("var state={},gran=(META.gran||'M')", "var state={},gran='Y'")
    t = titres_de(executer(tmp_path, html)["page"])[0]
    assert "2025" not in t and "en baisse" not in t and "sans changement" in t


@sans_node
def test_moyenne_dans_le_titre(mod, tmp_path):
    lignes = [{"mois": f"2025-{m:02d}", "zone": z, "centre": c, "taux_couverture": 80.0 if m < 7 else 90.0} for m in range(1, 8) for z in "AB" for c in "xy"]
    t = titres_de(executer(tmp_path, mod.gen(pd.DataFrame(lignes))["html"])["page"])[0]
    assert "90" in t and "en hausse de 10 points" in t


@sans_node
def test_passage_annonce_le_vrai_total(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"{a}-{m:02d}", "produit": p, "ventes": 100, "objectif_ventes": 110} for a in (2024, 2025) for m in range(1, 13)
                       for p in ("A", "B", "C") + (("Nouveau",) if a == 2025 else ())])
    t = next(x for x in titres_de(executer(tmp_path, mod.gen(df)["html"])["page"]) if "principal écart" in x)
    assert "en hausse de 33,3 %" in t and "Nouveau (+1 200)" in t
