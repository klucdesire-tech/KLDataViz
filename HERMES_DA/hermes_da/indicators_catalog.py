# -*- coding: utf-8 -*-
"""Catalogue d'indicateurs : dictionnaire importable (FACULTATIF) + normes intégrées.

Deux modes (décidés par la présence d'un dictionnaire importé) :

- **Mode A — sans dictionnaire** : l'agent choisit lui-même les indicateurs selon les
  normes internationales du domaine (base intégrée ``BUILTIN_DOMAINS``), d'après les données.
- **Mode B — avec dictionnaire** : le dictionnaire importé est LA référence. Ses indicateurs
  sont intégrés en priorité dans toutes les analyses et le tableau de bord ; un indicateur du
  dictionnaire absent des données est signalé, et un indicateur trouvé dans les données mais
  hors dictionnaire est affiché et marqué « hors dictionnaire ».

Le dictionnaire se présente comme le référentiel MER : colonnes
``domaine, code, libelle, type, numerateur, denominateur, mode_calcul, total, frequence, ventilations``.
``parse_dictionary`` accepte les en-têtes exacts du fichier MER (français) et les mappe sur ce schéma.

``to_domains`` convertit la liste d'indicateurs en la structure attendue par l'agent dashboard
(``{domaine: {nom, mots, indicateurs}}``) — chaque indicateur devenant un « ki » avec mots-clés
générés depuis son code et son libellé.
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata

import pandas as pd

# Colonnes du schéma générique (ordre d'affichage / stockage)
SCHEMA_COLS = ["domaine", "code", "libelle", "type", "numerateur", "denominateur",
               "mode_calcul", "total", "frequence", "ventilations"]

# Correspondance entre les en-têtes du fichier MER (français) et le schéma générique
MER_MAP = {
    "domaine": "domaine",
    "code": "code",
    "indicateur": "libelle",
    "libelle": "libelle",
    "type": "type",
    "numerateur": "numerateur",
    "denominateur": "denominateur",
    "mode de calcul": "mode_calcul",
    "calcul du total annuel": "total",
    "total": "total",
    "frequence": "frequence",
    "principales ventilations": "ventilations",
    "ventilations": "ventilations",
}

# Mots vides : retirés des mots-clés générés depuis les libellés (mots trop génériques)
_STOP = {"de", "du", "des", "le", "la", "les", "un", "une", "et", "ou", "au", "aux", "a", "d",
         "sous", "avec", "pour", "dans", "sur", "par", "nombre", "pourcentage", "proportion",
         "personnes", "patients", "patient", "parmi", "dont", "tout", "tous", "toute", "toutes",
         "qui", "que", "quel", "quelle", "leurs", "leur", "son", "sa", "ses", "ce", "cette", "ces",
         "au cours", "a la", "au moins", "au plus", "a recu", "ayant", "recoivent", "est", "sont"}

# Mots indiquant qu'une hausse de l'indicateur est une DÉGRADATION (→ bon = -1)
_NEGATIFS = ("deces", "decede", "mortalite", "perdu", "perdus", "abandon", "arret", "echec",
             "non supprim", "non supprime", "1000", "delai", "attente", "rupture", "annul",
             "absence", "incident", "refus", "infection", "infectes")


def _norm(t) -> str:
    """Normalise pour la comparaison : minuscules, sans accents, ponctuation → espace."""
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def _ki(nom, tous, un=(), sauf=(), bon=1):
    return {"nom": nom, "tous": list(tous), "un": list(un), "sauf": list(sauf), "bon": bon}


def _bon(nom) -> int:
    n = _norm(nom)
    return -1 if any(m in n for m in _NEGATIFS) else 1


# --------------------------------------------------------------------------
# Lecture du dictionnaire (Excel/CSV) → liste d'indicateurs normalisés
# --------------------------------------------------------------------------
def parse_dictionary(path) -> list:
    """Lit un dictionnaire d'indicateurs (Excel ou CSV) et renvoie une liste de dicts normalisés.

    Accepte les en-têtes du fichier MER (Domaine, Code, Indicateur, Type, Numérateur,
    Dénominateur, Mode de calcul, Calcul du total annuel, Fréquence, Principales ventilations)
    comme des en-têtes déjà génériques (domaine, code, libelle…).
    """
    if str(path).lower().endswith((".xlsx", ".xls", ".xlsm")):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)
    # Mappe les en-têtes français (sans accents) vers le schéma générique
    ren = {}
    for c in df.columns:
        key = _norm(str(c))
        ren[c] = MER_MAP.get(key, key.lower().strip())
    df = df.rename(columns=ren)
    out = []
    for _, r in df.iterrows():
        code = str(r.get("code") or "").strip()
        libelle = str(r.get("libelle") or "").strip()
        if not code and not libelle:
            continue  # ligne vide
        out.append({
            "domaine": str(r.get("domaine") or "").strip(),
            "code": code,
            "libelle": libelle or code,
            "type": str(r.get("type") or "").strip(),
            "numerateur": str(r.get("numerateur") or "").strip(),
            "denominateur": str(r.get("denominateur") or "").strip(),
            "mode_calcul": str(r.get("mode_calcul") or "").strip(),
            "total": str(r.get("total") or "").strip(),
            "frequence": str(r.get("frequence") or "").strip(),
            "ventilations": str(r.get("ventilations") or "").strip(),
        })
    return out


def _keywords(code: str, libelle: str):
    """Mots-clés d'un indicateur : code exact (tous) + mots significatifs ET bigrammes du libellé (un)."""
    tous = [code.lower()] if code else []
    norm = _norm(libelle)
    words = [w for w in norm.split() if w not in _STOP and len(w) >= 3 and not w.isdigit()]
    bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)
               if len(words[i]) >= 3 and len(words[i+1]) >= 3]
    un = list(dict.fromkeys(bigrams + words))[:20]
    return tous, un


def to_domains(indicators: list) -> dict:
    """Liste d'indicateurs → {domaine_cle: {nom, mots, indicateurs}} pour l'agent.

    Les mots du domaine sont DISTINCTIFS (fréquents dans ce domaine, rares ailleurs) et incluent
    les bigrammes (« charge virale »), pour discriminer des sous-domaines proches (Dépistage vs
    Suppression virale) qui partagent des mots génériques (statut, résultat…).
    """
    from collections import Counter
    doms: dict = {}
    for ind in indicators:
        dom = ind.get("domaine") or "Indicateurs"
        cle = _norm(dom).replace(" ", "_") or "general"
        if cle not in doms:
            doms[cle] = {"nom": dom, "indicateurs": [], "_freq": Counter()}
        tous, un = _keywords(ind["code"], ind["libelle"])
        doms[cle]["indicateurs"].append(
            _ki(ind["libelle"] or ind["code"], tous, un=un, bon=_bon(ind["libelle"])))
        doms[cle]["_freq"].update(un)
    # IDF : un mot présent dans beaucoup de domaines est peu distinctif (statut, résultat…)
    ndomains = Counter()
    for d in doms.values():
        for w in set(d["_freq"]):
            ndomains[w] += 1
    for cle, d in doms.items():
        scored = []
        for w, f in d["_freq"].items():
            idf = 1.0 / (1.0 + ndomains[w])          # rares entre domaines → plus distinctifs
            scored.append((f * idf, len(w), w))      # départage : bigramme (plus long) d'abord
        d["mots"] = [w for _, _, w in sorted(scored, reverse=True)][:12]
        del d["_freq"]
    return doms


# --------------------------------------------------------------------------
# Normes intégrées (Mode A) — repli quand aucun dictionnaire n'est importé
# --------------------------------------------------------------------------
BUILTIN_DOMAINS = {
    "vih": {
        "nom": "VIH / Sida",
        "mots": ["vih", "hiv", "arv", "tarv", "seropositif", "seropositifs", "charge virale",
                 "depistage", "depistes", "index testing", "pvvih"],
        "indicateurs": [
            _ki("Personnes dépistées (résultat reçu)", ["depist"], sauf=["positif"]),
            _ki("Cas positifs dépistés", ["positif"], sauf=["charge virale"]),
            _ki("Nouveaux sous TARV", ["initi"]),
            _ki("Résultats de charge virale reçus", ["charge virale"], un=["recu", "realise"],
                sauf=["1000", "supprim"]),
            _ki("Charge virale supprimée", ["charge virale"], un=["supprim", "indetect", "< 1000", "<1000"],
                sauf=["non supprim"]),
            _ki("Charge virale ≥ 1 000 copies/mL", ["charge virale", "1000"], sauf=["supprim", "<"], bon=-1),
            _ki("Décès", ["deces"], bon=-1),
        ],
    },
    "paludisme": {
        "nom": "Paludisme",
        "mots": ["palu", "paludisme", "malaria", "moustiquaire", "moustiquaires", "mild", "tdr", "rdt", "cta"],
        "indicateurs": [_ki("Cas confirmés", ["cas", "confirm"]), _ki("Décès", ["deces"], bon=-1),
                        _ki("Moustiquaires distribuées", [], un=["mild", "moustiquaire"])],
    },
    "tuberculose": {
        "nom": "Tuberculose",
        "mots": ["tuberculose", "tb", "tpm", "bacteriologiquement"],
        "indicateurs": [_ki("Cas notifiés", ["cas"], un=["notifi", "tb", "tuberculose"]),
                        _ki("Décès", ["deces"], bon=-1)],
    },
    "vaccination": {
        "nom": "Vaccination / PEV",
        "mots": ["vaccin", "vaccination", "vaccines", "pev", "penta", "penta3", "rougeole", "var", "bcg"],
        "indicateurs": [_ki("Doses administrées", ["dose"]), _ki("Enfants vaccinés", ["enfant", "vaccin"])],
    },
    "sante_maternelle": {
        "nom": "Santé maternelle & néonatale",
        "mots": ["maternel", "maternelle", "cpn", "cpn1", "cpn4", "anc", "accouchement", "accouchements",
                 "cesarienne", "neonatal", "postnatal"],
        "indicateurs": [_ki("Consultations prénatales", [], un=["cpn", "prenatal"]),
                        _ki("Accouchements assistés", ["accouch"]),
                        _ki("Décès maternels", ["deces", "matern"], bon=-1)],
    },
}


# --------------------------------------------------------------------------
# Registre PostgreSQL (schéma `hermes`) + import / chargement
# --------------------------------------------------------------------------
def _dsn():
    from hermes_da import config
    return config.dsn()


def _import_hash(indicators: list) -> str:
    """Empreinte du dictionnaire (pour détecter une ré-importation identique)."""
    h = hashlib.sha256()
    for i in sorted(indicators, key=lambda x: (x["domaine"], x["code"])):
        h.update("|".join(i.get(k, "") for k in SCHEMA_COLS).encode("utf-8"))
    return h.hexdigest()


def import_dictionary(path, structure: str = "default") -> dict:
    """Importe un dictionnaire dans ``hermes.dictionnaire_indicateurs`` (par structure).

    Renvoie {importe: n, structure: ..., deja_present: bool}.
    """
    import psycopg
    indicators = parse_dictionary(path)
    if not indicators:
        raise ValueError("Dictionnaire vide : aucun indicateur reconnu (vérifiez les colonnes domaine/code/libellé).")
    h = _import_hash(indicators)
    with psycopg.connect(_dsn()) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS hermes.dictionnaire_indicateurs (
                    id serial PRIMARY KEY,
                    structure text NOT NULL,
                    domaine text, code text, libelle text, type text,
                    numerateur text, denominateur text, mode_calcul text,
                    total text, frequence text, ventilations text,
                    import_hash text, importe_le timestamptz DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS idx_dict_ind_structure ON hermes.dictionnaire_indicateurs(structure);
            """)
            cur.execute("SELECT import_hash FROM hermes.dictionnaire_indicateurs WHERE structure=%s LIMIT 1",
                        (structure,))
            row = cur.fetchone()
            if row and row[0] == h:
                conn.rollback()
                return {"importe": len(indicators), "structure": structure, "deja_present": True}
            cur.execute("DELETE FROM hermes.dictionnaire_indicateurs WHERE structure=%s", (structure,))
            for i in indicators:
                cur.execute("""
                    INSERT INTO hermes.dictionnaire_indicateurs
                    (structure, domaine, code, libelle, type, numerateur, denominateur,
                     mode_calcul, total, frequence, ventilations, import_hash)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (structure, i.get("domaine"), i.get("code"), i.get("libelle"), i.get("type"),
                      i.get("numerateur"), i.get("denominateur"), i.get("mode_calcul"),
                      i.get("total"), i.get("frequence"), i.get("ventilations"), h))
        conn.commit()
    return {"importe": len(indicators), "structure": structure, "deja_present": False}


def load_dictionary(structure: str = "default") -> list:
    """Dictionnaire importé pour une structure (liste d'indicateurs). [] si aucun."""
    import psycopg
    try:
        with psycopg.connect(_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT domaine, code, libelle, type, numerateur, denominateur,
                           mode_calcul, total, frequence, ventilations
                    FROM hermes.dictionnaire_indicateurs WHERE structure=%s
                    ORDER BY domaine, code
                """, (structure,))
                rows = cur.fetchall()
    except psycopg.errors.UndefinedTable:
        return []
    except Exception:
        return []
    return [dict(zip(SCHEMA_COLS, r)) for r in rows]


def catalogue(structure: str = "default"):
    """Structure « domaine → indicateurs » à utiliser : dictionnaire (Mode B) sinon normes (Mode A)."""
    indicators = load_dictionary(structure)
    if not indicators:
        return BUILTIN_DOMAINS, False
    return to_domains(indicators), True



# --------------------------------------------------------------------------
# Matching indicateur → données + calcul par type (Nombre = somme/comptage, Pourcentage = taux)
# --------------------------------------------------------------------------
def _stem(w):
    """Racine simple pour rapprocher « supprimée / supprimé / SUPPRIME / patients ».

    Ne tronque pas les mots courts (« sans » reste « sans », pas « san »)."""
    if w.endswith("ee"):
        w = w[:-2] + "e"
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    if len(w) >= 5 and w.endswith("s"):
        w = w[:-1]
    return w


def match_and_compute(df, indicators):
    """Pour chaque indicateur du dictionnaire : présent dans les données ? et valeur selon son type.

    - Format long (colonne « indicateur/code/libellé » + « valeur ») : matcher le code exact ou
      les mots-clés ; « Nombre » = somme, « Pourcentage » = valeur lue (déjà un taux).
    - Format liste (une ligne = une observation) : matcher une colonne catégorielle dont les VALEURS
      portent les mots-clés (ex. SUPPRIME / NON-SUPPRIME) ; « Pourcentage » = taux (positif / total),
      « Nombre » = comptage du positif. Un taux privilégie une colonne BINAIRE (2 valeurs).

    Renvoie une liste de dicts {code, libelle, type, present, valeur, detail}.
    """
    import pandas as pd
    ic = next((c for c in df.columns if _norm(c) in ("indicator", "indicateur", "indicateurs",
                                                     "code", "libelle", "libellé")), None)
    vc = next((c for c in df.columns if _norm(c) in ("value", "valeur", "val", "montant", "mesure", "total")
               and pd.to_numeric(df[c], errors="coerce").notna().any()), None)

    def _kw(ind):
        return _keywords(ind["code"], ind["libelle"])

    def _stems(ind):
        tous, un = _kw(ind)
        return {_stem(w) for w in un if (" " in w) or len(w) >= 4}  # bigramme ou mot >= 4 (exclut vih/hiv/arv)

    out = []
    # --- Format long ---
    if ic is not None and vc is not None:
        labels = df[ic].dropna().astype(str)
        for ind in indicators:
            code = ind["code"].strip().lower()
            mask = labels.str.strip().str.lower().eq(code)
            if not mask.any():
                tous, _ = _kw(ind)
                ln = labels.str.lower().str.replace(r"\s+", " ", regex=True)
                mask = ln.apply(lambda t: all(m in t for m in tous)) if tous else pd.Series(False, index=labels.index)
            if not mask.any():
                out.append({"code": ind["code"], "libelle": ind["libelle"], "type": ind["type"],
                            "present": False, "valeur": None, "detail": None})
                continue
            vals = pd.to_numeric(df.loc[mask, vc], errors="coerce")
            val = float(vals.sum(min_count=1)) if vals.notna().any() else None
            out.append({"code": ind["code"], "libelle": ind["libelle"], "type": ind["type"],
                        "present": True, "valeur": val, "detail": f"somme de « {vc} »"})
        return out

    # --- Format liste (une ligne par observation) ---
    cat_cols = []
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]) or pd.api.types.is_datetime64_any_dtype(df[c]):
            continue
        vals = [str(v).strip() for v in df[c].dropna().unique()
                if str(v).strip().lower() not in ("", "nan", "none", "null", "nat")]
        if 2 <= len(vals) <= 8:
            cat_cols.append((c, vals))

    for ind in indicators:
        stems = _stems(ind)
        is_rate = (ind["type"] or "").strip().lower() in ("pourcentage", "proportion", "taux", "%", "rate", "pct")
        cands = cat_cols
        if is_rate:
            cands = [(c, v) for c, v in cat_cols if len(v) == 2] or cat_cols  # taux : colonne binaire d'abord
        hit = None
        for c, vals in cands:
            vstem = {_stem(w) for v in vals for w in _norm(v).split() if len(w) >= 3}
            if stems & vstem:
                hit = (c, vals)
                break
        if hit is None:
            out.append({"code": ind["code"], "libelle": ind["libelle"], "type": ind["type"],
                        "present": False, "valeur": None, "detail": None})
            continue
        c, vals = hit
        def _negated(v):
            n = _norm(v)
            return n.startswith("non ") or n == "non" or n.startswith("pas ") or n == "pas"
        pos = [v for v in vals if not _negated(v)
               and stems & {_stem(w) for w in _norm(v).split() if len(w) >= 3}]
        neg = [v for v in vals if v not in pos]
        n_pos = int(df[c].astype(str).str.strip().isin(pos).sum())
        n_neg = int(df[c].astype(str).str.strip().isin(neg).sum())
        if is_rate:
            val = round(n_pos / (n_pos + n_neg) * 100, 1) if (n_pos + n_neg) else None
            detail = f"{n_pos} / {n_pos + n_neg}"
        else:
            val = float(n_pos)
            detail = f"comptage {c} = {pos}"
        out.append({"code": ind["code"], "libelle": ind["libelle"], "type": ind["type"],
                    "present": True, "valeur": val, "detail": detail})
    return out
