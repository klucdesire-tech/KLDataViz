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


_PERIOD_NAMES = ("quarter", "period", "periode", "periodname", "periodid", "periodcode",
                 "trimestre", "mois", "date", "year", "annee")

_IDLIKE = re.compile(r"(^|[\s_.])(n°|no|num(ero|éro)?|id|code|matricule|ref|référence|reference|labno|pec|datim)($|[\s_.])", re.I)


def _period_col(df):
    """Colonne de période simple : nom exact (« period », « trimestre »…) ou préfixe date/période/année…"""
    for c in df.columns:
        n = _norm(c)
        if n in _PERIOD_NAMES or re.match(r"^(date|periode|annee|mois|trimestre|quarter)\b", n):
            return c
    return None


_MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
         "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "january": 1, "february": 2,
         "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8, "september": 9,
         "october": 10, "november": 11, "december": 12}


def _pkey(v):
    """Clé de tri chronologique d'une période (« 2025-04 », « 2025Q3 », « T4 2025 », « Avril 2025 »…).

    Le tri alphabétique placerait « Avril 2025 » avant « Mars 2025 » : on lit l'année puis le mois."""
    n = _norm(v)
    y = re.search(r"(19|20)\d\d", n)
    year = int(y.group(0)) if y else 0
    rest = n.replace(y.group(0), " ") if y else n
    q = re.search(r"\b(?:t|q|trimestre\s*)([1-4])\b|(?:t|q)([1-4])\b", rest)
    if q:
        return (year, int(q.group(1) or q.group(2)) * 3, n)
    m = next((num for nom, num in _MOIS.items() if nom in rest.split()), None)
    if m is None:
        d = re.search(r"\b(0?[1-9]|1[0-2])\b", rest)
        m = int(d.group(1)) if d else 0
    return (year, m, n)


def _is_snapshot(ind):
    """Un indicateur « instantané » (snapshot) : on prend la valeur de fin de période, on n'additionne pas."""
    t = (ind.get("total") or "").lower()
    return any(m in t for m in ("snapshot", "instantane", "valeur de fin", "ne pas additionner", "sans objet"))




# --------------------------------------------------------------------------
# Matching assisté par LLM (cas ambigus que le matching par mots-clés rate)
# --------------------------------------------------------------------------
MATCH_SYSTEM = """Tu relies un fichier de données à un dictionnaire d'indicateurs métier.
Pour CHAQUE indicateur, détermine s'il est calculable depuis les colonnes du fichier, et comment.
Réponds en JSON STRICT (aucun texte avant ni après) :
{"matches":[{"code":"...","present":true,"colonne":"...","calcul":"taux"|"comptage","positif":"...","negatif":"..."}]}

Règles :
- « Pourcentage »/« taux » : present=true seulement si une colonne catégorielle porte le résultat
  (ex. SUPPRIME / NON-SUPPRIME). « calcul »="taux", « positif »=la valeur positive, « negatif »=l'autre.
- « Nombre » : « calcul »="comptage" ET « positif » = la valeur exacte de la colonne à compter
  (ex. « Perdu de vue »). Jamais de somme d'une colonne numérique d'une liste d'observations.
- Dans le doute (mot seulement voisin, colonne générique), « present »=false : mieux vaut absent que faux.
- Indicateur non calculable (colonnes/valeurs absentes) : « present »=false.
- Ne cite que des colonnes qui existent réellement dans le fichier."""


def _compute_from_mapping(df, mapping, indicators):
    """Calcule les valeurs à partir du mapping renvoyé par le LLM."""
    import pandas as pd
    by_code = {i["code"].strip().lower(): i for i in indicators}
    out = []
    for m in mapping.get("matches", []):
        code = str(m.get("code") or "").strip().lower()
        ind = by_code.get(code)
        if ind is None:
            continue
        base = {"code": ind["code"], "libelle": ind["libelle"], "type": ind["type"]}
        if not m.get("present"):
            out.append(dict(base, present=False, valeur=None, detail=None))
            continue
        col = m.get("colonne")
        if col is None or col not in df.columns:
            out.append(dict(base, present=False, valeur=None, detail=None))
            continue
        calcul = str(m.get("calcul") or "").lower()
        s_col = df[col].astype(str).str.strip()
        present_vals = set(s_col.unique())
        pos, neg = str(m.get("positif") or "").strip(), str(m.get("negatif") or "").strip()
        # Une liste d'observations ne s'additionne pas et ne se compte pas « en entier » : il faut une valeur
        # précise de la colonne (« Perdu de vue », « SUPPRIME »), réellement présente dans les données.
        if pos not in present_vals or pd.api.types.is_numeric_dtype(df[col]):
            out.append(dict(base, present=False, valeur=None, detail=None))
            continue
        n_pos = int((s_col == pos).sum())
        if calcul in ("taux", "pourcentage", "rate", "pct", "proportion"):
            # dénominateur = les seules valeurs renseignées (une cellule vide n'est ni « oui » ni « non »)
            filled = df[col].dropna().astype(str).str.strip()
            filled = filled[~filled.str.lower().isin(("", "nan", "none", "null", "nat"))]
            n_neg = int((filled == neg).sum()) if neg in present_vals else int(filled.ne(pos).sum())
            val = round(n_pos / (n_pos + n_neg) * 100, 1) if (n_pos + n_neg) else None
            out.append(dict(base, present=True, valeur=val, detail=f"{n_pos} / {n_pos + n_neg}"))
        else:
            out.append(dict(base, present=True, valeur=float(n_pos), detail=f"lignes « {pos} » (IA)"))
    return out


def match_and_compute_llm(df, indicators, llm):
    """Matching via le LLM : renvoie la même structure que match_and_compute, ou None en cas d'échec.

    ``llm(messages, max_tokens) -> texte`` est fourni par l'appelant (console / orchestrateur).
    Fichier où les indicateurs sont explicites (format long ou large) : None — le calcul déterministe fait foi,
    le LLM ne sait pas filtrer une colonne « indicateur » (il additionnerait toute la colonne « valeur »)."""
    import json
    import pandas as pd
    if _as_long(df, indicators)[1] is not None:
        return None
    cols = []
    for c in df.columns:
        entry = {"nom": str(c), "type": "numerique" if pd.api.types.is_numeric_dtype(df[c]) else "texte"}
        # Confidentialité : on n'envoie JAMAIS de valeurs brutes (codes patients, dates, résultats).
        # Seulement les étiquettes des colonnes catégorielles (2-8 valeurs : SUPPRIME/NON-SUPPRIME, OUI/NON…).
        if not pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_datetime64_any_dtype(df[c]) \
                and not _IDLIKE.search(_norm(str(c))):  # jamais un identifiant, même à peu de valeurs
            vc_ = df[c].dropna().astype(str).str.strip()
            vc_ = vc_[~vc_.str.lower().isin(("", "nan", "none", "null", "nat"))].value_counts()
            if 2 <= len(vc_) <= 8 and int(vc_.min()) >= 2:
                entry["categorie"] = [str(v) for v in vc_.index]
        cols.append(entry)
    payload = json.dumps({
        "indicateurs": [{k: i.get(k, "") for k in ("code", "libelle", "type", "numerateur", "denominateur")}
                        for i in indicators],
        "colonnes": cols,
    }, ensure_ascii=False)
    try:
        raw = llm([{"role": "system", "content": MATCH_SYSTEM},
                   {"role": "user", "content": payload}], 8000) or ""
    except Exception:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        mapping = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(mapping, dict) or "matches" not in mapping:
        return None
    return _compute_from_mapping(df, mapping, indicators)


def _part(label_norm: str, code_norm: str):
    """Ligne/colonne relevant de l'indicateur `code` ? '' (valeur), 'n' (numérateur), 'd' (dénominateur), ou None.

    Convention DATIM/DHIS2 : « TX_PVLS (N, DSD) », « TX_PVLS_D ». Jamais un autre code qui commence pareil
    (TX_CURR ≠ TX_CURR_NAT)."""
    if label_norm == code_norm:
        return ""
    if label_norm.startswith(code_norm + " "):
        nxt = label_norm[len(code_norm) + 1:].split()[0]
        if nxt in ("n", "d"):
            return nxt
        if nxt in ("dsd", "ta", "total"):
            return ""
    return None


def _as_long(df, indicators):
    """(df, colonne indicateur, colonne valeur) si les indicateurs sont explicites dans le fichier.

    Format long : colonnes « indicateur » + « valeur ». Format large : une colonne numérique par code
    (TX_CURR, TX_PVLS_N…), ramenée au format long en gardant TOUTES les autres colonnes (période numérique
    comprise). Sinon (liste d'observations) : (df, None, None)."""
    import pandas as pd
    codes = [_norm(i["code"]) for i in indicators if i.get("code")]

    def known(label):
        n = _norm(label)
        return any(_part(n, c) is not None for c in codes)

    ic = next((c for c in df.columns if _norm(c) in ("indicator", "indicateur", "indicateurs",
                                                     "code", "libelle", "libellé")), None)
    vc = next((c for c in df.columns if _norm(c) in ("value", "valeur", "val", "montant", "mesure", "total")
               and pd.to_numeric(df[c], errors="coerce").notna().any()), None)
    if ic is not None and vc is not None:
        # Une colonne « code »/« libellé » n'est une colonne d'indicateurs que si elle porte des codes du
        # dictionnaire (sinon : code site ou code patient d'une liste d'observations)
        if _norm(ic) in ("indicator", "indicateur", "indicateurs") or \
                any(known(v) for v in df[ic].dropna().astype(str).unique()[:500]):
            return df, ic, vc
    wide = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and known(c)]
    if wide:
        keep = [c for c in df.columns if c not in wide]
        return df.melt(id_vars=keep, value_vars=wide, var_name="indicateur", value_name="valeur"), "indicateur", "valeur"
    return df, None, None


# Mots de contexte des libellés (« dans le cadre du programme », « 1er test », « contact clinique »…) : présents
# dans les libellés mais trop banals dans les données pour désigner à eux seuls un indicateur.
_CONTEXTE = {_stem(w) for w in (
    "cadre", "programme", "programmes", "contact", "contacts", "test", "tests", "inscription", "inscrit", "stock",
    "laboratoire", "site", "sites", "service", "services", "clinique", "cliniques", "resultat", "resultats", "periode",
    "statut", "personnes", "patients", "nombre", "total", "categorie", "type", "zone", "niveau", "point", "points",
    "prestation", "suivi", "dernier", "derniers", "derniere", "mois", "annee", "trimestre", "semestre", "cours",
    "fin", "debut", "date", "valeur", "pepfar", "appuyes", "appuye", "national", "nationales", "normes", "selon",
    "medicale", "medical", "volontaire", "generale", "population", "populations", "traitement", "enfants", "enfant",
    "adultes", "adulte", "femmes", "femme", "hommes", "homme", "sante", "agents", "dossier", "formation")}


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
    df, ic, vc = _as_long(df, indicators)

    def _kw(ind):
        return _keywords(ind["code"], ind["libelle"])

    def _stems(ind):
        tous, un = _kw(ind)
        return {_stem(w) for w in un if (" " in w) or len(w) >= 4}  # bigramme ou mot >= 4 (exclut vih/hiv/arv)

    out = []
    # --- Format long ---
    if ic is not None and vc is not None:
        labels = df[ic].dropna().astype(str).map(_norm)
        pcol = _period_col(df)
        absent = lambda ind: {"code": ind["code"], "libelle": ind["libelle"], "type": ind["type"],  # noqa: E731
                              "present": False, "valeur": None, "detail": None}
        for ind in indicators:
            parts = labels.map(lambda t, c=_norm(ind["code"]): _part(t, c)).dropna()
            if parts.empty:
                out.append(absent(ind))
                continue
            is_rate = (ind["type"] or "").strip().lower() in ("pourcentage", "proportion", "taux", "%", "rate", "pct")
            snapshot = _is_snapshot(ind)
            sub = df.loc[parts.index].copy()
            sub["__v"] = pd.to_numeric(sub[vc], errors="coerce")
            sub["__part"] = parts
            sub = sub.dropna(subset=["__v"])
            if sub.empty:
                out.append(absent(ind))
                continue
            sub["__p"] = sub[pcol].astype(str) if pcol is not None and sub[pcol].notna().any() else ""
            last = sorted(sub["__p"].unique(), key=_pkey)[-1]
            fin = sub[sub["__p"] == last]
            lib_p = f", {last}" if last else ""
            if is_rate:
                num, den = fin.loc[fin["__part"] == "n", "__v"].sum(), fin.loc[fin["__part"] == "d", "__v"].sum()
                if (fin["__part"] == "n").any() and den > 0:
                    # taux global exact : Σ numérateurs / Σ dénominateurs (pondéré par la taille des sites)
                    val, detail = round(float(num / den * 100), 1), f"{float(num):g} / {float(den):g}{lib_p}"
                else:
                    v = fin.loc[fin["__part"] == "", "__v"]
                    if v.empty:
                        out.append(absent(ind))
                        continue
                    val = round(float(v.mean()), 1)
                    detail = (f"moyenne simple de {len(v)} valeurs{lib_p} (sans numérateur/dénominateur)"
                              if len(v) > 1 else f"valeur{lib_p}")
            else:
                cnt = sub[sub["__part"] != "d"]  # un comptage = la valeur (ou le numérateur), jamais le dénominateur
                if snapshot:
                    v = cnt.loc[cnt["__p"] == last, "__v"]
                    val, detail = float(v.sum()), f"valeur de fin de période{lib_p}"
                else:
                    n_per = cnt["__p"].nunique()
                    val = float(cnt["__v"].sum())
                    detail = f"somme de {n_per} périodes" if last else f"somme de « {vc} »"
            out.append({"code": ind["code"], "libelle": ind["libelle"], "type": ind["type"],
                        "present": True, "valeur": val, "detail": detail})
        return out

    # --- Format liste (une ligne par observation) ---
    cat_cols, yes_no = [], []
    _OUI, _NON = {"oui", "o", "yes", "y", "true", "vrai", "1", "1 0"}, {"non", "n", "no", "false", "faux", "0", "0 0"}
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            continue
        uniq = {_norm(v) for v in df[c].dropna().astype(str).unique()} - {""}
        if uniq and uniq <= (_OUI | _NON) and uniq & _OUI and uniq & _NON:
            # Colonne Oui/Non (ou 1/0) : c'est son NOM qui dit ce qu'elle mesure (« cv_supprimee »)
            yes_no.append((c, [v for v in df[c].dropna().astype(str).str.strip().unique() if _norm(v) in _OUI]))
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            continue
        vals = [str(v).strip() for v in df[c].dropna().unique()
                if str(v).strip().lower() not in ("", "nan", "none", "null", "nat")]
        if 2 <= len(vals) <= 8:
            cat_cols.append((c, vals))

    # Une valeur ne désigne un indicateur que si TOUS ses mots significatifs appartiennent au libellé de
    # l'indicateur ET qu'au moins un de ces mots est distinctif (présent dans 2 indicateurs du dictionnaire au plus).
    # « SUPPRIMÉ » → TX_PVLS ; mais « Arrêt de traitement », « Population générale », « Consultation médicale » ou
    # « Enfant » ne désignent aucun indicateur (mots génériques ou étrangers au libellé) : dans le doute, absent.
    from collections import Counter
    stems_by = {id(i): _stems(i) for i in indicators}
    freq = Counter(st for sts in stems_by.values() for st in sts)

    def _negated(v):
        n = _norm(v)
        return n.startswith("non ") or n == "non" or n.startswith("pas ") or n == "pas"

    def _designates(v, stems):
        words = {_stem(w) for w in _norm(v).split() if len(w) >= 3 and w not in _STOP}
        # au moins un mot PORTEUR : distinctif (2 indicateurs au plus) et pas un mot de contexte du libellé
        return bool(words) and words <= stems and any(freq[w] <= 2 and w not in _CONTEXTE for w in words)

    for ind in indicators:
        stems = stems_by[id(ind)]
        is_rate = (ind["type"] or "").strip().lower() in ("pourcentage", "proportion", "taux", "%", "rate", "pct")
        cands = [(c, v) for c, v in cat_cols if len(v) == 2] if is_rate else cat_cols  # un taux : colonne binaire
        hit = None
        for c, vals in cands:
            pos = [v for v in vals if not _negated(v) and _designates(v, stems)]
            if pos:
                hit = (c, vals, pos)
                break
        if hit is None:
            for c, oui in yes_no:
                if _designates(str(c), stems):
                    hit = (c, [v for v in df[c].dropna().astype(str).str.strip().unique()], oui)
                    break
        if hit is None:
            out.append({"code": ind["code"], "libelle": ind["libelle"], "type": ind["type"],
                        "present": False, "valeur": None, "detail": None})
            continue
        c, vals, pos = hit
        neg = [v for v in vals if v not in pos]
        n_pos = int(df[c].astype(str).str.strip().isin(pos).sum())
        n_neg = int(df[c].astype(str).str.strip().isin(neg).sum())
        if is_rate:
            val = round(n_pos / (n_pos + n_neg) * 100, 1) if (n_pos + n_neg) else None
            detail = f"{n_pos} / {n_pos + n_neg}"
        else:
            val = float(n_pos)
            detail = f"lignes « {', '.join(pos)} »"
        out.append({"code": ind["code"], "libelle": ind["libelle"], "type": ind["type"],
                    "present": True, "valeur": val, "detail": detail})
    return out
