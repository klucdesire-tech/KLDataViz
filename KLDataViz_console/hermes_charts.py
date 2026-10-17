"""Hermes choisit les graphiques d'un fichier et en rédige la lecture.

Deux appels à l'IA, chacun sur un résumé (jamais le fichier entier) :
1. `propose` : à partir du profil des colonnes, Hermes écrit une « recette » par graphique
   (type, mesure, agrégation, axe, série, filtre). Le moteur l'exécute avec pandas en vérifiant chaque
   colonne : une recette invalide est écartée, aucune valeur n'est inventée par l'IA.
2. `interpret` : Hermes lit les chiffres réellement calculés et rédige la lecture de chaque graphique.

Les graphiques produits ont le même format que `server._smart_charts` (kind, type, title, labels, values…),
plus `question` et `lecture`. Sans IA ou en cas d'échec, on retombe sur les graphiques automatiques.
"""
import json
import re

import numpy as np
import pandas as pd

MAX_CHARTS = 6
TYPES = {"evolution", "classement", "repartition", "comparaison", "distribution", "relation"}
AGGS = {"somme": "sum", "moyenne": "mean", "nombre": "count", "derniere": "last", "dernière": "last",
        "sum": "sum", "mean": "mean", "count": "count", "last": "last"}


# --------------------------------------------------------------------------- #
# Profil envoyé à l'IA
# --------------------------------------------------------------------------- #
def profile(df, file_name, period_of):
    """Résumé des colonnes : type, valeurs fréquentes, plages, colonnes de période."""
    cols = []
    for c in df.columns:
        s = df[c]
        info = {"nom": str(c), "valeurs_distinctes": int(s.nunique(dropna=True)),
                "manquantes_pct": round(float(s.isna().mean() * 100), 1)}
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            v = s.dropna()
            info.update(type="nombre", min=_r(v.min()), max=_r(v.max()), moyenne=_r(v.mean()),
                        entre_0_et_1=bool(len(v) and v.between(0, 1).all()))
        else:
            per = period_of(s)
            info["type"] = "periode" if per is not None else "texte"
            info["exemples"] = [str(x) for x in s.dropna().astype(str).value_counts().head(8).index]
        cols.append(info)
    return {"fichier": file_name, "lignes": int(len(df)), "colonnes": cols}


def _r(x):
    try:
        x = float(x)
        return round(x, 4) if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


SYSTEM_PROPOSE = (
    "Tu es Hermes, analyste de données en santé publique et en gestion. On te donne le profil des colonnes "
    "d'un fichier (pas les données). Choisis au plus {n} graphiques qui répondent aux questions qu'un "
    "responsable se pose vraiment sur ces données (évolution d'un indicateur clé, classement des sites ou "
    "districts, écart entre groupes, répartition, valeurs inhabituelles). Évite les graphiques redondants ou "
    "sans intérêt (identifiants, colonnes constantes, sommes de taux).\n"
    "Règles : utilise UNIQUEMENT des noms de colonnes présents dans le profil, écrits à l'identique. "
    "Agrégation « moyenne » pour un taux, un pourcentage, un score ; « somme » pour un flux (nouveaux cas, dépistés, "
    "consultations) ; « derniere » pour un STOCK mesuré à chaque période (file active, TX_CURR, patients sous traitement, "
    "stock, effectif présent) : un stock ne s'additionne jamais d'une période à l'autre, on garde la dernière période "
    "(une évolution d'un stock utilise « somme », calculée période par période) ; "
    "« nombre » pour compter des lignes (mesure = null). Une colonne de type « periode » sert d'axe pour "
    "une évolution.\n"
    "Types : evolution (axe = période ; serie optionnelle ≤ 4 valeurs), classement (axe = catégorie, barres "
    "triées), repartition (axe = catégorie ≤ 6 valeurs, part du total), comparaison (axe × serie, barres "
    "groupées), distribution (histogramme de la mesure), relation (nuage de points : axe et mesure numériques).\n"
    "Réponds avec un objet JSON valide et rien d'autre :\n"
    '{{"graphiques":[{{"type":"evolution|classement|repartition|comparaison|distribution|relation",'
    '"titre":"titre court qui dit ce qu\'on voit","question":"la question à laquelle il répond",'
    '"mesure":"colonne numérique ou null","agregation":"somme|moyenne|nombre|derniere","axe":"colonne",'
    '"serie":"colonne ou null","filtre":{{"colonne":"…","valeur":"…"}} ou null,"top":12}}]}}'
)

SYSTEM_INTERPRET = (
    "Tu es Hermes, analyste de données. Pour chaque graphique fourni (avec ses valeurs exactes), écris une "
    "lecture de 2 à 3 phrases pour un décideur non technique : le constat principal, l'écart ou la tendance "
    "chiffrés, et ce qui mérite d'être vérifié. Utilise UNIQUEMENT les chiffres fournis, arrondis simplement, "
    "au format français (1 234,5). N'invente aucune cause : une explication possible est présentée comme une "
    "hypothèse. Termine par une synthèse générale de 2 phrases.\n"
    "Réponds avec un objet JSON valide et rien d'autre :\n"
    '{"lectures":[{"index":0,"lecture":"…"}],"synthese":"…"}'
)


# --------------------------------------------------------------------------- #
# Exécution des recettes
# --------------------------------------------------------------------------- #
def _col(df, name):
    if name is None:
        return None
    if name in df.columns:
        return name
    low = {str(c).strip().lower(): c for c in df.columns}
    return low.get(str(name).strip().lower())


def _num(v):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else round(float(v), 4)


def _period_index(s, period_of):
    """Clé de tri chronologique et libellé (« 2025-04 ») pour une colonne de période ; None si ce n'en est pas une."""
    dt = period_of(s)
    if dt is None:
        return None
    return dt


def build_one(df, g, period_of):
    t = str(g.get("type") or "").lower()
    if t not in TYPES:
        raise ValueError(f"type inconnu : {t}")
    agg = AGGS.get(str(g.get("agregation") or "somme").lower(), "sum")
    mes, axe, ser = _col(df, g.get("mesure")), _col(df, g.get("axe")), _col(df, g.get("serie"))
    if g.get("mesure") and mes is None:
        raise ValueError(f"colonne absente : {g.get('mesure')}")
    if axe is None:
        raise ValueError(f"colonne absente : {g.get('axe')}")
    if g.get("serie") and ser is None:
        ser = None
    if mes is None:
        agg = "count"
    elif not pd.api.types.is_numeric_dtype(df[mes]):
        raise ValueError(f"{mes} n'est pas numérique")
    d = df
    f = g.get("filtre")
    if isinstance(f, dict) and _col(df, f.get("colonne")) is not None:
        fc = _col(df, f["colonne"])
        d = d[d[fc].astype(str).str.strip().str.lower() == str(f.get("valeur")).strip().lower()]
        if d.empty:
            raise ValueError("filtre sans ligne")
    last_label = None
    if agg == "last":  # stock : seule la dernière période compte, puis somme sur les lignes restantes
        if t == "evolution":
            agg = "sum"
        else:
            pcol = next((c for c in d.columns if c != mes and period_of(d[c]) is not None), None)
            if pcol is None:
                agg = "sum"
            else:
                dt = period_of(d[pcol])
                d = d[dt == dt.max()]
                last_label = str(dt.max().to_period("M"))
                agg = "sum"
    top = max(3, min(int(g.get("top") or 12), 15))
    title = str(g.get("titre") or "")[:90]
    sub = {"sum": "Somme", "mean": "Moyenne", "count": "Nombre de lignes"}[agg]
    if last_label:
        sub = f"Dernière période ({last_label})"
    if isinstance(f, dict) and f.get("valeur") is not None and not last_label:
        sub += f" · {f.get('colonne')} = {f.get('valeur')}"
    base = {"title": title, "question": str(g.get("question") or "")[:200], "hermes": True}

    def agg_by(frame, keys):
        if agg == "count":
            return frame.groupby(keys).size()
        return getattr(frame.groupby(keys)[mes], agg)()

    if t == "evolution":
        dt = _period_index(d[axe], period_of)
        if dt is None:
            raise ValueError(f"{axe} n'est pas une période")
        d = d.assign(_k=dt.dt.to_period("M").astype(str)).dropna(subset=["_k"])
        d = d[d["_k"] != "NaT"]
        series_names, values = [], []
        if ser is not None and 2 <= d[ser].nunique() <= 12:
            keep = (d.groupby(ser).size() if agg == "count" else d.groupby(ser)[mes].sum()).sort_values(ascending=False).head(4).index
            tab = agg_by(d[d[ser].isin(keep)], ["_k", ser]).unstack(ser).sort_index()
            labels = [str(x) for x in tab.index]
            for c in tab.columns:
                series_names.append(str(c))
                values.append([_num(v) for v in tab[c].values])
        else:
            s = agg_by(d, "_k").sort_index()
            labels = [str(x) for x in s.index]
            series_names, values = [str(mes or "Lignes")], [[_num(v) for v in s.values]]
        if len(labels) < 2:
            raise ValueError("moins de 2 périodes")
        return dict(base, kind="trend", type="line", subtitle=f"{sub} par mois", labels=labels, values=values,
                    seriesNames=series_names)
    if t in ("classement", "repartition"):
        s = agg_by(d, axe).dropna().sort_values(ascending=False)
        if len(s) < 2:
            raise ValueError("moins de 2 catégories")
        if t == "repartition":
            if (s < 0).any():
                raise ValueError("valeurs négatives")
            if len(s) > 6:
                s = pd.concat([s.head(5), pd.Series([s.iloc[5:].sum()], index=["Autres"])])
            return dict(base, kind="share", type="doughnut", subtitle="Part du total",
                        labels=[str(x) for x in s.index], values=[[_num(v) for v in s.values]])
        head = s.head(top)
        return dict(base, kind="ranking", type="bar", horizontal=True,
                    subtitle=sub + (f" · {len(head)} premiers sur {len(s)}" if len(s) > len(head) else "") + " · ligne : moyenne",
                    labels=[str(x) for x in head.index], values=[[_num(v) for v in head.values]],
                    seriesNames=[str(mes or "Lignes")], ref={"value": _num(s.mean()), "label": "Moyenne"})
    if t == "comparaison":
        if ser is None:
            raise ValueError("série manquante")
        keep_s = (d.groupby(ser).size() if agg == "count" else d.groupby(ser)[mes].sum()).sort_values(ascending=False).head(4).index
        keep_a = (d.groupby(axe).size() if agg == "count" else d.groupby(axe)[mes].sum()).sort_values(ascending=False).head(top).index
        tab = agg_by(d[d[ser].isin(keep_s) & d[axe].isin(keep_a)], [axe, ser]).unstack(ser).reindex(keep_a)
        return dict(base, kind="report", type="bar", subtitle=sub, labels=[str(x) for x in tab.index],
                    values=[[_num(v) if pd.notna(v) else 0 for v in tab[c].values] for c in tab.columns],
                    seriesNames=[str(c) for c in tab.columns])
    if t == "distribution":
        if mes is None:
            raise ValueError("mesure manquante")
        v = d[mes].dropna()
        lo, hi = v.quantile(0.01), v.quantile(0.99)
        v = v[(v >= lo) & (v <= hi)]
        if v.nunique() < 5:
            raise ValueError("trop peu de valeurs")
        counts, edges = np.histogram(v, bins=min(10, int(v.nunique())))
        f2 = lambda x: f"{x:,.0f}".replace(",", " ") if abs(x) >= 10 else f"{x:.2f}".replace(".", ",")
        return dict(base, kind="distribution", type="histogram", subtitle="Nombre de lignes par tranche",
                    labels=[f"{f2(edges[i])} – {f2(edges[i + 1])}" for i in range(len(counts))],
                    values=[[int(x) for x in counts]], seriesNames=["Lignes"])
    if t == "relation":
        if mes is None or not pd.api.types.is_numeric_dtype(d[axe]):
            raise ValueError("relation entre deux colonnes numériques")
        pts = d[[axe, mes]].dropna()
        if len(pts) < 5:
            raise ValueError("trop peu de points")
        r = float(pts[axe].corr(pts[mes]))
        if len(pts) > 400:
            pts = pts.sample(400, random_state=0)
        return dict(base, kind="relation", type="scatter", subtitle=f"Chaque point est une ligne · corrélation r = {r:.2f}".replace(".", ",", 1),
                    labels=[float(x) for x in pts[axe]], values=[[float(y) for y in pts[mes]]],
                    seriesNames=[str(mes)], xTitle=str(axe), yTitle=str(mes), trend=True)
    raise ValueError(t)


def build(df, specs, period_of):
    charts, errors = [], []
    for g in (specs or [])[:MAX_CHARTS + 2]:
        if not isinstance(g, dict):
            continue
        try:
            charts.append(build_one(df, g, period_of))
        except Exception as e:  # recette invalide : écartée, jamais réparée en silence
            errors.append(f"{g.get('titre') or g.get('type')} : {e}")
        if len(charts) >= MAX_CHARTS:
            break
    return charts, errors


# --------------------------------------------------------------------------- #
# Appels à l'IA
# --------------------------------------------------------------------------- #
def _compact(c):
    """Ce que l'IA relit d'un graphique : titre, sous-titre et valeurs (tronquées)."""
    labels = (c.get("labels") or [])[:20]
    out = {"titre": c.get("title"), "precision": c.get("subtitle"), "type": c.get("kind")}
    if c.get("kind") == "relation":
        out["correlation"] = c.get("subtitle")
        return out
    out["series"] = {str(n): dict(zip(map(str, labels), (v or [])[:20]))
                     for n, v in zip(c.get("seriesNames") or ["valeur"], c.get("values") or [])}
    if c.get("ref"):
        out["moyenne_de_reference"] = c["ref"].get("value")
    return out


def run(df, file_name, base_charts, llm, parse, period_of, progress=lambda _t: None, comprehension=None):
    """Graphiques choisis et commentés par Hermes ; repli sur base_charts (graphiques automatiques)."""
    if llm is None:
        return {"charts": base_charts, "ia": False, "source": "regles", "raison": "IA non configurée"}
    progress("Hermes étudie les colonnes du fichier…")
    prof = profile(df, file_name, period_of)
    if comprehension:  # rôles déjà établis à l'étape Vérifier (stock, flux, taux…) : même lecture du fichier
        prof["comprehension_etape_verifier"] = {k: comprehension.get(k) for k in ("resume", "grain", "colonnes")}
    content = llm([{"role": "system", "content": SYSTEM_PROPOSE.format(n=MAX_CHARTS)},
                   {"role": "user", "content": json.dumps(prof, ensure_ascii=False, default=str)}], 6000)
    data = parse(content) or {}
    charts, errors = build(df, data.get("graphiques") if isinstance(data, dict) else None, period_of)
    source = "hermes"
    if len(charts) < 2:  # recettes inutilisables : on commente les graphiques automatiques
        charts, source = list(base_charts), "automatiques"
    if not charts:
        return {"charts": [], "ia": True, "source": source, "erreurs": errors}
    progress("Hermes rédige la lecture des graphiques…")
    payload = [dict(_compact(c), index=i) for i, c in enumerate(charts)]
    content = llm([{"role": "system", "content": SYSTEM_INTERPRET},
                   {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}], 6000)
    data = parse(content) or {}
    for it in (data.get("lectures") or []) if isinstance(data, dict) else []:
        try:
            i = int(it.get("index"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 0 <= i < len(charts) and it.get("lecture"):
            charts[i] = dict(charts[i], lecture=re.sub(r"\s+", " ", str(it["lecture"])).strip()[:900])
    synthese = str(data.get("synthese") or "")[:900] if isinstance(data, dict) else ""
    return {"charts": charts, "ia": True, "source": source, "synthese": synthese, "erreurs": errors}
