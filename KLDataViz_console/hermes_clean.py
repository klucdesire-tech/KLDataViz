"""Hermes relit les corrections proposées par les règles et en propose de plus fines.

Un seul appel à l'IA, sur un profil du fichier (jamais les lignes complètes) :
- `avis` : pour chaque correction des règles (doublons, types, imputations…), « recommande » ou « deconseille »
  avec une raison (ex. ne pas compléter par la médiane un nombre de cas où le vide veut dire « aucun cas ») ;
- `corrections` : recettes vérifiables que le moteur exécute lui-même :
    harmoniser      {colonne, correspondances {variante: forme retenue}}
    remplacer       {colonne, valeurs [..], par (null = vide)}   ex. « - », « N/A », « ND »
    exclure_lignes  {colonne, valeurs [..]}                       ex. lignes « Total »
    plage           {colonne, min, max}  valeurs impossibles → vides (taux > 100, effectif négatif)
    texte           {colonne}            codes à garder en texte (le moteur ne les convertit pas en nombres)
Chaque recette est validée sur les données (colonnes et valeurs existantes) et reçoit son impact réel
(nombre de cellules ou de lignes touchées) ; une recette sans effet est écartée.
"""
import json
import re
import unicodedata

import numpy as np
import pandas as pd

TYPES = {"harmoniser", "remplacer", "exclure_lignes", "plage", "texte"}
SENTINELS = {"-", "--", "—", "n/a", "na", "nd", "n.d.", "?", ".", "néant", "neant", "null", "none", "#n/a", "#ref!", "#div/0!",
             "#value!", "non renseigné", "non renseigne", "inconnu"}
MAX_CORR = 12


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _col(df, name):
    if name in df.columns:
        return name
    low = {str(c).strip().lower(): c for c in df.columns}
    return low.get(str(name or "").strip().lower())


def profile(df, rule_items, file_name):
    """Ce que Hermes voit : par colonne, valeurs fréquentes, variantes proches, valeurs suspectes, plages."""
    cols = []
    for c in df.columns:
        s = df[c]
        info = {"nom": str(c), "manquantes": int(s.isna().sum()), "distinctes": int(s.nunique(dropna=True))}
        txt = s.dropna().astype(str).str.strip()
        sent = txt[txt.str.lower().isin(SENTINELS)].value_counts().head(6)
        if len(sent):
            info["valeurs_suspectes"] = {str(k): int(v) for k, v in sent.items()}
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            v = s.dropna()
            info.update(type="nombre", min=_f(v.min()), max=_f(v.max()), mediane=_f(v.median()),
                        negatifs=int((v < 0).sum()), superieurs_100=int((v > 100).sum()), zeros=int((v == 0).sum()))
        else:
            num = pd.to_numeric(txt.str.replace(",", ".", regex=False), errors="coerce")
            info["type"] = "texte"
            if len(txt) and num.notna().mean() >= 0.8:
                info["ressemble_a_des_nombres"] = True
                info["zeros_en_tete"] = int(txt.str.match(r"^0\d").sum())
            if info["distinctes"] <= 80:
                info["valeurs"] = {str(k): int(v) for k, v in txt.value_counts().head(15).items()}
                groups = {}
                for val in txt.unique():
                    groups.setdefault(_norm(val), set()).add(val)
                proches = [sorted(g) for g in groups.values() if len(g) > 1]
                if proches:
                    info["variantes_proches"] = proches[:10]
        cols.append(info)
    return {"fichier": file_name, "lignes": int(len(df)), "colonnes": cols,
            "corrections_des_regles": rule_items}


def _f(x):
    try:
        x = float(x)
        return round(x, 4) if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


SYSTEM = (
    "Tu es Hermes, data manager expérimenté (données sanitaires et de gestion). On te donne le profil d'un "
    "fichier et la liste des corrections automatiques proposées par des règles.\n"
    "1. Donne ton avis sur chaque correction des règles : « recommande » ou « deconseille », avec une raison "
    "courte et concrète. Déconseille par exemple de compléter par la médiane un effectif ou un nombre de cas "
    "(le vide peut vouloir dire « aucun cas » ou « non rapporté »), ou de convertir en nombre un code "
    "(code site, matricule, téléphone, code avec zéros en tête).\n"
    "2. Propose au plus {n} corrections supplémentaires réellement utiles, UNIQUEMENT parmi ces types :\n"
    "- harmoniser : {{colonne, correspondances: {{variante: forme retenue}}}} pour des libellés qui désignent la "
    "même chose (casse, accents, espaces, fautes) ; appuie-toi sur « variantes_proches » et « valeurs » ;\n"
    "- remplacer : {{colonne, valeurs: [...], par: null}} pour des vides déguisés (« - », « N/A », « ND »…) ;\n"
    "- exclure_lignes : {{colonne, valeurs: [...]}} pour des lignes de total ou de sous-total mêlées au détail ;\n"
    "- plage : {{colonne, min, max}} pour des valeurs impossibles (taux > 100, effectif négatif) ;\n"
    "- texte : {{colonne}} pour un code à garder en texte.\n"
    "Utilise les noms de colonnes et les valeurs EXACTEMENT comme dans le profil. N'invente rien : si rien "
    "n'est utile, renvoie une liste vide.\n"
    "Réponds avec un objet JSON valide et rien d'autre :\n"
    '{{"avis":[{{"id":"…","verdict":"recommande|deconseille","raison":"…"}}],'
    '"corrections":[{{"type":"…","colonne":"…","titre":"phrase courte","raison":"…", …}}]}}'
)


# --------------------------------------------------------------------------- #
# Validation et exécution
# --------------------------------------------------------------------------- #
def _line(i):
    return int(i) + 2  # numéro de ligne dans le fichier source, en-tête compris


def _py(v):
    """Valeur sérialisable en JSON (numpy → Python, manquant → None)."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v.item() if hasattr(v, "item") else v


def _mask_values(s, values):
    vals = {str(v).strip() for v in values if v is not None}
    return s.astype(str).str.strip().isin(vals) & s.notna()


def _values(c):
    """Valeurs visées, tolérant « valeur » au singulier, une chaîne seule et les guillemets ajoutés par l'IA."""
    raw = c.get("valeurs", c.get("valeur"))
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    return [str(v).strip().strip("«»\"'“”").strip() for v in raw if v is not None]


def validate(df, items):
    """Recettes conservées avec leur impact mesuré sur les données ; les autres sont écartées avec la raison."""
    kept, rejected = [], []
    for k, c in enumerate(items or []):
        if not isinstance(c, dict):
            continue
        t = str(c.get("type") or "").lower()
        col = _col(df, c.get("colonne"))
        try:
            if t not in TYPES:
                raise ValueError(f"type inconnu « {t} »")
            if col is None:
                raise ValueError(f"colonne absente « {c.get('colonne')} »")
            s = df[col]
            out = {"type": t, "colonne": str(col), "titre": str(c.get("titre") or "")[:160],
                   "raison": str(c.get("raison") or "")[:300]}
            if t == "harmoniser":
                mp = {str(a).strip(): str(b).strip() for a, b in (c.get("correspondances") or {}).items()
                      if str(a).strip() != str(b).strip()}
                present = set(s.dropna().astype(str).str.strip())
                mp = {a: b for a, b in mp.items() if a in present}
                if not mp:
                    raise ValueError("aucune variante présente")
                out["correspondances"] = mp
                impact = int(_mask_values(s, mp.keys()).sum())
            elif t in ("remplacer", "exclure_lignes"):
                vals = _values(c)
                impact = int(_mask_values(s, vals).sum())
                out["valeurs"] = vals
                if t == "remplacer":
                    out["par"] = None if c.get("par") in (None, "", "null") else str(c.get("par"))
            elif t == "plage":
                lo, hi = c.get("min"), c.get("max")
                num = pd.to_numeric(s, errors="coerce")
                bad = pd.Series(False, index=s.index)
                if lo is not None:
                    bad |= num < float(lo)
                if hi is not None:
                    bad |= num > float(hi)
                impact = int(bad.sum())
                out.update(min=lo, max=hi)
            else:  # texte
                impact = int(s.notna().sum())
            if impact <= 0:
                raise ValueError("aucune valeur concernée")
            out["impact"] = impact
            out["id"] = f"h:{len(kept)}"
            kept.append(out)
        except Exception as e:
            recette = {k: v for k, v in c.items() if k not in ("titre", "raison")}
            rejected.append(f"{c.get('titre') or t} : {e} — {json.dumps(recette, ensure_ascii=False)[:200]}")
        if len(kept) >= MAX_CORR:
            break
    return kept, rejected


def apply(df, corrections):
    """Applique les recettes acceptées (avant le nettoyage par règles). Retourne (df, détails par id, colonnes texte)."""
    df = df.copy()
    details, keep_text = {}, []
    for c in corrections or []:
        col = _col(df, c.get("colonne"))
        if col is None:
            continue
        t, cid = c.get("type"), c.get("id") or ""
        s = df[col]
        if t == "harmoniser":
            mp = c.get("correspondances") or {}
            m = _mask_values(s, mp.keys())
            ex = [{"ligne": _line(i), "avant": _py(s[i]), "apres": mp.get(str(s[i]).strip())} for i in s[m].index[:8]]
            df.loc[m, col] = s[m].astype(str).str.strip().map(mp)
            details[cid] = {"n": int(m.sum()), "exemples": ex}
        elif t == "remplacer":
            m = _mask_values(s, _values(c))
            ex = [{"ligne": _line(i), "avant": _py(s[i]), "apres": c.get("par")} for i in s[m].index[:8]]
            df.loc[m, col] = np.nan if c.get("par") is None else c.get("par")
            details[cid] = {"n": int(m.sum()), "exemples": ex}
        elif t == "exclure_lignes":
            m = _mask_values(s, _values(c))
            details[cid] = {"n": int(m.sum()), "lignes": [_line(i) for i in df.index[m][:15]]}
            df = df[~m]
        elif t == "plage":
            num = pd.to_numeric(s, errors="coerce")
            m = pd.Series(False, index=s.index)
            if c.get("min") is not None:
                m |= num < float(c["min"])
            if c.get("max") is not None:
                m |= num > float(c["max"])
            ex = [{"ligne": _line(i), "avant": _py(s[i]), "apres": None} for i in s[m].index[:8]]
            df.loc[m, col] = np.nan
            details[cid] = {"n": int(m.sum()), "exemples": ex}
        elif t == "texte":
            keep_text.append(str(col))
            details[cid] = {"n": int(s.notna().sum())}
    return df, details, keep_text


def run(df, rule_items, file_name, llm, parse, progress=lambda _t: None):
    if llm is None:
        return {"ia": False, "avis": [], "corrections": [], "raison": "IA non configurée"}
    progress("Hermes relit les corrections…")
    prof = profile(df, rule_items, file_name)
    content = llm([{"role": "system", "content": SYSTEM.format(n=MAX_CORR)},
                   {"role": "user", "content": json.dumps(prof, ensure_ascii=False, default=str)}], 12000)
    data = parse(content)
    if not isinstance(data, dict):  # pas de réponse exploitable : on le dit, les règles restent seules
        return {"ia": False, "avis": [], "corrections": [], "raison": "Hermes n'a pas répondu à temps."}
    known = {str(it.get("id")) for it in rule_items}
    avis = []
    for a in data.get("avis") or []:
        if isinstance(a, dict) and str(a.get("id")) in known and a.get("verdict") in ("recommande", "deconseille"):
            avis.append({"id": str(a["id"]), "verdict": a["verdict"], "raison": str(a.get("raison") or "")[:300]})
    corrections, rejected = validate(df, data.get("corrections"))
    return {"ia": True, "avis": avis, "corrections": corrections, "rejetees": rejected}
