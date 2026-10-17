"""Description des jeux de données du registre (page Données).

Aucun appel à l'IA ici : le titre, le résumé, la période, les mots-clés et le rôle des colonnes viennent de la
compréhension rédigée par Hermes à l'étape Vérifier (hermes_clean) ; les changements d'une version à l'autre sont
calculés par règles (lignes, colonnes ajoutées ou retirées, période étendue). Un petit fichier JSON par jeu de
données, à côté du registre, garde le tout.
"""
import datetime as dt
import json
import os
import re


def _path(meta_dir, dataset_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", str(dataset_id))[:120]
    if not safe:
        raise ValueError("Jeu de données inconnu.")
    return os.path.join(meta_dir, f"{safe}.json")


def load(meta_dir, dataset_id):
    try:
        with open(_path(meta_dir, dataset_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(meta_dir, dataset_id, meta):
    p = _path(meta_dir, dataset_id)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(p + ".tmp", p)


def stats(df, period_of):
    """Empreinte d'une version : lignes, colonnes, première et dernière période."""
    first = last = None
    for c in df.columns:
        dt_s = period_of(df[c])
        if dt_s is not None and dt_s.notna().any():
            first, last = str(dt_s.min().to_period("M")), str(dt_s.max().to_period("M"))
            break
    return {"lignes": int(len(df)), "colonnes": [str(c) for c in df.columns], "debut": first, "fin": last}


def changes(prev, cur):
    """Ce qui change entre deux versions, en phrases courtes."""
    if not prev:
        return ["Première version enregistrée."]
    out = []
    d = cur["lignes"] - prev.get("lignes", 0)
    if d:
        out.append(f"{'+' if d > 0 else ''}{d} ligne(s) ({prev.get('lignes', 0)} → {cur['lignes']}).")
    added = [c for c in cur["colonnes"] if c not in prev.get("colonnes", [])]
    gone = [c for c in prev.get("colonnes", []) if c not in cur["colonnes"]]
    if added:
        out.append("Colonne(s) ajoutée(s) : " + ", ".join(added[:6]) + ("…" if len(added) > 6 else "") + ".")
    if gone:
        out.append("Colonne(s) retirée(s) : " + ", ".join(gone[:6]) + ("…" if len(gone) > 6 else "") + ".")
    if cur.get("fin") and prev.get("fin") and cur["fin"] != prev["fin"]:
        out.append(f"Période étendue jusqu'à {cur['fin']} (avant : {prev['fin']}).")
    elif cur.get("debut") and prev.get("debut") and cur["debut"] != prev["debut"]:
        out.append(f"Début de période modifié : {cur['debut']} (avant : {prev['debut']}).")
    return out or ["Même structure ; valeurs mises à jour."]


def describe(meta_dir, dataset_id, version_no, df, period_of, comprehension=None, dataset_name=None):
    meta = load(meta_dir, dataset_id)
    versions = meta.setdefault("versions", {})
    cur = stats(df, period_of)
    v = str(int(version_no or 0))
    earlier = sorted((int(k) for k in versions if k.isdigit() and int(k) < int(v)), reverse=True)
    prev = versions[str(earlier[0])]["stats"] if earlier else None
    versions[v] = {"date": dt.datetime.now().isoformat(timespec="seconds"), "stats": cur, "changements": changes(prev, cur)}
    if isinstance(comprehension, dict) and comprehension.get("resume"):
        for k in ("titre", "resume", "grain", "periode"):
            if comprehension.get(k):
                meta[k] = str(comprehension[k])[:500]
        meta["mots_cles"] = [str(m)[:40] for m in (comprehension.get("mots_cles") or [])][:10]
        meta["colonnes"] = [{"nom": c.get("nom"), "role": c.get("role")} for c in (comprehension.get("colonnes") or [])][:60]
        meta["source"] = "hermes"
    meta.setdefault("titre", dataset_name or "")
    if not meta.get("periode") and cur.get("debut"):
        meta["periode"] = cur["debut"] + (f" à {cur['fin']}" if cur.get("fin") and cur["fin"] != cur["debut"] else "")
    meta["mis_a_jour"] = versions[v]["date"]
    _save(meta_dir, dataset_id, meta)
    return meta


def summary(meta):
    """Ce que la page Données affiche pour un jeu de données."""
    if not meta:
        return None
    return {k: meta.get(k) for k in ("titre", "resume", "grain", "periode", "mots_cles", "source", "mis_a_jour")}
