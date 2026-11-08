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
    assert b["declarations"] == {"colonne": "centre", "unites": 4, "periodes": 12, "recues": 48, "attendues": 48, "taux": 100.0}


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
    c = _constats(Q.bilan(_sain(), anomalies=anomalies, aujourd_hui=AUJOURD_HUI))["extremes"]
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


def test_rapport_pdf_contient_le_bilan(tmp_path, monkeypatch):
    server, src = _serveur(tmp_path)
    import pdf_report  # noqa: PLC0415
    vus = []
    origine = pdf_report.qualite_pdf
    monkeypatch.setattr(pdf_report, "qualite_pdf", lambda story, q, st: (vus.append(q), origine(story, q, st)))
    out = tmp_path / "rapport.pdf"
    pdf_report.build(server.analyze(src), str(out))
    assert vus and vus[0]["note"] <= 69 and out.read_bytes()[:4] == b"%PDF"
