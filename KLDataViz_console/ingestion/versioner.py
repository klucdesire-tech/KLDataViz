# -*- coding: utf-8 -*-
"""
Versionneur de l'agent DATA INGESTION.

Garanties appliquées :
  * la source n'est JAMAIS modifiée — elle est copiée à l'octet (zone RAW, marquée
    lecture seule) puis son empreinte SHA-256 est revérifiée après l'ingestion ;
  * chaque import crée une VERSION DE TRAVAIL (zone WORK) : Parquet typé +
    aperçu CSV + schéma + manifeste ;
  * dataset_id = identité logique stable (même source = mêmes versions successives) ;
    dataset_version_id = une version précise et immuable (v001, v002, …).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

from .detectors import sha256_file

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE = BASE_DIR / "storage"
RAW_DIR = STORAGE / "raw"
WORK_DIR = STORAGE / "work"
VERSIONS_DIR = STORAGE / "versions"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _slug(text: str, max_len: int = 48) -> str:
    keep = []
    for ch in str(text):
        if ch.isalnum() or ch in "-_.":
            keep.append(ch)
        elif ch in " .":
            keep.append("_")
    slug = "".join(keep).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return (slug or "dataset")[:max_len]


def dataset_id_for(source_kind: str, logical_name: str, origin: str) -> str:
    """Identifiant logique stable d'un dataset (stable entre deux imports de la même source)."""
    import hashlib

    key = f"{source_kind}|{logical_name.lower()}|{str(origin).lower()}"
    return "DS-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12].upper()


def dataset_version_id_for(dataset_id: str, version_no: int, content_sha256: str) -> str:
    import hashlib

    key = f"{dataset_id}|v{version_no:03d}|{content_sha256[:16]}"
    return "DV-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:14].upper() + f"-v{version_no:03d}"


class Versioner:
    """Gère les zones RAW / WORK / VERSIONS sur disque."""

    def __init__(self, storage_root: str | Path | None = None):
        root = Path(storage_root) if storage_root else STORAGE
        self.raw = root / "raw"
        self.work = root / "work"
        self.versions = root / "versions"
        for d in (self.raw, self.work, self.versions):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ inventaire
    def existing_versions(self, dataset_id: str) -> list[dict]:
        out = []
        ddir = self.versions / dataset_id
        if not ddir.exists():
            return out
        for vdir in sorted(ddir.iterdir()):
            man = vdir / "manifest.json"
            if man.exists():
                try:
                    out.append(json.loads(man.read_text(encoding="utf-8")))
                except Exception:
                    continue
        return out

    def next_version_no(self, dataset_id: str) -> int:
        nums = [int(v.get("version_no", 0)) for v in self.existing_versions(dataset_id)]
        return (max(nums) + 1) if nums else 1

    # ------------------------------------------------------------------ copie source
    def freeze_source(self, source_path: str | Path, dataset_id: str, version_no: int) -> dict:
        """Copie la source à l'octet dans RAW (aucune écriture dans le dossier d'origine)."""
        src = Path(source_path)
        if not src.exists():
            raise FileNotFoundError(f"Source introuvable : {src}")
        before = sha256_file(src)
        size = src.stat().st_size
        mtime = datetime.fromtimestamp(src.stat().st_mtime).astimezone().isoformat(timespec="seconds")

        dest_dir = self.raw / dataset_id / f"v{version_no:03d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            # Copie RAW déjà présente : on la réutilise si elle est identique
            try:
                if sha256_file(dest) == before:
                    return {"raw_path": str(dest), "raw_sha256": before, "copy_identical": True,
                            "size_bytes": size, "source_mtime": mtime, "reused": True}
            except Exception:
                pass
            _make_writable(dest)
            dest.unlink()
        shutil.copy2(src, dest)
        try:  # marquer la copie en lecture seule (protection supplémentaire)
            os.chmod(dest, stat.S_IREAD)
        except Exception:
            pass
        after = sha256_file(dest)
        return {
            "raw_path": str(dest),
            "raw_sha256": after,
            "copy_identical": before == after,
            "size_bytes": size,
            "source_mtime": mtime,
            "reused": False,
        }

    # ------------------------------------------------------------------ version de travail
    def version_dir(self, dataset_id: str, version_no: int) -> Path:
        d = self.versions / dataset_id / f"v{version_no:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def work_dir(self, dataset_id: str, version_no: int) -> Path:
        d = self.work / dataset_id / f"v{version_no:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_version_files(self, dataset_id: str, version_no: int, schema: dict,
                            columns: list[dict], source_metadata: dict, manifest: dict) -> dict:
        vdir = self.version_dir(dataset_id, version_no)
        (vdir / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
        (vdir / "source_metadata.json").write_text(json.dumps(source_metadata, ensure_ascii=False, indent=2, default=str),
                                                   encoding="utf-8")
        _write_variables_csv(vdir / "variables.csv", columns)
        (vdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return {"version_dir": str(vdir), "schema_file": str(vdir / "schema.json"),
                "variables_file": str(vdir / "variables.csv"), "manifest_file": str(vdir / "manifest.json")}

    def latest_version(self, dataset_id: str) -> dict | None:
        vs = self.existing_versions(dataset_id)
        return vs[-1] if vs else None

    def storage_stats(self) -> dict:
        def size_of(p: Path) -> int:
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0

        return {"raw_bytes": size_of(self.raw), "work_bytes": size_of(self.work),
                "versions_bytes": size_of(self.versions),
                "datasets": sorted([d.name for d in self.versions.iterdir()]) if self.versions.exists() else []}


def _make_writable(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass


def _write_variables_csv(path: Path, columns: list[dict]) -> None:
    import csv

    fields = ["ordinal", "name", "normalized_type", "source_type", "semantic_role", "nullable",
              "null_count", "null_pct", "distinct_count", "distinct_pct", "id_candidate",
              "constant", "min", "max", "samples"]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for c in columns:
            row = dict(c)
            if isinstance(row.get("samples"), list):
                row["samples"] = " | ".join(str(s) for s in row["samples"][:3])
            w.writerow(row)
