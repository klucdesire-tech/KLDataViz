# -*- coding: utf-8 -*-
"""Banc des chiffres affichés : chaque tuile, chaque carte et chaque phrase de synthèse du tableau de bord, comparées
à un calcul de référence indépendant (`oracle_chiffres.py`), sur des fichiers fictifs propres et piégés, avec des
filtres et des regroupements par trimestre et par année.

La page calcule ses chiffres en JavaScript, dans le navigateur : elle est exécutée telle quelle sous Node
(`banc_chiffres.js`). `test_le_banc_voit_une_erreur` prouve que le banc n'est pas complaisant : une erreur glissée
dans la page doit le faire échouer."""
import json
import os
import shutil
import subprocess
import sys

import pandas as pd
import pytest

from _harness import APP, reset_app_modules

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import jeux_pieges as P  # noqa: E402
import jeux_reference as J  # noqa: E402
import oracle_chiffres as O  # noqa: E402

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js absent : vérification faite par la CI")

SCENARIOS = {
    "sante": [{}, {"gran": "Q"}, {"gran": "Y"}, {"filtres": {"district": "Sakassou"}}],
    "ventes": [{}, {"gran": "Q"}, {"gran": "Y"}, {"filtres": {"zone": "Nord"}}],
    "dhis2_long": [{}, {"gran": "Q"}, {"filtres": {"unite_organisation": "Buyo"}}],
    "formulaire": [{}, {"filtres": {"sexe": "Femmes"}}],
    "simple": [{}],
    "sans_dates": [{}, {"filtres": {"sexe": "Femme"}}],
    "stock_jour": [{}, {"gran": "Q"}, {"filtres": {"site": "Site Sud"}}],
    "trous": [{}, {"gran": "Q"}, {"gran": "Y"}, {"filtres": {"zone": "Zone C"}}],
    "negatifs": [{}, {"gran": "Q"}],
    "gros": [{}, {"gran": "Q"}, {"gran": "Y"}, {"filtres": {"zone": "Sud"}}],
    "communes": [{}],
    "libelles": [{}, {"filtres": {"ville": "Abidjan"}}],
}


def _jeu(nom):
    return (J.JEUX.get(nom) or P.JEUX[nom])()


def _vue(nom, df):
    """Le fichier tel que l'utilisateur le pense : un export DHIS2 en long se lit indicateur par indicateur."""
    if nom == "dhis2_long":
        return df.pivot_table(index=["periode", "unite_organisation"], columns="indicateur", values="valeur",
                              aggfunc="sum").reset_index()
    return df


def _page(df):
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import generate_dashboard  # noqa: PLC0415
    return generate_dashboard(df, "", llm=False, dictionnaire=[], composition=True)["html"]


def _releves(tmp_path, html, scenarios):
    page = tmp_path / "page.html"
    page.write_text(html, encoding="utf-8")
    r = subprocess.run([NODE, os.path.join(ICI, "banc_chiffres.js"), str(page), json.dumps(scenarios)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[:900]
    return json.loads(r.stdout)


def _ecarts(nom, df0, releves):
    ecarts, n = [], 0
    for rel in releves:
        df = O.preparer(_vue(nom, df0))
        if rel["periode"]:
            df["__p"] = pd.to_datetime(df[rel["periode"]].astype(str), errors="coerce").dt.strftime("%Y-%m-%d")
        for k, v in (rel["scenario"].get("filtres") or {}).items():
            df = df[df[k].astype(str) == v]
        n += len(rel["kpis"]) + len(rel["cartes"])
        ecarts += [f"{rel['scenario']} : {e}" for e in O.comparer(df, rel, rel.get("partiel"))]
    return ecarts, n


@pytest.mark.parametrize("nom", sorted(SCENARIOS))
def test_chiffres_affiches_exacts(nom, tmp_path):
    df0 = _jeu(nom)
    releves = _releves(tmp_path, _page(df0), SCENARIOS[nom])
    ecarts, n = _ecarts(nom, df0, releves)
    assert n >= 2 * len(releves), f"trop peu de chiffres relevés ({n}) : le banc ne lit plus la page"
    assert all(r["cartes"] for r in releves), "page composée absente"
    assert not ecarts, "\n".join(ecarts[:20])


DERNIER_JOUR = ("if(a==='dernier'){var lp=null;rows.forEach(function(r){if(r.__p&&(lp==null||r.__p>lp))lp=r.__p});"
                "rows=rows.filter(function(r){return r.__p===lp});a='somme'}")
DERNIER_MOIS = ("if(a==='dernier'){var lp=null;rows.forEach(function(r){var m=r.__p?String(r.__p).slice(0,7):null;"
                "if(m&&(lp==null||m>lp))lp=m});rows=rows.filter(function(r){return r.__p&&String(r.__p).slice(0,7)===lp});a='somme'}")

MUTATIONS = {
    "moyenne fausse": ("sans_dates", {}, "if(a==='moyenne'||a==='taux')return s/v.length;", "if(a==='moyenne'||a==='taux')return s/(v.length+1);"),
    "somme fausse": ("ventes", {}, "var s=v.reduce(function(p,q){return p+q},0);", "var s=v.reduce(function(p,q){return p+q},1);"),
    "classement inversé": ("ventes", {}, ".sort(function(a,b){return b[1]-a[1]}).slice(0,top||12)}", ".sort(function(a,b){return a[1]-b[1]}).slice(0,top||12)}"),
    "trimestres décalés": ("ventes", {"gran": "Q"}, "(Math.floor((+p.slice(5,7)-1)/3)+1)", "(Math.floor((+p.slice(5,7))/3)+1)"),
    "stock au dernier jour": ("stock_jour", {}, DERNIER_MOIS, DERNIER_JOUR),
    "mois manquant pris pour une baisse": ("trous", {}, "if(A&&B){var ma={}", "if(false){var ma={}"),
    "manque non signalé": ("trous", {}, "return t&&m.note?t+'. '+m.note:t", "return t"),
}


@pytest.mark.parametrize("erreur", sorted(MUTATIONS))
def test_le_banc_voit_une_erreur(erreur, tmp_path):
    nom, scenario, juste, faux = MUTATIONS[erreur]
    df0 = _jeu(nom)
    html = _page(df0)
    assert juste in html, "le code visé par l'erreur a changé : mettre à jour la mutation"
    ecarts, _ = _ecarts(nom, df0, _releves(tmp_path, html.replace(juste, faux), [scenario]))
    assert ecarts, f"erreur « {erreur} » glissée dans la page, et le banc ne l'a pas vue"


# ---------------------------------------------------------------- corrections trouvées par le banc
def test_stock_date_au_jour_compte_tout_le_dernier_mois():
    """Quatre sites déclarent leur file active les 5, 11, 17 et 23 : la tuile additionne les quatre, pas le dernier."""
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import generate_dashboard  # noqa: PLC0415
    df = P.stock_jour()
    tuile = next(k for k in generate_dashboard(df, "", llm=False, dictionnaire=[], composition=True)["kpis"]
                 if k["colonne"] == "file_active")
    assert tuile["nom"] == "File active (mai 2025)"
    assert tuile["valeur"] == df[df["date"].str.startswith("2025-05")]["file_active"].sum()


def test_libelles_harmonises_sans_toucher_au_fichier_recu():
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    from hermes_da.adapters.dashboard import harmoniser_libelles  # noqa: PLC0415
    df = P.libelles()
    df.attrs["agrege"] = True
    avant = df.copy()
    h = harmoniser_libelles(df)
    assert sorted(h["ville"].unique()) == ["Abidjan", "Bouaké", "Daloa", "Korhogo"]
    assert h["cas"].sum() == df["cas"].sum() and h.attrs == {"agrege": True}
    assert df.equals(avant)                                  # le tableau reçu est intact
    propre = J.ventes()
    assert harmoniser_libelles(propre) is propre             # rien à changer : aucune copie


def test_baisse_due_a_un_manque_le_dit(tmp_path):
    rel = _releves(tmp_path, _page(P.trous()), [{}])[0]
    courbe = next(c for c in rel["cartes"] if c["type"] == "courbe")
    assert "moins de valeurs déclarées en mai 25" in courbe["texte"]
    assert "la baisse peut venir des données manquantes" in courbe["texte"]
