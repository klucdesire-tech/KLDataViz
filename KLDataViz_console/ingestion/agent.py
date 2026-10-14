# -*- coding: utf-8 -*-
"""
Agent DATA INGESTION — orchestrateur.

Mission : importer des données (fichiers ou bases) sans jamais modifier la source,
détecter format / encodage / séparateur / feuilles / colonnes / types / structure /
volumétrie / identifiants, créer une VERSION DE TRAVAIL et enregistrer les
métadonnées dans PostgreSQL.

Contrat de sortie (toujours le même, même en cas d'échec) :
    dataset_id, dataset_version_id, schema, row_count, column_count,
    variables, source_metadata, warnings, errors  (+ status, version, storage, audit)
"""
from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import detectors, readers
from .store import Registry, default_dsn
from .versioner import Versioner, dataset_id_for, dataset_version_id_for, now_iso

INGESTED_BY = os.environ.get("KLINGEST_USER") or os.environ.get("USERNAME") or os.environ.get("USER") or "agent"


def _run_id() -> str:
    return "RUN-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6].upper()


def ingest(source: str, *, kind: str = "auto", sheet: str | int | None = None, table: str | None = None,
           schema_name: str = "public", where: str | None = None, encoding: str | None = None,
           separator: str | None = None, header: int | None = None, limit: int | None = None,
           dataset_name: str | None = None, registry_dsn: str | None = None, registry_schema: str = "ingestion",
           force_sqlite: bool = False, force: bool = False, prefer_work_format: str = "parquet",
           storage_root: str | Path | None = None) -> dict:
    """Ingère une source et renvoie le contrat de sortie complet."""
    t0 = time.perf_counter()
    run_id = _run_id()
    started = now_iso()
    audit: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    def step(label: str, detail: Any = None) -> None:
        audit.append({"step": label, "at": now_iso(), "detail": detail})

    versioner = Versioner(storage_root)
    registry = Registry(dsn=registry_dsn, force_sqlite=force_sqlite, schema=registry_schema)

    dataset_id = dataset_version_id = None
    source_metadata: dict[str, Any] = {}
    schema: dict[str, Any] = {}
    variables: list[dict] = []
    row_count = column_count = 0
    status = "OK"
    version_info: dict[str, Any] = {}
    storage_info: dict[str, Any] = {}
    store_info: dict[str, Any] = {}
    cached_read: tuple[Any, dict] | None = None
    logical = dataset_name or (table or Path(str(source)).stem or "dataset")

    try:
        step("resolution_source", {"source": source, "kind": kind})
        if kind == "auto":
            kind = "postgres" if (str(source).startswith(("postgres://", "postgresql://", "host=")) or table) else "fichier"

        # ------------------------------------------------------------------ identité du dataset
        if kind == "postgres":
            logical = dataset_name or table or "table"
            origin = f"{source}::{schema_name}.{table}"
            source_metadata = {"kind": "postgres", "origin": origin, "table": f"{schema_name}.{table}",
                               "dsn": _mask(source), "where": where, "retrieved_at": now_iso()}
        elif kind == "sqlite":
            logical = dataset_name or table or Path(source).stem
            origin = f"{source}::{table}"
            source_metadata = {"kind": "sqlite", "origin": origin, "path": str(source), "table": table,
                               "retrieved_at": now_iso()}
        else:
            is_url = str(source).startswith(("http://", "https://"))
            local = _download(source) if is_url else Path(source)
            if not Path(local).exists():
                raise FileNotFoundError(f"Source introuvable : {local}")
            logical = dataset_name or Path(local).stem
            origin = str(source)
            source_metadata = {"kind": "fichier", "origin": origin, "path": str(local), "is_url": is_url,
                               "filename": Path(local).name, "retrieved_at": now_iso()}

        dataset_id = dataset_id_for(kind, logical, _norm_origin(origin))
        audit.append({"step": "identite_dataset", "at": now_iso(),
                      "detail": {"dataset_id": dataset_id, "nom_logique": logical}})

        # ------------------------------------------------------------------ détections (source intacte)
        detections: dict[str, Any] = {}
        content_sha = None
        fmt = kind
        if kind == "fichier":
            fmt_info = detectors.detect_format(source_metadata["path"])
            fmt = fmt_info["format"]
            content_sha = detectors.sha256_file(source_metadata["path"])
            enc_info = detectors.detect_encoding(source_metadata["path"]) if fmt in ("csv", "tsv", "psv", "json", "texte") else None
            sep_info = None
            if fmt in ("csv", "tsv", "psv", "texte"):
                head = Path(source_metadata["path"]).read_text(
                    encoding=(enc_info or {}).get("encoding") or "utf-8", errors="replace")[:200_000]
                sep_info = detectors.detect_separator(head)
            sheets = detectors.detect_excel_sheets(source_metadata["path"]) if fmt in ("xlsx", "xls") else None
            detections = {"format": fmt_info, "encoding": enc_info, "separator": sep_info, "excel": sheets}
            source_metadata.update({
                "format": fmt, "extension": fmt_info["extension"], "format_confidence": fmt_info["confidence"],
                "size_bytes": Path(source_metadata["path"]).stat().st_size,
                "modified_at": datetime.fromtimestamp(Path(source_metadata["path"]).stat().st_mtime).astimezone().isoformat(timespec="seconds"),
                "sha256": content_sha,
                "encoding": (enc_info or {}).get("encoding"), "encoding_confidence": (enc_info or {}).get("confidence"),
                "separator": (sep_info or {}).get("separator"),
                "sheet_count": (sheets or {}).get("sheet_count"), "sheets": (sheets or {}).get("sheets"),
                "detections": detections,
            })
            if fmt_info["confidence"] < 0.7:
                warnings.append(f"Format détecté avec une confiance faible ({fmt} — {fmt_info['confidence']}).")
            if enc_info and enc_info["confidence"] < 0.6:
                warnings.append(f"Encodage incertain ({enc_info['encoding']} — confiance {enc_info['confidence']}) ; "
                                f"vérifier les caractères accentués.")
            elif enc_info:
                proches = [c for c in (enc_info.get("candidates") or [])[1:]
                           if c.get("confidence", 0) >= 0.4 and c.get("encoding") != enc_info["encoding"]]
                if proches:
                    warnings.append(f"Encodage ambigu : « {enc_info['encoding']} » retenu, "
                                    f"alternatives plausibles {[c['encoding'] for c in proches]}. "
                                    f"Vérifier les accents si anomalie (option --encoding).")
        else:
            enc_info = sep_info = None
            # Instantané complet de la table (lecture seule) : sert aussi d'empreinte de contenu
            if kind == "postgres":
                df_probe, read_meta = readers.read_postgres_table(source, table, schema=schema_name,
                                                                  limit=limit, where=where)
            else:
                df_probe, read_meta = readers.read_sqlite_table(source, table, limit=limit)
            cached_read = (df_probe, read_meta)
            content_sha = sha256_dataframe(df_probe)
            source_metadata.update(read_meta)
            source_metadata["format"] = "postgres-table" if kind == "postgres" else "sqlite-table"
            source_metadata["sha256"] = content_sha
            source_metadata["sha256_scope"] = "instantané de la table (colonnes + valeurs)"
            detections = {"table": read_meta.get("source_columns"), "primary_key": read_meta.get("primary_key")}
            step("instantane_table", {"colonnes": read_meta.get("columns"),
                                      "lignes_source": read_meta.get("row_count_source") or read_meta.get("rows"),
                                      "empreinte_contenu": content_sha[:16]})
        step("detections", {"format": source_metadata.get("format"), "encoding": source_metadata.get("encoding"),
                            "separateur": source_metadata.get("separator"),
                            "feuilles": source_metadata.get("sheet_count")})

        # ------------------------------------------------------------------ version (idempotence)
        existing = versioner.existing_versions(dataset_id)
        previous = existing[-1] if existing else None
        same_content = bool(previous and content_sha and previous.get("content_sha256") == content_sha
                            and previous.get("sheet") == (sheet or source_metadata.get("sheet"))
                            and previous.get("read_options_hash") == _opts_hash(encoding, separator, header, limit, where))
        if same_content and not force:
            version_no = previous["version_no"]
            dataset_version_id = previous["dataset_version_id"]
            status = "DEJA_INGERE"
            warnings.append(f"Contenu identique à la version v{version_no:03d} ({dataset_version_id}) — "
                            f"aucune nouvelle version créée (utiliser --force pour forcer un nouvel import).")
        else:
            version_no = versioner.next_version_no(dataset_id)
            dataset_version_id = dataset_version_id_for(dataset_id, version_no, content_sha or _opts_hash(
                encoding, separator, header, limit, where) or "snapshot")
        step("version", {"dataset_id": dataset_id, "dataset_version_id": dataset_version_id,
                         "version_no": version_no, "nouvelle": not same_content})

        # ------------------------------------------------------------------ copie RAW (source jamais modifiée)
        raw_path = previous.get("raw_path") if same_content else None
        if kind == "fichier" and not same_content:
            frozen = versioner.freeze_source(source_metadata["path"], dataset_id, version_no)
            raw_path = frozen["raw_path"]
            source_metadata["sha256_copy"] = frozen["raw_sha256"]
            source_metadata["copy_identical"] = frozen["copy_identical"]
            if not frozen["copy_identical"]:
                errors.append("La copie RAW diffère de la source : ingestion interrompue.")
                raise RuntimeError("Copie RAW non conforme (intégrité source non garantie).")
        step("copie_raw", {"raw_path": raw_path, "source_intacte": True})

        # ------------------------------------------------------------------ lecture
        work_input = raw_path if kind == "fichier" else None
        read_opts = dict(encoding=encoding, separator=separator, header=header, limit=limit, sheet=sheet)
        if kind == "fichier":
            df, read_meta = readers.read_any(work_input, fmt=source_metadata["format"], **read_opts)
        else:
            df, read_meta = cached_read
        warnings.extend(read_meta.get("warnings") or [])
        source_metadata["read"] = read_meta
        row_count, column_count = int(df.shape[0]), int(df.shape[1])
        step("lecture", {"lignes": row_count, "colonnes": column_count, "lecteur": read_meta.get("reader")})

        # ------------------------------------------------------------------ schéma / variables
        schema, variables, schema_warnings = detectors.build_schema(
            df, name=logical, source_format=source_metadata["format"],
            sheet=read_meta.get("sheet"), extra_warnings=read_meta.get("warnings"))
        warnings.extend(w for w in schema_warnings if w not in warnings)
        identifiers = schema["identifier_columns"]
        step("schema", {"colonnes": column_count, "identifiants": identifiers,
                        "structure": schema["structure"]["structure"]})

        # ------------------------------------------------------------------ version de travail
        work_dir = versioner.work_dir(dataset_id, version_no)
        work_path, work_warnings, work_info = readers.write_work_copy(df, work_dir, logical, prefer=prefer_work_format)
        warnings.extend([w for w in work_warnings if w not in warnings])
        storage_info = {"work_dir": str(work_dir), **work_info,
                        "variables_csv": str(versioner.version_dir(dataset_id, version_no) / "variables.csv")}

        # ------------------------------------------------------------------ manifeste + fichiers de version
        version_info = {"dataset_id": dataset_id, "dataset_version_id": dataset_version_id,
                        "version_no": version_no, "content_sha256": content_sha, "created_at": now_iso(),
                        "source_path": source_metadata.get("path") or source_metadata.get("origin"),
                        "raw_path": raw_path, "work_path": str(work_path),
                        "sheet": read_meta.get("sheet"), "table": table,
                        "read_options_hash": _opts_hash(encoding, separator, header, limit, where),
                        "row_count": row_count, "column_count": column_count, "status": status,
                        "reused": bool(same_content and not force), "forced": bool(force and same_content),
                        "ingested_by": INGESTED_BY}
        vfiles = versioner.write_version_files(dataset_id, version_no, schema, variables, source_metadata, version_info)
        storage_info.update(vfiles)

        # ------------------------------------------------------------------ intégrité source (preuve)
        integrity: dict[str, Any] = {}
        if kind == "fichier":
            after = detectors.sha256_file(source_metadata["path"])
            integrity = {"sha256_before": content_sha, "sha256_after": after,
                         "source_unchanged": after == content_sha,
                         "mode": "lecture seule (aucune écriture émise vers la source)"}
            if after != content_sha:
                errors.append("La source a changé pendant l'ingestion — les données importées restent celles de la copie RAW.")
            if read_meta.get("encoding_candidates") is None:
                pass
        else:
            integrity = {"mode": "lecture seule (SELECT uniquement, aucune écriture vers la table source)",
                         "source_unchanged": True, "table": f"{schema_name}.{table}"}
        source_metadata["integrity"] = integrity
        step("integrite_source", integrity)

    except Exception as exc:  # noqa: BLE001
        status = "ERREUR"
        errors.append(f"{type(exc).__name__} : {exc}")
        audit.append({"step": "erreur", "at": now_iso(), "detail": traceback.format_exc(limit=3)})

    # ---------------------------------------------------------------------- métadonnées PostgreSQL
    if dataset_id:
        register_version = bool(version_info.get("dataset_version_id")) if version_info else False
        result = {
            "status": status, "dataset_id": dataset_id, "dataset_version_id": dataset_version_id,
            "schema": schema, "row_count": row_count, "column_count": column_count, "variables": variables,
            "source_metadata": source_metadata, "warnings": warnings, "errors": errors, "audit": audit,
            "_register_version": register_version,
            "_dataset": {"dataset_id": dataset_id,
                         "dataset_name": dataset_name or source_metadata.get("filename") or logical,
                         "source_kind": source_metadata.get("kind"), "origin": source_metadata.get("origin"),
                         "logical_key": str(source_metadata.get("origin"))},
            "_version": {"dataset_version_id": dataset_version_id, "dataset_id": dataset_id,
                         "version_no": version_info.get("version_no", 0), "content_sha256": version_info.get("content_sha256") or "",
                         "raw_path": version_info.get("raw_path"), "work_path": version_info.get("work_path"),
                         "created_at": version_info.get("created_at") or started},
            "_run": {"run_id": run_id, "started_at": started, "finished_at": now_iso(),
                     "duration_ms": int((time.perf_counter() - t0) * 1000)},
        }
        try:
            store_info = registry.ensure()
            store_info["written"] = registry.save(result)
            store_info["warnings"] = list(registry.warnings)
            if registry.warnings:
                warnings.extend(w for w in registry.warnings if w not in warnings)
            step("metadonnees", {"backend": registry.backend, **store_info.get("written", {})})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Enregistrement des métadonnées impossible ({type(exc).__name__} : {exc}) ; "
                          f"les fichiers de version restent disponibles sur disque.")
            store_info = {"backend": registry.backend, "error": str(exc)}
            step("metadonnees_echec", str(exc))

    if not errors and warnings and status == "OK":
        status = "OK_AVEC_AVERTISSEMENTS"

    out = {
        "status": status,
        "dataset_id": dataset_id,
        "dataset_version_id": dataset_version_id,
        "schema": schema,
        "row_count": row_count,
        "column_count": column_count,
        "variables": variables,
        "source_metadata": source_metadata,
        "warnings": warnings,
        "errors": errors,
        "run_id": run_id,
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "ingested_at": started,
        "ingested_by": INGESTED_BY,
        "version": version_info,
        "storage": storage_info,
        "metadata_store": store_info,
        "audit": audit,
        "semantic_hints": detectors.semantic_hints(variables),
    }
    return out


def _norm_origin(origin: str) -> str:
    """Normalise l'origine pour que « C:/a/b.csv » et « C:\\a\\b.csv » désignent le MÊME dataset."""
    text = str(origin)
    if text.startswith(("http://", "https://")) or "::" in text or "=" in text:
        return text.lower()
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(text))))
    except Exception:
        return text


def _opts_hash(encoding, separator, header, limit, where) -> str:
    import hashlib

    return hashlib.sha256("|".join(str(x) for x in (encoding, separator, header, limit, where)).encode()).hexdigest()[:16]


def sha256_dataframe(df) -> str:
    """Empreinte stable du contenu d'un DataFrame (utilisée pour les sources sans fichier)."""
    import hashlib

    h = hashlib.sha256()
    h.update("|".join(f"{c}:{df[c].dtype}" for c in df.columns).encode("utf-8"))
    h.update(df.to_csv(index=False).encode("utf-8"))
    return h.hexdigest()


def _download(url: str) -> Path:
    """Télécharge une source distante dans le scratch local (la source distante reste intacte)."""
    import tempfile
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "KLDataViz-Ingestion/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    ext = Path(url.split("?")[0]).suffix or ".csv"
    tmp = Path(tempfile.mkdtemp(prefix="klingest_")) / f"source{ext}"
    tmp.write_bytes(data)
    return tmp


def _mask(dsn: str) -> str:
    import re

    return re.sub(r"(password=)\S+", r"\1***", re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", str(dsn)))


def ingest_to_json(source: str, **kwargs) -> str:
    return json.dumps(ingest(source, **kwargs), ensure_ascii=False, indent=2, default=str)
