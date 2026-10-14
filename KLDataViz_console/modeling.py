"""
Agent 2 — Modélisation des données.

Nettoyage et mise en forme : détection/correction des types, traitement des
valeurs manquantes (méthodes statistiques), suppression des doublons,
transformation/formatage et proposition d'une disposition des variables.

Fonctionne en DataFrame pandas, indépendant du serveur (réutilisable).
"""
import re

import numpy as np
import pandas as pd


def _line(i) -> int:
    """N° de ligne dans le fichier source (l'en-tête est la ligne 1)."""
    return int(i) + 2


def _short(x, n=60):
    """Valeur lisible et courte pour le détail des corrections."""
    if x is None or (not isinstance(x, (list, dict)) and pd.isna(x)):
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x)
    s = str(x)
    return s if len(s) <= n else s[: n - 1] + "…"


def _slugify(name: str) -> str:
    """Normalise un nom de colonne : minuscules, sans accents, snake_case."""
    s = str(name).strip().lower()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("ë", "e"),
                 ("à", "a"), ("â", "a"), ("ä", "a"), ("î", "i"), ("ï", "i"),
                 ("ô", "o"), ("ö", "o"), ("ù", "u"), ("û", "u"), ("ü", "u"),
                 ("ç", "c"), ("œ", "oe"), ("æ", "ae")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "colonne"


def _coerce_series(s: pd.Series):
    """Tente de convertir une colonne texte vers booléen / numérique / date.

    Retourne (nouvelle_serie, type_detecte) où type_detecte vaut None si aucune
    conversion pertinente n'est trouvée.
    """
    if not (pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)):
        return s, None
    non_null = s.dropna()
    if len(non_null) == 0:
        return s, None
    vals = non_null.astype(str).str.strip()
    low = vals.str.lower()

    # Booléen : au plus 2 valeurs issues d'un vocabulaire restreint.
    bool_vocab = {"true", "false", "vrai", "faux", "oui", "non",
                  "yes", "no", "0", "1", "t", "f"}
    if low.nunique() <= 2 and low.isin(bool_vocab).all():
        mp = {"true": True, "vrai": True, "oui": True, "yes": True,
              "1": True, "t": True,
              "false": False, "faux": False, "non": False, "no": False,
              "0": False, "f": False}
        out = s.map(lambda x: mp.get(str(x).strip().lower())
                    if pd.notna(x) else np.nan)
        return out.astype("boolean"), "booléen"

    # Numérique : gère « 1 234,56 », « 1234.56 », espaces insécables, signes.
    def _clean_num(x):
        return (str(x).replace("\u00a0", "").replace(" ", "")
                .replace(",", "."))

    clean = vals.map(_clean_num)
    num = pd.to_numeric(clean, errors="coerce")
    if num.notna().mean() >= 0.95:
        full = pd.to_numeric(s.map(_clean_num), errors="coerce")
        non_na = full.dropna()
        if len(non_na) and (non_na % 1 == 0).all():
            full = full.astype("Int64")  # entier nullable
        return full, "numérique"

    # Date : format mixte (jj/mm/aaaa, aaaa-mm-jj, etc.).
    dt = pd.to_datetime(vals, errors="coerce", format="mixed", dayfirst=True)
    if dt.notna().mean() >= 0.9:
        return pd.to_datetime(s, errors="coerce", format="mixed", dayfirst=True), "date"

    # Texte : on trime au minimum. Comparaison sur les seules valeurs remplies : une case vide
    # n'est jamais égale à elle-même et faisait signaler à tort toute colonne avec des vides.
    filled = non_null.astype(str)
    if (filled != filled.str.strip()).any():
        return s.map(lambda x: x.strip() if isinstance(x, str) else x), "texte (trim)"
    return s, None


def clean_dataframe(df: pd.DataFrame, skip=None):
    """Pipeline de nettoyage complet. Retourne (df_nettoyé, rapport).

    `skip` : corrections à ne pas appliquer, identifiées par « vides »,
    « doublons », « noms », « type:<colonne> » ou « imput:<colonne> »
    (nom d'origine ou nom normalisé).
    """
    skip = set(skip or [])
    df = df.copy()
    rapport = {
        "lignes_avant": int(len(df)),
        "colonnes_avant": int(len(df.columns)),
        "doublons_supprimes": 0,
        "colonnes_vides": [],
        "colonnes_renommees": {},
        "types_corriges": {},
        "imputations": [],
        "ordre_colonnes": [],
        "lignes_apres": 0,
        "colonnes_apres": 0,
        # Détail de chaque correction, indexé comme les corrections de l'interface
        # (« doublons », « vides », « type:<col> », « imput:<col> ») ; « ligne » = n° de ligne
        # dans le fichier source, en-tête compris.
        "details": {},
    }
    details = rapport["details"]
    df.index = pd.RangeIndex(len(df))  # index = position d'origine, conservé jusqu'à la fin

    # 1. Colonnes entièrement vides
    vide = [c for c in df.columns if df[c].notna().sum() == 0]
    if vide and "vides" not in skip:
        df = df.drop(columns=vide)
        rapport["colonnes_vides"] = [str(c) for c in vide]

    # 2. Doublons (lignes identiques)
    if "doublons" not in skip:
        dup = df[df.duplicated(keep=False)]
        if len(dup):
            groups = dup.groupby(pd.util.hash_pandas_object(dup, index=False), sort=False).groups.values()
            details["doublons"] = {"groupes": [
                {"lignes": [_line(i) for i in list(ix)[:10]],
                 "apercu": {str(c): _short(dup.at[ix[0], c]) for c in list(df.columns)[:6]}}
                for ix in list(groups)[:8]]}
        n0 = len(df)
        df = df.drop_duplicates()
        rapport["doublons_supprimes"] = n0 - len(df)

    # 3. Normalisation des noms de colonnes
    ren, used = {}, set()
    for c in df.columns:
        ns = base = _slugify(c)
        k = 2
        while ns in used:  # deux libellés différents peuvent donner le même nom normalisé
            ns = f"{base}_{k}"
            k += 1
        used.add(ns)
        if ns != str(c):
            ren[str(c)] = ns
    if ren and "noms" not in skip:
        df = df.rename(columns=ren)
        rapport["colonnes_renommees"] = ren
    origine = {v: k for k, v in rapport["colonnes_renommees"].items()}

    def _skipped(kind, c):
        return f"{kind}:{c}" in skip or f"{kind}:{origine.get(str(c), c)}" in skip

    # 3.5. Chaînes vides → NaN (pour les colonnes texte)
    for c in df.columns:
        if (pd.api.types.is_object_dtype(df[c])
                or pd.api.types.is_string_dtype(df[c])):
            df[c] = df[c].replace(r"^\s*$", np.nan, regex=True)

    # 4. Correction des types (typage automatique)
    for c in df.columns:
        s = df[c]
        avant = str(s.dtype)
        out, t = _coerce_series(s)
        if t and not _skipped("type", c):
            df[c] = out
            rapport["types_corriges"][str(c)] = {"avant": avant, "apres": t}
            ok = s.notna() & out.notna()
            lost = s.notna() & out.isna()  # valeurs qui ne passent pas la conversion : elles deviennent vides
            # Exemples parlants : d'abord les valeurs dont l'écriture change (espaces, virgule décimale…)
            changed = ok & (s.astype(str).str.replace(" ", " ") != out.astype(str))
            pick = s[changed].drop_duplicates().index[:5]
            if len(pick) < 5:
                pick = pick.append(s[ok & ~changed].drop_duplicates().index[: 5 - len(pick)])
            details[f"type:{c}"] = {
                "valeurs_modifiees": int(changed.sum()),
                "exemples": [{"ligne": _line(i), "avant": _short(s[i]), "apres": _short(out[i])} for i in pick],
                "non_convertibles": int(lost.sum()),
                "exemples_non_convertibles": [{"ligne": _line(i), "valeur": _short(s[i])}
                                              for i in s[lost].index[:8]],
            }

    # 5. Valeurs manquantes (méthodes statistiques)
    for c in df.columns:
        na = int(df[c].isna().sum())
        if na == 0:
            continue
        # On ne fabrique pas d'identifiant : les ID manquants restent manquants.
        if re.search(r"(^|_)(id|key|code|ref|numero|num_ordre)$", str(c).lower()):
            continue
        if _skipped("imput", c) or na == len(df):  # colonne vide conservée : on ne l'invente pas
            continue
        missing = df.index[df[c].isna()]
        details[f"imput:{c}"] = {"n": na, "lignes": [_line(i) for i in missing[:30]],
                                 "dont_non_convertibles": details.get(f"type:{c}", {}).get("non_convertibles", 0)}
        if pd.api.types.is_bool_dtype(df[c]):
            mode = df[c].mode()
            val = bool(mode.iloc[0]) if len(mode) else False
            df[c] = df[c].fillna(val)
            rapport["imputations"].append({
                "colonne": str(c), "methode": "mode", "n": na, "valeur": val,
            })
        elif pd.api.types.is_numeric_dtype(df[c]):
            med = df[c].median()
            df[c] = df[c].fillna(med)
            rapport["imputations"].append({
                "colonne": str(c), "methode": "médiane", "n": na,
                "valeur": float(med) if pd.notna(med) else None,
            })
        else:
            mode = df[c].mode()
            val = mode.iloc[0] if len(mode) else "inconnu"
            df[c] = df[c].fillna(val)
            rapport["imputations"].append({
                "colonne": str(c), "methode": "mode", "n": na, "valeur": str(val),
            })

    # 6. Disposition des variables (ordre logique : id → date/bool → cat → num)
    def _rank(c):
        nm = str(c).lower()
        if re.search(r"(^|_)(id|key|code|numero|num|ref)", nm):
            return 0
        if (pd.api.types.is_datetime64_any_dtype(df[c])
                or pd.api.types.is_bool_dtype(df[c])):
            return 1
        if pd.api.types.is_numeric_dtype(df[c]):
            return 3
        return 2

    ordered = sorted(df.columns, key=_rank)
    df = df[ordered].reset_index(drop=True)
    rapport["ordre_colonnes"] = [str(c) for c in ordered]

    rapport["lignes_apres"] = int(len(df))
    rapport["colonnes_apres"] = int(len(df.columns))
    return df, rapport


def bivariate_stats(df: pd.DataFrame):
    """Statistiques bivariées : corrélation numérique-numérique et association
    catégoriel-catégoriel (V de Cramér) + numérique vs catégoriel (moyennes)."""
    out = {"correlations": [], "cramer": [], "groupes": []}
    numeric = df.select_dtypes(include=[np.number])
    cat = df.select_dtypes(exclude=[np.number])

    # Numérique × numérique : corrélation de Pearson
    if numeric.shape[1] >= 2:
        cm = numeric.corr()
        for i in range(len(cm.columns)):
            for j in range(i):
                r = float(cm.iloc[i, j])
                if pd.notna(r):
                    out["correlations"].append({
                        "a": str(cm.columns[i]), "b": str(cm.columns[j]),
                        "r": round(r, 3),
                        "force": ("forte" if abs(r) >= 0.7 else
                                  "modérée" if abs(r) >= 0.4 else "faible"),
                    })
        out["correlations"].sort(key=lambda x: -abs(x["r"]))

    # Catégoriel × catégoriel : V de Cramér (top 15 par force)
    if cat.shape[1] >= 2:
        for i in range(len(cat.columns)):
            for j in range(i):
                a, b = cat.columns[i], cat.columns[j]
                try:
                    ct = pd.crosstab(cat[a], cat[b])
                    obs = ct.values.astype(float)
                    n = obs.sum()
                    if n == 0 or ct.shape[0] <= 1 or ct.shape[1] <= 1:
                        continue
                    # χ² calculé sans scipy (indépendance)
                    row_s = obs.sum(axis=1, keepdims=True)
                    col_s = obs.sum(axis=0, keepdims=True)
                    exp = (row_s @ col_s) / n
                    exp[exp == 0] = 1e-12
                    chi2 = float(((obs - exp) ** 2 / exp).sum())
                    k = min(ct.shape) - 1
                    v = (chi2 / (n * k)) ** 0.5 if k else 0.0
                    out["cramer"].append({
                        "a": str(a), "b": str(b), "v": round(v, 3),
                    })
                except Exception:
                    continue
        out["cramer"].sort(key=lambda x: -x["v"])

    # Numérique vs catégoriel : moyenne par modalité (top 6 colonnes num)
    if len(numeric.columns) and len(cat.columns):
        for nc in numeric.columns[:6]:
            for cc in cat.columns:
                try:
                    if cat[cc].nunique() > 20:
                        continue
                    grp = df.groupby(cc, observed=True)[nc].agg(
                        ["mean", "count"]).reset_index()
                    if len(grp) <= 15:
                        out["groupes"].append({
                            "num": str(nc), "cat": str(cc),
                            "labels": [str(x) for x in grp[cc].tolist()],
                            "moyennes": [round(float(m), 3) for m in grp["mean"].tolist()],
                        })
                except Exception:
                    continue

    return out
