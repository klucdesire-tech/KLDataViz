# -*- coding: utf-8 -*-
"""Calcul de référence des chiffres du tableau de bord, écrit indépendamment de la page.

La page calcule tout en JavaScript, dans le navigateur. Ici, les mêmes chiffres sont recalculés avec pandas, à partir
du fichier d'origine et de la définition de chaque calcul, sans réutiliser une ligne du code testé :
- somme, moyenne, médiane, minimum, maximum : sur les valeurs numériques de la colonne, les vides ignorés ;
- compte : les lignes, ou les cases remplies de la colonne ;
- taux : la part de « oui » d'une colonne oui/non, en %, ou la moyenne d'une colonne de taux (×100 si elle va de 0 à 1) ;
- dernier (stocks : population, file active…) : la somme sur le dernier mois présent, et non sur le dernier jour ;
- une période (trimestre, année) n'est complète que si elle contient tous ses mois : 3 pour un trimestre, 12 pour un an ;
- un groupe se compare d'une année sur l'autre sur les mois qu'il a déclarés les deux fois (un mois manquant n'est pas
  une baisse) ; un groupe présent une seule année compte tel quel ;
- une somme dont la dernière période a moins de valeurs déclarées que la précédente doit le dire ;
- les libellés d'un groupe ignorent les espaces en trop et la casse (« Oui » et « oui » sont le même groupe).

`comparer` rend la liste des écarts entre ce que la page affiche et la référence : une liste vide est un succès."""
import math
import re

import numpy as np
import pandas as pd

OUI = {"oui", "yes", "true", "vrai"}
NON = {"non", "no", "false", "faux"}
VIDES = {"", "nan", "none", "null", "na", "n/a"}
MOIS_PAR = {"Q": 3, "Y": 12}


# ---------------------------------------------------------------- calculs de base
TOTAL = re.compile(r"^\s*(sous[-\s]?|grand\s+)?total(e|s|aux)?(\s+g[ée]n[ée]ral)?\s*$|^\s*ensemble\s*$", re.I)


def preparer(df):
    """Libellés de groupe ramenés à une forme : espaces en trop retirés, casse ignorée (forme la plus fréquente) ;
    lignes « Total » écartées : elles répètent la somme des autres lignes."""
    df = df.copy()
    texte = [c for c in df.columns if df[c].dtype == object or pd.api.types.is_string_dtype(df[c])]
    if texte and "niveau" not in df.columns:
        cumul = df[texte].apply(lambda s: s.astype(str).str.match(TOTAL) & s.notna()).any(axis=1)
        if cumul.any() and cumul.mean() <= .2 and (~cumul).sum() >= 2:
            df = df[~cumul]
    for c in df.columns:
        if df[c].dtype != object and not pd.api.types.is_string_dtype(df[c]):
            continue
        txt = df[c].map(lambda v: " ".join(v.split()) if isinstance(v, str) else v)
        cles = txt.map(lambda v: v.casefold() if isinstance(v, str) else v)
        forme = {}
        for k, v in zip(cles, txt):
            if isinstance(v, str):
                forme.setdefault(k, {}).setdefault(v, 0)
                forme[k][v] += 1
        choix = {k: max(f.items(), key=lambda kv: kv[1])[0] for k, f in forme.items()}
        df[c] = [choix[k] if isinstance(v, str) else v for k, v in zip(cles, txt)]
    return df


def nombres(s):
    return pd.to_numeric(s, errors="coerce").dropna()


def mois(df):
    return df["__p"].astype(str).str[:7].where(df["__p"].notna())


def cle(df, gran):
    m = mois(df)
    if gran == "Y":
        return m.str[:4]
    if gran == "Q":
        return m.str[:4] + "-T" + ((m.str[5:7].astype(float) - 1) // 3 + 1).astype("Int64").astype(str)
    return m


def agg(df, col, a):
    a = (a or "").lower()
    if a == "dernier":
        if "__p" not in df or df["__p"].isna().all():
            return None
        m = mois(df)
        df, a = df[m == m.max()], "somme"
    if a in ("compte", "count", "effectif") or not col:
        if not col:
            return len(df)
        t = df[col].astype(str).str.strip().str.lower()
        return int((df[col].notna() & ~t.isin(VIDES)).sum())
    if a in ("taux", "rate", "pct", "proportion"):
        t = df[col].dropna().astype(str).str.strip().str.lower()
        t = t[~t.isin(VIDES)]
        if not len(t):
            return None
        if t.isin(OUI | NON).all():
            return 100.0 * t.isin(OUI).sum() / len(t)
        v = nombres(t)
        if not len(v):
            return None
        return float(v.mean() * 100) if v.max() <= 1 else float(v.mean())
    v = nombres(df[col])
    if not len(v):
        return None
    return {"somme": v.sum(), "sum": v.sum(), "moyenne": v.mean(), "mean": v.mean(), "max": v.max(), "min": v.min(),
            "mediane": v.median(), "médiane": v.median(), "median": v.median()}.get(a, v.sum()).item()


def groupes(df, dim, col, a, top=None):
    """{groupe: valeur}, du plus grand au plus petit ; les groupes vides et les valeurs absentes écartés."""
    d = df[df[dim].notna() & ~df[dim].astype(str).str.strip().str.lower().isin(VIDES)]
    sortie = {}
    for k, g in d.groupby(d[dim].astype(str), sort=False):
        v = agg(g, col, a)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            sortie[k] = v
    ordre = sorted(sortie.items(), key=lambda kv: -kv[1])
    return dict(ordre[:top] if top else ordre)


def serie(df, col, a, gran):
    k = cle(df, gran)
    sortie = {}
    for p, g in df[k.notna()].groupby(k[k.notna()]):
        v = agg(g, col, a)
        if v is not None:
            sortie[p] = v
    return dict(sorted(sortie.items()))


def periodes_completes(df, s, gran, partiel=None):
    """Retire la dernière période si elle n'est pas finie : mois en cours, trimestre ou année sans tous ses mois."""
    if len(s) < 2:
        return s
    der = list(s)[-1]
    k, m = cle(df, gran), mois(df)
    complet = True
    if gran in MOIS_PAR:
        complet = m[k == der].nunique() >= MOIS_PAR[gran]
    if partiel and cle(pd.DataFrame({"__p": [partiel]}), gran).iloc[0] == der:
        complet = False
    return s if complet else dict(list(s.items())[:-1])


def correlation(x, y):
    return float(np.corrcoef(x, y)[0, 1])


# ---------------------------------------------------------------- lecture des nombres affichés
def lire(texte):
    """« 12 345 » → (12345, 0.5) ; « 1,3 M » → (1.3e6, 5e4) ; « 104 k » → (104000, 500) ; « −688 » → (-688, 0.5).
    Rend la valeur et la marge d'arrondi de l'affichage."""
    t = str(texte).replace(" ", "").replace(" ", "").replace(" ", "").replace("−", "-").replace("+", "")
    t = t.replace("%", "")
    mult = 1
    if t.endswith("M"):
        mult, t = 1e6, t[:-1]
    elif t.endswith("k"):
        mult, t = 1e3, t[:-1]
    t = t.replace(",", ".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        raise ValueError(f"nombre illisible : {texte!r}")
    dec = len(t.split(".")[1]) if "." in t else 0
    return float(t) * mult, 0.5 * 10 ** -dec * mult + 1e-9


def proche(affiche, attendu, relatif=1e-9):
    if affiche is None or attendu is None:
        return affiche is None and attendu is None
    return abs(float(affiche) - float(attendu)) <= max(1e-6, abs(float(attendu)) * relatif)


def egal_affiche(texte, attendu):
    v, marge = lire(texte)
    return abs(v - attendu) <= marge


# ---------------------------------------------------------------- comparaison carte par carte
MOIS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def etiquette(p, gran):
    if gran == "Y":
        return p
    if gran == "Q":
        return p[5:] + " " + p[2:4]
    return MOIS_FR[int(p[5:7]) - 1] + " " + p[2:4]


def _evolution(a, b, nature, ecarts, ou):
    """Vérifie « en hausse de 6,2 % », « en baisse de 3 points », « sans changement »."""
    texte = str(ecarts.get("_texte", ""))
    d = b - a
    if nature == "taux" and max(abs(a), abs(b)) > 2:
        attendu, unite = abs(d), "point"
    else:
        if not a:
            return
        attendu, unite = abs(d / a * 100), "%"
    t = ecarts["_valeur"]
    if t == "sans changement":
        if round(attendu, 1) != 0:
            ou.append(f"« sans changement » alors que l'écart vaut {attendu:.2f} {unite}")
        return
    sens = "hausse" if d > 0 else "baisse"
    if sens not in t:
        ou.append(f"sens faux : « {t} » pour {a} → {b} ({texte})")
    m = re.search(r"de ([\d\s ,.]+)", t)
    if m and not egal_affiche(m.group(1).strip(), attendu):
        ou.append(f"évolution affichée « {t} », attendue {attendu:.3f} {unite} ({a} → {b})")


def comparer_kpi(df, k):
    ou = []
    if k.get("formule") or k.get("hors") or k.get("approx"):
        return ou  # formules d'Adifa’ et estimations : vérifiées par leurs propres tests
    att = agg(df, k["colonne"], k["agregation"])
    if not proche(k["valeur"], att):
        ou.append(f"tuile « {k['nom']} » : {k['valeur']} affiché, {att} attendu")
    if k.get("dimension") and k.get("tete"):
        g = groupes(df, k["dimension"], k["colonne"], k["agregation"])
        if g:
            haut = max(g.values())
            if not proche(k["tete"][1], haut) or not proche(g.get(k["tete"][0]), haut):
                ou.append(f"tuile « {k['nom']} », en tête : {k['tete']} affiché, {max(g, key=g.get)} ({haut}) attendu")
    return ou


def comparer_carte(df, c, gran, partiel=None):
    t, d, m, ou = c["type"], c["donnees"], c["message"], []
    col, a, dim = c["mesure"], c["agregation"], c["dimension"]
    if d and d.get("erreur"):
        return [f"{t} : erreur JavaScript {d['erreur']}"]
    if isinstance(m, dict) and m.get("erreur"):
        return [f"{t} : erreur dans la phrase {m['erreur']}"]

    if t in ("classement", "anneau", "treemap"):
        top = {"classement": 12, "anneau": 5, "treemap": 40}[t]
        att = groupes(df, dim, col, a)
        aff = dict(d["groupes"])
        for k, v in aff.items():
            if not proche(v, att.get(k)):
                ou.append(f"{t} {col} par {dim} : {k} = {v} affiché, {att.get(k)} attendu")
        manquants = [k for k in list(att)[:top] if k not in aff and att[k] != list(att.values())[min(top, len(att)) - 1]]
        if manquants:
            ou.append(f"{t} {col} par {dim} : groupes absents du haut du classement {manquants[:3]}")
        if m and t == "classement":
            premier, second = list(att.items())[:2]
            if m["premier"] != premier[0] and not proche(att.get(m["premier"]), premier[1]):
                ou.append(f"classement : « {m['premier']} » cité en tête, {premier[0]} attendu")
            if not egal_affiche(m["valeur"], premier[1]) or not egal_affiche(m["valeur2"], second[1]):
                ou.append(f"classement : valeurs citées {m['valeur']} et {m['valeur2']}, attendues {premier[1]} et {second[1]}")
        if m and t in ("anneau", "treemap"):
            positifs = {k: v for k, v in att.items() if v > 0}
            total = sum(positifs.values())
            premier = max(positifs, key=positifs.get)
            if not egal_affiche(m["part"], 100 * positifs[premier] / total):
                ou.append(f"{t} {col} : part citée {m['part']} pour {m['premier']}, attendue "
                          f"{100 * positifs[premier] / total:.2f} % du total ({len(positifs)} groupes)")

    elif t == "courbe":
        s = serie(df, col, a, gran)
        aff = dict((p, v) for p, v in d["serie"])
        for p, v in s.items():
            if not proche(aff.get(p), v):
                ou.append(f"courbe {col} : {p} = {aff.get(p)} affiché, {v} attendu")
        comp = periodes_completes(df, s, gran, partiel)
        if m and len(comp) >= 2:
            (pa, va), (pb, vb) = list(comp.items())[-2:]
            if m["periode"] != etiquette(pb, gran):
                ou.append(f"courbe {col} : phrase sur « {m['periode']} », dernière période complète {etiquette(pb, gran)}")
            elif not egal_affiche(m["dernier"], vb):
                ou.append(f"courbe {col} : {m['dernier']} cité pour {m['periode']}, {vb} attendu")
            else:
                _evolution(va, vb, c.get("nature"), {"_valeur": m["evolution"], "_texte": c.get("texte")}, ou)
            if str(a).lower() in ("somme", "sum", "compte", "count", "dernier"):
                k = cle(df, gran)
                na, nb = (nombres(df.loc[k == p, col]).size for p in (pa, pb))
                signale = "moins de valeurs déclarées" in str(c.get("texte") or "")
                if na and nb < na * .9 and not signale:
                    ou.append(f"courbe {col} : {nb} valeurs en {pb} contre {na} en {pa}, la phrase ne le signale pas")
                if signale and not (na and nb < na * .9):
                    ou.append(f"courbe {col} : manque signalé à tort ({nb} contre {na})")

    elif t == "chaleur":
        if d:
            k = cle(df, gran)
            for case, v in d["M"].items():
                g, p = case.split("|")
                att = agg(df[(df[dim].astype(str) == g) & (k == p)], col, a) if ((df[dim].astype(str) == g) & (k == p)).any() else None
                if not proche(v, att):
                    ou.append(f"chaleur {col} : {g} en {p} = {v} affiché, {att} attendu")
            if m:
                vals = [v for v in d["M"].values() if v is not None]
                if not egal_affiche(m["valeur"], max(vals)):
                    ou.append(f"chaleur {col} : maximum cité {m['valeur']}, attendu {max(vals)}")

    elif t in ("pente", "passage"):
        mo = mois(df)
        annees = sorted(mo.dropna().str[:4].unique())
        if len(annees) >= 2:
            a0, a1 = annees[-2], annees[-1]
            communs = sorted(set(mo[mo.str[:4] == a0].str[5:7]) & set(mo[mo.str[:4] == a1].str[5:7]))
            if d and d.get("communs") is not None and sorted(d["communs"]) != communs:
                ou.append(f"{t} {col} : mois comparés {d['communs']}, mois communs {communs}")
            if d:
                A = df[(mo.str[:4] == a0) & mo.str[5:7].isin(communs)]
                B = df[(mo.str[:4] == a1) & mo.str[5:7].isin(communs)]
                ga, gb = {}, {}
                cles_groupes = (set(A[dim].dropna().astype(str)) | set(B[dim].dropna().astype(str)))
                for k in [k for k in cles_groupes if k.strip().lower() not in VIDES]:
                    Ak, Bk = A[A[dim].astype(str) == k], B[B[dim].astype(str) == k]
                    if len(Ak) and len(Bk):  # à périmètre constant : les mois que ce groupe a déclarés les deux années
                        m0, m1 = set(mois(Ak).str[5:7]), set(mois(Bk).str[5:7])
                        Ak, Bk = Ak[mois(Ak).str[5:7].isin(m0 & m1)], Bk[mois(Bk).str[5:7].isin(m0 & m1)]
                    va, vb = (agg(Ak, col, a) if len(Ak) else None), (agg(Bk, col, a) if len(Bk) else None)
                    if va is not None:
                        ga[k] = va
                    if vb is not None:
                        gb[k] = vb
                for k, v0, v1 in d["ecarts"]:
                    if not proche(v0, ga.get(k)) or not proche(v1, gb.get(k)):
                        ou.append(f"{t} {col} : {k} {v0} → {v1} affiché, {ga.get(k)} → {gb.get(k)} attendu")
                if m and t == "passage":
                    t0, t1 = sum(ga.values()), sum(gb.values())
                    _evolution(t0, t1, c.get("nature"), {"_valeur": m["evolution"], "_texte": c.get("texte")}, ou)
                if m:
                    e = {k: gb[k] - ga[k] for k in set(ga) & set(gb)} if t == "pente" else \
                        {k: gb.get(k, 0) - ga.get(k, 0) for k in set(ga) | set(gb)}
                    k_max = max(e, key=lambda k: abs(e[k]))
                    if not egal_affiche(m["ecart"], e[k_max]) or (m["premier"] != k_max and not proche(abs(e.get(m["premier"], 0)), abs(e[k_max]))):
                        ou.append(f"{t} {col} : plus fort écart cité {m['premier']} {m['ecart']}, attendu {k_max} {e[k_max]:+}")

    elif t == "cible":
        obj = c["objectif"]
        R = df[nombres(df[col]).reindex(df.index).notna() & nombres(df[obj]).reindex(df.index).notna()]
        mes, cib = agg(R, col, a), agg(R, obj, a)
        if not proche(d["mesure"], mes) or not proche(d["objectif"], cib):
            ou.append(f"cible {col} : {d['mesure']} sur {d['objectif']} affiché, {mes} sur {cib} attendu")
        if m and m.get("taux") and cib:
            if not egal_affiche(m["taux"], 100 * mes / cib):
                ou.append(f"cible {col} : taux cité {m['taux']}, attendu {100 * mes / cib:.2f} %")
        if dim and m and m.get("bilan"):
            gm, go = groupes(R, dim, col, a), groupes(R, dim, obj, a)
            n = [k for k in gm if go.get(k) and go[k] > 0]
            atteints = sum(1 for k in n if gm[k] >= go[k])
            if m["bilan"] != f"{atteints} sur {len(n)} à l'objectif ou au-dessus":
                ou.append(f"cible {col} : « {m['bilan']} », attendu {atteints} sur {len(n)}")

    elif t == "bulles":
        gx, gy = groupes(df, dim, col, a), groupes(df, dim, c["mesure_y"], c["agregation_y"])
        for k, x, y in d["e"]:
            if not proche(x, gx.get(k)) or not proche(y, gy.get(k)):
                ou.append(f"bulles {col} / {c['mesure_y']} : {k} ({x}, {y}) affiché, ({gx.get(k)}, {gy.get(k)}) attendu")
        if m:
            ks = [k for k in gx if k in gy]
            r = correlation([gx[k] for k in ks], [gy[k] for k in ks])
            lien = "vont de pair" if r >= .7 else "évoluent en sens inverse" if r <= -.7 else "sans lien net"
            if m["lien"] != lien:
                ou.append(f"bulles : « {m['lien']} » alors que la corrélation vaut {r:.2f} ({lien})")

    elif t == "comparee":
        if d:
            for x in d["S"]:
                s = serie(df[df[dim].astype(str) == x["d"]], col, a, gran)
                for p, v in zip(d["P"], x["vals"]):
                    if not proche(v, s.get(p)):
                        ou.append(f"comparée {col} : {x['d']} en {p} = {v} affiché, {s.get(p)} attendu")
            comp = periodes_completes(df, serie(df, col, a, gran), gran, partiel)
            if d["P"] != list(comp):
                ou.append(f"comparée {col} : périodes {d['P'][-3:]} affichées, périodes complètes {list(comp)[-3:]}")

    elif t == "histogramme":
        v = nombres(df[col])
        if d and len(v):
            b, _ = np.histogram(v, bins=10, range=(v.min(), v.max()))
            if list(b) != list(d["b"]):
                ou.append(f"histogramme {col} : tranches {d['b']} affichées, {list(b)} attendues")
            if m:
                i = int(np.argmax(b))
                if not egal_affiche(m["part"], 100 * b[i] / len(v)):
                    ou.append(f"histogramme {col} : part citée {m['part']}, attendue {100 * b[i] / len(v):.2f} %")
    return ou


def comparer(df, releve, partiel=None):
    """Tous les écarts d'un relevé (une page, un filtre, un regroupement)."""
    gran = releve["gran"]
    ou = []
    for k in releve["kpis"]:
        ou += comparer_kpi(df, k)
    for c in releve["cartes"]:
        ou += comparer_carte(df, c, gran, partiel)
    return ou
