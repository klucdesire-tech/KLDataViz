# -*- coding: utf-8 -*-
"""Moteur de correspondance dictionnaire ↔ colonnes (hermes_da.correspondance) : formules, rôles, propositions."""
import pandas as pd

from hermes_da import correspondance as C

MER = [
    {"domaine": "Traitement", "code": "TX_CURR", "libelle": "Nombre de patients actuellement sous TARV", "type": "Nombre",
     "total": "Indicateur instantané (snapshot) : total annuel = valeur T4"},
    {"domaine": "Traitement", "code": "TX_ML", "libelle": "Nombre de patients sans contact clinique", "type": "Nombre", "total": ""},
    {"domaine": "Suppression virale", "code": "TX_PVLS", "libelle": "% de patients avec charge virale supprimée",
     "type": "Pourcentage", "total": "Indicateur instantané (snapshot) : valeur de fin de période"},
    {"domaine": "Pays", "code": "VL_SUPPRESSION_NAT", "libelle": "% de PVVIH avec charge virale supprimée", "type": "Pourcentage", "total": ""},
    {"domaine": "Prévention", "code": "TB_PREV", "libelle": "% de patients ayant achevé le TPT", "type": "Pourcentage", "total": ""},
]


def _patients(n=40):
    return pd.DataFrame({
        "id_patient": [f"P{i:03}" for i in range(n)],
        "region": ["Nawa", "Août-Région"] * (n // 2),
        "date_prelevement": ["2025-01-05", "2025-02-05", "2025-03-05", "2025-04-05"] * (n // 4),
        "date_naissance": ["1990-01-01"] * n,
        "statut_actuel": ["Actif"] * 30 + ["Perdu de vue"] * 6 + ["Décédé"] * 4,
        "cv_supprimee": ["Oui"] * 28 + ["Non"] * 8 + [None] * 4,
        "tpt_demarre": ["Oui", "Non"] * (n // 2),
    })


def test_modes_de_calcul():
    df = _patients()
    assert C.evaluer(df, {"mode": "comptage", "numerateur": {"colonne": "statut_actuel", "valeurs": ["actif"]}}) == 30
    # taux : les cases vides ne sont ni au numérateur ni au dénominateur (28 / 36)
    assert C.evaluer(df, {"mode": "taux", "numerateur": {"colonne": "cv_supprimee", "valeurs": ["Oui"]}}) == 77.8
    d = pd.DataFrame({"ind": ["N", "D", "N", "D"], "periode": ["2025-01", "2025-01", "2025-02", "2025-02"], "v": [9, 10, 45, 50]})
    f = {"mode": "rapport", "colonne": "v", "numerateur": {"colonne": "ind", "valeurs": ["N"]},
         "denominateur": {"colonne": "ind", "valeurs": ["D"]}}
    assert C.evaluer(d, f) == 90.0
    cle = d["periode"]
    assert C.evaluer(d, dict(f, stock=True), cle) == 90.0           # dernière période : 45 / 50
    s = pd.DataFrame({"p": ["2025-01", "2025-01", "2025-02"], "pop": [100, 200, 330]})
    assert C.evaluer(s, {"mode": "somme", "colonne": "pop", "stock": True}, s["p"]) == 330   # stock : dernier mois
    assert C.evaluer(s, {"mode": "mediane", "colonne": "pop"}) == 200


def test_repartition_et_serie_avec_la_meme_formule():
    df = _patients()
    f = {"mode": "taux", "numerateur": {"colonne": "tpt_demarre", "valeurs": ["Oui"]}}
    par = dict(C.evaluer_par(df, f, "region"))
    assert par == {"Nawa": 100.0, "Août-Région": 0.0}
    cle = C.cle_periode(df, "date_prelevement")
    pts = C.serie(df, f, cle)
    assert [p for p, _ in pts] == ["2025-01", "2025-02", "2025-03", "2025-04"]   # dates ISO lues telles quelles


def test_roles_des_colonnes():
    r = C.roles(_patients())
    assert r["periode"] == "date_prelevement"                       # jamais la date de naissance
    assert r["dimensions"][0] == "region" and "id_patient" in r["identifiants"]
    assert "cv_supprimee" not in r["dimensions"]                    # une colonne Oui/Non est un indicateur
    m = pd.DataFrame({"periodname": ["Août 2025", "Septembre 2025"] * 6, "valeur": range(12)})
    assert C.roles(m)["periode"] == "periodname"                    # mois en lettres, accentués


def test_propositions_par_regles():
    df = _patients()
    props = {p["code"]: p for p in C.proposer_regles(df, MER)}
    assert C.evaluer(df, props["TX_CURR"]["formule"]) == 30 and props["TX_CURR"]["evolution"] is False  # état actuel
    assert C.evaluer(df, props["TX_ML"]["formule"]) == 6
    assert props["TX_PVLS"]["formule"]["mode"] == "taux"
    assert "TB_PREV" not in props                                   # TPT DÉMARRÉ ≠ TB_PREV (achevé, définition MER)
    fini = {p["code"]: p for p in C.proposer_regles(df.assign(tpt_termine=["Oui", "Non"] * 20), MER)}
    assert fini["TB_PREV"]["formule"]["numerateur"]["colonne"] == "tpt_termine"
    assert "VL_SUPPRESSION_NAT" not in props                        # même formule que TX_PVLS : pas de doublon
    for p in props.values():
        assert C.valider(df, p["formule"]) == []


def test_export_long_rapport_et_stock():
    d = pd.DataFrame({"indicateur": ["TX_PVLS (N, DSD)", "TX_PVLS (D, DSD)", "TX_CURR"] * 2,
                      "periode": ["2025-T3"] * 3 + ["2025-T4"] * 3, "valeur": [80, 100, 500, 90, 100, 520]})
    props = {p["code"]: p for p in C.proposer_regles(d, MER)}
    assert props["TX_PVLS"]["formule"]["mode"] == "rapport" and props["TX_CURR"]["formule"]["stock"] is True


def test_validation_et_description():
    df = _patients()
    assert C.valider(df, {"mode": "comptage", "numerateur": {"colonne": "statut_actuel", "valeurs": ["Inventé"]}})
    assert C.valider(df, {"mode": "somme", "colonne": "absente"})
    assert C.valider(df, {"mode": "inconnu"})
    t = C.decrire({"mode": "taux", "numerateur": {"colonne": "cv_supprimee", "valeurs": ["Oui"]}})
    assert "cv supprimee" in t and "%" in t


def test_resume_pour_l_ia_sans_valeur_individuelle():
    cols = {c["nom"]: c for c in C.resume_colonnes(_patients())}
    assert "categories" not in cols["id_patient"] and cols["id_patient"]["role"] == "identifiant"
    assert "categories" not in cols["date_naissance"]
    assert sorted(cols["statut_actuel"]["categories"]) == ["Actif", "Décédé", "Perdu de vue"]


def test_export_long_sans_code_jamais_compte_en_lignes():
    """Export DHIS2 en libellés : aucune règle ne compte des lignes de la colonne des indicateurs."""
    d = pd.DataFrame({"dataname": ["Patients avec CV supprimée", "Patients avec résultat de CV", "Patients sous TARV"] * 2,
                      "periodname": ["Août 2025"] * 3 + ["Septembre 2025"] * 3, "value": [80, 90, 500, 81, 90, 520]})
    for p in C.proposer_regles(d, MER):
        assert p["formule"]["mode"] not in ("comptage", "taux") or p["formule"]["numerateur"]["colonne"] != "dataname"


def test_vides_et_rapport_incomplet():
    df = pd.DataFrame({"cv": ["Oui", "NA", "Non", "nan", "Oui"], "region": ["A", "NA", "B", "A", "B"], "v": [1.25, 2, 3, 4, 5]})
    assert C.evaluer(df, {"mode": "comptage", "numerateur": {"colonne": "cv", "valeurs": ["Oui", "NA"]}}) == 2
    assert C.evaluer(df, {"mode": "moyenne", "colonne": "v"}) == 3.05                # pas d'arrondi caché
    assert "NA" not in dict(C.evaluer_par(df, {"mode": "comptage", "numerateur": {"colonne": "cv", "valeurs": ["Oui"]}}, "region"))
    assert C.valider(df, {"mode": "rapport", "colonne": "v", "numerateur": {"colonne": "cv", "valeurs": ["Oui"]}})
