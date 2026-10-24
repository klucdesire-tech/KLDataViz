# -*- coding: utf-8 -*-
"""Correspondance dictionnaire ↔ colonnes côté console : propositions (règles + IA vérifiée), enregistrement,
route qui régénère le tableau de bord, et parité du calcul Python / page (evalF). Données fictives."""
import json
import os
import re
import shutil
import subprocess
import sys

import pandas as pd
import pytest

from _harness import call, reset_app_modules, start

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "..", "HERMES_DA"))

DICO = [{"code": "TX_CURR", "libelle": "Nombre de patients actuellement sous TARV", "type": "Nombre", "total": ""},
        {"code": "TX_PVLS", "libelle": "% de patients avec charge virale supprimée", "type": "Pourcentage", "total": ""},
        {"code": "HTS_POS", "libelle": "Nombre de personnes dépistées positives", "type": "Nombre", "total": ""}]


def _df(n=40):
    return pd.DataFrame({"id_patient": [f"P{i:03}" for i in range(n)], "region": ["Nawa", "Gbôklé"] * (n // 2),
                         "mois": ["2025-01", "2025-02"] * (n // 2), "statut": ["Actif"] * 30 + ["Perdu de vue"] * 10,
                         "resultat_cv": ["Supprimé"] * 25 + ["Non supprimé"] * 15,
                         "resultat_test": ["Positif"] * 8 + ["Négatif"] * 32})


def test_propositions_regles_puis_ia_verifiee():
    reset_app_modules()
    import correspondances  # noqa: PLC0415
    vus = []

    def ia(messages, n):
        vus.append(messages[1]["content"])
        return json.dumps({"indicateurs": [
            {"code": "HTS_POS", "formule": {"mode": "comptage", "numerateur": {"colonne": "resultat_test", "valeurs": ["Positif"]}},
             "evolution": True, "explication": "résultat du test"},
            {"code": "TX_PVLS", "formule": {"mode": "comptage", "numerateur": {"colonne": "inventee", "valeurs": ["x"]}}}]})
    corr = correspondances.obtenir(_df(), DICO, ia, lambda t: json.loads(t))
    par = {i["code"]: i for i in corr["indicateurs"]}
    assert par["TX_CURR"]["source"] == "regles" and par["HTS_POS"]["source"] == "ia"
    assert all(i["statut"] == "propose" for i in corr["indicateurs"])
    # l'IA ne reçoit ni identifiant ni valeur individuelle
    assert "P001" not in vus[0] and "Positif" in vus[0]
    # mémorisée : un 2e appel ne redemande rien à l'IA
    correspondances.obtenir(_df(), DICO, ia, lambda t: json.loads(t))
    assert len(vus) == 1


def test_enregistrer_verifie_les_formules():
    reset_app_modules()
    import correspondances  # noqa: PLC0415
    df = _df()
    correspondances.obtenir(df, DICO)
    with pytest.raises(ValueError, match="aucune des valeurs"):
        correspondances.enregistrer(df, {"indicateurs": [{"code": "TX_CURR", "statut": "valide",
                                         "formule": {"mode": "comptage", "numerateur": {"colonne": "statut", "valeurs": ["Inexistant"]}}}]}, DICO)
    corr = correspondances.enregistrer(df, {"indicateurs": [{"code": "TX_CURR", "statut": "valide"}], "periode": "mois"}, DICO)
    assert next(i for i in corr["indicateurs"] if i["code"] == "TX_CURR")["statut"] == "valide" and corr["periode"] == "mois"


def test_route_enregistrer_regenere_le_tableau(tmp_path):
    reset_app_modules()
    import server  # noqa: PLC0415
    import dictionnaire  # noqa: PLC0415
    server._llm_config = lambda: None
    p = tmp_path / "patients.csv"
    _df().to_csv(p, index=False)
    b = tmp_path / "dico.csv"
    pd.DataFrame([{"Code": d["code"], "Indicateur": d["libelle"], "Type": d["type"]} for d in DICO]).to_csv(b, index=False)
    dictionnaire.importer(b.read_bytes(), "dico.csv")
    httpd, base = start(server)
    try:
        code, r = call(base, "/api/correspondance", {"file_path": str(p)})
        assert code == 200 and {i["code"] for i in r["correspondance"]["indicateurs"]} >= {"TX_CURR", "TX_PVLS"}
        code, r = call(base, "/api/correspondance/enregistrer",
                       {"file_path": str(p), "indicateurs": [{"code": "TX_CURR", "statut": "valide"}]})
        assert code == 200 and "TX_CURR" in r["html"] and "evalF" in r["html"]
        tx = next(i for i in r["correspondance"]["indicateurs"] if i["code"] == "TX_CURR")
        assert tx["statut"] == "valide" and tx["valeur"] == 30
    finally:
        httpd.shutdown()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js absent : parité vérifiée par la CI")
def test_parite_python_et_page(tmp_path):
    """La même formule donne le même résultat en Python (tuile, aperçu) et dans la page (filtres)."""
    from hermes_da import correspondance as C  # noqa: PLC0415
    from hermes_da.adapters.dashboard import TEMPLATE  # noqa: PLC0415
    js = TEMPLATE[TEMPLATE.index("var NULLS="):]
    morceaux = [re.search(r"var NULLS=.*?\n", js).group(0), re.search(r"function isNull\(.*?\n", js).group(0),
                re.search(r"function num\(.*?\n", js).group(0), re.search(r"function groupBy\(.*?\n", js).group(0)]
    a, b = js.index("function condF("), js.index("function renderKpis(")
    df = _df().assign(cv=["Oui"] * 20 + ["Non"] * 15 + ["NA"] * 5, v=[i * 1.37 for i in range(40)],
                      zone=["Nord", "Sud", "NA", "nan"] * 10)
    df["__p"] = df["mois"]
    formules = [
        {"mode": "comptage", "numerateur": {"colonne": "statut", "valeurs": ["actif"]}},
        {"mode": "taux", "numerateur": {"colonne": "cv", "valeurs": ["Oui"]}},
        {"mode": "taux", "numerateur": {"colonne": "resultat_cv", "valeurs": ["Supprimé"]},
         "denominateur": {"colonne": "resultat_cv", "valeurs": ["Supprimé", "Non supprimé"]}},
        {"mode": "somme", "colonne": "v", "numerateur": {"colonne": "region", "valeurs": ["Nawa"]}},
        {"mode": "moyenne", "colonne": "v"}, {"mode": "mediane", "colonne": "v"},
        {"mode": "somme", "colonne": "v", "stock": True},
        {"mode": "comptage", "numerateur": {"colonne": "cv", "valeurs": ["Oui", "NA"]}},
        {"mode": "moyenne", "colonne": "v", "filtre": {"colonne": "zone", "valeurs": ["Nord", "NA"]}},
        {"mode": "rapport", "colonne": "v", "numerateur": {"colonne": "zone", "valeurs": ["Nord"]},
         "denominateur": {"colonne": "zone", "valeurs": ["Nord", "Sud"]}},
    ]
    script = "".join(morceaux) + js[a:b] + "var rows=" + df.to_json(orient="records", force_ascii=False) + ";" \
        + "console.log(JSON.stringify(" + json.dumps(formules, ensure_ascii=False) + ".map(function(f){return evalF(rows,f)})));"
    f = tmp_path / "parite.js"
    f.write_text(script, encoding="utf-8")
    page = json.loads(subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8").stdout)
    python = [C.evaluer(df, fo, df["__p"]) for fo in formules]
    for fo, x, y in zip(formules, python, page):
        assert x == pytest.approx(y, rel=1e-9), (fo, x, y)


def test_signature_suit_le_dictionnaire_et_enregistrer_controle():
    reset_app_modules()
    import correspondances  # noqa: PLC0415
    df = _df()
    assert correspondances.signature(df, DICO) != correspondances.signature(df, DICO[:2])
    correspondances.obtenir(df, DICO)
    with pytest.raises(ValueError, match="absent du dictionnaire"):
        correspondances.enregistrer(df, {"indicateurs": [{"code": "INVENTE", "statut": "propose"}]}, DICO)
    with pytest.raises(ValueError, match="Pourcentage"):  # un % calculé comme un nombre
        correspondances.enregistrer(df, {"indicateurs": [{"code": "TX_PVLS", "statut": "valide", "formule":
                                    {"mode": "comptage", "numerateur": {"colonne": "resultat_cv", "valeurs": ["Supprimé"]}}}]}, DICO)


def test_ia_export_long_comptage_de_lignes_ecarte():
    reset_app_modules()
    import correspondances  # noqa: PLC0415
    d = pd.DataFrame({"indicateur": ["CV supprimée", "CV réalisée"] * 3, "periode": ["2025-01"] * 6, "valeur": [8, 10, 9, 10, 7, 10]})
    dico = [{"code": "TX_PVLS", "libelle": "% CV supprimée", "type": "Pourcentage"}]

    def ia(messages, n):
        return json.dumps({"indicateurs": [{"code": "TX_PVLS", "formule": {"mode": "taux",
                           "numerateur": {"colonne": "indicateur", "valeurs": ["CV supprimée"]}}}]})
    assert correspondances._proposer_ia(d, dico, {"identifiants": []} | __import__("hermes_da.correspondance",
                                        fromlist=["roles"]).roles(d), ia, json.loads) == []


def test_dictionnaire_sans_type_et_ia_comptage_nu_ecarte():
    reset_app_modules()
    import correspondances  # noqa: PLC0415
    df = _df()
    sans_type = [dict(d, type="") for d in DICO]
    correspondances.obtenir(df, sans_type)
    corr = correspondances.enregistrer(df, {"indicateurs": [{"code": "TX_PVLS", "statut": "valide", "formule":
                                       {"mode": "taux", "numerateur": {"colonne": "resultat_cv", "valeurs": ["Supprimé"]}}}]}, sans_type)
    assert next(i for i in corr["indicateurs"] if i["code"] == "TX_PVLS")["statut"] == "valide"
    from hermes_da import correspondance as C  # noqa: PLC0415
    d = pd.DataFrame({"dataname": ["CV supprimée", "CV réalisée"] * 3, "value": [8, 10, 9, 10, 7, 10]})
    ia = lambda m, n: json.dumps({"indicateurs": [{"code": "HTS_POS", "formule": {"mode": "comptage"}}]})  # noqa: E731
    assert correspondances._proposer_ia(d, DICO, C.roles(d), ia, json.loads) == []
