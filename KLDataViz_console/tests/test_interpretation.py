# -*- coding: utf-8 -*-
"""Tests de l'interprétation en clair — données fictives uniquement."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import interpretation as L
import predictive as P


def _mensuel(colonne, debut, pas, bruit=0.0, mois=36, graine=1):
    rng = np.random.default_rng(graine)
    return pd.DataFrame({"date": [f"{p}-28" for p in pd.period_range("2023-01", periods=mois, freq="M").astype(str)],
                         colonne: [debut + pas * i + rng.normal(0, bruit) if bruit else debut + pas * i for i in range(mois)]})


def _lecture(df, colonne, horizon=6):
    r = P.analyse(df, cible=colonne, periode="date", horizon=horizon)
    assert not r["erreurs"], r["erreurs"]
    return L.predictif(r), r["resultat"]


def test_baisse_de_chiffre_d_affaires_est_a_craindre():
    lecture, res = _lecture(_mensuel("chiffre_affaires_fcfa", 5_000_000, -60_000, 8_000), "chiffre_affaires_fcfa")
    assert res["etat"] == "utilisable"
    assert lecture["alerte"]
    assert lecture["phrases"][0].startswith("Si rien n'est fait, une baisse de chiffre d'affaires est à craindre d'ici 6 mois")
    assert "FCFA par mois" in lecture["phrases"][0] and "−" in lecture["phrases"][0]


def test_chiffres_de_la_phrase_viennent_du_calcul():
    df = _mensuel("recettes", 1000, -10)
    lecture, res = _lecture(df, "recettes", horizon=3)
    # 3 derniers mois : 670, 660, 650, soit 660 ; prévus : 640, 630, 620, soit 630
    assert "de 660 à 630 par mois en moyenne" in lecture["phrases"][0]
    assert "−5 %" in lecture["phrases"][0]


def test_hausse_de_retours_est_a_craindre():
    lecture, _ = _lecture(_mensuel("retours", 100, 8, 1), "retours")
    assert lecture["alerte"] and "une hausse de retours est à craindre" in lecture["phrases"][0]


def test_hausse_de_recettes_n_est_pas_une_alerte():
    lecture, _ = _lecture(_mensuel("recettes", 1000, 40, 2), "recettes")
    assert not lecture["alerte"]
    assert "Si la tendance se poursuit, une hausse de recettes est attendue" in lecture["phrases"][0]


def test_sens_inconnu_reste_neutre():
    lecture, _ = _lecture(_mensuel("temperature", 400, -8, 1), "temperature")
    assert not lecture["alerte"]
    assert "à craindre" not in " ".join(lecture["phrases"]) and "Si rien n'est fait" not in lecture["phrases"][0]


def test_stable_n_annonce_ni_hausse_ni_baisse():
    res = {"type": "prevision", "etat": "utilisable", "agregation": "somme", "tendance_pente": -0.2, "tendance_p": 0.6,
           "validation": {"periodes_testees": 6, "erreur_modele": 3.0, "erreur_reference": 40.0, "reference": "moyenne"},
           "historique": {"valeurs": [1000, 1002, 998, 1001]}, "previsions": [{"periode": "2026-01", "valeur": 1000, "bas": 990, "haut": 1010}]}
    lecture = L.predictif({"cible": "recettes", "resultat": res})
    assert lecture["phrases"][0].startswith("Aucune hausse ni baisse nette de recettes")
    assert not lecture["alerte"]


def test_pente_non_significative_n_annonce_pas_de_baisse():
    res = {"type": "prevision", "etat": "indicatif", "agregation": "somme", "tendance_pente": -20.0, "tendance_p": 0.4,
           "validation": {"periodes_testees": 6, "erreur_modele": 30.0, "erreur_reference": 40.0, "reference": "moyenne"},
           "historique": {"valeurs": [1000, 1200, 800, 1000]}, "previsions": [{"periode": "2026-01", "valeur": 900, "bas": 700, "haut": 1100}]}
    assert L.predictif({"cible": "recettes", "resultat": res})["phrases"][0].startswith("Aucune hausse ni baisse nette")


def test_modele_insuffisant_n_annonce_rien():
    res = {"type": "prevision", "etat": "insuffisant", "agregation": "somme", "tendance_pente": -50.0, "tendance_p": 0.001,
           "validation": {"periodes_testees": 6, "erreur_modele": 90.0, "erreur_reference": 40.0, "reference": "moyenne"},
           "historique": {"valeurs": [900, 800, 700, 600, 500, 400]},
           "previsions": [{"periode": "2026-01", "valeur": 300, "bas": 100, "haut": 500}]}
    lecture = L.predictif({"cible": "chiffre_affaires", "resultat": res})
    texte = " ".join(lecture["phrases"])
    assert lecture["phrases"][0] == "Ces données ne permettent pas d'annoncer une hausse ou une baisse de chiffre d'affaires."
    assert "à craindre" not in texte and "300" not in texte and not lecture["alerte"]


def test_indicatif_reste_prudent():
    res = {"type": "prevision", "etat": "indicatif", "agregation": "somme", "tendance_pente": -10.0, "tendance_p": 0.01,
           "validation": {"periodes_testees": 4, "erreur_modele": 30.0, "erreur_reference": 40.0, "reference": "moyenne"},
           "historique": {"valeurs": [100, 90, 80, 70]}, "previsions": [{"periode": "2026-02", "valeur": 60, "bas": -5, "haut": 80}]}
    lecture = L.predictif({"cible": "ventes", "resultat": res})
    assert "une baisse de ventes est possible" in lecture["phrases"][0]
    assert not lecture["alerte"]
    assert "À prendre comme une indication" in " ".join(lecture["phrases"])
    assert "entre 0 et 80" in lecture["phrases"][1] and "février 2026" in lecture["phrases"][1]


def test_part_de_oui_en_pourcent():
    df = pd.DataFrame([{"date": f"{p}-28", "annule": "Oui" if i % 10 < (30 - k) / 3 else "Non"}
                       for k, p in enumerate(pd.period_range("2023-01", periods=30, freq="M").astype(str)) for i in range(10)])
    r = P.analyse(df, cible="annule", periode="date", horizon=3)
    lecture = L.predictif(r)
    assert "part de Oui pour annule" in lecture["phrases"][0] and " %" in lecture["phrases"][0]


def test_regression_dit_l_effet_en_clair():
    rng = np.random.default_rng(3)
    n = 400
    quantite = rng.normal(500, 100, n)
    df = pd.DataFrame({"quantite": quantite, "bruit": rng.normal(0, 10, n)})
    df["retours"] = 0.03 * quantite + rng.normal(0, 1, n)
    r = P.analyse(df, cible="retours", predicteurs=["quantite", "bruit"])
    res = r["resultat"]
    lecture = L.predictif(r)
    texte = " ".join(lecture["phrases"])
    assert res["etat"] == "utilisable"
    assert f"expliquent environ {round(res['r2_test'] * 100)} % des variations de retours" in texte
    assert "+100 sur « quantite » va avec environ +3 sur « retours »" in texte
    assert "Aucun effet net de « bruit »" in texte
    assert "Un lien n'est pas une cause" in texte


def test_regression_petit_coefficient_garde_ses_chiffres():
    """Coefficient de 0,00002 : arrondi à 4 décimales, il valait 0 et la phrase aurait menti."""
    rng = np.random.default_rng(4)
    n = 300
    df = pd.DataFrame({"ca": rng.normal(1_000_000, 200_000, n)})
    df["retours"] = 0.00002 * df["ca"] + rng.normal(0, 0.5, n)
    r = P.analyse(df, cible="retours", predicteurs=["ca"])
    assert r["resultat"]["coefficients"][1]["coef"] == pytest.approx(0.00002, rel=0.2)
    assert "+100 000 sur « ca » va avec environ +2" in " ".join(L.predictif(r)["phrases"])


def test_regression_insuffisante_n_explique_rien():
    res = {"type": "lineaire", "etat": "insuffisant", "r2_test": -0.2, "coefficients": [
        {"variable": "(constante)", "coef": 1.0, "p": 0.0, "ecart_type": None},
        {"variable": "x", "coef": 2.0, "p": 0.001, "ecart_type": 3.0}]}
    phrases = L.predictif({"cible": "ventes", "resultat": res})["phrases"]
    assert len(phrases) == 1 and "ne vous appuyez pas" in phrases[0] and "moins bien qu'une simple moyenne" in phrases[0]


def test_regression_sur_lignes_dependantes_le_dit():
    res = {"type": "lineaire", "etat": "utilisable", "r2_test": 0.66, "rmse_test": 1.2, "ecart_type_cible": 2.0, "coefficients": []}
    assert "à vérifier sur les données détaillées" in " ".join(L.predictif({"cible": "retours", "resultat": res}, True)["phrases"])


def test_logistique_en_clair():
    res = {"type": "logistique", "etat": "utilisable", "n_test": 90, "positifs_test": 20, "vrais_positifs_test": 12,
           "matrice_confusion": {"faux_positifs": 3},
           "coefficients": [{"variable": "(constante)", "coef_standardise": 0.1},
                            {"variable": "age", "coef_standardise": -0.9}, {"variable": "poids", "coef_standardise": 0.05}]}
    phrases = L.predictif({"cible": "abandon", "resultat": res})["phrases"]
    assert "a retrouvé 12 cas « Oui » de « abandon » sur 20, avec 3 fausses alertes" in phrases[0]
    assert "Plus la valeur de « age » est haute, moins le « Oui » est probable." in phrases
    assert not any("poids" in p for p in phrases)


def test_logistique_sans_vrai_positif_n_est_pas_lue():
    res = {"type": "logistique", "etat": "indicatif", "n_test": 90, "positifs_test": 8, "vrais_positifs_test": 0, "coefficients": []}
    assert "ne permet pas de prévoir" in L.predictif({"cible": "abandon", "resultat": res})["phrases"][0]


def test_erreur_du_modele_sans_phrase():
    assert L.predictif({"cible": "x", "resultat": {"erreur": "série trop courte"}})["phrases"] == []
    assert L.predictif({"cible": "x", "resultat": None})["phrases"] == []


def _test(**k):
    return dict({"significatif": True, "structurel": False, "force": "forte"}, **k)


def test_tests_en_phrases_du_plus_net_au_moins_net():
    inf = {"n_tests": 4, "n_significatifs": 3,
           "correlations": [_test(a="retours", b="quantite", r=0.7, force="modérée")],
           "associations": [_test(a="categorie", b="region", force="faible")],
           "comparaisons": [_test(num="chiffre_affaires_fcfa", cat="region", groupes=[
               {"label": "Bouaké", "moyenne": 400000.0}, {"label": "Abidjan", "moyenne": 1200000.0}])]}
    phrases = L.tests(inf)["phrases"]
    assert phrases[0] == ("« chiffre d'affaires » change selon « region » : en moyenne, Abidjan est le plus haut (1,2 million FCFA) "
                          "et Bouaké le plus bas (400 000 FCFA), soit 3 fois plus.")
    assert phrases[1] == "« retours » et « quantite » montent et descendent ensemble (lien modéré)."
    assert "la répartition de l'un change selon l'autre" in phrases[2]


def test_tests_lien_negatif():
    inf = {"n_tests": 1, "n_significatifs": 1, "correlations": [_test(a="prix", b="ventes", r=-0.8)]}
    assert L.tests(inf)["phrases"] == ["Quand « prix » monte, « ventes » a tendance à baisser (lien fort)."]


def test_tests_ecarte_le_negligeable_et_les_effectifs_faibles():
    inf = {"n_tests": 2, "n_significatifs": 2,
           "correlations": [_test(a="a", b="b", r=0.1, force="négligeable")],
           "associations": [_test(a="c", b="d", attendus_faibles=True)]}
    assert L.tests(inf)["phrases"] == []


def test_tests_sans_resultat():
    assert L.tests({"n_tests": 5, "n_significatifs": 0})["phrases"] == ["Aucun lien net dans ce fichier : les écarts observés peuvent venir du hasard."]


def test_tests_annonce_le_reste():
    inf = {"n_tests": 7, "n_significatifs": 7, "correlations": [_test(a=f"a{i}", b=f"b{i}", r=0.9) for i in range(7)]}
    phrases = L.tests(inf)["phrases"]
    assert len(phrases) == 6 and phrases[-1] == "2 autres résultats dans les tableaux ci-dessous."


@pytest.mark.parametrize("v,attendu", [(5482792, "5,5 millions"), (1200000, "1,2 million"), (54832, "54 800"), (660, "660"),
                                       (2.74, "2,74"), (0.00002, "0,00002"), (-1500, "−1 500"), (0, "0")])
def test_nombre(v, attendu):
    assert L.nombre(v) == attendu


def test_nom_lisible():
    assert L.nom_lisible("chiffre_affaires_fcfa") == "chiffre d'affaires"
    assert L.nom_lisible("taux_humidite_pct") == "taux humidite"


def test_route_et_json(tmp_path):
    from _harness import reset_app_modules
    reset_app_modules()
    import server
    p = tmp_path / "ventes.csv"
    _mensuel("ventes", 5000, -60, 10).assign(zone=(["Nord", "Sud", "Est"] * 12)).to_csv(p, index=False)
    a = server.analyze(str(p))
    assert isinstance(a["inference"]["lecture"]["phrases"], list)
    json.dumps(a["inference"], allow_nan=False)
    import predictive
    df = pd.read_csv(p)
    assert server._lecture_predictive(predictive.analyse(df, "ventes", None, "date", 6, None), df)["phrases"]


def test_lecture_en_panne_laisse_les_chiffres(monkeypatch):
    from _harness import reset_app_modules
    reset_app_modules()
    import server
    monkeypatch.setitem(sys.modules, "interpretation", None)
    assert server._lecture_predictive({"resultat": {}}, pd.DataFrame({"a": [1]})) == {"phrases": [], "alerte": False}
