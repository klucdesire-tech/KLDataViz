# -*- coding: utf-8 -*-
"""Titres-messages : le moteur écrit et calcule, à chaque filtre ; Adifa’ ne donne que le nom métier des colonnes."""
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


def ia(libelles=None, kpis=None, capture=None):
    """Une Adifa’ factice : répond au choix des KPI, puis aux noms de colonnes."""
    def appel(messages, n):
        if capture is not None:
            capture.append(messages)
        if '"libelles"' in messages[0]["content"]:
            return json.dumps({"libelles": libelles or {}}, ensure_ascii=False)
        return json.dumps({"kpis": kpis or [{"nom": "x", "agregation": "somme", "colonne": "marge"}], "graphiques": []})
    return appel


@pytest.mark.parametrize("texte,colonne", [
    ("tests de dépistage HTS", "HTS_TST"),
    ("nouvelles initiations de traitement", "TX_NEW"),
    ("chiffre d'affaires", "chiffre_affaires"),
    ("consultations prénatales CPN1", "cpn1"),
    ("retards de livraison", "retards"),
    ("femmes en grossesse", "suivi"),
    ("quartier", "zone"),
    ("première consultation prénatale", "cpn1"),
    ("tests de dépistage VIH positifs", "HTS_TST_POS"),
    ("unité organisationnelle", "unite_organisation"),
    ("part de marché", "part_marche"),
    ("dépenses par habitant", "depenses_par_habitant"),
    ("poids en kg", "poids_kg"),
])
def test_nom_accepte(mod, texte, colonne):
    assert mod.T.valider(texte, colonne) == texte


@pytest.mark.parametrize("texte,raison", [
    ("ventes en hausse", "un sens"),
    ("meilleures ventes", "un jugement"),
    ("ventes de 2025", "un chiffre"),
    ("ventes : à surveiller", "une phrase"),
    ("{premier}", "une variable"),
    ("<b>ventes</b>", "du code"),
    ("ventes totales de toutes les zones du pays", "trop long"),
    ("v", "trop court"),
    ("ventes en forte croissance", "un sens"),
    ("ventes en milliers", "une quantité"),
    ("ventes en tonnes", "une unité"),
    ("ventes en fcfa", "une unité"),
    ("ventes en heures", "une unité"),
    ("ventes en °C", "une unité"),
    ("ventes nettes", "une autre grandeur"),
    ("part des ventes", "une autre grandeur"),
    ("ventes par mois", "une autre grandeur"),
    (None, "pas un texte"),
])
def test_nom_refuse(mod, texte, raison):
    assert mod.T.valider(texte, "ventes") is None, raison


def test_toutes_les_cartes_ont_une_formule(mod):
    df = J.JEUX["ventes"]()
    plan = mod.T.appliquer(mod.C.composer(mod.F.lire_fiche(df), df), {})
    assert all(c["formule"] for ligne in plan["lignes"] for c in ligne)
    assert mod.T.formule({"type": "cible"}) != mod.T.formule({"type": "cible", "dimension": "zone"})


def test_seuls_les_noms_de_colonnes_partent_vers_l_ia(mod):
    capture, df = [], J.JEUX["ventes"]()
    mod.gen(df, llm=ia(capture=capture))
    envoi = next(m[1]["content"] for m in capture if '"libelles"' in m[0]["content"])
    assert set(json.loads(envoi)) <= set(df.columns)


def test_ia_en_panne_le_moteur_formule_seul(mod):
    def appel(messages, n):
        if '"libelles"' in messages[0]["content"]:
            raise RuntimeError("panne")
        return json.dumps({"kpis": [{"nom": "x", "agregation": "somme", "colonne": "marge"}], "graphiques": []})
    res = mod.gen(J.JEUX["ventes"](), llm=appel)
    assert 'id="compo"' in res["html"] and res["spec"]["libelles"] == {}


def test_reponse_illisible_sans_effet(mod):
    res = mod.gen(J.JEUX["ventes"](), llm=lambda m, n: "pas du JSON" if '"libelles"' in m[0]["content"]
                  else json.dumps({"kpis": [{"nom": "x", "agregation": "somme", "colonne": "marge"}], "graphiques": []}))
    assert res["spec"]["libelles"] == {}


@pytest.mark.parametrize("reponse", ['{"libelles": ["recettes", "agence"]}', '{"libelles": "recettes"}', '["recettes"]', '{"libelles": null}'])
def test_reponse_mal_formee_garde_la_page(mod, reponse):
    res = mod.gen(J.JEUX["ventes"](), llm=lambda m, n: reponse if '"libelles"' in m[0]["content"]
                  else json.dumps({"kpis": [{"nom": "x", "agregation": "somme", "colonne": "marge"}], "graphiques": []}))
    assert 'id="compo"' in res["html"] and res["spec"]["libelles"] == {}


def test_enregistrement_mal_forme_garde_la_page(mod):
    df = J.JEUX["ventes"]()
    spec = dict(mod.gen(df)["spec"], libelles=["recettes"])
    assert 'id="compo"' in mod.gen(df, spec=spec)["html"]


def test_sans_ia_aucun_nom(mod):
    assert mod.gen(J.JEUX["ventes"]())["spec"]["libelles"] == {}


def test_vedette_choisie_par_adifa(mod):
    df = J.JEUX["sante"]()
    fiche = mod.F.lire_fiche(df)
    assert mod.C.composer(fiche, df)["mesure"] == "consultations"
    assert mod.C.composer(fiche, df, vedettes=["inconnue", "cpn4"])["mesure"] == "cpn4"
    assert mod.C.composer(fiche, df, vedettes=["cpn4"])["lignes"][0][0]["mesure"] == "cpn4"


def test_objectif_prime_sur_le_choix_d_adifa(mod):
    df = J.JEUX["ventes"]()
    assert mod.C.composer(mod.F.lire_fiche(df), df, vedettes=["marge"])["mesure"] == "chiffre_affaires"


def test_kpi_d_adifa_devient_la_vedette(mod):
    res = mod.gen(J.JEUX["sante"](), llm=ia(kpis=[{"nom": "Effectif", "agregation": "compte", "colonne": "consultations"}, {"nom": "CPN4", "agregation": "somme", "colonne": "cpn4"}]))
    m = json.loads(re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", res["html"], re.S).group(1))
    assert m["composition"]["mesure"] == "cpn4"


def test_noms_gardes_a_l_actualisation_sans_ia(mod):
    df = J.JEUX["ventes"]()
    spec = mod.gen(df, llm=ia(libelles={"marge": "marge brute"}))["spec"]
    assert spec["libelles"] == {"marge": "marge brute"}

    def interdit(*a):
        raise AssertionError("l'actualisation ne doit pas appeler l'IA")
    assert "marge brute" in mod.gen(df, spec=spec, llm=interdit, composition=False)["html"]


def test_nom_altere_dans_l_enregistrement_ignore(mod):
    df = J.JEUX["ventes"]()
    spec = mod.gen(df, llm=ia(libelles={"marge": "marge brute"}))["spec"]
    spec["libelles"] = {"marge": "<script>en hausse"}
    assert "<script>en hausse" not in mod.gen(df, spec=spec)["html"]


def test_actualisation_sans_kpi_calculable_garde_sa_page(mod):
    df = J.JEUX["ventes"]()
    spec = {"kpis": [{"nom": "x", "agregation": "somme", "colonne": "renommee"}], "graphiques": [], "_ia": True, "composition": True}
    assert 'id="compo"' in mod.gen(df, spec=spec, composition=False)["html"]


@sans_node
@pytest.mark.parametrize("jeu", sorted(J.JEUX))
def test_titres_sans_trou(mod, tmp_path, jeu):
    for t in titres_de(executer(tmp_path, mod.gen(J.JEUX[jeu]())["html"])["page"]):
        assert "{" not in t and "undefined" not in t and "NaN" not in t and "null" not in t


@sans_node
def test_classement_exact_et_recalcule_apres_filtre(mod, tmp_path):
    df = J.JEUX["sante"]()
    s = executer(tmp_path, mod.gen(df)["html"], {"region": "Abidjan"})
    tous = df.groupby("district")["consultations"].sum().sort_values(ascending=False)
    t = next(x for x in titres_de(s["page"]) if "valeur la plus haute pour" in x and "devant" in x)
    assert f"pour {tous.index[0]} ({tous.iloc[0]:,})".replace(",", " ") in t and f"devant {tous.index[1]}" in t
    filtre = df[df["region"] == "Abidjan"].groupby("district")["consultations"].sum().sort_values(ascending=False)
    t2 = next(x for x in titres_de(s["filtre"]) if "valeur la plus haute pour" in x and "devant" in x)
    assert f"pour {filtre.index[0]} ({filtre.iloc[0]:,})".replace(",", " ") in t2 and tous.index[-1] not in t2


@sans_node
def test_nom_d_adifa_dans_le_titre_et_le_sous_titre(mod, tmp_path):
    df = pd.DataFrame([{"zone": z, "ventes": v} for z, v in (("A", 100), ("B", 300), ("C", 50), ("D", 80))])
    res = mod.gen(df, llm=lambda m, n: json.dumps({"libelles": {"ventes": "recettes", "zone": "agence"}}) if '"libelles"' in m[0]["content"]
                  else json.dumps({"kpis": [{"nom": "x", "agregation": "somme", "colonne": "ventes"}], "graphiques": []}))
    page = executer(tmp_path, res["html"])["page"]
    assert "Recettes : valeur la plus haute pour B (300), devant A (100)" in titres_de(page) and "Recettes par agence" in page


@sans_node
def test_courbe_ignore_le_mois_incomplet(mod, tmp_path):
    """Données arrêtées au 3 juillet : juillet n'est pas une chute de 90 %."""
    jours = pd.date_range("2025-01-01", "2025-07-03").strftime("%Y-%m-%d")
    df = pd.DataFrame([{"date": j, "zone": z, "ventes": 10} for j in jours for z in "ABC"])
    t = titres_de(executer(tmp_path, mod.gen(df)["html"])["page"])[0]
    assert "juil." not in t and "en baisse de 9" not in t and "juin 25" in t


@sans_node
def test_courbe_montre_le_mois_incomplet_sans_le_cacher(mod, tmp_path):
    jours = pd.date_range("2025-01-01", "2025-07-03").strftime("%Y-%m-%d")
    df = pd.DataFrame([{"date": j, "zone": z, "ventes": 10} for j in jours for z in "ABC"])
    page = executer(tmp_path, mod.gen(df)["html"])["page"]
    assert 'stroke-dasharray="4 4"' in page and "juil. 25 est incomplet" in page


@sans_node
def test_comparee_affiche_la_meme_periode_que_le_titre(mod, tmp_path):
    jours = pd.date_range("2025-01-01", "2025-07-03").strftime("%Y-%m-%d")
    df = pd.DataFrame([{"date": j, "zone": z, "ventes": v} for j in jours for z, v in (("A", 10), ("B", 20), ("C", 30))])
    page = re.sub(r"\s", " ", executer(tmp_path, mod.gen(df)["html"])["page"])
    t = next(x for x in titres_de(page) if "pour C en juin 25" in x)
    assert "(900)" in t and re.search(r">900</tspan> C<", page) and not re.search(r">90</tspan> C<", page)
    assert "De janv. 25 à juin 25" in page and "juil. 25 est incomplet : non affiché" in page


@sans_node
def test_comparee_groupe_arrete_plus_tot_sans_valeur_affichee(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "ventes": 10 * i} for m in range(1, 13) for i, z in enumerate("ABCD", 1) if not (z == "D" and m > 4)])
    page = re.sub(r"\s", " ", executer(tmp_path, mod.gen(df)["html"])["page"])
    assert ">—</tspan> D<" in page


@sans_node
def test_comparee_axe_resserre_et_annonce(mod, tmp_path):
    """Des valeurs de 7,2 à 7,7 ne doivent plus se lire comme cinq traits plats sur une échelle partant de 0."""
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "region": z, "centre": c, "taux_humidite": v + m * .01} for m in range(1, 9) for z, v in (("A", 7.2), ("B", 7.4), ("C", 7.6)) for c in "xy"])
    page = re.sub(r"\s", " ", executer(tmp_path, mod.gen(df)["html"])["page"])
    assert "resserré pour lire les écarts" in page and "cp-mg" not in page and 'class="cp-cc"' in page


@sans_node
def test_comparee_axe_negatif_resserre_annonce(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "centre": c, "taux_ecart": v + m * .1} for m in range(1, 9) for z, v in (("A", -5.0), ("B", -4.0), ("C", -3.0)) for c in "xy"])
    assert "resserré pour lire les écarts" in re.sub(r"\s", " ", executer(tmp_path, mod.gen(df)["html"])["page"])


@sans_node
def test_comparee_sans_note_d_axe_fausse_sur_de_tres_petites_valeurs(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "centre": c, "taux_ecart": .0012 + m * .00001 + i * .0001} for m in range(1, 9) for i, z in enumerate("ABC") for c in "xy"])
    assert "Axe de 0 à 0" not in re.sub(r"\s", " ", executer(tmp_path, mod.gen(df)["html"])["page"])


@sans_node
def test_comparee_periode_manquante_coupe_la_ligne(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "ventes": 50 + m} for m in range(1, 9) for z in "ABC" if not (z == "B" and m == 4)])
    chemins = re.findall(r'<path d="(M[^"]+)" fill="none" stroke="#[0-9A-F]{6}" stroke-width="2" stroke-linejoin', executer(tmp_path, mod.gen(df)["html"])["page"])
    assert sorted(c.count("M") for c in chemins) == [1, 1, 2]


@sans_node
def test_comparee_couleur_gardee_apres_filtre(mod, tmp_path):
    """La couleur suit le groupe, pas son rang : un filtre ne repeint pas ceux qui restent."""
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "region": r, "ventes": 50 + m + i} for m in range(1, 9) for i, (z, r) in enumerate((("A", "N"), ("B", "S"), ("C", "S"), ("D", "S")))])
    s = executer(tmp_path, mod.gen(df)["html"], {"region": "S"})
    couleur = lambda p, g: re.search(r'fill="(#[0-9A-F]{6})"/><text[^>]*><tspan[^>]*>[^<]*</tspan> ' + g + "<", p).group(1)  # noqa: E731
    assert couleur(s["page"], "C") == couleur(s["filtre"], "C") and couleur(s["page"], "D") == couleur(s["filtre"], "D")


@sans_node
def test_comparee_annonce_le_groupe_ecarte(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "ventes": 50 + m} for m in range(1, 9) for z in "ABCD" if z != "D" or m == 8])
    assert "Trop peu de périodes pour : D." in re.sub(r"\s", " ", executer(tmp_path, mod.gen(df)["html"])["page"])


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
def test_taux_en_fraction_compare_dans_l_unite_affichee(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "taux_guerison": 0.30 + m * 0.01} for m in range(1, 13) for z in "ABC"])
    t = titres_de(executer(tmp_path, mod.gen(df)["html"])["page"])[0]
    assert "point" not in t and "en hausse de 2,4 %" in t


@sans_node
def test_taux_qui_franchit_un_reste_une_hausse(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "taux_guerison": 0.98 if m < 12 else 1.02} for m in range(1, 13) for z in "ABC"])
    t = titres_de(executer(tmp_path, mod.gen(df)["html"])["page"])[0]
    assert "en hausse de 4,1 %" in t


@sans_node
def test_passage_annonce_le_vrai_total(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"{a}-{m:02d}", "produit": p, "ventes": 100, "objectif_ventes": 110} for a in (2024, 2025) for m in range(1, 13)
                       for p in ("A", "B", "C") + (("Nouveau",) if a == 2025 else ())])
    t = next(x for x in titres_de(executer(tmp_path, mod.gen(df)["html"])["page"]) if "principal écart" in x)
    assert "en hausse de 33,3 %" in t and "pour Nouveau (+1 200)" in t


@sans_node
def test_objectif_plafond_jamais_dit_atteint(mod, tmp_path):
    """12 jours pour un plafond de 10 : le moteur ne connaît pas le sens de l'objectif, il constate sans juger."""
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "delai": 12, "objectif_delai": 10} for m in range(1, 8) for z in "ABCD"])
    t = re.sub(r"\s", " ", executer(tmp_path, mod.gen(df)["html"])["page"])
    assert "atteign" not in t and "objectif atteint" not in t and "4 sur 4 à l'objectif ou au-dessus" in t.replace("&#39;", "'")


@sans_node
def test_titre_ne_nomme_pas_un_groupe_absent_du_graphique(mod, tmp_path):
    df = pd.DataFrame([{"mois": f"2025-{m:02d}", "zone": z, "ventes": 10 * (i + 1)} for m in range(1, 13) for i, z in enumerate("ABC")] + [{"mois": "2025-12", "zone": "Nouveau", "ventes": 999}])
    page = re.sub(r"\s", " ", executer(tmp_path, mod.gen(df)["html"])["page"])
    assert "Ventes : valeur la plus haute pour C en déc. 25 (30)" in titres_de(page) and "Trop peu de périodes pour : Nouveau." in page


@sans_node
def test_nom_de_groupe_a_accolades(mod, tmp_path):
    df = pd.DataFrame([{"zone": z, "ventes": v} for z, v in (("{premier}", 300), ("{part}", 200), ("C", 50), ("D", 80))])
    assert not any("{" in t for t in titres_de(executer(tmp_path, mod.gen(df)["html"])["page"]))
