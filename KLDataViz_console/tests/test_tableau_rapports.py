# -*- coding: utf-8 -*-
"""Tableau de bord d'un rapport remis à plat (colonne « niveau » : total / sexe / âge), données fictives.

Le même chiffre y figure au total, par sexe et par sexe × âge : additionnés, ils triplaient chaque tuile et « Total »
passait pour une tranche d'âge. Le tableau ne doit compter chaque chiffre qu'une fois, au niveau le plus fin déclaré."""
import json
import os
import sys

import pandas as pd

from _harness import reset_app_modules

AGES = ["<15 ans", "15-49 ans", "50+ ans"]


def _rapport():
    """2 trimestres × 3 établissements fictifs ; « Dépistés » ventilé sexe × âge, « Éligibles » au total seulement.
    Au centre Gamma (T2), le total déclaré (99) ne vaut pas la somme de son détail (90) : erreur de saisie fictive."""
    lignes = []
    sites = [("Région Est", "District A", "Centre Alpha"), ("Région Est", "District B", "Centre Beta"),
             ("Région Ouest", "District C", "Centre Gamma")]
    for p, (periode, feuille) in enumerate([("FY26Q1", "Dépistage_fy26q1"), ("FY26Q2", "Dépistage_fy26q2")]):
        for s, (reg, dis, site) in enumerate(sites):
            base = dict(feuille=feuille, periode=periode, region=reg, district=dis, site=site)
            detail = {(sx, a): 5 + 3 * s + 2 * p + i + (4 if sx == "Féminin" else 0)
                      for sx in ("Féminin", "Masculin") for i, a in enumerate(AGES)}
            ind = "Nombre de clients dépistés"
            for (sx, a), v in detail.items():
                lignes.append(dict(base, indicateur=ind, niveau="age", sexe=sx, tranche_age=a, valeur=float(v)))
            for sx in ("Féminin", "Masculin"):
                lignes.append(dict(base, indicateur=ind, niveau="sexe", sexe=sx, tranche_age="Total",
                                   valeur=float(sum(v for (x, _), v in detail.items() if x == sx))))
            total = sum(detail.values()) + (9 if (site, periode) == ("Centre Gamma", "FY26Q2") else 0)
            lignes.append(dict(base, indicateur=ind, niveau="total", sexe="Total", tranche_age="Total", valeur=float(total)))
            lignes.append(dict(base, indicateur="Nombre de clients éligibles", niveau="total", sexe="Total",
                               tranche_age="Total", valeur=float(100 + 10 * s + p)))
    return pd.DataFrame(lignes)


def _oracle(df, **filtre):
    """Somme attendue par indicateur, calculée à part : détail par âge s'il existe, sinon total déclaré."""
    for c, v in filtre.items():
        df = df[df[c] == v]
    out = {}
    for ind, d in df.groupby("indicateur"):
        age = d[d.niveau == "age"]
        out[ind] = float(age.valeur.sum() if len(age) else d[d.niveau == "total"].valeur.sum())
    return out


def _dashboard():
    reset_app_modules()
    import server  # noqa: PLC0415
    racine = os.path.join(os.path.dirname(server.__file__), "..", "HERMES_DA")
    if racine not in sys.path:
        sys.path.insert(0, racine)
    from hermes_da.adapters import dashboard  # noqa: PLC0415
    return server, dashboard


def test_vue_rapport_un_chiffre_une_fois():
    _, D = _dashboard()
    df = _rapport()
    v = D.vue_rapport(df)
    assert "niveau" not in v.columns and "feuille" not in v.columns  # feuille = période : pas un filtre de plus
    assert "Total" not in set(v["tranche_age"].dropna()) and "Total" not in set(v["sexe"].dropna())
    attendu = _oracle(df)
    for ind, d in v.groupby("indicateur"):
        assert float(d.valeur.sum()) == attendu[ind]
    # 3 fois moins que la somme brute pour l'indicateur ventilé (détail + par sexe + total)
    brut = float(df[df.indicateur == "Nombre de clients dépistés"].valeur.sum())
    assert attendu["Nombre de clients dépistés"] < brut / 2.9
    # l'indicateur déclaré seulement au total est gardé, sans sexe ni âge
    el = v[v.indicateur == "Nombre de clients éligibles"]
    assert len(el) == 6 and el["sexe"].isna().all() and el["tranche_age"].isna().all()


def test_vue_rapport_laisse_les_autres_tableaux_intacts():
    _, D = _dashboard()
    df = pd.DataFrame({"district": ["A", "B"], "niveau": ["Primaire", "Secondaire"], "valeur": [1.0, 2.0],
                       "indicateur": ["x", "x"]})
    assert D.vue_rapport(df) is df  # « niveau » scolaire : pas un rapport total / sexe / âge
    sans = pd.DataFrame({"district": ["A"], "valeur": [1.0]})
    assert D.vue_rapport(sans) is sans


def test_tuiles_justes_et_sans_comptage_de_cases():
    _, D = _dashboard()
    df = _rapport()
    spec = {"kpis": [{"nom": "Dépistés", "agregation": "somme", "colonne": "Nombre de clients dépistés",
                      "dimension": "region"},
                     {"nom": "Éligibles", "agregation": "somme", "colonne": "Nombre de clients éligibles"},
                     {"nom": "Nombre de sites rapportant", "agregation": "compte", "colonne": "Nombre de clients dépistés"},
                     {"nom": "Médiane par ligne", "agregation": "mediane", "colonne": "Nombre de clients dépistés"}],
            "graphiques": []}
    r = D.generate_dashboard(df, "t", llm=lambda m, n: json.dumps(spec))
    tuiles = {k["nom"]: k["valeur"] for k in r["kpis"]}
    attendu = _oracle(df)
    assert tuiles == {"Dépistés": attendu["Nombre de clients dépistés"], "Éligibles": attendu["Nombre de clients éligibles"]}
    top = next(k for k in r["kpis"] if k["nom"] == "Dépistés")["top"]
    assert top[1] == max(_oracle(df, region=g)["Nombre de clients dépistés"] for g in ("Région Est", "Région Ouest"))
    assert "niveau" not in r["filter_cols"] and "feuille" not in r["filter_cols"]


def test_bilan_qualite_lit_le_fichier_d_origine(monkeypatch):
    server, _ = _dashboard()
    monkeypatch.setattr(server, "_llm_config", lambda: None)
    out = server.build_dashboard(_rapport())
    # l'écart total déclaré / détail (Centre Gamma) reste signalé : il n'existe que dans le fichier d'origine
    assert "désagrégations" in out["html"]
    tuiles = {k["colonne"]: k["valeur"] for k in out["kpis"] if k["agregation"] == "somme"}
    assert tuiles.get("Nombre de clients dépistés") == _oracle(_rapport())["Nombre de clients dépistés"]


def _case(lignes, **base):
    return pd.DataFrame([dict(dict(periode="FY26Q1", region="R", district="D", site="Centre Delta",
                                   indicateur="Nombre de clients dépistés"), **base, **l) for l in lignes])


def test_detail_par_age_d_un_seul_sexe():
    """Total 100 = 60 F + 40 M ; âges remplis pour les femmes seulement : 60 F par âge + 40 M, jamais 60."""
    _, D = _dashboard()
    df = _case([dict(niveau="total", sexe="Total", tranche_age="Total", valeur=100.0),
                dict(niveau="sexe", sexe="Féminin", tranche_age="Total", valeur=60.0),
                dict(niveau="sexe", sexe="Masculin", tranche_age="Total", valeur=40.0),
                dict(niveau="age", sexe="Féminin", tranche_age="<15 ans", valeur=25.0),
                dict(niveau="age", sexe="Féminin", tranche_age="15+ ans", valeur=35.0)])
    v = D.vue_rapport(df)
    assert float(v.valeur.sum()) == 100.0
    assert v.groupby("sexe").valeur.sum().to_dict() == {"Féminin": 60.0, "Masculin": 40.0}
    assert v[v.sexe == "Masculin"].tranche_age.isna().all()


def test_age_non_croise_avec_le_sexe():
    """Ventilation par sexe ET par âge séparément (âge sans sexe) : un seul découpage, total juste, jamais « Total »."""
    server, D = _dashboard()
    df = _case([dict(niveau="total", sexe="Total", tranche_age="Total", valeur=100.0),
                dict(niveau="sexe", sexe="Féminin", tranche_age="Total", valeur=55.0),
                dict(niveau="sexe", sexe="Masculin", tranche_age="Total", valeur=45.0),
                dict(niveau="age", sexe="Total", tranche_age="<15 ans", valeur=30.0),
                dict(niveau="age", sexe="Total", tranche_age="15+ ans", valeur=70.0)])
    df = pd.concat([df, _rapport()], ignore_index=True)  # 2 lignes à âge non croisé parmi beaucoup d'autres
    v = D.vue_rapport(df)
    delta = v[v.site == "Centre Delta"]
    assert float(delta.valeur.sum()) == 100.0 and delta.sexe.isna().all()
    # et l'indicateur reste dans les tuiles (il n'est pas pris pour une ligne de « Total » à écarter)
    r = D.generate_dashboard(df, "t", llm=False)
    tuiles = {k["colonne"]: k["valeur"] for k in r["kpis"]}
    assert tuiles["Nombre de clients dépistés"] == _oracle(_rapport())["Nombre de clients dépistés"] + 100.0


def test_libelle_avec_espace_en_trop_une_seule_case():
    _, D = _dashboard()
    df = _case([dict(niveau="total", sexe="Total", tranche_age="Total", valeur=99.0, site="Centre Alpha"),
                dict(niveau="age", sexe="Féminin", tranche_age="<15 ans", valeur=30.0, site="Centre Alpha ")])
    r = D.generate_dashboard(df, "t", llm=lambda m, n: json.dumps(
        {"kpis": [{"nom": "Dépistés", "agregation": "somme", "colonne": "Nombre de clients dépistés"}], "graphiques": []}))
    assert [k["valeur"] for k in r["kpis"]] == [30.0]


def test_niveaux_ecrits_autrement_et_repli_sans_ia():
    """« Âge » / « TOTAL » reconnus ; sans IA ni « Nombre de… », une somme par indicateur, jamais un taux additionné."""
    _, D = _dashboard()
    df = _case([dict(niveau="TOTAL", sexe="Total", tranche_age="Total", valeur=100.0, indicateur="Clients dépistés"),
                dict(niveau="Âge", sexe="Féminin", tranche_age="<15 ans", valeur=40.0, indicateur="Clients dépistés"),
                dict(niveau="Âge", sexe="Masculin", tranche_age="<15 ans", valeur=60.0, indicateur="Clients dépistés"),
                dict(niveau="total", sexe="Total", tranche_age="Total", valeur=0.12, indicateur="Taux de positivité")])
    v = D.vue_rapport(df)
    assert float(v[v.indicateur == "Clients dépistés"].valeur.sum()) == 100.0
    r = D.generate_dashboard(df, "t", llm=False)
    tuiles = {k["colonne"]: (k["agregation"], k["valeur"]) for k in r["kpis"]}
    assert tuiles.get("Clients dépistés") == ("somme", 100.0)
    assert ("somme", 0.12) not in tuiles.values() and all(a in ("somme", "taux", "dernier") for a, _ in tuiles.values())
