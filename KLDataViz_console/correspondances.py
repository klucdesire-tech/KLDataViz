# -*- coding: utf-8 -*-
"""Correspondances validées entre le dictionnaire d'indicateurs et les colonnes d'un fichier, par espace.

Parcours : à la première génération d'un tableau de bord sur une structure de fichier (mêmes colonnes), Hermes
PROPOSE une formule par indicateur (règles, puis IA pour ce que les règles n'ont pas trouvé) ; l'utilisateur les
valide, corrige ou ignore ; elles sont mémorisées pour cette structure et réutilisées ensuite (actualisation d'une
source connectée comprise, sans rappeler l'IA). Les calculs sont faits par hermes_da.correspondance.

L'IA ne voit que les noms de colonnes, leurs types et les étiquettes de catégories répétées — jamais une valeur
individuelle (nom, code patient, date). Chacune de ses propositions est vérifiée contre le fichier avant d'être gardée.

Stockage : <espace>/storage/correspondances.json  {signature des colonnes: correspondance}.
"""
import datetime as dt
import hashlib
import json
import os
import sys
import threading

import tenancy as T

_lock = threading.Lock()
STATUTS = ("propose", "valide", "ignore")


def _C():
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "HERMES_DA")
    if d not in sys.path:
        sys.path.insert(0, d)
    from hermes_da import correspondance
    return correspondance


def _path():
    d = os.environ.get("KLDV_CORR_DIR") or T.current().dir("storage")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "correspondances.json")


def signature(df, dictionnaire=None) -> str:
    """Même structure de fichier (mêmes colonnes) ET même dictionnaire = même correspondance, quel que soit le contenu.
    Un dictionnaire réimporté ou modifié (codes, types) redonne donc de nouvelles propositions."""
    cols = sorted(str(c).strip().lower() for c in df.columns)
    dico = sorted(f"{i.get('code', '')}:{i.get('type', '')}".lower() for i in (dictionnaire or []))
    return hashlib.sha1(("|".join(cols) + "#" + "|".join(dico)).encode("utf-8")).hexdigest()[:16]


def _tout():
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def charger(df, dictionnaire=None):
    return _tout().get(signature(df, dictionnaire))


def _sauver(df, corr, dictionnaire=None):
    with _lock:
        d = _tout()
        d[signature(df, dictionnaire)] = dict(corr, maj=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        p = _path()
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(p + ".tmp", p)


# ---------------------------------------------------------------------------------------------- propositions de l'IA
SYSTEM_IA = """Tu relies les indicateurs d'un dictionnaire métier aux colonnes d'un fichier de données, en écrivant pour
chacun une FORMULE dans ce langage (JSON) :
  {"mode": "comptage"|"taux"|"somme"|"rapport"|"moyenne"|"mediane",
   "numerateur": {"colonne": "...", "valeurs": ["...", ...]},   // lignes comptées ou retenues (valeurs EXACTES des catégories)
   "denominateur": {"colonne": "...", "valeurs": [...]},         // taux : lignes de référence (facultatif : numérateur renseigné)
   "colonne": "...",                                              // somme, moyenne, médiane, rapport : colonne chiffrée
   "filtre": {"colonne": "...", "valeurs": [...]},                // facultatif : lignes à garder d'abord
   "stock": true|false}                                           // valeur de la dernière période (file active…)
Exemples : TX_CURR (Nombre) = {"mode":"comptage","numerateur":{"colonne":"statut","valeurs":["Actif"]}} ;
TX_PVLS (Pourcentage) = {"mode":"taux","numerateur":{"colonne":"resultat_cv","valeurs":["Supprimé"]},
"denominateur":{"colonne":"resultat_cv","valeurs":["Supprimé","Non supprimé"]}}.
Règles :
- N'utilise QUE des colonnes et des valeurs qui figurent dans la liste fournie (orthographe exacte).
- Un indicateur « Pourcentage » → mode taux ou rapport ; « Nombre » → comptage ou somme.
- « evolution » : false si la colonne décrit un état actuel (statut actuel), true si l'indicateur a un sens par période.
- Export « long » (une colonne d'indicateurs + une colonne de valeurs, ex. DHIS2) : utilise TOUJOURS
  « somme » ou « rapport » sur la colonne des valeurs, filtrés par le(s) libellé(s) d'indicateur ; jamais un comptage
  ou un taux de lignes (une ligne n'y est pas un patient).
- Si aucune colonne ne permet de calculer l'indicateur, n'écris rien pour lui : mieux vaut absent que faux.
Réponds en JSON STRICT : {"indicateurs":[{"code":"...","formule":{...},"evolution":true,"explication":"une phrase"}]}"""


def _proposer_ia(df, indicateurs, r, llm, parse):
    C = _C()
    payload = {"indicateurs": [{k: i.get(k, "") for k in ("code", "libelle", "type", "numerateur", "denominateur", "total")}
                               for i in indicateurs][:80],
               "colonnes": C.resume_colonnes(df, r)}
    try:
        txt = llm([{"role": "system", "content": SYSTEM_IA},
                   {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], 6000)
    except Exception:
        return []
    data = parse(txt) if txt else None
    by = {str(i["code"]).strip().lower(): i for i in indicateurs}
    long_ic, _vc = C.colonnes_longues(df, indicateurs)
    out = []
    for it in (data or {}).get("indicateurs", []) if isinstance(data, dict) else []:
        ind = by.get(str(it.get("code") or "").strip().lower())
        f = it.get("formule")
        if ind is None or not isinstance(f, dict) or C.valider(df, f):
            continue  # proposition invérifiable contre le fichier : écartée
        if not C.mode_compatible(ind, f.get("mode")):
            continue  # un pourcentage calculé comme un nombre (ou l'inverse) : écarté
        if long_ic and f.get("mode") in ("comptage", "taux"):
            continue  # export long : une ligne n'est pas un patient, compter des lignes n'a pas de sens
        out.append({"code": ind["code"], "libelle": ind.get("libelle") or ind["code"], "type": ind.get("type") or "",
                    "formule": f, "evolution": bool(it.get("evolution", True)), "source": "ia", "confiance": 0.6,
                    "explication": str(it.get("explication") or "")[:240]})
    return out


def obtenir(df, dictionnaire, llm=None, parse=None):
    """Correspondance pour ce fichier : la mémorisée si elle existe, sinon des propositions (mémorisées)."""
    if not dictionnaire:
        return None
    corr = charger(df, dictionnaire)
    if corr:
        return corr
    C = _C()
    r = C.roles(df)
    props = C.proposer_regles(df, dictionnaire, r)
    vus = {p["code"].lower() for p in props}
    reste = [i for i in dictionnaire if i["code"].lower() not in vus]
    if llm and parse and reste:
        props += _proposer_ia(df, reste, r, llm, parse)
    for p in props:
        p["statut"] = "propose"
    corr = {"periode": r["periode"], "dimensions": r["dimensions"], "indicateurs": props}
    _sauver(df, corr, dictionnaire)
    return corr


def enregistrer(df, data: dict, dictionnaire=None) -> dict:
    """Enregistre les choix de l'utilisateur. Une formule validée est vérifiée contre le fichier et contre le
    dictionnaire (code connu, calcul cohérent avec le type Nombre / Pourcentage) ; erreur sinon."""
    C = _C()
    corr = charger(df, dictionnaire) or {"indicateurs": []}
    connus = {str(i.get("code", "")).lower(): i for i in (dictionnaire or [])}
    par_code = {i["code"].lower(): i for i in corr.get("indicateurs", [])}
    for it in data.get("indicateurs") or []:
        code = str(it.get("code") or "").strip()
        if not code:
            continue
        cur = par_code.get(code.lower(), {"code": code, "libelle": it.get("libelle") or code, "type": it.get("type") or "",
                                          "source": "utilisateur", "confiance": 1.0, "explication": ""})
        if it.get("formule") is not None and it["formule"] != cur.get("formule"):
            cur["formule"], cur["source"] = it["formule"], "utilisateur"
            cur["explication"] = "formule modifiée par l'utilisateur"
        if "evolution" in it:
            cur["evolution"] = bool(it["evolution"])
        st = it.get("statut") or cur.get("statut") or "propose"
        if st not in STATUTS:
            raise ValueError("Statut inconnu.")
        if connus and code.lower() not in connus:
            raise ValueError(f"{code} : absent du dictionnaire d'indicateurs.")
        if st == "valide":
            err = C.valider(df, cur.get("formule"))
            if err:
                raise ValueError(f"{code} : " + " ; ".join(err))
            ind = connus.get(code.lower()) or cur
            if not C.mode_compatible(ind, (cur.get("formule") or {}).get("mode")):
                raise ValueError(f"{code} : un indicateur « {ind.get('type')} » demande un calcul "
                                 + ("en taux, en rapport ou en moyenne de % ." if C.est_taux(ind) else "en nombre (comptage ou somme)."))
        cur["statut"] = st
        par_code[code.lower()] = cur
    if data.get("periode") is not None:
        if data["periode"] and data["periode"] not in df.columns:
            raise ValueError("Colonne de période inconnue.")
        corr["periode"] = data["periode"] or None
    if isinstance(data.get("dimensions"), list):
        corr["dimensions"] = [d for d in data["dimensions"] if d in df.columns][:4]
    corr["indicateurs"] = list(par_code.values())
    _sauver(df, corr, dictionnaire)
    return corr


def oublier(df, dictionnaire=None):
    with _lock:
        d = _tout()
        d.pop(signature(df, dictionnaire), None)
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)


def public(df, corr, lib=lambda c: str(c)):
    """Vue pour l'interface : chaque proposition décrite en clair, avec sa valeur sur ce fichier, et de quoi la modifier."""
    if not corr:
        return None
    C = _C()
    cle = C.cle_periode(df, corr.get("periode"))
    inds = []
    for i in corr.get("indicateurs", []):
        f = i.get("formule")
        err = C.valider(df, f) if f else ["pas de formule"]
        inds.append({"code": i["code"], "libelle": i.get("libelle"), "type": i.get("type"), "statut": i.get("statut"),
                     "source": i.get("source"), "confiance": i.get("confiance"), "explication": i.get("explication"),
                     "evolution": i.get("evolution", True), "formule": f, "description": C.decrire(f, lib) if f else "",
                     "valeur": None if err else C.evaluer(df, f, cle), "erreurs": err})
    r = C.roles(df)
    cols = []
    for c in df.columns:
        if c in r["identifiants"]:
            continue
        e = {"nom": str(c), "libelle": lib(c), "numerique": bool(__import__("pandas").api.types.is_numeric_dtype(df[c]))}
        vals = df[c].dropna().astype(str).str.strip().value_counts()
        if 2 <= len(vals) <= 30:
            e["categories"] = [str(v) for v in vals.index]
        cols.append(e)
    return {"periode": corr.get("periode"), "dimensions": corr.get("dimensions") or [],
            "indicateurs": inds, "colonnes": cols,
            "periodes_possibles": [c for c in df.columns if c == r["periode"] or C._date_like(df[c])][:8]}
