# -*- coding: utf-8 -*-
"""
Détecteurs de l'agent DATA INGESTION.

Contient : format, encodage, séparateur, feuilles Excel, ligne d'en-tête,
structure, schéma, types, identifiants potentiels.

Règle absolue : ce module ne fait que LIRE la source (jamais d'écriture, jamais de
renommage, jamais de sauvegarde dans le dossier source).
"""
from __future__ import annotations

import codecs
import csv
import io
import json
import os
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

SAMPLE_BYTES = 2_000_000
DELIMITER_CANDIDATES = (",", ";", "\t", "|", ":")
ID_NAME_RE = re.compile(
    r"(^|[_\-\s])(id|ids|code|key|cle|num|numero|no|ref|reference|matricule|identifiant)s?([_\-\s]|$)",
    re.I,
)
PERIOD_NAME_RE = re.compile(
    r"(19|20)\d{2}|^t[1-4]$|trim|janv|fev|mars|avr|mai|juin|juil|aout|sept|oct|nov|dec|^q[1-4]",
    re.I,
)

DATE_HINTS = ("date", "jour", "mois", "annee", "periode", "period", "created", "updated", "observation")
MEASURE_HINTS = ("montant", "valeur", "value", "total", "nombre", "nb", "qte", "quantite", "count", "sum", "tx_", "taux", "ratio", "pct", "pourcent")
FREE_TEXT_HINTS = ("commentaire", "observation_txt", "note", "description", "adresse", "libelle", "designation")


# --------------------------------------------------------------------------------------
# Format
# --------------------------------------------------------------------------------------
def _read_bytes(path: str | os.PathLike, limit: int = SAMPLE_BYTES) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(limit)


def sha256_file(path: str | os.PathLike, block: int = 1 << 20) -> str:
    """Empreinte SHA-256 en flux (aucune écriture, aucun chargement complet en mémoire)."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def _looks_text(raw: bytes) -> bool:
    if not raw:
        return False
    sample = raw[:4096]
    if b"\x00" in sample:
        return False
    printable = sum(1 for b in sample if 9 <= b <= 13 or 32 <= b <= 126 or b >= 128)
    return printable / max(1, len(sample)) > 0.85


def detect_format(path: str | os.PathLike) -> dict:
    """Détecte le format réel du fichier (extension + signature binaire)."""
    p = Path(path)
    ext = p.suffix.lower()
    head = _read_bytes(p, 4096)
    evidence: list[str] = [f"extension={ext or '(aucune)'}"]
    fmt, confidence = "inconnu", 0.3

    if head[:4] == b"PAR1":
        fmt, confidence = "parquet", 1.0
        evidence.append("signature PAR1 en tête de fichier")
    elif head[:2] == b"PK" and zipfile.is_zipfile(p):
        try:
            names = zipfile.ZipFile(p).namelist()
        except Exception:
            names = []
        if any(n.startswith("xl/") for n in names):
            fmt, confidence = "xlsx", 1.0
            evidence.append("archive ZIP contenant xl/workbook.xml")
        elif any(n.startswith("word/") for n in names):
            fmt, confidence = "docx", 0.9
        elif any(n.startswith("META-INF/") for n in names):
            fmt, confidence = "ods", 0.6
        else:
            fmt, confidence = "archive", 0.4
    elif head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        fmt, confidence = "xls", 1.0
        evidence.append("signature OLE2 (Excel 97-2003)")
    elif head[:15] == b"SQLite format 3":
        fmt, confidence = "sqlite", 1.0
        evidence.append("en-tête SQLite")
    elif _looks_text(head):
        stripped = head.lstrip()
        if stripped[:1] in (b"{", b"["):
            # Échantillon probablement tronqué : on tente une analyse JSON complète
            try:
                with open(p, "r", encoding="utf-8", errors="strict") as fh:
                    json.load(fh)
                fmt, confidence = "json", 0.95
                evidence.append("document JSON complet et valide")
            except UnicodeDecodeError:
                for enc in ("utf-8-sig", "cp1252", "latin-1"):
                    try:
                        with open(p, "r", encoding=enc) as fh:
                            json.load(fh)
                        fmt, confidence = "json", 0.9
                        evidence.append(f"document JSON valide (encodage {enc})")
                        break
                    except Exception:
                        continue
            except Exception:
                # JSON volumineux ou léger défaut : la première valeur non blanche reste un marqueur fort
                fmt, confidence = "json", 0.6
                evidence.append("accolade/crochet initial : JSON probable (analyse complète impossible sur l'échantillon)")
        elif stripped[:4] in (b"true", b"null"):
            fmt, confidence = "json", 0.7
            evidence.append("valeur JSON scalaire en tête")
        else:
            fmt, confidence = "csv", 0.7
            evidence.append("contenu texte tabulaire")
    else:
        fmt, confidence = "binaire", 0.4

    if ext and fmt == "csv" and ext in (".tsv", ".txt", ".psv"):
        fmt = {".tsv": "tsv", ".psv": "psv"}.get(ext, "csv")
        confidence = 0.8
        evidence.append(f"extension texte structuré {ext}")
    if ext in (".xlsx", ".xlsm") and fmt == "csv":
        fmt, confidence = "xlsx", 0.6
        evidence.append("extension Excel malgré un contenu texte")

    return {"format": fmt, "extension": ext, "confidence": round(confidence, 2), "evidence": evidence}


# --------------------------------------------------------------------------------------
# Encodage
# --------------------------------------------------------------------------------------
def detect_encoding(path: str | os.PathLike) -> dict:
    """Détecte l'encodage du fichier (BOM, charset_normalizer, chardet + validation stricte)."""
    raw = _read_bytes(path)
    evidence: list[str] = []
    candidates: list[tuple[str, float, str]] = []

    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe\x00\x00", "utf-32-le"),
                     (b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xff\xfe", "utf-16-le"),
                     (b"\xfe\xff", "utf-16-be")):
        if raw.startswith(bom):
            return {"encoding": enc, "bom": True, "confidence": 1.0, "evidence": [f"BOM {enc}"],
                    "candidates": [{"encoding": enc, "confidence": 1.0, "source": "bom"}], "lossy": False}

    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw[:1_000_000]).best()
        if best is not None and best.encoding:
            conf = float(getattr(best, "chaos", 0.0) or 0.0)
            candidates.append((best.encoding, max(0.5, 1.0 - min(conf, 0.5)), "charset_normalizer"))
            evidence.append(f"charset_normalizer → {best.encoding} (chaos={conf:.2f})")
    except Exception as exc:  # dependance optionnelle
        evidence.append(f"charset_normalizer indisponible ({exc})")

    try:
        import chardet

        guess = chardet.detect(raw[:1_000_000]) or {}
        if guess.get("encoding"):
            candidates.append((guess["encoding"], float(guess.get("confidence") or 0.0), "chardet"))
            evidence.append(f"chardet → {guess['encoding']} ({float(guess.get('confidence') or 0):.2f})")
    except Exception as exc:
        evidence.append(f"chardet indisponible ({exc})")

    if not candidates:
        candidates.append(("utf-8", 0.4, "defaut"))

    # Normalisation des alias de codec (utf_8 == utf-8 …) pour éviter les faux doublons
    def canon(name: str) -> str:
        try:
            return codecs.lookup(name).name
        except Exception:
            return str(name).lower()

    seen: dict[str, tuple[str, float, str]] = {}
    for enc, conf, src in candidates:
        key = canon(enc)
        if key not in seen or conf > seen[key][1]:
            seen[key] = (enc, conf, src)
    candidates = list(seen.values())

    # Validation : on décode et on compte les caractères de remplacement
    scored = []
    for enc, conf, src in candidates:
        try:
            text = raw[:1_000_000].decode(enc)
            bad = text.count("\ufffd")
            ratio = bad / max(1, len(text))
            scored.append({"encoding": enc, "confidence": round(min(0.99, conf * (1 - min(ratio, 0.5))), 2),
                           "source": src, "replacement_chars": bad})
        except Exception:
            scored.append({"encoding": enc, "confidence": 0.0, "source": src, "replacement_chars": -1})
    scored.sort(key=lambda c: c["confidence"], reverse=True)
    best_enc = scored[0]

    decode_ok = True
    try:
        raw[:1_000_000].decode(best_enc["encoding"])
    except Exception:
        decode_ok = False

    return {
        "encoding": best_enc["encoding"],
        "bom": False,
        "confidence": best_enc["confidence"],
        "evidence": evidence,
        "candidates": scored,
        "lossy": (not decode_ok) or best_enc["encoding"].lower().replace("-", "") in ("ascii",),
    }


# --------------------------------------------------------------------------------------
# Séparateur
# --------------------------------------------------------------------------------------
def detect_separator(text: str, candidates: Sequence[str] = DELIMITER_CANDIDATES) -> dict:
    """Détecte le séparateur d'un fichier délimité (Sniffer + heuristique de fréquence)."""
    lines = [ln for ln in text.splitlines() if ln.strip()][:50]
    if not lines:
        return {"separator": ",", "label": "virgule", "confidence": 0.2, "evidence": ["fichier vide"], "scores": {}}

    scores: dict[str, dict] = {}
    for cand in candidates:
        counts = [ln.count(cand) for ln in lines]
        if not any(counts):
            continue
        modal = max(set(counts), key=counts.count)
        consistency = counts.count(modal) / len(counts)
        if modal == 0:
            continue
        # le séparateur "espace" est pénalisé (présent dans du texte libre)
        penalty = 0.55 if cand == " " else 1.0
        scores[cand] = {"fields": modal + 1, "consistency": round(consistency, 3),
                        "score": round(consistency * min(modal, 12) * penalty, 3)}

    sniffer_sep, sniffer_ok = None, False
    try:
        sniffer_sep = csv.Sniffer().sniff("\n".join(lines[:20]), delimiters="".join(candidates)).delimiter
        sniffer_ok = True
    except Exception:
        pass

    if not scores and not sniffer_ok:
        return {"separator": ",", "label": "virgule", "confidence": 0.2,
                "evidence": ["aucun séparateur détecté — virgule par défaut"], "scores": {}}

    best = max(scores.items(), key=lambda kv: kv[1]["score"])[0] if scores else sniffer_sep
    if sniffer_ok and sniffer_sep in scores and sniffer_sep == best:
        conf = min(0.99, scores[best]["consistency"] + 0.15)
    elif sniffer_ok and sniffer_sep:
        best = sniffer_sep if (scores.get(sniffer_sep, {}).get("score", 0) >= scores.get(best, {}).get("score", 0) * 0.8) else best
        conf = min(0.95, scores.get(best, {}).get("consistency", 0.5))
    else:
        conf = scores.get(best, {}).get("consistency", 0.5) * 0.9

    labels = {",": "virgule", ";": "point-virgule", "\t": "tabulation", "|": "barre verticale", ":": "deux-points", " ": "espace"}
    return {"separator": best, "label": labels.get(best, best), "confidence": round(float(conf), 2),
            "evidence": [f"colonnes détectées={scores.get(best, {}).get('fields', '?')}",
                         f"constance={scores.get(best, {}).get('consistency', '?')}",
                         f"sniffer={sniffer_sep or 'échec'}"],
            "scores": scores}


# --------------------------------------------------------------------------------------
# Ligne d'en-tête
# --------------------------------------------------------------------------------------
def guess_header_row(rows: Sequence[Sequence[Any]], max_scan: int = 12) -> int:
    """Cherche la ligne d'en-tête dans les premières lignes (exports avec préambule)."""
    best_idx, best_score = 0, float("-inf")
    for i, row in enumerate(rows[:max_scan]):
        cells = [c for c in row if c is not None and str(c).strip() != ""]
        if not cells:
            continue
        strings = sum(1 for c in cells if isinstance(c, str) and not _is_number(c))
        numerics = len(cells) - strings
        distinct = len({str(c).strip() for c in cells})
        below = rows[i + 1: i + 4]
        below_fill = sum(len([c for c in r if c is not None and str(c).strip() != ""]) for r in below) / max(1, len(below))
        score = len(cells) + 2.2 * strings + 0.8 * distinct - 3.0 * numerics + 0.35 * below_fill
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx


def _is_number(value: Any) -> bool:
    try:
        float(str(value).replace(",", ".").replace(" ", ""))
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------------------
# Feuilles Excel
# --------------------------------------------------------------------------------------
def detect_excel_sheets(path: str | os.PathLike) -> dict:
    """Inventorie les feuilles d'un classeur Excel (nom, dimensions, visibilité)."""
    sheets, evidence = [], []
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for idx, name in enumerate(wb.sheetnames):
            ws = wb[name]
            try:
                dim = ws.calculate_dimension()
            except Exception:
                dim = "?"
            m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", dim or "")
            rows = cols = None
            if m:
                rows = int(m.group(4)) - int(m.group(2)) + 1
                cols = _col_index(m.group(3)) - _col_index(m.group(1)) + 1
            sheets.append({"name": name, "index": idx, "dimension": dim, "rows_estimate": rows,
                           "columns_estimate": cols, "state": getattr(ws, "sheet_state", "visible"),
                           "hidden": getattr(ws, "sheet_state", "visible") != "visible"})
        evidence.append(f"{len(sheets)} feuille(s) détectée(s) : " + ", ".join(s["name"] for s in sheets))
        wb.close()
    except Exception as exc:
        evidence.append(f"lecture des feuilles impossible : {exc}")
    visible = [s for s in sheets if not s["hidden"]] or sheets
    return {"sheets": sheets, "count": len(sheets), "active": visible[0]["name"] if visible else None,
            "evidence": evidence}


def _col_index(letters: str) -> int:
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx


# --------------------------------------------------------------------------------------
# Types / variables / structure / identifiants
# --------------------------------------------------------------------------------------
def normalize_type(series, sample: int = 500) -> dict:
    """Type normalisé d'une colonne (strategy pandas dtype + test de contenu)."""
    import pandas as pd

    dtype = str(series.dtype)
    info = {"source_type": dtype, "normalized_type": "text", "sub_type": None, "evidence": []}
    kind = series.dtype.kind if hasattr(series.dtype, "kind") else "O"
    if kind in "iu":
        info["normalized_type"] = "integer"
    elif kind == "f":
        info["normalized_type"] = "decimal"
    elif kind == "b":
        info["normalized_type"] = "boolean"
    elif str(dtype).startswith("datetime"):
        info["normalized_type"] = "datetime"
    elif str(dtype) == "category":
        info["normalized_type"] = "category"
    else:
        vals = series.dropna().astype(str).head(sample)
        if len(vals):
            parsed_dates = pd.to_datetime(vals, errors="coerce", format="mixed")
            ratio_date = float(parsed_dates.notna().mean())
            ratio_num = float(pd.to_numeric(vals.str.replace(" ", "").str.replace(",", "."), errors="coerce").notna().mean())
            ratio_bool = float(vals.str.lower().isin(["true", "false", "oui", "non", "vrai", "faux", "y", "n", "0", "1"]).mean())
            if ratio_date >= 0.9:
                is_dt = any((":" in v) or ("T" in v and "-" in v) for v in vals.head(20))
                info["normalized_type"] = "datetime" if is_dt else "date"
                info["evidence"].append(f"{ratio_date:.0%} des valeurs analysables en date")
            elif ratio_bool >= 0.98:
                info["normalized_type"] = "boolean"
                info["evidence"].append("valeurs booléennes textuelles")
            elif ratio_num >= 0.95:
                info["normalized_type"] = "decimal"
                info["evidence"].append(f"{ratio_num:.0%} des valeurs numériques (type source texte)")
            else:
                avg_len = float(vals.str.len().mean())
                info["normalized_type"] = "text"
                info["sub_type"] = "texte long" if avg_len > 40 else "catégorie"
                info["evidence"].append(f"longueur moyenne={avg_len:.0f}")
    return info


def analyze_column(name: str, series, row_count: int, sample_values: int = 3) -> dict:
    """Profil complet d'une colonne : type, complétude, unicité, rôle sémantique, identifiant."""
    import pandas as pd

    non_null = series.dropna()
    nulls = int(len(series) - len(non_null))
    distinct = int(non_null.nunique())
    typ = normalize_type(series)
    lower = str(name).lower()
    samples = []
    for v in non_null.head(sample_values).tolist():
        s = v.isoformat() if isinstance(v, (datetime, date)) else str(v)
        samples.append(s[:80])

    id_like_name = bool(ID_NAME_RE.search(lower)) or lower.endswith(("_id", "id"))
    unique_ratio = (distinct / len(non_null)) if len(non_null) else 0.0
    id_candidate = bool(len(non_null) and distinct == len(non_null) and len(non_null) > 1 and unique_ratio == 1.0)
    id_reasons = []
    long_text = typ.get("sub_type") == "texte long"
    if id_candidate and long_text and not id_like_name:
        # Une colonne de texte libre intégralement unique n'est pas un identifiant : c'est une description
        id_candidate = False
        id_reasons.append("valeurs uniques mais texte long : identifiant improbable (à vérifier)")
    elif id_candidate:
        id_reasons.append("valeurs uniques à 100 %")
        if id_like_name:
            id_reasons.append("nom de colonne de type identifiant")
    elif id_like_name and unique_ratio > 0.5:
        id_reasons.append("nom d'identifiant, quasi-unique (clé probable d'une dimension)")

    role = "texte"
    if id_candidate and (id_like_name or typ["normalized_type"] in ("integer", "text")):
        role = "identifiant"
    elif typ["normalized_type"] in ("date", "datetime") or any(h in lower for h in DATE_HINTS):
        role = "temporelle"
    elif typ["normalized_type"] in ("integer", "decimal") and not id_like_name:
        role = "mesure"
    elif typ["normalized_type"] == "boolean":
        role = "dimension"
    elif any(h in lower for h in FREE_TEXT_HINTS) or typ.get("sub_type") == "texte long":
        role = "texte_libre"
    else:
        role = "dimension"

    measure_like = any(h in lower for h in MEASURE_HINTS)
    return {
        "name": str(name),
        "source_type": typ["source_type"],
        "normalized_type": typ["normalized_type"],
        "sub_type": typ["sub_type"],
        "semantic_role": role,
        "nullable": nulls > 0,
        "null_count": nulls,
        "null_pct": round(nulls / len(series) * 100, 2) if len(series) else 0.0,
        "distinct_count": distinct,
        "distinct_pct": round(unique_ratio * 100, 2),
        "unique": distinct == len(non_null) and len(non_null) > 1,
        "constant": distinct == 1,
        "id_candidate": id_candidate,
        "id_reasons": id_reasons,
        "measure_like_name": measure_like,
        "min": _safe_minmax(non_null, "min"),
        "max": _safe_minmax(non_null, "max"),
        "samples": samples,
        "evidence": typ["evidence"],
    }


def _safe_minmax(values, how: str) -> Any:
    if not len(values):
        return None
    try:
        v = values.min() if how == "min" else values.max()
    except Exception:
        try:
            v = min(values.astype(str)) if how == "min" else max(values.astype(str))
        except Exception:
            return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v if not isinstance(v, float) else round(v, 6)
    return str(v)


def detect_composite_keys(columns: list[dict], frame, max_cols: int = 10, max_combos: int = 240) -> list[dict]:
    """Cherche des clés primaires composites (combinaisons uniques de 2 à 3 colonnes)."""
    import itertools

    if frame is None or len(frame) < 2:
        return []
    candidates = [c["name"] for c in columns if not c["constant"]][:max_cols]
    out, tested = [], 0
    for size in (2, 3):
        for combo in itertools.combinations(candidates, size):
            if tested >= max_combos:
                return out
            tested += 1
            try:
                dup = int(frame[list(combo)].duplicated().sum())
            except Exception:
                continue
            if dup == 0:
                out.append({"columns": list(combo), "unique": True, "rows": int(len(frame)),
                            "certainty": "Probable" if size == 2 else "Possible"})
    return out


def detect_structure(columns: list[dict]) -> dict:
    """Détecte la structure logique du jeu de données (plat / long / large)."""
    measures = [c for c in columns if c["semantic_role"] == "mesure"]
    dates = [c for c in columns if c["semantic_role"] == "temporelle"]
    period_cols = [c for c in columns if PERIOD_NAME_RE.search(c["name"])]
    indicator_cols = [c for c in columns if re.search(r"indicateur|indicator|metric|variable|libelle_ind", c["name"], re.I)]

    if indicator_cols and measures:
        structure, certainty = "long (indicateur / valeur)", "Probable"
    elif len(period_cols) >= 3 and len(period_cols) >= 0.5 * len(columns):
        structure, certainty = "large (périodes en colonnes)", "Probable"
    elif dates and measures:
        structure, certainty = "plat (une ligne = une observation datée)", "Certain"
    else:
        structure, certainty = "plat", "Probable"

    return {"structure": structure, "certainty": certainty, "measure_columns": [c["name"] for c in measures],
            "date_columns": [c["name"] for c in dates], "period_columns": [c["name"] for c in period_cols],
            "indicator_columns": [c["name"] for c in indicator_cols]}


def build_schema(frame, name: str, source_format: str, sheet: str | None = None,
                 extra_warnings: list[str] | None = None) -> tuple[dict, list[dict], list[str]]:
    """Construit le schéma complet : variables, clés candidates, structure, avertissements."""
    warnings = list(extra_warnings or [])
    columns = []
    row_count = int(len(frame))
    for i, col in enumerate(frame.columns):
        info = analyze_column(col, frame[col], row_count)
        info["ordinal"] = i + 1
        columns.append(info)

    ids = [c["name"] for c in columns if c["id_candidate"]]
    composite = detect_composite_keys(columns, frame)
    structure = detect_structure(columns)

    for c in columns:
        if c["constant"] and row_count > 1:
            warnings.append(f"Colonne constante « {c['name']} » ({c['distinct_count']} valeur unique) : à confirmer.")
        if c["null_pct"] >= 50:
            warnings.append(f"Colonne « {c['name']} » vide à {c['null_pct']} %.")
        if c["source_type"] == "object" and c["normalized_type"] in ("decimal", "date", "datetime"):
            warnings.append(f"Colonne « {c['name']} » de type texte contenant des {c['normalized_type']} : conversion à prévoir.")
    if row_count <= 1:
        warnings.append(f"Jeu de données à {row_count} ligne(s) : toutes les colonnes sont mécaniquement constantes ; "
                        f"volumétrie insuffisante pour des statistiques (agrégat JSON ou ligne unique de synthèse ?).")
    if not ids and not composite:
        warnings.append("Aucun identifiant unique détecté : clé technique à générer pour la table de travail.")
    duplicates = int(frame.duplicated().sum()) if row_count else 0
    if duplicates:
        warnings.append(f"{duplicates} ligne(s) entièrement dupliquée(s) détectée(s).")

    schema = {
        "table_name": name,
        "source_format": source_format,
        "sheet": sheet,
        "row_count": row_count,
        "column_count": int(frame.shape[1]),
        "columns": [{k: v for k, v in c.items() if k not in ("evidence", "samples", "id_reasons")} for c in columns],
        "primary_key_candidate": composite[0]["columns"] if composite else (ids[:1] or None),
        "identifier_columns": ids,
        "composite_keys": composite,
        "duplicate_rows": duplicates,
        "structure": structure,
        "estimated_entity": _guess_entity(name, columns),
    }
    return schema, columns, warnings


def _guess_entity(name: str, columns: list[dict]) -> str:
    names = {c["name"].lower() for c in columns}
    if {"site", "district", "region"} & names and any(c["semantic_role"] == "mesure" for c in columns):
        return "observation sanitaire (site / période / indicateur)"
    if any("client" in n for n in names):
        return "client"
    if any(re.search(r"produit|article", n) for n in names):
        return "produit"
    if any("patient" in n for n in names):
        return "patient"
    return "entité non documentée"


def semantic_hints(columns: list[dict]) -> list[dict]:
    """Suggestions de sens (jamais de définition inventée : uniquement des hypothèses marquées)."""
    hints = []
    for c in columns:
        if c["id_candidate"]:
            hints.append({"column": c["name"], "hypothesis": "identifiant unique", "certainty": "Certain"})
        elif c["semantic_role"] == "mesure":
            hints.append({"column": c["name"], "hypothesis": "mesure numérique exploitable en KPI",
                          "certainty": "Probable" if c["measure_like_name"] else "Possible"})
        elif c["semantic_role"] == "temporelle":
            hints.append({"column": c["name"], "hypothesis": "axe temporel (analyses de tendance)", "certainty": "Probable"})
    return hints
