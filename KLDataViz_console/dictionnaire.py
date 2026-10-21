# -*- coding: utf-8 -*-
"""
Dictionnaire d'indicateurs de l'espace (facultatif) : le référentiel métier de l'organisation
(ex. indicateurs MER PEPFAR : code, libellé, type Nombre/Pourcentage, numérateur, dénominateur,
calcul du total annuel…).

Importé, il devient la référence du tableau de bord (Mode B de HERMES_DA) : ses indicateurs sont
calculés selon leur type, ceux absents des données sont signalés, et les tuiles hors dictionnaire
sont marquées. Sans dictionnaire, l'agent applique les normes intégrées (Mode A).

Stockage : <espace>/storage/dictionnaire.json, sur le disque de l'espace — chaque espace a le sien.
Le dictionnaire ne contient que des définitions (aucune donnée) ; le fichier importé n'est pas conservé.
"""
import datetime as dt
import json
import os
import sys
import tempfile
import threading

import tenancy as T

MAX_INDICATEURS = 1000
MAX_CHAMP = 2000
_lock = threading.Lock()


def _catalog():
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "HERMES_DA")
    if d not in sys.path:
        sys.path.insert(0, d)
    from hermes_da import indicators_catalog
    return indicators_catalog


def _path():
    # KLDV_DICT_DIR : dossier imposé (tests) ; sinon le stockage de l'espace courant
    d = os.environ.get("KLDV_DICT_DIR") or T.current().dir("storage")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "dictionnaire.json")


def _read():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and isinstance(data.get("indicateurs"), list) else None
    except (OSError, ValueError):
        return None


def charger() -> list:
    """Indicateurs du dictionnaire de l'espace ([] si aucun) — à passer au tableau de bord."""
    data = _read()
    return list(data["indicateurs"]) if data else []


def info() -> dict:
    """Résumé affiché dans la console (sans le détail des indicateurs)."""
    data = _read()
    if not data:
        return {"actif": False}
    inds = data["indicateurs"]
    domaines = {}
    for i in inds:
        domaines[i.get("domaine") or "Indicateurs"] = domaines.get(i.get("domaine") or "Indicateurs", 0) + 1
    return {"actif": True, "nom": data.get("nom"), "importe_le": data.get("importe_le"),
            "indicateurs": len(inds), "domaines": domaines,
            "apercu": [{"code": i.get("code"), "libelle": i.get("libelle"), "type": i.get("type")} for i in inds[:8]]}


def importer(data: bytes, name: str) -> dict:
    """Lit un dictionnaire (Excel ou CSV) et le remplace dans l'espace. Lève ValueError s'il est illisible."""
    ext = os.path.splitext(str(name or ""))[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xls", ".csv"):
        raise ValueError("Format non pris en charge : importez un fichier Excel (.xlsx) ou CSV.")
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            inds = _catalog().parse_dictionary(tmp)
        except Exception as exc:
            raise ValueError(f"Dictionnaire illisible : {exc}") from None
    finally:
        try:
            os.remove(tmp)  # le fichier importé n'est pas conservé : seules les définitions le sont
        except OSError:
            pass
    clean = []
    for i in inds:
        code, lib = str(i.get("code") or "").strip(), str(i.get("libelle") or "").strip()
        if code.lower() in ("", "nan") or lib.lower() in ("", "nan"):
            continue
        clean.append({k: ("" if str(v).strip().lower() == "nan" else str(v).strip()[:MAX_CHAMP])
                      for k, v in i.items()})
    if not clean:
        raise ValueError("Aucun indicateur reconnu : il faut au moins les colonnes « Code » et « Indicateur » "
                         "(ou « Libellé »), avec une ligne par indicateur.")
    if len(clean) > MAX_INDICATEURS:
        raise ValueError(f"Dictionnaire trop long ({len(clean)} indicateurs, maximum {MAX_INDICATEURS}).")
    doc = {"nom": os.path.basename(str(name))[:120], "importe_le": dt.datetime.now(dt.timezone.utc).isoformat(),
           "indicateurs": clean}
    with _lock:
        p = _path()
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(p + ".tmp", p)
    return info()


def supprimer() -> dict:
    with _lock:
        try:
            os.remove(_path())
        except FileNotFoundError:
            pass
    return info()
