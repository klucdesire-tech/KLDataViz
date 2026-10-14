"""Moteur « Architecte de données » : profilage + restructuration de fichiers tabulaires.

Usage :
    python -I data_architect.py profile <fichier> [--sheet NOM] [--out rapport.json]
    python -I data_architect.py restructure <fichier> <spec.json> <dossier_sortie>
    python -I data_architect.py profile <fichier1> <fichier2> ...       (relations entre fichiers)
    python -I data_architect.py restructure-multi <spec.json> <dossier_sortie> <fichier1> <fichier2> ...

Le profilage est déterministe : il mesure, il ne devine pas. L'interprétation
(qu'est-ce qu'une ligne ? quelle structure cible ?) revient au LLM, qui s'appuie
sur ce rapport puis écrit un spec.json exécuté par `restructure`.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------- #
# Chargement
# --------------------------------------------------------------------------- #

ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def _sniff_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()[:200_000]
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        enc, text = "latin-1", raw.decode("latin-1", errors="replace")
    try:
        sep = csv.Sniffer().sniff(text.splitlines()[0] if text else ",", delimiters=";,\t|").delimiter
    except csv.Error:
        sep = max(";,\t|", key=lambda d: text.split("\n", 1)[0].count(d))
    return enc, sep


def load_table(path: str | Path, sheet: str | None = None) -> tuple[pd.DataFrame, dict]:
    path = Path(path)
    ext = path.suffix.lower()
    info: dict = {"fichier": path.name, "format": ext.lstrip(".")}
    if ext in (".csv", ".txt", ".tsv"):
        enc, sep = _sniff_text(path)
        df = pd.read_csv(path, sep=sep, encoding=enc, low_memory=False)
        info.update(encodage=enc, separateur=sep)
    elif ext in (".xlsx", ".xlsm", ".xls"):
        sheets = pd.ExcelFile(path).sheet_names
        info["feuilles"] = sheets
        df = pd.read_excel(path, sheet_name=sheet or sheets[0])
        info["feuille_lue"] = sheet or sheets[0]
    elif ext == ".json":
        df = pd.json_normalize(json.loads(path.read_text(encoding="utf-8")))
    elif ext == ".parquet":
        df = pd.read_parquet(path)
    else:
        raise ValueError(f"Format non pris en charge : {ext}")
    df.columns = [str(c).strip() for c in df.columns]
    return df, info


# --------------------------------------------------------------------------- #
# Détection de dimensions cachées dans les en-têtes (format « large »)
# --------------------------------------------------------------------------- #

MOIS = r"(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre|january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|fev|mar|apr|avr|jun|jul|aug|sep|oct|nov|dec)"
DIMENSION_PATTERNS: dict[str, str] = {
    "periode": rf"\b{MOIS}\.?\s*\d{{4}}\b|\b\d{{4}}[-/](?:0[1-9]|1[0-2])\b|\b(?:T|Q)[1-4]\s*\d{{4}}\b|\b\d{{4}}\s*(?:T|Q)[1-4]\b|\bS[12]\s*\d{{4}}\b",
    "tranche_age": r"\b\d{1,3}\s*[-–à]\s*\d{1,3}\s*(?:ans|an|mois|years|yrs)\b|\b\d{1,3}\s*(?:ans|years)\s*(?:et\s*plus|\+|and\s*over)|\b(?:<|>|moins de|plus de)\s*\d{1,3}\s*(?:ans|mois|years)\b",
    "sexe": r"\b(?:masculin|f[ée]minin|homme|femme|hommes|femmes|male|female|gar[cç]ons?|filles?)\b",
    "annee": r"(?<![\d-])(?:19|20)\d{2}(?![\d-])",
}


def _strip_dimensions(header: str) -> tuple[str, dict]:
    found = {}
    stem = header
    for dim, pat in DIMENSION_PATTERNS.items():
        m = re.search(pat, stem, flags=re.IGNORECASE)
        if m:
            found[dim] = m.group(0).strip()
            stem = stem[: m.start()] + " " + stem[m.end():]
    stem = re.sub(r"\s+", " ", stem).strip(" -_:|")
    return stem, found


def _common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    s1, s2 = min(strings), max(strings)
    i = 0
    while i < min(len(s1), len(s2)) and s1[i] == s2[i]:
        i += 1
    return s1[:i]


def detect_wide_layout(columns: list[str]) -> dict | None:
    """Repère les colonnes dont l'en-tête encode des dimensions (période, âge, sexe...)."""
    parsed = [(c, *_strip_dimensions(c)) for c in columns]
    with_dims = [p for p in parsed if p[2]]
    if len(with_dims) < 4:
        return None
    dims = Counter(d for _, _, found in with_dims for d in found)
    stems = Counter(stem for _, stem, _ in with_dims)
    prefix = _common_prefix([s for s in stems])
    # coupe le préfixe commun sur une frontière de mot
    prefix = prefix[: prefix.rfind(" ") + 1] if " " in prefix else ""
    indicators = sorted({s[len(prefix):].strip() for s in stems})
    values = {d: sorted({f[d] for _, _, f in with_dims if d in f}) for d in dims}
    return {
        "colonnes_concernees": len(with_dims),
        "dimensions_detectees": {d: {"nb_colonnes": n, "valeurs": values[d]} for d, n in dims.items()},
        "prefixe_commun": prefix.strip(),
        "indicateurs": indicators,
        "nb_indicateurs": len(indicators),
        "combinaisons_attendues": len(indicators) * _prod(len(v) for v in values.values()),
    }


def _prod(xs) -> int:
    r = 1
    for x in xs:
        r *= x
    return r


# --------------------------------------------------------------------------- #
# Profilage par colonne
# --------------------------------------------------------------------------- #

def _profile_column(s: pd.Series) -> dict:
    n = len(s)
    nn = s.notna().sum()
    p: dict = {
        "type_pandas": str(s.dtype),
        "pct_vide": round(100 * (1 - nn / n), 1) if n else 0,
        "nb_distinct": int(s.nunique(dropna=True)),
    }
    if nn == 0:
        p["alerte"] = ["colonne entièrement vide"]
        return p
    alerts = []
    vals = s.dropna()
    if p["nb_distinct"] == 1:
        alerts.append("valeur constante")
    if pd.api.types.is_numeric_dtype(s):
        num = vals.astype(float)
        frac = (num % 1 != 0).mean()
        p.update(
            min=float(num.min()), max=float(num.max()), moyenne=round(float(num.mean()), 3),
            mediane=float(num.median()), pct_zero=round(100 * (num == 0).mean(), 1),
            pct_negatif=round(100 * (num < 0).mean(), 1), pct_non_entier=round(100 * frac, 1),
        )
        top_val, top_n = Counter(num).most_common(1)[0]
        p["valeur_la_plus_frequente"] = {"valeur": top_val, "pct": round(100 * top_n / nn, 1)}
        if 0 < frac < 0.95 and (num % 0.5 == 0).mean() > 0.99:
            alerts.append("mélange entiers / x,5 : trace probable d'imputation par médiane")
        if top_n / nn > 0.2 and p["nb_distinct"] > 5 and top_val not in (0, 1):
            alerts.append(f"pic suspect sur {top_val} ({p['valeur_la_plus_frequente']['pct']} %) : imputation ?")
        if p["pct_zero"] == 0 and nn > 50 and num.min() > 0 and frac < 0.5 and num.median() < 50:
            alerts.append("aucun zéro alors que les valeurs sont petites : zéros supprimés ou remplacés ?")
        if frac > 0 and "code" in str(s.name).lower():
            alerts.append("code stocké comme nombre décimal : à traiter comme texte")
        q1, q3 = num.quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr > 0:
            p["nb_valeurs_extremes"] = int(((num < q1 - 3 * iqr) | (num > q3 + 3 * iqr)).sum())
    else:
        txt = vals.astype(str)
        p["exemples"] = txt.drop_duplicates().head(5).tolist()
        p["longueur_max"] = int(txt.str.len().max())
        dates = pd.to_datetime(txt, errors="coerce", dayfirst=True, format="mixed")
        if dates.notna().mean() > 0.9:
            p["semble_date"] = True
            p["plage_dates"] = [str(dates.min().date()), str(dates.max().date())]
        numeric_like = pd.to_numeric(txt.str.replace(",", ".").str.replace(r"\s", "", regex=True), errors="coerce")
        if numeric_like.notna().mean() > 0.9:
            alerts.append("texte contenant des nombres : convertir (virgule décimale ?)")
        if txt.str.contains("�|Ã©|Ã¨", regex=True).any():
            alerts.append("caractères mal encodés détectés")
        if txt.ne(txt.str.strip()).any():
            alerts.append("espaces superflus en début/fin")
        first = txt.str.extract(r"^([^\s]+)")[0]
        top_prefixes = first.value_counts()
        if 2 <= len(top_prefixes) <= max(30, nn // 4) and top_prefixes.iloc[0] > 1:
            p["prefixes_frequents"] = top_prefixes.head(12).to_dict()
        case_variants = txt.str.lower().str.strip().nunique()
        if case_variants < p["nb_distinct"]:
            alerts.append(f"{p['nb_distinct'] - case_variants} variantes de casse/espaces pour une même valeur")
    if p["nb_distinct"] == nn and nn == n:
        p["identifiant_potentiel"] = True
    if alerts:
        p["alerte"] = alerts
    return p


# --------------------------------------------------------------------------- #
# Profilage au niveau des lignes
# --------------------------------------------------------------------------- #

def _row_checks(df: pd.DataFrame, label_col: str | None) -> dict:
    num = df.select_dtypes("number")
    out: dict = {
        "lignes_dupliquees": int(df.duplicated().sum()),
        "lignes_entierement_vides": int(df.isna().all(axis=1).sum()),
    }
    if num.shape[1] >= 3:
        sig = pd.util.hash_pandas_object(num.round(6), index=False)
        dup = sig[sig.duplicated(keep=False)]
        if len(dup):
            groups = [
                (df.loc[ix, label_col].astype(str).tolist() if label_col else list(ix))[:15]
                for ix in dup.groupby(dup).groups.values()
            ]
            out["groupes_de_lignes_aux_valeurs_identiques"] = {
                "groupes": groups,
                "interpretation": "souvent des agrégats/niveaux hiérarchiques ou des lignes entièrement imputées",
            }
        totals = num.sum(axis=1, min_count=1)
        rest = totals.sum()
        suspects = []
        for i, t in totals.items():
            if pd.notna(t) and t > 0 and abs(t - (rest - t)) / max(rest - t, 1) < 0.02:
                suspects.append(i)
        if suspects:
            out["ligne_total_probable"] = [str(df.at[i, label_col]) if label_col else i for i in suspects]
        # Niveaux hiérarchiques mélangés aux détails : libellé d'un seul mot quand les autres en ont
        # plusieurs (« NAWA » parmi « CSR-D PUBLIC DE ... »), ou valeurs identiques à 2 autres lignes ou plus.
        agg = {}
        if label_col:
            labels = df[label_col].astype(str).str.strip()
            multi = labels.str.contains(r"\s")
            if multi.mean() >= 0.8:
                for i in labels[~multi].index:
                    agg[i] = "libellé d'un seul mot (nom de zone ?) parmi des noms d'établissements"
            for ix in (dup.groupby(dup).groups.values() if len(dup) else []):
                if len(ix) >= 3:
                    for i in ix:
                        agg[i] = (agg[i] + " ; " if i in agg else "") + f"valeurs identiques à {len(ix) - 1} autres lignes"
            for i in suspects:
                agg[i] = (agg[i] + " ; " if i in agg else "") + "total égal à la somme des autres lignes"
        if agg:
            out["lignes_agregat_probables"] = [
                {"libelle": str(df.at[i, label_col]), "raison": r} for i, r in list(agg.items())[:30]]
        fill = num.notna().mean(axis=1)
        out["completude_lignes"] = {"min_pct": round(100 * fill.min(), 1), "mediane_pct": round(100 * fill.median(), 1)}
    return out


def _guess_label_col(df: pd.DataFrame) -> str | None:
    best, score = None, -1
    for c in df.select_dtypes(exclude="number").columns:
        s = df[c].dropna().astype(str)
        if s.empty:
            continue
        sc = s.nunique() / len(df) + s.str.contains(" ").mean()
        if re.search(r"name|nom|libell|label|designation", c, re.I):
            sc += 1
        if sc > score:
            best, score = c, sc
    return best


def profile(df: pd.DataFrame, info: dict | None = None) -> dict:
    label_col = _guess_label_col(df)
    cols = {c: _profile_column(df[c]) for c in df.columns}
    report = {
        "source": info or {},
        "forme": {"lignes": int(df.shape[0]), "colonnes": int(df.shape[1])},
        "cellules_vides_pct": round(100 * df.isna().mean().mean(), 1),
        "colonne_libelle_probable": label_col,
        "colonnes_vides": [c for c, p in cols.items() if p["pct_vide"] == 100],
        "colonnes_constantes": [c for c, p in cols.items() if "valeur constante" in p.get("alerte", [])],
        "identifiants_potentiels": [c for c, p in cols.items() if p.get("identifiant_potentiel")],
        "format_large": detect_wide_layout(list(df.columns)),
        "lignes": _row_checks(df, label_col),
        "colonnes": cols,
    }
    alert_counter = Counter(a.split(":")[0].split(" (")[0].strip() for p in cols.values() for a in p.get("alerte", []))
    report["synthese_alertes"] = dict(alert_counter.most_common())
    num_cols = [c for c in df.select_dtypes("number").columns if cols[c]["pct_vide"] < 100]
    const_num = [c for c in num_cols if "valeur constante" in cols[c].get("alerte", [])]
    if len(df) > 20 and num_cols and len(const_num) / len(num_cols) > 0.2:
        report["alerte_majeure"] = (
            f"{len(const_num)}/{len(num_cols)} colonnes numériques ont la MÊME valeur pour toutes les "
            f"{len(df)} lignes : elles ont très probablement été entièrement imputées et ne contiennent "
            "aucune information réelle."
        )
    return report


# --------------------------------------------------------------------------- #
# Restructuration pilotée par un spec JSON
# --------------------------------------------------------------------------- #
#
# {
#   "id_columns": ["organisationunitid", "organisationunitname"],
#   "drop_columns": ["organisationunitdescription"],
#   "drop_rows": {"column": "organisationunitname", "values": ["BUYO", "SOUBRE"]},
#   "melt": {
#     "regex": "^(?P<periode>\\S+ \\d{4}) SIG - Nombre de (?P<mesure>consultants|consultations) (?:dans la|au|en) (?P<service>.+?) (?P<tranche_age>\\d\\d-\\d\\d ans|50 ans et plus)$",
#     "value_name": "valeur",
#     "dropna": true
#   },
#   "value_rules": {"non_integer": "flag"},          # flag | round | null
#   "recode": {"periode": {"Mars 2026": "2026-03"}},
#   "derive": {"type": {"from": "organisationunitname", "regex": "^(\\S+)"}},
#   "fact_name": "fait_consultation",
#   "dimensions": {
#     "dim_etablissement": {"key": "organisationunitid",
#                           "columns": ["organisationunitname", "type"]},
#     "dim_tranche_age": {"key": "tranche_age"}
#   }
# }

def restructure(df: pd.DataFrame, spec: dict) -> dict[str, pd.DataFrame]:
    df = df.copy()
    df = df.drop(columns=[c for c in spec.get("drop_columns", []) if c in df.columns])
    if dr := spec.get("drop_rows"):
        removed = df[df[dr["column"]].astype(str).isin(dr["values"])]
        df = df.drop(removed.index)
    else:
        removed = df.iloc[0:0]
    for new, rule in spec.get("derive", {}).items():
        df[new] = df[rule["from"]].astype(str).str.extract(rule["regex"], flags=re.IGNORECASE)[0]

    id_cols = [c for c in spec.get("id_columns", []) if c in df.columns]
    id_cols += [c for c in spec.get("derive", {}) if c not in id_cols]
    tables: dict[str, pd.DataFrame] = {}

    if melt := spec.get("melt"):
        if melt.get("auto"):
            # Dimensions détectées automatiquement dans les en-têtes (période, âge, sexe...),
            # le reste de l'en-tête (sans le préfixe commun) devient « indicateur ».
            parsed = {c: _strip_dimensions(c) for c in df.columns if c not in id_cols}
            value_cols = [c for c, (_, found) in parsed.items() if found]
            prefix = _common_prefix([parsed[c][0] for c in value_cols])
            prefix = prefix[: prefix.rfind(" ") + 1] if " " in prefix else ""
            dims = sorted({d for c in value_cols for d in parsed[c][1]})
            mapping = pd.DataFrame(
                [{"_col": c, "indicateur": parsed[c][0][len(prefix):].strip(), **{d: parsed[c][1].get(d) for d in dims}}
                 for c in value_cols]
            ).set_index("_col")
        else:
            rx = re.compile(melt["regex"], flags=re.IGNORECASE)
            value_cols = [c for c in df.columns if c not in id_cols and rx.search(c)]
        unmatched = [c for c in df.columns if c not in id_cols and c not in value_cols]
        long = df.melt(id_vars=id_cols, value_vars=value_cols, var_name="_col", value_name=melt.get("value_name", "valeur"))
        if melt.get("auto"):
            parts = mapping.loc[long["_col"]].reset_index(drop=True)
            parts.index = long.index
        else:
            parts = long["_col"].str.extract(rx)
        fact = pd.concat([long.drop(columns="_col"), parts], axis=1)
        vname = melt.get("value_name", "valeur")
        if melt.get("dropna", True):
            fact = fact[fact[vname].notna()]
        fact = fact[id_cols + list(parts.columns) + [vname]]
    else:
        fact, unmatched = df, []
        vname = None

    for col, mapping in spec.get("recode", {}).items():
        if col in fact.columns:
            fact[col] = fact[col].replace(mapping)

    if vname and (rule := spec.get("value_rules", {}).get("non_integer")):
        frac = fact[vname].notna() & (fact[vname] % 1 != 0)
        if rule == "flag":
            fact["valeur_suspecte"] = frac
        elif rule == "round":
            fact[vname] = fact[vname].round()
        elif rule == "null":
            fact.loc[frac, vname] = pd.NA

    # Toutes les dimensions sont construites avant de retirer quoi que ce soit de la table de faits :
    # une dimension peut en référencer une autre (établissement → district).
    dims = spec.get("dimensions", {})
    keys = {d["key"] for d in dims.values()}
    to_drop = set()
    for name, d in dims.items():
        key = d["key"]
        if key not in fact.columns:
            raise KeyError(f"clé « {key} » de {name} absente de la table de faits")
        cols = [key] + [c for c in d.get("columns", []) if c in fact.columns and c != key]
        tables[name] = fact[cols].drop_duplicates(subset=[key]).sort_values(key).reset_index(drop=True)
        to_drop |= set(cols[1:]) - keys
    fact = fact.drop(columns=sorted(to_drop))

    tables = {spec.get("fact_name", "fait"): fact.reset_index(drop=True), **tables}
    tables["_journal"] = pd.DataFrame(
        [{"etape": "lignes retirées", "detail": ", ".join(map(str, removed.get(spec.get("drop_rows", {}).get("column"), pd.Series(dtype=str)).tolist()))},
         {"etape": "colonnes non transformées", "detail": ", ".join(unmatched)},
         {"etape": "lignes table de faits", "detail": str(len(fact))}]
    )
    return tables


# --------------------------------------------------------------------------- #
# Plusieurs fichiers : relations et modèle commun
# --------------------------------------------------------------------------- #

KEY_NAME = re.compile(r"(^|[_\s])(id|code|cle|clé|key|ref|num|matricule)([_\s]|$)|id$|uid$|code", re.IGNORECASE)


def _norm_name(c: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(c).lower())


def _key_values(s: pd.Series) -> set | None:
    """Valeurs normalisées d'une colonne candidate à une jointure (None si elle n'en est pas une)."""
    s = s.dropna()
    if len(s) < 2 or s.nunique() < 2:
        return None
    if pd.api.types.is_bool_dtype(s):
        return None
    if pd.api.types.is_numeric_dtype(s):
        if (s % 1 != 0).any():
            return None
        if not (KEY_NAME.search(str(s.name)) or s.is_unique):  # un comptage n'est pas une clé
            return None
        s = s.astype("int64")
    vals = s.astype(str).str.strip().str.upper()
    if vals.str.len().mean() > 60:  # texte libre
        return None
    return set(vals.unique())


def find_relations(dfs: dict[str, pd.DataFrame], top: int = 20) -> list[dict]:
    """Colonnes de fichiers différents dont les valeurs se recoupent : jointures candidates."""
    cands = []
    for f, df in dfs.items():
        for c in df.columns:
            v = _key_values(df[c])
            if v:
                cands.append((f, c, v, bool(df[c].dropna().is_unique)))
    rel = []
    for i, (f1, c1, v1, u1) in enumerate(cands):
        for f2, c2, v2, u2 in cands[i + 1:]:
            if f1 == f2:
                continue
            inter = len(v1 & v2)
            if inter < 2:
                continue
            r1, r2 = inter / len(v1), inter / len(v2)
            if max(r1, r2) < 0.5:
                continue
            card = "1:1" if u1 and u2 else "N:1" if u2 else "1:N" if u1 else "N:N"
            rel.append({
                "gauche": f"{f1} :: {c1}", "droite": f"{f2} :: {c2}", "cardinalite": card,
                "valeurs_communes": inter,
                "pct_gauche_trouvees": round(100 * r1, 1), "pct_droite_trouvees": round(100 * r2, 1),
                "noms_proches": _norm_name(c1) == _norm_name(c2) or _norm_name(c1) in _norm_name(c2)
                or _norm_name(c2) in _norm_name(c1),
            })
    rel.sort(key=lambda r: (r["noms_proches"], max(r["pct_gauche_trouvees"], r["pct_droite_trouvees"]),
                            r["valeurs_communes"]), reverse=True)
    return rel[:top]


def profile_many(tables: dict[str, tuple[pd.DataFrame, dict]]) -> dict:
    return {"fichiers": {n: profile(df, info) for n, (df, info) in tables.items()},
            "relations": find_relations({n: df for n, (df, _) in tables.items()})}


def restructure_many(dfs: dict[str, pd.DataFrame], spec: dict) -> dict[str, pd.DataFrame]:
    """spec = {"fichiers": {nom: spec d'un fichier}, "relations": [...]} ; les dimensions de même
    nom venant de plusieurs fichiers sont fusionnées (dédoublonnées sur leur clé = 1re colonne)."""
    out: dict[str, pd.DataFrame] = {}
    journal = []
    for name, sub in spec.get("fichiers", {}).items():
        if name not in dfs:
            raise KeyError(f"fichier inconnu dans le spec : {name}")
        for tname, t in restructure(dfs[name], sub).items():
            if tname == "_journal":
                journal.append(t.assign(fichier=name))
            elif tname not in out:
                out[tname] = t
            elif tname.startswith("dim_"):
                key = t.columns[0]
                merged = pd.concat([out[tname], t], ignore_index=True)
                out[tname] = merged.drop_duplicates(subset=[key]).sort_values(key).reset_index(drop=True)
            else:
                out[f"{tname}_{_norm_name(Path(name).stem)[:20]}"] = t
    if journal:
        out["_journal"] = pd.concat(journal, ignore_index=True)
    if spec.get("relations"):
        out["_relations"] = pd.DataFrame(spec["relations"])
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _json_default(o):
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def _write_tables(tables: dict[str, pd.DataFrame], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, t in tables.items():
        t.to_csv(out / f"{name}.csv", index=False, sep=";", encoding="utf-8-sig")
    with pd.ExcelWriter(out / "modele_analyse.xlsx") as xw:
        for name, t in tables.items():
            t.to_excel(xw, sheet_name=name[:31], index=False)
    sys.stdout.reconfigure(encoding="utf-8")
    for name, t in tables.items():
        print(f"{name}: {t.shape[0]} lignes x {t.shape[1]} colonnes")
    print(f"Sorties dans {out}")


def _load_named(paths: list[str]) -> dict[str, tuple[pd.DataFrame, dict]]:
    tables = {}
    for p in paths:
        df, info = load_table(p)
        name = Path(p).name
        while name in tables:
            name = "_" + name
        tables[name] = (df, info)
    return tables


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("profile", "restructure", "restructure-multi"):
        print(__doc__)
        return 1
    if argv[0] == "restructure-multi":
        spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        tables = _load_named(argv[3:])
        _write_tables(restructure_many({n: df for n, (df, _) in tables.items()}, spec), Path(argv[2]))
        return 0
    if argv[0] == "profile":
        sheet = argv[argv.index("--sheet") + 1] if "--sheet" in argv else None
        flags = {"--sheet", "--out"}
        files = [a for i, a in enumerate(argv[1:], 1) if a not in flags and argv[i - 1] not in flags]
        if len(files) > 1:
            rep = profile_many(_load_named(files))
        else:
            df, info = load_table(files[0], sheet)
            rep = profile(df, info)
        text = json.dumps(rep, ensure_ascii=False, indent=1, default=_json_default)
        if "--out" in argv:
            Path(argv[argv.index("--out") + 1]).write_text(text, encoding="utf-8")
            print(f"Rapport écrit : {argv[argv.index('--out') + 1]}")
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(text)
        return 0
    df, _ = load_table(argv[1])
    spec = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    _write_tables(restructure(df, spec), Path(argv[3]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
