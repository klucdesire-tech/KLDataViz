# -*- coding: utf-8 -*-
"""Tests statistiques et prévisions dans les rapports — données fictives, IA simulée."""
import os
import sys
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inferential as I
import interpretation as L
import predictive as P
import rapport_analyses as RA

EXPERTE = {"conclusion": "Les écarts entre zones semblent nets.", "lecture": ["La zone Nord se détache."], "prudence": ["Un lien n'est pas une cause."], "a_verifier": ["Contrôler la zone Nord."]}


def _ventes():
    rng = np.random.default_rng(1)
    lignes = []
    for m, p in enumerate(pd.period_range("2023-01", periods=30, freq="M").astype(str)):
        for j in range(1, 11):
            zone = "Nord" if j % 2 else "Sud"
            lignes.append({"date": f"{p}-{j * 2:02d}", "zone": zone, "ventes_fcfa": 1000 - 8 * m + (120 if zone == "Nord" else 0) + rng.normal(0, 10), "poids": rng.normal(5, 1)})
    return pd.DataFrame(lignes)


def _inf():
    inf = I.analyse(_ventes())
    return dict(inf, lecture=L.tests(inf))


def test_tests_avec_lecture_d_adifa():
    b = RA.tests(_inf(), EXPERTE)
    assert b["conclusion"] == EXPERTE["conclusion"] and b["source"].startswith("Lecture d'Adifa")
    assert b["colonnes"] == RA.ENTETES_TESTS and len(b["lignes"]) == b["n_tests"]
    assert b["lignes"][0][-1] != "Peut venir du hasard"


def test_tests_sans_ia_gardent_la_lecture_du_moteur():
    b = RA.tests(_inf())
    assert b["source"] == "Lecture écrite par le calcul" and b["conclusion"]


def test_tests_absents():
    assert RA.tests(None) is None
    assert RA.tests({"n_tests": 0, "n_structure": 0}) is None


@pytest.mark.parametrize("x,dep,attendu", [({"significatif": False}, False, "Peut venir du hasard"), ({"significatif": True, "force": "forte"}, True, "À vérifier"),
                                           ({"significatif": True, "attendus_faibles": True}, False, "À vérifier"), ({"significatif": True, "force": "négligeable"}, False, "Lien très faible"),
                                           ({"significatif": True, "force": "forte"}, False, "Lien probable")])
def test_conclusion_comme_a_l_ecran(x, dep, attendu):
    assert RA.conclusion_test(x, dep) == attendu


def test_prevision_avec_mois_et_fourchette():
    r = P.analyse(_ventes(), cible="ventes_fcfa", periode="date", horizon=3)
    b = RA.prediction({"resultat": r})
    assert b["titre"] == "Prévision : ventes" and len(b["lignes"]) == 3
    assert "2025" in b["lignes"][0][0] or "2026" in b["lignes"][0][0]
    assert b["lignes"][0][1].endswith("FCFA") and b["metriques"][0][0] == "Fiabilité"


def test_regression_liste_les_effets():
    rng = np.random.default_rng(2)
    df = pd.DataFrame({"a": rng.normal(0, 1, 300), "b": rng.normal(0, 1, 300)})
    df["y"] = 4 * df["a"] + rng.normal(0, 1, 300)
    b = RA.prediction({"resultat": P.analyse(df, cible="y", predicteurs=["a", "b"])})
    assert b["colonnes"][0] == "Colonne" and {x[0]: x[-1] for x in b["lignes"]} == {"a": "net", "b": "non net"}


def test_logistique():
    res = {"type": "logistique", "etat": "utilisable", "n_test": 90, "positifs_test": 20, "vrais_positifs_test": 12, "auc_test": 0.8,
           "matrice_confusion": {"faux_positifs": 3}, "coefficients": [{"variable": "(constante)", "coef_standardise": 0.1}, {"variable": "age", "coef_standardise": -0.9}]}
    b = RA.prediction({"resultat": {"cible": "abandon", "resultat": res}})
    assert ["Cas « Oui » retrouvés", "12 sur 20"] in b["metriques"] and b["lignes"] == [["age", "le Oui devient moins probable"]]


def test_prediction_en_erreur_ignoree():
    assert RA.prediction({"resultat": {"cible": "x", "resultat": {"erreur": "trop court"}}}) is None
    assert RA.prediction({"resultat": {"cible": "x", "resultat": None}}) is None


def test_contenu_rapport_planifie_sans_prevision():
    c = RA.contenu({"inference": _inf()})
    assert c["tests"] and c["predictions"] == []
    assert RA.contenu({}) == {}
    assert RA.contenu({"_analyses": {"tests": None, "predictions": [1]}}) == {"tests": None, "predictions": [1]}


# ---------- exports ----------

@pytest.fixture
def serveur(tmp_path, monkeypatch):
    from _harness import reset_app_modules
    reset_app_modules()
    import server
    p = tmp_path / "ventes.csv"
    df = _ventes()
    df.to_csv(p, index=False)
    monkeypatch.setattr(server, "_llm_config", lambda: None)
    st = server.T.state()
    st.update(last_result=server.analyze(str(p)), result_path=str(p), predictifs=[], lectures_expertes={})
    resultat = P.analyse(df, cible="ventes_fcfa", periode="date", horizon=3)
    server._garder_predictif(st, {"fichier": str(p), "resultat": resultat, "colonnes": list(df.columns), "dependantes": False})
    return server, st, str(p)


def test_rapport_reprend_tests_et_prevision(serveur):
    server, st, _ = serveur
    r = server._analyses_pour_rapport(st["last_result"], st)
    assert r["_analyses"]["tests"]["n_tests"] > 0
    assert [b["titre"] for b in r["_analyses"]["predictions"]] == ["Prévision : ventes"]
    assert "_analyses" not in st["last_result"]


def test_prevision_d_un_autre_fichier_ignoree(serveur):
    server, st, _ = serveur
    st["predictifs"][0]["fichier"] = "autre.csv"
    assert server._analyses_pour_rapport(st["last_result"], st)["_analyses"]["predictions"] == []


def test_lecture_deja_obtenue_reprise_sans_nouvel_appel(serveur, monkeypatch):
    import lecture_experte as LE
    server, st, _ = serveur
    e = st["predictifs"][0]
    st["lectures_expertes"][server._cle_lecture(LE.faits_predictif(e["resultat"], e["colonnes"], e["dependantes"]))] = EXPERTE
    b = server._analyses_pour_rapport(st["last_result"], st)["_analyses"]["predictions"][0]
    assert b["conclusion"] == EXPERTE["conclusion"]


def test_analyse_refaite_remplace_la_precedente(serveur):
    server, st, p = serveur
    n = len(st["predictifs"])
    server._garder_predictif(st, {"fichier": p, "resultat": dict(st["predictifs"][0]["resultat"]), "colonnes": [], "dependantes": False})
    assert len(st["predictifs"]) == n


def test_sans_acces_rien_n_est_ajoute(serveur, monkeypatch):
    server, st, _ = serveur
    monkeypatch.setattr(server, "_predictif_actif", lambda: False)
    assert "_analyses" not in server._analyses_pour_rapport(st["last_result"], st)


def test_panne_ne_bloque_pas_le_rapport(serveur, monkeypatch):
    server, st, _ = serveur
    monkeypatch.setattr(server, "_lecture_experte", lambda *a, **k: 1 / 0)
    assert server._analyses_pour_rapport(st["last_result"], st) is st["last_result"]


def test_export_excel(serveur):
    server, st, _ = serveur
    fn = server.export_excel(server._analyses_pour_rapport(st["last_result"], st))
    wb = openpyxl.load_workbook(os.path.join(server.T.exports_dir(), fn))
    assert {"Lecture statistique", "Tests statistiques", "Prédiction 1"} <= set(wb.sheetnames)
    assert wb["Tests statistiques"]["A1"].value == "Ce qui est comparé" and wb["Prédiction 1"]["A1"].value == "Mois"


def test_export_html_echappe(serveur):
    server, st, _ = serveur
    r = server._analyses_pour_rapport(st["last_result"], st)
    r["_analyses"]["tests"]["conclusion"] = "<script>alert(1)</script>"
    fn = server.export_html(r)
    html = open(os.path.join(server.T.exports_dir(), fn), encoding="utf-8").read()
    assert "Tests statistiques" in html and "Prévision : ventes" in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def _texte_pdf(pdf_report, result, tmp_path, monkeypatch):
    """Le texte du rapport, relevé avant la mise en page."""
    vu = {}

    class Doc:
        def __init__(self, *a, **k):
            pass

        def build(self, story, **k):
            vu["story"] = story
    monkeypatch.setattr(pdf_report, "SimpleDocTemplate", Doc)
    pdf_report.build(result, str(tmp_path / "r.pdf"))
    return " ".join(x.getPlainText() for x in vu["story"] if hasattr(x, "getPlainText"))


def test_export_pdf_numerote_les_sections(serveur, tmp_path, monkeypatch):
    import pdf_report
    server, st, _ = serveur
    texte = _texte_pdf(pdf_report, server._analyses_pour_rapport(st["last_result"], st), tmp_path, monkeypatch)
    assert "7. Tests statistiques et prévisions" in texte and "8. Valeurs hors norme" in texte and "9. Méthodologie" in texte
    assert "Prévision : ventes" in texte and "Valeur p corrigée" in texte


def test_export_pdf_sans_analyses_garde_ses_numeros(serveur, tmp_path, monkeypatch):
    import pdf_report
    server, st, _ = serveur
    r = {k: v for k, v in st["last_result"].items() if k != "inference"}
    texte = _texte_pdf(pdf_report, r, tmp_path, monkeypatch)
    assert "7. Valeurs hors norme" in texte and "8. Méthodologie" in texte and "Valeur p corrigée" not in texte


def test_pdf_reel_se_construit(serveur):
    server, st, _ = serveur
    fn = server.export_pdf(server._analyses_pour_rapport(st["last_result"], st))
    assert open(os.path.join(server.T.exports_dir(), fn), "rb").read(4) == b"%PDF"


def test_carte_predictive_repart_de_zero():
    """Page : à chaque nouvelles données, résultat effacé, calculs en cours ignorés, carte rechargée."""
    html = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
    assert "if(state.predFor!==currentDataPath()){state.predFor=currentDataPath();predReset();predLoad()}" in html
    assert "Object.assign(state,{predFor:null,path,name" in html
    assert "if(tour!==predTour)return;" in html
