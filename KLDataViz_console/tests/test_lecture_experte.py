# -*- coding: utf-8 -*-
"""Tests de la lecture d'Adifa’ : réponses d'IA simulées, données fictives, aucun appel réseau."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inferential as I
import lecture_experte as X
import predictive as P

FAITS = {"analyse": "prévision mensuelle", "mesure": "tonnage", "fiabilite": "indicatif", "niveau_recent": "1 274 t", "prevision_moyenne": "984 t",
         "variation": "−23 %", "dernier_mois_prevu": {"valeur": "813 t", "bas": "0 t", "haut": "2 217 t"}}


def _ia(reponse):
    return (lambda m, n: json.dumps(reponse, ensure_ascii=False)), json.loads


def _lire(reponse, faits=FAITS):
    llm, parse = _ia(reponse)
    return X.lire(faits, llm, parse)


def test_lecture_valide_conservee():
    l = _lire({"conclusion": "Une baisse est possible : −23 %, de 1 274 t à 984 t.", "lecture": ["La fourchette va de 0 t à 2 217 t pour le dernier mois."],
               "prudence": ["L'écart est large."], "a_verifier": ["Regarder la série mois par mois."]})
    assert l["conclusion"].startswith("Une baisse est possible") and len(l["lecture"]) == 1 and l["a_verifier"]


def test_conclusion_avec_chiffre_invente_rend_la_main_au_moteur():
    assert _lire({"conclusion": "Une baisse de 31 % est possible.", "lecture": []}) is None


def test_phrase_avec_chiffre_invente_retiree():
    l = _lire({"conclusion": "Une baisse est possible.", "lecture": ["Cela représente 4 500 t de moins.", "Le niveau récent est de 1 274 t."]})
    assert l["lecture"] == ["Le niveau récent est de 1 274 t."]


def test_arrondi_proche_accepte_mais_pas_un_autre_chiffre():
    assert _lire({"conclusion": "Le niveau récent est de 1 270 t."})
    assert _lire({"conclusion": "Le niveau récent est de 1 300 t."}) is None


def test_chiffre_colle_a_un_mot_n_est_pas_un_chiffre():
    assert _lire({"conclusion": "Une baisse est possible pour le groupe R2."})


def test_cause_affirmee_retiree():
    assert _lire({"conclusion": "La baisse est due à la saison."}) is None
    l = _lire({"conclusion": "Une baisse est possible.", "lecture": ["La pluie provoque ce recul."]})
    assert l["lecture"] == []


def test_cause_autorisee_en_prudence_pour_la_rejeter():
    l = _lire({"conclusion": "Une baisse est possible.", "prudence": ["Rien ne montre que le prix provoque ce recul."]})
    assert l["prudence"]


def test_certitude_retiree():
    assert _lire({"conclusion": "La baisse est certaine."}) is None


def test_certains_mois_n_est_pas_une_certitude():
    assert _lire({"conclusion": "Une baisse est possible, sans en faire une prévision certaine.",
                  "lecture": ["On ne sait pas si certains mois sont plus forts."]})["lecture"]


def test_fausse_definition_de_la_valeur_p_retiree():
    l = _lire({"conclusion": "Une baisse est possible.", "lecture": ["La probabilité que ce soit le hasard est faible."],
               "prudence": ["La chance que ce soit dû au hasard reste réelle."]})
    assert l["lecture"] == [] and l["prudence"] == []


def test_mot_qui_suppose_une_action_retire():
    assert _lire({"conclusion": "Le chiffre d'affaires est le seul levier net."}) is None


def test_saisonnalite_non_testee_si_historique_trop_court():
    """24 mois d'historique dont 6 de test : il reste 18 mois, la saisonnalité n'a pas été éprouvée."""
    df = pd.DataFrame({"date": [f"{p}-28" for p in pd.period_range("2024-01", periods=24, freq="M").astype(str)], "ventes": [1000 + i % 12 * 40 for i in range(24)]})
    assert X.faits_predictif(P.analyse(df, cible="ventes", periode="date", horizon=3), df.columns)["saisonnalite"].startswith("non testée")


def test_construction_de_colonne_doit_rester_une_supposition():
    l = _lire({"conclusion": "Une baisse est possible.", "lecture": ["La valeur est calculée à partir du tonnage."],
               "prudence": ["La valeur semble calculée à partir du tonnage."]})
    assert l["lecture"] == [] and l["prudence"]


def test_modele_insuffisant_n_annonce_rien():
    faits = dict(FAITS, fiabilite="insuffisant")
    assert _lire({"conclusion": "Le tonnage devrait baisser d'ici 6 mois."}, dict(faits, horizon_mois=6)) is None
    assert _lire({"conclusion": "Une baisse est attendue."}, faits) is None
    assert _lire({"conclusion": "Rien ne permet d'annoncer une hausse ou une baisse."}, faits)


def test_limite_du_nombre_de_phrases():
    l = _lire({"conclusion": "Une baisse est possible.", "lecture": [f"Phrase {c}." for c in "abcde"], "prudence": ["a", "b", "c"], "a_verifier": ["a", "b", "c"]})
    assert (len(l["lecture"]), len(l["prudence"]), len(l["a_verifier"])) == (3, 2, 2)


def test_reponse_inexploitable():
    assert X.lire(FAITS, lambda m, n: None, lambda t: None) is None
    assert X.lire(FAITS, lambda m, n: "pas du json", lambda t: None) is None
    assert X.lire(None, lambda m, n: "{}", json.loads) is None
    assert _lire({"conclusion": 12}) is None


def test_champs_mal_formes_ne_cassent_pas():
    l = _lire({"conclusion": "Une baisse est possible.", "lecture": "texte", "prudence": [3, None]})
    assert l["lecture"] == [] and l["prudence"] == []


def _ventes():
    rng = np.random.default_rng(1)
    n = 300
    return pd.DataFrame({"region": rng.choice(["Nord", "Sud"], n), "rayon": rng.choice(["A", "B", "C"], n),
                         "quantite": rng.normal(100, 10, n), "ca_fcfa": rng.normal(5000, 500, n)})


def test_aucune_ligne_du_fichier_n_est_transmise():
    """Seuls des résultats partent vers l'IA : une valeur isolée d'une ligne ne doit pas s'y trouver."""
    df = _ventes()
    df.loc[0, "quantite"] = 987654.321
    df.loc[1, "region"] = "Nord"
    faits = X.faits_tests(I.analyse(df), df.columns)
    texte = json.dumps(faits, ensure_ascii=False)
    assert "987654" not in texte and "987 654" not in texte


def test_faits_tests_listent_les_colonnes_testees():
    df = _ventes()
    faits = X.faits_tests(I.analyse(df), df.columns)
    assert set(faits["colonnes_testees"]) >= {"region", "rayon", "quantite", "ca"}
    assert len(faits["tests"]) == faits["tests_faits"]


def test_faits_regression_distinguent_les_effets():
    rng = np.random.default_rng(2)
    n = 300
    df = pd.DataFrame({"a": rng.normal(0, 1, n), "b": rng.normal(0, 1, n)})
    df["y"] = 5 * df["a"] + rng.normal(0, 1, n)
    faits = X.faits_predictif(P.analyse(df, cible="y", predicteurs=["a", "b"]), df.columns)
    assert {c["colonne"]: c["resultat"] for c in faits["colonnes_du_modele"]} == {"a": "net", "b": "non net"}


def test_faits_prevision_lisibles():
    df = pd.DataFrame({"date": [f"{p}-28" for p in pd.period_range("2023-01", periods=30, freq="M").astype(str)], "ventes": [1000 - 10 * i for i in range(30)]})
    faits = X.faits_predictif(P.analyse(df, cible="ventes", periode="date", horizon=3), df.columns)
    assert faits["niveau_recent"] == "720" and faits["prevision_moyenne"] == "690" and faits["variation"] == "−4 %"
    json.dumps(faits, allow_nan=False)


def test_faits_sans_resultat():
    assert X.faits_predictif({"cible": "x", "resultat": {"erreur": "trop court"}}) is None
    assert X.faits_tests({"n_tests": 0, "n_structure": 0}) is None


def _server(tmp_path):
    from _harness import reset_app_modules
    reset_app_modules()
    import server
    p = tmp_path / "ventes.csv"
    _ventes().to_csv(p, index=False)
    server.T.state()["last_result"] = server.analyze(str(p))
    server.T.state().pop("lectures_expertes", None)
    return server


def test_sans_ia_rien_ne_part(tmp_path, monkeypatch):
    server = _server(tmp_path)
    monkeypatch.setattr(server, "_llm_config", lambda: None)
    assert server._lecture_experte("tests") is None


def test_meme_lecture_demandee_une_seule_fois(tmp_path, monkeypatch):
    server = _server(tmp_path)
    appels = []

    def llm(messages, *a, **k):
        appels.append(messages)
        return '{"conclusion": "Les écarts semblent faibles.", "lecture": [], "prudence": [], "a_verifier": []}'
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "x", "model": "m"})
    monkeypatch.setattr(server, "_llm_call", llm)
    assert server._lecture_experte("tests")["conclusion"] == "Les écarts semblent faibles."
    assert server._lecture_experte("tests")["conclusion"] == "Les écarts semblent faibles."
    assert len(appels) == 1 and "colonnes_testees" in appels[0][1]["content"]


def test_apres_redemarrage_la_lecture_des_tests_reste_possible(tmp_path, monkeypatch):
    """La page garde l'analyse, le serveur l'a perdue : il la recalcule depuis le fichier."""
    server = _server(tmp_path)
    p = str(tmp_path / "ventes.csv")
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "x", "model": "m"})
    monkeypatch.setattr(server, "_llm_call", lambda *a, **k: '{"conclusion": "Les écarts semblent faibles."}')
    etat = server.T.state()
    etat.update(last_result=None, result_path=None)
    etat.pop("lectures_expertes", None)
    assert server._lecture_experte("tests") is None
    assert server._lecture_experte("tests", p)["conclusion"] == "Les écarts semblent faibles."
    assert etat["result_path"] == p


def test_lecture_des_tests_suit_le_fichier_affiche(tmp_path, monkeypatch):
    server = _server(tmp_path)
    autre = tmp_path / "autre.csv"
    rng = np.random.default_rng(3)
    pd.DataFrame({"ville": rng.choice(["Man", "Daloa"], 120), "poids": rng.normal(5, 1, 120), "taille": rng.normal(170, 9, 120)}).to_csv(autre, index=False)
    envoye = []

    def llm(messages, *a, **k):
        envoye.append(messages[1]["content"])
        return '{"conclusion": "Les écarts semblent faibles."}'
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "x", "model": "m"})
    monkeypatch.setattr(server, "_llm_call", llm)
    server._lecture_experte("tests", str(autre))
    assert "poids" in envoye[0] and "quantite" not in envoye[0]
