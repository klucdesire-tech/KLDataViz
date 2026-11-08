# -*- coding: utf-8 -*-
"""Lecture fidèle des valeurs (`lecture.py`) à réponse connue : nombres écrits en texte, ordre du jour et du mois,
dates impossibles. De bout en bout : du fichier CSV jusqu'aux chiffres du tableau de bord et au bilan qualité.
Fichiers entièrement fictifs."""
import datetime as dt
import json
import os
import re
import sys

import pandas as pd
import pytest

from _harness import APP, reset_app_modules

NBSP = " "


@pytest.fixture
def L():
    reset_app_modules()
    import lecture
    return lecture


@pytest.mark.parametrize("texte, attendu", [
    ("1 234,5", 1234.5), (f"1{NBSP}234,5", 1234.5), ("1 234,5", 1234.5), ("12 500 FCFA", 12500), ("F CFA 3 000", 3000),
    ("45,2 %", 45.2), ("1.234,5", 1234.5), ("-12,75", -12.75), ("0,5", 0.5), ("1 000 000", 1000000), ("12,5 €", 12.5),
])
def test_nombres_ecrits_a_la_main(L, texte, attendu):
    df, conv = L.nombres(pd.DataFrame({"x": [texte, "2,5", "3 000"]}))
    assert df["x"].iloc[0] == attendu and conv and conv[0]["colonne"] == "x"


def test_codes_telephones_et_texte_laisses_tels_quels(L):
    df = pd.DataFrame({"code": ["007", "012", "120"], "tel": ["07 07 07 07 07"] * 3, "nom": ["CS A", "CS B", "CS C"],
                       "decimal_anglais": ["1.234", "2.5", "x"]})
    sortie, conv = L.nombres(df)
    assert conv == [] and sortie is df                     # aucune copie : rien n'a changé


@pytest.mark.parametrize("colonne, valeurs", [
    ("v", ["071 234 567", "072 345 678", "073 456 789"]),              # téléphones par groupes de 3 (relecture PR 198)
    ("v", ["0 123 456", "1 234 567", "2 345 678"]),                     # un seul zéro initial suffit
    ("telephone", ["1 234 567", "2 345 678", "3 456 789"]),            # le nom de la colonne le dit
    ("code_site", ["1 234", "2 345", "3 456"]),
    ("numero_cni", ["123 456 789", "234 567 890", "345 678 901"]),
])
def test_telephones_et_codes_jamais_convertis(L, colonne, valeurs):
    df = pd.DataFrame({colonne: valeurs})
    sortie, conv = L.nombres(df)
    assert conv == [] and sortie[colonne].tolist() == valeurs          # zéro initial gardé, pas une mesure


def test_zero_virgule_reste_un_nombre(L):
    df, conv = L.nombres(pd.DataFrame({"taux_moyen": ["0,5", "0,25", "1 200,5"]}))
    assert df["taux_moyen"].tolist() == [0.5, 0.25, 1200.5]


def test_valeur_qui_n_est_pas_un_nombre_ecartee_avec_sa_ligne(L):
    valeurs = [f"1 {100 + i},5" for i in range(30)]
    valeurs[17] = "trois cents"
    valeurs[4] = "NA"                                       # valeur d'attente : vide, pas une erreur
    df, conv = L.nombres(pd.DataFrame({"depenses": valeurs}))
    assert df["depenses"].iloc[0] == 1100.5 and pd.isna(df["depenses"].iloc[17]) and pd.isna(df["depenses"].iloc[4])
    assert conv[0]["convertis"] == 28 and conv[0]["ecartees"] == 1 and conv[0]["exemples_ecartes"] == [(19, "trois cents")]
    assert conv[0]["attentes"] == 1 and conv[0]["exemples_attentes"] == [(6, "NA")]    # « pas de donnée », noté


@pytest.mark.parametrize("dates, ordre", [
    (["03/04/2025", "15/04/2025"], "jour"), (["04/23/2025", "05/01/2025"], "mois"),
    (["04/23/2025", "23/04/2025"], "melange"), (["03/04/2025", "04/05/2025"], "jour"), (["2025-01", "2025-02"], None),
])
def test_ordre_du_jour_et_du_mois(L, dates, ordre):
    assert L.ordre_dates(dates) == ordre


def test_nettoyage_lit_les_dates_a_l_anglaise(L):
    reset_app_modules()
    import modeling
    d = modeling.to_dates(["04/23/2025", "05/01/2025", "12/31/2024"])
    assert [x.strftime("%Y-%m-%d") for x in d] == ["2025-04-23", "2025-05-01", "2024-12-31"]
    d = modeling.to_dates(["23/04/2025", "05/01/2025"])            # usage français, inchangé
    assert [x.strftime("%Y-%m-%d") for x in d] == ["2025-04-23", "2025-01-05"]


# ---------------------------------------------------------------- de bout en bout
def _serveur():
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import server  # noqa: PLC0415
    return server


def _csv(tmp_path, lignes, nom="fictif.csv"):
    p = tmp_path / nom
    p.write_text("\n".join(lignes), encoding="utf-8")
    return str(p)


def _meta(html):
    return json.loads(re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", html, re.S).group(1))


def test_csv_francais_compte_dans_les_totaux(tmp_path):
    """Export Excel français : point-virgule, espaces de milliers, virgule décimale, FCFA. Le tableau de bord additionne
    les vrais montants ; avant, la colonne n'était pas un nombre et le total manquait."""
    server = _serveur()
    lignes, total = ["mois;region;depenses"], 0.0
    for m in range(1, 13):
        for r, base in (("Région 1", 1200), ("Région 2", 3400), ("Région 3", 870)):
            v = base * 10 + m * 7 + .5
            total += v
            lignes.append(f"2025-{m:02d};{r};{v:,.1f} FCFA".replace(",", " ").replace(".", ","))
    src = _csv(tmp_path, lignes)
    df, _ = server._read_df(src)
    assert pd.api.types.is_numeric_dtype(df["depenses"]) and abs(df["depenses"].sum() - total) < 1e-6
    tuile = next(k for k in server._build_dashboard(df)["kpis"] if k["colonne"] == "depenses")
    assert abs(tuile["valeur"] - total) < 1e-6
    q = server.analyze(src)["qualite"]
    cles = {c["cle"]: c for d in q["dimensions"] for c in d["constats"]}
    assert cles["nombre_converti"]["gravite"] == "info" and "nombre_texte" not in cles


def test_texte_dans_une_colonne_de_nombres_signale_a_sa_ligne(tmp_path):
    server = _serveur()
    lignes = ["mois;centre;consultations"] + [f"2025-{1 + i // 3:02d};CS {'ABC'[i % 3]};1 {100 + i},5" for i in range(36)]
    lignes[10] = "2025-04;CS A;trois cents"                      # ligne 11 du fichier (en-tête = 1)
    q = server.analyze(_csv(tmp_path, lignes))["qualite"]
    c = next(c for d in q["dimensions"] for c in d["constats"] if c["cle"] == "texte_dans_nombres")
    assert c["gravite"] == "grave" and c["lignes"] == [11] and "trois cents" in c["detail"]
    # signalée une fois : pas aussi comme « case vide »
    assert not any(c["cle"] == "vides" and c["colonne"] == "consultations" for d in q["dimensions"] for c in d["constats"])


def test_dates_a_l_anglaise_dans_le_tableau_de_bord(tmp_path):
    server = _serveur()
    lignes = ["date;site;cas"] + [f"{m:02d}/{j:02d}/2025;Site {s};{10 + m}" for m in range(1, 7) for j in (5, 23) for s in "AB"]
    src = _csv(tmp_path, lignes)
    df, _ = server._read_df(src)
    from hermes_da.adapters import dashboard  # noqa: PLC0415
    _, periodes = dashboard._period(df)
    assert sorted(set(p[:7] for p in periodes)) == [f"2025-{m:02d}" for m in range(1, 7)]   # pas d'avril → mai
    q = server.analyze(src)["qualite"]
    assert any(c["cle"] == "dates_mois_premier" for d in q["dimensions"] for c in d["constats"])


def test_dates_dans_deux_ordres_signalees(tmp_path):
    server = _serveur()
    lignes = ["date;site;cas", "04/23/2025;A;3", "23/04/2025;B;4"] + [f"0{m}/05/2025;A;{m}" for m in range(1, 9)]
    q = server.analyze(_csv(tmp_path, lignes))["qualite"]
    c = next(c for d in q["dimensions"] for c in d["constats"] if c["cle"] == "dates_deux_ordres")
    assert c["gravite"] == "grave"


def test_date_impossible_hors_des_periodes_mais_dans_les_totaux(tmp_path):
    """Une date de 2031 (erreur de saisie) n'étire plus la période affichée ; sa valeur reste dans le total."""
    server = _serveur()
    lignes = ["mois;centre;cas"] + [f"2025-{m:02d};CS {c};10" for m in range(1, 7) for c in "AB"] + ["2031-03;CS A;10"]
    df, _ = server._read_df(_csv(tmp_path, lignes))
    from hermes_da.adapters import dashboard  # noqa: PLC0415
    _, periodes = dashboard._period(df)
    assert pd.isna(periodes.iloc[-1]) and max(p for p in periodes.dropna()) == "2025-06"
    res = server._build_dashboard(df)
    assert next(k for k in res["kpis"] if k["colonne"] == "cas")["valeur"] == 130
    meta = _meta(res["html"])
    assert "2031" not in json.dumps(meta.get("range") or "")
