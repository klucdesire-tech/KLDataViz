# -*- coding: utf-8 -*-
"""Bilan qualité (`qualite.py`) à réponse connue : chaque défaut est placé à une ligne précise d'un fichier fictif,
et le bilan doit le retrouver, avec le bon nombre de lignes et les bons numéros de ligne. Les fichiers propres ne
doivent porter aucune alerte : une fausse alerte use la confiance autant qu'un oubli."""
import datetime as dt
import os
import sys

import numpy as np
import pandas as pd
import pytest

from _harness import APP, reset_app_modules

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import jeux_pieges as P  # noqa: E402
import jeux_reference as J  # noqa: E402

AUJOURD_HUI = dt.date(2025, 6, 15)


@pytest.fixture
def Q():
    reset_app_modules()
    import qualite
    return qualite


def _sain():
    """Rapport mensuel fictif : 4 centres × 12 mois, une ligne par centre et par mois, aucun défaut."""
    rng = np.random.default_rng(7)
    lignes = [{"mois": f"2025-{m:02d}" if m <= 5 else f"2024-{m:02d}", "centre": c, "district": "Nord" if i < 2 else "Sud",
               "consultations": int(rng.integers(300, 400)), "cpn1": int(rng.integers(30, 40)),
               "taux_couverture": round(float(rng.uniform(60, 90)), 1), "rupture": rng.choice(["Oui", "Non"])}
              for m in range(1, 13) for i, c in enumerate(["CS A", "CS B", "CS C", "CS D"])]
    return pd.DataFrame(lignes).sort_values(["mois", "centre"]).reset_index(drop=True)


def _sale():
    """Le même rapport, avec un défaut connu par ligne. Ligne du fichier = index + 2 (l'en-tête est la ligne 1)."""
    df = _sain().astype(object)
    df.loc[3, "consultations"] = "NA"                 # valeur d'attente               → ligne 5
    df.loc[5, "cpn1"] = -4                            # comptage négatif               → ligne 7
    df.loc[8, "taux_couverture"] = 140.0              # taux au-delà de 100 %          → ligne 10
    df.loc[10, "consultations"] = "trois cents"       # texte dans une colonne de nombres → ligne 12
    df.loc[12, "rupture"] = "peut-être"               # réponse hors oui/non           → ligne 14
    df.loc[14, "centre"] = "Total"                    # ligne de total mêlée aux détails → ligne 16
    df.loc[16, "mois"] = "2031-03"                    # date dans le futur             → ligne 18
    df.loc[18, "cpn1"] = None                         # case vide                      → ligne 20
    double = df.loc[[20]].copy()
    double["consultations"] = 999                     # même centre, même mois, autre chiffre → lignes 22 et 50
    exact = df.loc[[25]]                              # ligne strictement identique    → ligne 51
    return pd.concat([df, double, exact], ignore_index=True)


def _constats(b):
    return {c["cle"]: dict(c, dimension=d["cle"]) for d in b["dimensions"] for c in d["constats"]}


def test_fichier_sain_sans_alerte(Q):
    b = Q.bilan(_sain(), aujourd_hui=AUJOURD_HUI)
    assert b["note"] == 100 and b["graves"] == 0 and b["attentions"] == 0, b["resume"]
    d = b["declarations"]
    assert {k: d[k] for k in ("colonne", "unites", "periodes", "recues", "attendues", "taux")} ==         {"colonne": "centre", "unites": 4, "periodes": 12, "recues": 48, "attendues": 48, "taux": 100.0}
    assert all(u["cases"] == "1" * 12 for u in d["par_unite"]) and d["dernier"]["manquants"] == []


ATTENDUS = {  # clé du constat : (dimension, gravité, nombre, lignes)
    "attente": ("completude", "attention", 1, [5]),
    "negatif": ("validite", "grave", 1, [7]),
    "taux_hors_bornes": ("validite", "attention", 1, [10]),
    "texte_dans_nombres": ("validite", "grave", 1, [12]),
    "oui_non": ("validite", "attention", 1, [14]),
    "ligne_total": ("coherence", "attention", 1, [16]),
    "total_faux": ("coherence", "grave", 1, [16]),
    "date_future": ("validite", "attention", 1, [18]),
    "cle_double": ("unicite", "grave", 2, [22, 50]),
    "doublons": ("unicite", "grave", 1, [51]),
}


@pytest.mark.parametrize("cle", sorted(ATTENDUS))
def test_chaque_defaut_trouve_a_sa_ligne(Q, cle):
    c = _constats(Q.bilan(_sale(), aujourd_hui=AUJOURD_HUI)).get(cle)
    dim, grav, n, lignes = ATTENDUS[cle]
    assert c is not None, f"défaut « {cle} » non trouvé"
    assert (c["dimension"], c["gravite"], c["n"], c["lignes"]) == (dim, grav, n, lignes)


def test_case_vide_comptee(Q):
    vides = [c for c in _constats(Q.bilan(_sale(), aujourd_hui=AUJOURD_HUI)).values() if c["cle"] == "vides"]
    assert vides and vides[0]["colonne"] == "cpn1" and vides[0]["lignes"] == [20]


def test_note_et_resume_disent_la_gravite(Q):
    b = Q.bilan(_sale(), aujourd_hui=AUJOURD_HUI)
    assert b["note"] <= 69 and b["graves"] >= 5
    assert "peuvent être faux" in b["resume"]
    assert {d["cle"] for d in b["dimensions"]} == {"completude", "unicite", "validite", "coherence", "plausibilite", "actualite"}
    assert next(d for d in b["dimensions"] if d["cle"] == "unicite")["note"] <= 69
    assert Q.principaux(b)[0]["gravite"] == "grave"
    # la date de 2031, déjà signalée, ne crée pas 69 « mois sans donnée » ni une actualité effondrée
    actu = next(d for d in b["dimensions"] if d["cle"] == "actualite")
    assert actu["note"] >= 90 and not any(c["cle"] == "mois_absents" for c in actu["constats"])


def test_saisie_en_double_dans_un_petit_rapport(Q):
    """11 lignes, une clé saisie deux fois : signalée, même si elle touche 18 % des lignes (relecture de la PR 195)."""
    df = pd.DataFrame([{"mois": f"2025-0{m}", "centre": c, "consultations": 100 + m}
                       for m in range(1, 6) for c in ("CS A", "CS B")])
    df = pd.concat([df, pd.DataFrame([{"mois": "2025-03", "centre": "CS A", "consultations": 999}])], ignore_index=True)
    c = _constats(Q.bilan(df, aujourd_hui=AUJOURD_HUI))["cle_double"]
    assert c["gravite"] == "grave" and c["n"] == 2 and c["lignes"] == [6, 12]


@pytest.mark.parametrize("densite", [400, 60])
def test_registre_jamais_pris_pour_des_doublons(Q, densite):
    """Registre daté au jour : plusieurs lignes pour un même centre et un même jour sont normales, même quand elles
    sont rares (registre peu dense : 60 consultations sur 150 jours)."""
    df = _registre().head(densite)
    cles = {c["cle"] for d in Q.bilan(df, aujourd_hui=AUJOURD_HUI)["dimensions"] for c in d["constats"]}
    assert "cle_double" not in cles


def _matrice():
    """5 centres, 6 mois. Manquent : B en mars, C en juin (dernier mois), E en février et mai. D ouvre en mars."""
    absents = {("B", 3), ("C", 6), ("E", 2), ("E", 5)}
    return pd.DataFrame([{"mois": f"2025-0{m}", "centre": c, "consultations": 100 + m}
                         for m in range(1, 7) for c in "ABCDE" if (c, m) not in absents and not (c == "D" and m < 3)])


def test_matrice_des_declarations_a_reponse_connue(Q):
    d = Q.bilan(_matrice(), aujourd_hui=AUJOURD_HUI)["declarations"]
    assert (d["recues"], d["attendues"], d["taux"]) == (24, 28, 85.7)       # D n'est pas attendu avant son ouverture
    lignes = {u["unite"]: (u["cases"], u["recues"], u["attendues"], u["manquants"]) for u in d["par_unite"]}
    assert lignes == {"A": ("111111", 6, 6, []), "B": ("110111", 5, 6, ["03/2025"]), "C": ("111110", 5, 6, ["06/2025"]),
                      "D": ("--1111", 4, 4, []), "E": ("101101", 4, 6, ["02/2025", "05/2025"])}
    assert [u["unite"] for u in d["par_unite"]][:3] == ["E", "B", "C"]      # les moins complets d'abord
    assert [(m["mois"], m["recues"], m["attendues"]) for m in d["par_mois"]] == [
        ("01/2025", 4, 4), ("02/2025", 3, 4), ("03/2025", 4, 5), ("04/2025", 5, 5), ("05/2025", 4, 5), ("06/2025", 4, 5)]
    assert d["dernier"] == {"mois": "06/2025", "manquants": ["C"]}
    c = _constats(Q.bilan(_matrice(), aujourd_hui=AUJOURD_HUI))["declarations"]
    assert c["gravite"] == "attention" and "pas encore de déclaration pour : C" in c["detail"]


def test_declarations_manquantes(Q):
    df = _sain()
    df = df[~((df["centre"] == "CS C") & df["mois"].isin(["2025-04", "2025-05"]))]
    c = _constats(Q.bilan(df, aujourd_hui=AUJOURD_HUI))["declarations"]
    assert c["gravite"] == "attention" and c["n"] == 2 and "CS C (2 mois)" in c["detail"]


def test_mois_absent_et_donnees_anciennes(Q):
    df = _sain()
    df = df[df["mois"] != "2024-09"]
    cs = _constats(Q.bilan(df, aujourd_hui=dt.date(2026, 3, 1)))
    assert cs["mois_absents"]["n"] == 1 and "09/2024" in cs["mois_absents"]["detail"]
    assert cs["anciennete"]["titre"] == "Dernières données : 05/2025" and cs["anciennete"]["gravite"] == "attention"


def test_nombres_ecrits_en_texte(Q):
    df = pd.DataFrame({"mois": ["2025-01", "2025-02", "2025-03"] * 2, "region": ["R1"] * 3 + ["R2"] * 3,
                       "depenses": ["1 234,5", "2 300,0", "987,25", "12 000,0", "4 500,5", "3 210,75"]})
    c = _constats(Q.bilan(df, aujourd_hui=AUJOURD_HUI))["nombre_texte"]
    assert c["gravite"] == "grave" and c["n"] == 6 and c["colonne"] == "depenses"


def test_libelles_ecrits_de_plusieurs_facons(Q):
    c = _constats(Q.bilan(P.libelles(), aujourd_hui=AUJOURD_HUI))["libelles"]
    assert c["gravite"] == "info" and "ABIDJAN" in c["detail"]


def test_ecarts_de_desagregation_repris(Q):
    controles = [{"indicateur": "TX_NEW", "site": "Site A", "controle": "Total = somme par sexe", "attendu": 12, "trouve": 10}]
    c = _constats(Q.bilan(_sain(), controles=controles, aujourd_hui=AUJOURD_HUI))["desagregation"]
    assert c["gravite"] == "grave" and "TX_NEW" in c["detail"] and "attendu 12" in c["detail"]


def test_valeurs_extremes_reprises(Q):
    anomalies = [{"column": "consultations", "type": "valeur aberrante (IQR)", "count": 2,
                  "detail": "2 valeur(s) hors de la plage habituelle [300 ; 400]"}]
    # fichier sans site ni période : les valeurs extrêmes viennent de la lecture globale (IQR) de l'analyse
    anomalies[0]["column"] = "age"
    c = _constats(Q.bilan(J.sans_dates(), anomalies=anomalies, aujourd_hui=AUJOURD_HUI))["extremes"]
    assert c["dimension"] == "plausibilite" and c["n"] == 2


def _registre():
    """Une ligne par consultation : plusieurs lignes par centre et par mois, et des mois sans patient, c'est normal."""
    rng = np.random.default_rng(9)
    n = 400
    jours = pd.to_datetime("2025-01-01") + pd.to_timedelta(rng.integers(0, 150, n), unit="D")
    return pd.DataFrame({"date_consultation": jours.strftime("%Y-%m-%d"), "centre": rng.choice(["CS A", "CS B", "CS C"], n),
                         "sexe": rng.choice(["F", "M"], n), "age": rng.integers(1, 80, n), "montant_paye": rng.integers(0, 5000, n)})


@pytest.mark.parametrize("nom", ["registre"] + sorted(J.JEUX) + sorted(P.JEUX))
def test_aucune_fausse_alerte_grave(Q, nom):
    df = _registre() if nom == "registre" else (J.JEUX.get(nom) or P.JEUX[nom])()
    b = Q.bilan(df, aujourd_hui=dt.date(2026, 1, 15))
    graves = [c["titre"] for d in b["dimensions"] for c in d["constats"] if c["gravite"] == "grave"]
    assert not graves, graves


def test_bilan_rapide_sur_un_gros_fichier(Q):
    import time
    df = pd.concat([P.gros()] * 6, ignore_index=True)       # 50 000 lignes
    t = time.time()
    Q.bilan(df, aujourd_hui=AUJOURD_HUI)
    assert time.time() - t < 15


# ---------------------------------------------------------------- branchements : import, tableau de bord, PDF
def _serveur(tmp_path):
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import server  # noqa: PLC0415
    src = tmp_path / "rapport_fictif.csv"
    _sale().to_csv(src, index=False)
    return server, str(src)


def test_analyse_porte_le_bilan(tmp_path):
    server, src = _serveur(tmp_path)
    r = server.analyze(src)
    q = r["qualite"]
    assert q["note"] <= 69 and q["graves"] >= 5
    assert {c["cle"] for d in q["dimensions"] for c in d["constats"]} >= {"doublons", "cle_double", "negatif", "total_faux"}


def test_tableau_de_bord_meme_bilan_que_l_import(tmp_path):
    server, src = _serveur(tmp_path)
    q = server.analyze(src)["qualite"]
    df, _ = server._read_df(src)
    html = server._build_dashboard(df)["html"]
    import json
    import re
    meta = json.loads(re.search(r"window\.META\s*=\s*(\{.*?\});\s*\n", html, re.S).group(1))
    assert (meta["qualite"]["note"], meta["qualite"]["graves"], meta["qualite"]["attentions"]) == (q["note"], q["graves"], q["attentions"])
    assert meta["qualite"]["principaux"][0]["gravite"] == "grave" and "function qualiteNote" in html
    assert meta["qualite"]["declarations"]["colonne"] == "centre" and "Déclarations reçues" in html


def test_rapport_pdf_contient_le_bilan(tmp_path, monkeypatch):
    server, src = _serveur(tmp_path)
    import pdf_report  # noqa: PLC0415
    vus = []
    origine = pdf_report.qualite_pdf
    monkeypatch.setattr(pdf_report, "qualite_pdf", lambda story, q, st: (vus.append(q), origine(story, q, st)))
    out = tmp_path / "rapport.pdf"
    pdf_report.build(server.analyze(src), str(out))
    assert vus and vus[0]["note"] <= 69 and out.read_bytes()[:4] == b"%PDF"
    assert vus[0]["declarations"]["par_unite"]                     # la matrice est bien passée au rapport


def test_matrice_dans_le_rapport_pdf(tmp_path):
    """La section des déclarations se construit : mois en colonnes, sites en retard en tableau."""
    reset_app_modules()
    import pdf_report  # noqa: PLC0415
    from reportlab.platypus import Paragraph, Table  # noqa: PLC0415
    import qualite  # noqa: PLC0415
    story = []
    pdf_report.qualite_pdf(story, qualite.bilan(_matrice(), aujourd_hui=AUJOURD_HUI), pdf_report._styles())
    textes = " ".join(p.getPlainText() for p in story if isinstance(p, Paragraph))
    assert "Déclarations reçues : 85,7 % (24 sur 28)" in textes.replace(" ", " ").replace(" ", " ")
    tableaux = [t for t in story if isinstance(t, Table)]
    assert len(tableaux) >= 3                                     # dimensions, mois, sites en retard


# ---------------------------------------------------------------- cohérence interne (étape 3)
def _suivi_mensuel():
    """4 centres × 24 mois, une ligne par centre et par mois : un CHU 5 fois plus grand que les autres, du paludisme
    qui double chaque saison des pluies. Rien d'anormal : aucune alerte attendue."""
    rng = np.random.default_rng(11)
    lignes = []
    for a in (2023, 2024):
        for m in range(1, 13):
            for c, taille in (("CHU Nord", 5), ("CS A", 1), ("CS B", 1), ("CS C", 1)):
                pluie = 2 if m in (6, 7, 8, 9, 10) else 1
                dep = int(rng.integers(180, 220) * taille)
                lignes.append({"mois": f"{a}-{m:02d}", "centre": c, "consultations": int(rng.integers(280, 320) * taille),
                               "cas_paludisme": int(rng.integers(40, 50) * taille * pluie), "depistes": dep,
                               "positifs": int(dep * .05), "cpn1": int(rng.integers(30, 36) * taille),
                               "cpn4": int(rng.integers(15, 20) * taille)})
    return pd.DataFrame(lignes)


def _constats_liste(b, cle):
    return [dict(c, dimension=d["cle"]) for d in b["dimensions"] for c in d["constats"] if c["cle"] == cle]


def test_suivi_sain_sans_alerte_de_coherence(Q):
    b = Q.bilan(_suivi_mensuel(), aujourd_hui=dt.date(2025, 1, 15))
    assert not _constats_liste(b, "extremes_site") and not _constats_liste(b, "regle"), b["resume"]


def test_valeur_hors_habitude_de_son_site(Q):
    df = _suivi_mensuel()
    i_dix = df.index[(df["centre"] == "CS B") & (df["mois"] == "2024-03")][0]
    i_bas = df.index[(df["centre"] == "CHU Nord") & (df["mois"] == "2023-11")][0]
    df.loc[i_dix, "consultations"] *= 10          # un zéro de trop
    df.loc[i_bas, "consultations"] = 5            # saisie tronquée
    c = _constats_liste(Q.bilan(df, aujourd_hui=dt.date(2025, 1, 15)), "extremes_site")
    assert len(c) == 1 and c[0]["colonne"] == "consultations" and c[0]["dimension"] == "plausibilite"
    assert c[0]["n"] == 2 and c[0]["lignes"] == sorted([i_dix + 2, i_bas + 2])
    assert "un zéro de trop" in c[0]["detail"] and c[0]["gravite"] == "attention"


def test_grand_site_pas_extreme_pour_l_iqr_global(Q):
    """L'IQR de tout le fichier signale le CHU (5 fois plus grand) : la lecture site par site le remplace."""
    anomalies = [{"column": "consultations", "type": "valeur aberrante (IQR)", "count": 24, "detail": "24 valeur(s)"}]
    b = Q.bilan(_suivi_mensuel(), anomalies=anomalies, aujourd_hui=dt.date(2025, 1, 15))
    assert not _constats_liste(b, "extremes")


def test_regle_ligne_positifs_et_testes(Q):
    df = _suivi_mensuel()
    i = df.index[(df["centre"] == "CS C") & (df["mois"] == "2024-06")][0]
    df.loc[i, "positifs"] = df.loc[i, "depistes"] + 7
    c = _constats_liste(Q.bilan(df, aujourd_hui=dt.date(2025, 1, 15)), "regle")
    assert len(c) == 1 and c[0]["gravite"] == "grave" and c[0]["dimension"] == "coherence"
    assert c[0]["lignes"] == [i + 2] and "positifs ≤ testés" in c[0]["titre"]


def test_regle_de_cohorte_par_site(Q):
    """CPN4 > CPN1 un mois donné peut arriver ; sur toute la période d'un site, non."""
    df = _suivi_mensuel()
    un_mois = df.index[(df["centre"] == "CS A") & (df["mois"] == "2024-02")][0]
    df.loc[un_mois, "cpn4"] = df.loc[un_mois, "cpn1"] + 3                  # un mois seulement : rien à dire
    assert not _constats_liste(Q.bilan(df, aujourd_hui=dt.date(2025, 1, 15)), "regle")
    df.loc[df["centre"] == "CS B", "cpn4"] = df.loc[df["centre"] == "CS B", "cpn1"] + 2   # toute la période
    c = _constats_liste(Q.bilan(df, aujourd_hui=dt.date(2025, 1, 15)), "regle")
    assert len(c) == 1 and c[0]["gravite"] == "attention" and "CS B" in c[0]["detail"] and c[0]["n"] == 24


def test_regle_marge_et_chiffre_d_affaires(Q):
    df = J.ventes()
    df.loc[17, "marge"] = df.loc[17, "chiffre_affaires"] * 2
    c = _constats_liste(Q.bilan(df, aujourd_hui=dt.date(2026, 1, 15)), "regle")
    assert len(c) == 1 and c[0]["lignes"] == [19] and "marge ≤ chiffre d'affaires" in c[0]["titre"]
