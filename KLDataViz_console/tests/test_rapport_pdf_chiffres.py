# -*- coding: utf-8 -*-
"""Banc des chiffres du rapport PDF : le rapport est assemblé pour des fichiers fictifs, son contenu (paragraphes et
tableaux) est relevé au moment de l'assemblage, puis chaque chiffre est recalculé avec pandas, indépendamment :
statistiques descriptives, qualité par colonne, modalités, corrélations, données des graphiques et chiffres cités dans
leur lecture. `test_le_banc_voit_une_erreur` glisse des erreurs dans le rapport pour prouver que le banc les voit."""
import os
import re
import sys

import numpy as np
import pandas as pd
import pytest

from _harness import APP, reset_app_modules

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
import jeux_pieges as P  # noqa: E402
import jeux_reference as J  # noqa: E402
import oracle_chiffres as O  # noqa: E402

JEUX = {"sante": J.sante, "ventes": J.ventes, "sans_dates": J.sans_dates, "trous": P.trous, "negatifs": P.negatifs}
OUI = {"oui", "yes", "vrai", "true", "1"}


# ---------------------------------------------------------------- relevé du rapport
def _texte(x):
    from reportlab.platypus import Paragraph, Table  # noqa: PLC0415
    if isinstance(x, Paragraph):
        return x.getPlainText()
    if isinstance(x, Table):
        return " ".join(_texte(c) for r in x._cellvalues for c in r)
    if isinstance(x, (list, tuple)):
        return " ".join(_texte(y) for y in x)
    return str(x)


def _aplatir(fs):
    for f in fs:
        sous = getattr(f, "_content", None) or getattr(f, "_flowables", None)
        if isinstance(sous, (list, tuple)):
            yield from _aplatir(sous)
        else:
            yield f


def releve(story):
    """[("p", texte)] et [("t", [[cellule, …], …])] dans l'ordre du rapport."""
    from reportlab.platypus import Paragraph, Table  # noqa: PLC0415
    out = []
    for f in _aplatir(story):
        if isinstance(f, Paragraph):
            out.append(("p", f.getPlainText()))
        elif isinstance(f, Table):
            out.append(("t", [[_texte(c) for c in r] for r in f._cellvalues]))
    return out


def tableau(rel, entete):
    """Lignes du tableau dont la première ligne commence par `entete`."""
    for genre, x in rel:
        if genre == "t" and x and x[0][:len(entete)] == entete:
            return x[1:]
    return []


@pytest.fixture(scope="module")
def rapports(tmp_path_factory):
    reset_app_modules()
    sys.path.insert(0, os.path.join(APP, "..", "HERMES_DA"))
    import pdf_report  # noqa: PLC0415
    import server  # noqa: PLC0415
    capt = {}
    origine = pdf_report.SimpleDocTemplate.build

    def build(self, story, *a, **k):
        capt["story"] = list(story)
        return origine(self, story, *a, **k)
    pdf_report.SimpleDocTemplate.build = build
    d = tmp_path_factory.mktemp("rapports")
    out = {}
    try:
        for nom, f in JEUX.items():
            src = str(d / f"{nom}.csv")
            f().to_csv(src, index=False)
            r = server.analyze(src)
            pdf_report.build(r, str(d / f"{nom}.pdf"))
            df, _ = server._read_df(src)
            out[nom] = (df, r, releve(capt["story"]))
    finally:
        pdf_report.SimpleDocTemplate.build = origine
    return out


def _colonne(r, libelle):
    """Libellé affiché → nom de colonne (les libellés viennent de l'analyse)."""
    inv = {str(v).strip().lower(): k for k, v in (r.get("libelles") or {}).items()}
    inv.update({str(k).strip().lower(): k for k in (r.get("libelles") or {})})
    inv.update({str(k).replace("_", " ").strip().lower(): k for k in (r.get("libelles") or {})})
    return inv.get(str(libelle).strip().lower())


def _egal(texte, attendu, quoi):
    try:
        ok = O.egal_affiche(texte.replace("−", "-"), attendu)
    except ValueError:
        return [f"{quoi} : « {texte} » illisible"]
    return [] if ok else [f"{quoi} : « {texte} » affiché, {attendu:.6g} attendu"]


# ---------------------------------------------------------------- contrôles
def stats(df, r, rel):
    ecarts = []
    for row in tableau(rel, ["Variable", "Moyenne"]):
        col = _colonne(r, row[0])
        if col is None:
            ecarts.append(f"statistiques : colonne « {row[0]} » introuvable")
            continue
        v = pd.to_numeric(df[col], errors="coerce").dropna()
        for texte, att, quoi in zip(row[1:6], (v.mean(), v.std(), v.min(), v.median(), v.max()),
                                    ("moyenne", "écart-type", "min", "médiane", "max")):
            ecarts += _egal(texte, float(att), f"{col} {quoi}")
    return ecarts


def qualite_colonnes(df, r, rel):
    ecarts = []
    for row in tableau(rel, ["Colonne", "Type", "Valeurs distinctes"]):
        col = _colonne(r, row[0])
        if col is None:
            continue
        s = df[col]
        ecarts += _egal(row[2], float(s.nunique(dropna=True)), f"{col} distinctes")
        ecarts += _egal(row[3], float(s.isna().sum()), f"{col} manquantes")
        ecarts += _egal(row[4], float(s.isna().mean() * 100), f"{col} % manquant")
    return ecarts


def modalites(df, r, rel):
    ecarts = []
    for row in tableau(rel, ["Colonne", "Principales valeurs (effectif ; part des lignes)"]):
        col = _colonne(r, row[0])
        if col is None:
            continue
        vc = df[col].astype(str).value_counts()
        for val, n, p in re.findall(r"(.+?) \(([\d\s  ]+) ; ([\d,\s  ]+) %\)(?: · |$)", row[1]):
            val = val.strip()
            if val not in vc:
                ecarts.append(f"{col} : modalité « {val} » absente du fichier")
                continue
            ecarts += _egal(n, float(vc[val]), f"{col}={val} effectif")
            ecarts += _egal(p, float(vc[val] / len(df) * 100), f"{col}={val} part")
    return ecarts


def correlations(df, r, rel):
    ecarts = []
    for row in tableau(rel, ["Variable A", "Variable B", "r"]):
        a, b = _colonne(r, row[0]), _colonne(r, row[1])
        if a is None or b is None:
            continue
        x, y = pd.to_numeric(df[a], errors="coerce"), pd.to_numeric(df[b], errors="coerce")
        ecarts += _egal(row[2], float(x.corr(y)), f"r({a}, {b})")
    return ecarts


def donnees_graphiques(df, r):
    """Chaque classement et chaque répartition « X par Y » refait avec pandas."""
    ecarts = []
    for c in r.get("smart_charts") or []:
        m = re.match(r"^(?:Répartition de |Taux « )?(.+?)(?: »)? par (.+)$", c.get("title") or "")
        if c.get("kind") not in ("ranking", "share") or not m or len(c.get("values") or []) != 1:
            continue
        mesure, dim = _colonne(r, m.group(1)), _colonne(r, m.group(2))
        if mesure is None or dim is None:
            ecarts.append(f"graphique « {c['title']} » : colonnes introuvables")
            continue
        g = df.groupby(df[dim].astype(str))[mesure]
        sous = str(c.get("subtitle") or "")
        if sous.startswith("Part de « Oui »") or c.get("title", "").startswith("Taux « "):
            t = df[mesure].astype(str).str.strip().str.lower()
            att = (t.isin(OUI) & df[mesure].notna()).groupby(df[dim].astype(str)).sum() / \
                df[mesure].notna().groupby(df[dim].astype(str)).sum() * 100
        elif c.get("agg") == "mean" or sous.startswith("Moyenne"):
            att = g.apply(lambda s: pd.to_numeric(s, errors="coerce").mean())
        else:
            att = g.apply(lambda s: pd.to_numeric(s, errors="coerce").sum())
        for lab, v in zip(c["labels"], c["values"][0]):
            if str(lab) not in att.index:
                ecarts.append(f"« {c['title']} » : groupe « {lab} » absent du fichier")
            elif not np.isclose(float(v), float(att[str(lab)]), rtol=1e-3, atol=0.01):
                ecarts.append(f"« {c['title']} » : {lab} = {v} dans le graphique, {att[str(lab)]:.6g} dans le fichier")
    return ecarts


def lectures(r, rel):
    """Chaque chiffre de la lecture d'un classement ou d'une répartition, recalculé depuis les données du graphique."""
    ecarts, paras = [], [x for g, x in rel if g == "p"]
    for i, c in enumerate(r.get("smart_charts") or []):
        titre = c.get("title") or ""
        lec = next((paras[k + 2] for k, x in enumerate(paras) if x.endswith(f"— {titre}") and k + 2 < len(paras)
                    and paras[k + 2].startswith("Lecture")), None)
        if lec is None or c.get("kind") not in ("ranking", "share") or len(c.get("values") or []) != 1:
            continue
        v = [float(x) for x in c["values"][0]]
        paires = sorted(zip(c["labels"], v), key=lambda x: -x[1])
        tot = sum(v)
        if c["kind"] == "share":
            m = re.search(r"plus grande part \(([\d,\s  ]+) %\)", lec)
            if m:
                ecarts += _egal(m.group(1), paires[0][1] / tot * 100, f"« {titre} » part du premier")
            m = re.search(r"cumulent ([\d,\s  ]+) %", lec)
            if m:
                ecarts += _egal(m.group(1), (paires[0][1] + paires[1][1]) / tot * 100, f"« {titre} » deux premières")
        else:
            m = re.search(r"arrive en tête avec (\d[\d\s  ]*(?:,\d+)?)", lec)
            if m:
                ecarts += _egal(m.group(1).strip(), paires[0][1], f"« {titre} » premier")
                if not lec.split("Lecture : ")[-1].startswith(str(paires[0][0])):
                    ecarts.append(f"« {titre} » : le premier cité n'est pas {paires[0][0]}")
            m = re.search(r"soit ([\d,\s  ]+) % du total affiché", lec)
            if m:
                ecarts += _egal(m.group(1), paires[0][1] / tot * 100, f"« {titre} » part du premier")
            m = re.search(r"de 1 à ([\d,\s  ]+)\.", lec)
            if m and paires[-1][1] > 0:
                ecarts += _egal(m.group(1), paires[0][1] / paires[-1][1], f"« {titre} » écart premier/dernier")
            m = re.search(r"(\d+) élément\(s\) sur (\d+) se situent au-dessus", lec)
            ref = (c.get("ref") or {}).get("value")
            if m and ref is not None:
                if int(m.group(1)) != sum(1 for x in v if x > ref) or int(m.group(2)) != len(v):
                    ecarts.append(f"« {titre} » : {m.group(0)} affiché")
            m = re.search(r"3 premiers concentrent ([\d,\s  ]+) %", lec)
            if m:
                ecarts += _egal(m.group(1), sum(x for _, x in paires[:3]) / tot * 100, f"« {titre} » 3 premiers")
    return ecarts


CONTROLES = {"statistiques": stats, "qualité par colonne": qualite_colonnes, "modalités": modalites,
             "corrélations": correlations}


@pytest.mark.parametrize("nom", sorted(JEUX))
def test_chiffres_du_rapport_pdf(rapports, nom):
    df, r, rel = rapports[nom]
    ecarts, vus = [], {}
    for quoi, f in CONTROLES.items():
        e = f(df, r, rel)
        vus[quoi] = e
        ecarts += e
    ecarts += donnees_graphiques(df, r) + lectures(r, rel)
    assert tableau(rel, ["Variable", "Moyenne"]), "tableau des statistiques introuvable : le banc ne lit plus le rapport"
    assert not ecarts, "\n".join(ecarts[:20])


def test_le_banc_voit_une_erreur(rapports):
    """Erreurs glissées dans le relevé du rapport : chacune doit être vue."""
    df, r, rel = rapports["ventes"]
    import copy
    faux = copy.deepcopy(rel)
    stats_rows = next(x for g, x in faux if g == "t" and x[0][:2] == ["Variable", "Moyenne"])
    stats_rows[1][1] = "9 999"                                         # moyenne fausse
    assert stats(df, r, faux)
    qual = next(x for g, x in faux if g == "t" and x[0][:3] == ["Colonne", "Type", "Valeurs distinctes"])
    qual[1][2] = "999"                                                 # nombre de valeurs distinctes faux
    assert qualite_colonnes(df, r, faux)
    r2 = copy.deepcopy(r)
    classement = next(c for c in r2["smart_charts"] if c["kind"] == "ranking")
    classement["values"][0][0] *= 1.5                                 # donnée de graphique fausse
    assert donnees_graphiques(df, r2)
    paras = [i for i, (g, x) in enumerate(faux) if g == "p" and x.startswith("Lecture") and "du total affiché" in x]
    i = paras[0]
    faux[i] = ("p", re.sub(r"soit [\d,\s  ]+ %", "soit 99,9 %", faux[i][1]))   # part fausse dans la lecture
    assert lectures(r, faux)
