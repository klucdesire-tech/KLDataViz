# -*- coding: utf-8 -*-
"""Dictionnaire d'indicateurs de l'espace : import, isolation par espace, et calculs du tableau de bord
fidèles au dictionnaire (données fictives uniquement)."""
import io
import os
import sys
import tempfile

import pandas as pd
import pytest

from _harness import reset_app_modules

DICO = pd.DataFrame([
    {"Domaine": "Traitement", "Code": "TX_CURR", "Indicateur": "Nombre de patients actuellement sous TARV",
     "Type": "Nombre", "Calcul du total annuel": "Indicateur instantané (snapshot) : total annuel = valeur T4"},
    {"Domaine": "Suppression virale", "Code": "TX_PVLS", "Indicateur": "% de patients avec charge virale supprimée",
     "Type": "Pourcentage", "Calcul du total annuel": "Indicateur instantané (snapshot) : valeur de fin de période"},
    {"Domaine": "Dépistage", "Code": "HTS_TST", "Indicateur": "Nombre de personnes dépistées",
     "Type": "Nombre", "Calcul du total annuel": "Somme des résultats trimestriels (T1+T2+T3+T4)"},
])


def _xlsx(df=DICO):
    b = io.BytesIO()
    df.to_excel(b, index=False)
    return b.getvalue()


def _long():
    """Format long fictif : 2 sites × 2 trimestres, périodes en toutes lettres (ordre alphabétique ≠ chronologique)."""
    rows = []
    for per, k in (("T4 2025", 1), ("T3 2025", 0)):
        for site, base in (("Site A", 100), ("Site B", 200)):
            rows += [{"site": site, "periode": per, "indicateur": "TX_CURR", "valeur": base + 10 * k},
                     {"site": site, "periode": per, "indicateur": "TX_PVLS", "valeur": 80 + 4 * k + (base == 200) * 2},
                     {"site": site, "periode": per, "indicateur": "HTS_TST", "valeur": 50}]
    return pd.DataFrame(rows)


def test_import_info_et_retrait():
    reset_app_modules()
    import dictionnaire  # noqa: PLC0415
    assert dictionnaire.info() == {"actif": False} and dictionnaire.charger() == []
    out = dictionnaire.importer(_xlsx(), "indicateurs_mer.xlsx")
    assert out["actif"] and out["indicateurs"] == 3 and out["domaines"]["Traitement"] == 1
    assert {i["code"] for i in dictionnaire.charger()} == {"TX_CURR", "TX_PVLS", "HTS_TST"}
    assert dictionnaire.supprimer() == {"actif": False}


def test_fichier_invalide_refuse():
    reset_app_modules()
    import dictionnaire  # noqa: PLC0415
    with pytest.raises(ValueError):
        dictionnaire.importer(b"x", "dico.pdf")
    with pytest.raises(ValueError, match="Aucun indicateur"):
        dictionnaire.importer(_xlsx(pd.DataFrame({"autre": [1, 2]})), "vide.xlsx")
    assert dictionnaire.charger() == []


def test_chaque_espace_a_son_dictionnaire(monkeypatch):
    reset_app_modules()
    import tenancy as T  # noqa: PLC0415
    import dictionnaire  # noqa: PLC0415
    monkeypatch.delenv("KLDV_DICT_DIR", raising=False)
    da, db = tempfile.mkdtemp(), tempfile.mkdtemp()

    class Esp:
        def __init__(self, d):
            self.d = d

        def dir(self, name):
            return os.path.join(self.d, name)

    monkeypatch.setattr(T, "current", lambda: Esp(da))
    dictionnaire.importer(_xlsx(), "mer.xlsx")
    monkeypatch.setattr(T, "current", lambda: Esp(db))
    assert dictionnaire.charger() == [] and dictionnaire.info()["actif"] is False


def test_calculs_fideles_au_dictionnaire():
    reset_app_modules()
    import dictionnaire  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(dictionnaire.__file__), "..", "HERMES_DA"))
    from hermes_da import indicators_catalog as cat  # noqa: PLC0415
    dictionnaire.importer(_xlsx(), "mer.xlsx")
    res = {r["code"]: r for r in cat.match_and_compute(_long(), dictionnaire.charger())}
    assert res["TX_CURR"]["valeur"] == 320.0      # instantané : 110 + 210 au dernier trimestre (T4), pas la somme
    assert res["TX_PVLS"]["valeur"] == 85.0       # taux : moyenne des sites au T4 (84, 86), jamais additionné
    assert res["HTS_TST"]["valeur"] == 200.0      # flux : somme des 2 trimestres × 2 sites


def test_tableau_de_bord_utilise_le_dictionnaire_de_l_espace(monkeypatch):
    reset_app_modules()
    import server  # noqa: PLC0415
    monkeypatch.setattr(server, "_llm_config", lambda: None)  # règles seules : aucun appel réseau
    with tempfile.TemporaryDirectory() as d:
        sys.path.insert(0, os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA"))
        from hermes_da import config as hconfig  # noqa: PLC0415
        monkeypatch.setattr(hconfig, "CONSOLE_DIR", type(hconfig.CONSOLE_DIR)(d))
        import dictionnaire  # noqa: PLC0415
        out = server.build_dashboard(_long())
        assert out.get("dictionnaire") is None          # sans dictionnaire : normes intégrées (Mode A)
        dictionnaire.importer(_xlsx(), "mer.xlsx")
        out = server.build_dashboard(_long())
        trouves = {r["code"]: r["valeur"] for r in out["dictionnaire"]["trouves"]}
        assert trouves == {"TX_CURR": 320.0, "TX_PVLS": 85.0, "HTS_TST": 200.0}
        assert "Indicateurs du dictionnaire" in out["html"]


def test_ia_ne_recoit_aucune_valeur_brute():
    reset_app_modules()
    import dictionnaire  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(dictionnaire.__file__), "..", "HERMES_DA"))
    from hermes_da import indicators_catalog as cat  # noqa: PLC0415
    vus = []
    df = pd.DataFrame({"code_patient": [f"P{i:03}" for i in range(4)], "nom": ["Aya", "Koffi", "Awa", "Yao"],
                       "statut": ["SUPPRIME", "SUPPRIME", "NON-SUPPRIME", "SUPPRIME"], "age": [34, 51, 28, 45]})
    inds = [{"code": "TX_PVLS", "libelle": "charge virale supprimée", "type": "Pourcentage"}]
    cat.match_and_compute_llm(df, inds, lambda m, n: vus.append(m[1]["content"]) or '{"matches":[]}')
    envoye = vus[0]
    for secret in ("P000", "Aya", "Koffi", "34", "SUPPRIME"):  # 4 lignes : même le statut ne part pas
        assert secret not in envoye


def test_format_large_une_colonne_par_code():
    reset_app_modules()
    import dictionnaire  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(dictionnaire.__file__), "..", "HERMES_DA"))
    from hermes_da import indicators_catalog as cat  # noqa: PLC0415
    large = _long().pivot_table(index=["site", "periode"], columns="indicateur", values="valeur").reset_index()
    dictionnaire.importer(_xlsx(), "mer.xlsx")
    res = {r["code"]: r["valeur"] for r in cat.match_and_compute(large, dictionnaire.charger()) if r["present"]}
    assert res == {"TX_CURR": 320.0, "TX_PVLS": 85.0, "HTS_TST": 200.0}


def test_l_ia_ne_remplace_pas_un_calcul_exact():
    """Cas vu en vrai : l'IA répond « somme de valeur » pour chaque code ; le calcul exact doit rester."""
    reset_app_modules()
    import dictionnaire  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(dictionnaire.__file__), "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import generate_dashboard  # noqa: PLC0415
    dictionnaire.importer(_xlsx(), "mer.xlsx")
    appels = []

    def ia_naive(messages, max_tokens):
        appels.append(messages[0]["content"][:40])
        return ('{"matches":[' + ",".join(f'{{"code":"{c}","present":true,"colonne":"valeur","calcul":"somme"}}'
                                          for c in ("TX_CURR", "TX_PVLS", "HTS_TST")) + "]}")
    out = generate_dashboard(_long(), "test", llm=ia_naive, dictionnaire=dictionnaire.charger())
    trouves = {r["code"]: r["valeur"] for r in out["dictionnaire"]["trouves"]}
    assert trouves == {"TX_CURR": 320.0, "TX_PVLS": 85.0, "HTS_TST": 200.0}
    assert not any("dictionnaire d'indicateurs" in a for a in appels)  # codes explicites : l'IA n'est pas consultée


def _cat_et_dico():
    reset_app_modules()
    import dictionnaire  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(dictionnaire.__file__), "..", "HERMES_DA"))
    from hermes_da import indicators_catalog as cat  # noqa: PLC0415
    dictionnaire.importer(_xlsx(), "mer.xlsx")
    return cat, dictionnaire.charger()


def test_relecture_hermes_format_large_annee_numerique():
    # 2 sites × 2 ans, année numérique : la période ne doit pas disparaître au dépivotage
    cat, dico = _cat_et_dico()
    large = pd.DataFrame({"site": ["A", "B", "A", "B"], "annee": [2024, 2024, 2025, 2025],
                          "TX_CURR": [50, 60, 62, 70], "TX_PVLS": [70, 65, 68, 67]})
    res = {r["code"]: r["valeur"] for r in cat.match_and_compute(large, dico) if r["present"]}
    assert res["TX_CURR"] == 132.0   # 62 + 70 en 2025, pas la somme des 4 lignes (242)
    assert res["TX_PVLS"] == 67.5    # moyenne des sites en 2025


def test_relecture_hermes_colonne_code_d_une_liste_patients():
    # « code » = code patient et « total » = un montant : ce n'est pas un export d'indicateurs
    cat, dico = _cat_et_dico()
    df = pd.DataFrame({"code": [f"P{i:03}" for i in range(12)], "total": range(12),
                       "statut": ["SUPPRIME"] * 9 + ["NON SUPPRIME"] * 3})
    assert cat._as_long(df, dico)[1] is None
    seen = []
    cat.match_and_compute_llm(df, dico, lambda m, n: seen.append(1) or '{"matches":[]}')
    assert seen, "le LLM doit rester disponible pour ce cas"


def test_taux_pondere_numerateur_denominateur():
    cat, dico = _cat_et_dico()
    df = pd.DataFrame({"site": ["A", "A", "B", "B"], "periode": ["2025-10"] * 4,
                       "indicateur": ["TX_PVLS (N, DSD)", "TX_PVLS (D, DSD)", "TX_PVLS_N", "TX_PVLS_D"],
                       "valeur": [900, 1000, 5, 10]})
    r = {x["code"]: x for x in cat.match_and_compute(df, dico)}["TX_PVLS"]
    assert r["valeur"] == 89.6        # 905 / 1010, pas la moyenne simple de 90 % et 50 % (70 %)


def test_tx_curr_ne_prend_pas_tx_curr_nat():
    cat, dico = _cat_et_dico()
    df = pd.DataFrame({"indicateur": ["TX_CURR_NAT"], "periode": ["2025"], "valeur": [99]})
    r = {x["code"]: x for x in cat.match_and_compute(df, dico)}["TX_CURR"]
    assert r["present"] is False


def test_categorie_envoyee_seulement_si_valeurs_repetees():
    cat, dico = _cat_et_dico()
    vus = []
    df = pd.DataFrame({"statut": ["S"] * 3 + ["NS"] * 6, "nom": [f"N{i}" for i in range(9)],
                       "rare": ["X"] * 8 + ["Y"]})
    cat.match_and_compute_llm(df, dico, lambda m, n: vus.append(m[1]["content"]) or '{"matches":[]}')
    import json  # noqa: PLC0415
    cols = {c["nom"]: c for c in json.loads(vus[0])["colonnes"]}
    assert sorted(cols["statut"]["categorie"]) == ["NS", "S"]
    assert "categorie" not in cols["nom"] and "categorie" not in cols["rare"]  # valeurs uniques : jamais envoyées


def test_echec_du_dictionnaire_journalise(capsys, monkeypatch):
    cat, dico = _cat_et_dico()
    from hermes_da.adapters.dashboard import generate_dashboard  # noqa: PLC0415

    def casse(*a, **k):
        raise RuntimeError("panne simulée")
    monkeypatch.setattr(cat, "match_and_compute", casse)
    out = generate_dashboard(_long(), "test", llm=False, dictionnaire=dico)
    assert "<html" in out["html"].lower() and out["dictionnaire"] is None   # le tableau de bord reste affiché
    assert "panne simulée" in capsys.readouterr().err


def test_import_trop_volumineux_refuse_avant_decodage():
    reset_app_modules()
    import server  # noqa: PLC0415
    from _harness import start, call  # noqa: PLC0415
    httpd, base = start(server)
    try:
        code, rep = call(base, "/api/dictionnaire/import", {"name": "gros.xlsx", "data": "A" * (1500 * 1024)})
        assert code == 400 and "1 Mo" in rep["error"]
    finally:
        httpd.shutdown()


def test_liste_patients_oui_non_et_variante_nationale_unique():
    """Colonne Oui/Non nommée « cv_supprimee » → TX_PVLS ; sa variante nationale identique n'est pas affichée en double."""
    reset_app_modules()
    import dictionnaire  # noqa: PLC0415
    sys.path.insert(0, os.path.join(os.path.dirname(dictionnaire.__file__), "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import generate_dashboard  # noqa: PLC0415
    dico = DICO.copy()
    dico.loc[len(dico)] = {"Domaine": "Pays", "Code": "VL_SUPPRESSION_NAT", "Indicateur": "% de PVVIH ayant une charge virale supprimée",
                           "Type": "Pourcentage", "Calcul du total annuel": "Annuel"}
    dictionnaire.importer(_xlsx(dico), "mer.xlsx")
    n = 40
    df = pd.DataFrame({"id_patient": [f"P{i}" for i in range(n)], "sexe": ["F", "M"] * (n // 2),
                       "statut_actuel": ["Actif"] * 30 + ["Arrêt de traitement"] * 10,
                       "cv_supprimee": ["Oui"] * 30 + ["Non"] * 10})
    out = generate_dashboard(df, "test", llm=False, dictionnaire=dictionnaire.charger())
    assert [(r["code"], r["valeur"]) for r in out["dictionnaire"]["trouves"]] == [("TX_PVLS", 75.0)]
