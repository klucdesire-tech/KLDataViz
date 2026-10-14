# -*- coding: utf-8 -*-
"""Agent ARCHITECTE DE DONNÉES : comprend un fichier et propose la meilleure structure d'analyse.

Le moteur déterministe (profilage + restructuration) vit dans data_architect.py, copie
identique du script du skill Claude « architecte-donnees ». Ce module ajoute la couche IA :
le LLM lit le profil, explique le fichier, liste les problèmes et écrit un spec de
restructuration, qui est vérifié en l'exécutant avant d'être proposé.
"""
import json
import os
import re
import time

import pandas as pd

import data_architect as A

MAX_PROMPT_CHARS = 30_000

SYSTEM = """Tu es Hermes, architecte de données senior (santé publique/DHIS2, finance, RH, ventes, enquêtes).
On te donne le PROFIL MESURÉ d'un fichier (statistiques calculées, pas des suppositions).
Ta mission : comprendre le fichier, diagnostiquer ses problèmes et proposer la meilleure structure pour l'analyse.

Règles :
- Appuie chaque problème sur un chiffre présent dans le profil. N'invente rien ; une hypothèse se dit « probable ».
- Dans les comptages, vide ≠ zéro : ne recommande jamais d'imputer des comptages par moyenne/médiane.
- Structure cible par défaut : table de faits au format long (une ligne = grain le plus fin, une colonne valeur)
  + tables de dimensions. Retire les lignes agrégées (totaux, niveaux hiérarchiques) de la table de faits :
  le profil les liste dans lignes.lignes_agregat_probables ; retire-les avec drop_rows sauf si tu as une
  raison chiffrée de penser le contraire, et cite-les dans les problèmes.
- Si le fichier est déjà au bon format, dis-le et propose seulement types, clés et dimensions.
- Français simple, pour un responsable non technique.

Le spec est exécuté tel quel par un moteur pandas. Clés possibles (toutes optionnelles) :
{"id_columns":[colonnes identifiant une ligne source],
 "drop_columns":[colonnes inutiles],
 "drop_rows":{"column":"<colonne>","values":["<valeurs exactes des lignes à retirer>"]},
 "derive":{"<nouvelle_col>":{"from":"<colonne texte>","regex":"<regex Python avec UN groupe capturant>"}},
 "melt":{"regex":"<regex Python avec groupes NOMMÉS (?P<nom>...) appliquée aux en-têtes à dépivoter>","value_name":"valeur","dropna":true}
   ou {"auto":true} pour laisser le moteur détecter période/âge/sexe dans les en-têtes,
 "recode":{"<colonne>":{"<ancienne valeur>":"<nouvelle>"}},
 "value_rules":{"non_integer":"flag"},
 "fact_name":"fait_<sujet>",
 "dimensions":{"dim_<entite>":{"key":"<clé>","columns":["<attributs>"]}}}
Les noms de colonnes doivent être EXACTEMENT ceux du profil.

Réponds avec un objet JSON valide et rien d'autre :
{"comprehension":{"source":"…","grain":"une ligne = …","dimensions":["…"],"mesures":["…"],"resume":"2 phrases"},
 "problemes":[{"titre":"…","preuve":"chiffre du profil","consequence":"…","severite":"bad|warn|info"}],
 "structure":[{"table":"…","role":"faits|dimension","colonnes":["…"],"description":"…"}],
 "analyses":["analyse ou indicateur concret rendu possible"],
 "recommandation":"la priorité n°1 en une phrase",
 "spec":{…}}"""


def _compact_profile(prof: dict, df: pd.DataFrame) -> dict:
    """Profil réduit pour tenir dans le prompt : les colonnes répétitives sont résumées."""
    cols = prof["colonnes"]
    wide = set(_wide_columns(df)) if prof.get("format_large") else set()
    keep, wide_summary = {}, {}
    for name, p in cols.items():
        if name in wide:  # colonnes répétitives : une ligne courte chacune suffit
            wide_summary[name] = ("VIDE" if p["pct_vide"] == 100 else
                                  f"min {p.get('min')} max {p.get('max')} distinct {p['nb_distinct']}"
                                  + (" | " + "; ".join(a.split(":")[0] for a in p["alerte"]) if p.get("alerte") else ""))
            continue
        slim = {k: v for k, v in p.items() if k in (
            "type_pandas", "pct_vide", "nb_distinct", "min", "max", "mediane", "pct_zero", "pct_non_entier",
            "valeur_la_plus_frequente", "exemples", "prefixes_frequents", "semble_date", "alerte", "identifiant_potentiel")}
        keep[name] = slim
    out = {k: prof[k] for k in ("source", "forme", "cellules_vides_pct", "colonne_libelle_probable", "colonnes_vides",
                                "identifiants_potentiels", "format_large", "lignes", "synthese_alertes") if k in prof}
    if prof.get("alerte_majeure"):
        out["alerte_majeure"] = prof["alerte_majeure"]
    out["colonnes_constantes"] = prof.get("colonnes_constantes", [])
    out["apercu"] = json.loads(df[[c for c in df.columns if c not in wide][:15]].head(3)
                               .to_json(orient="records", force_ascii=False))
    out["colonnes"] = keep
    if wide_summary:
        out["colonnes_format_large"] = wide_summary
    text = json.dumps(out, ensure_ascii=False, default=str)
    if len(text) > MAX_PROMPT_CHARS:  # trop de colonnes : on ne garde le détail que des colonnes en alerte
        out["colonnes"] = {n: p for n, p in keep.items() if p.get("alerte") or p.get("type_pandas") in ("str", "object")}
        out["noms_de_toutes_les_colonnes"] = list(cols)
        out["apercu"] = out["apercu"][:1]
    return out


def _parse_json(content):
    if not content:
        return None
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", content.strip())
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except Exception:
            return None


def _try_spec(df, spec):
    """Exécute le spec pour de vrai : renvoie (tables, None) ou (None, message d'erreur)."""
    if not isinstance(spec, dict) or not spec:
        return None, "spec vide"
    try:
        tables = A.restructure(df, spec)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    fact = next(iter(tables.values()))
    if fact.empty:
        return None, "la table de faits est vide"
    return tables, None


def _fallback(prof, df):
    """Proposition par règles quand l'IA est indisponible."""
    problemes = []
    if prof.get("alerte_majeure"):
        problemes.append({"titre": "Colonnes entièrement imputées", "preuve": prof["alerte_majeure"],
                          "consequence": "Ces colonnes ne contiennent aucune information réelle.", "severite": "bad"})
    aggregates = [a["libelle"] for a in prof["lignes"].get("lignes_agregat_probables", [])]
    if aggregates:
        problemes.append({"titre": "Lignes de zones ou de totaux mélangées aux établissements",
                          "preuve": ", ".join(aggregates[:10]),
                          "consequence": "Double comptage si on additionne tout : retirées de la table de faits.",
                          "severite": "bad"})
    for g in (prof["lignes"].get("groupes_de_lignes_aux_valeurs_identiques") or {}).get("groupes", []):
        if set(g) <= set(aggregates):
            continue
        problemes.append({"titre": "Lignes aux valeurs identiques", "preuve": ", ".join(map(str, g[:6])),
                          "consequence": "Agrégats ou lignes inventées : risque de double comptage.", "severite": "warn"})
    labels = {"mélange entiers / x,5": "Valeurs décimales dans des comptages (imputation probable)",
              "aucun zéro alors que les valeurs sont petites": "Aucun zéro dans des petits comptages",
              "colonne entièrement vide": "Colonnes entièrement vides",
              "code stocké comme nombre décimal": "Code stocké comme nombre"}
    for key, n in prof.get("synthese_alertes", {}).items():
        if key in labels:
            problemes.append({"titre": labels[key], "preuve": f"{n} colonne(s) concernée(s)",
                              "consequence": "À vérifier avant toute analyse.", "severite": "warn"})
    wide = prof.get("format_large")
    spec = None
    if wide:
        ids = [c for c in df.columns if c not in _wide_columns(df)]
        spec = {"id_columns": ids, "melt": {"auto": True}, "value_rules": {"non_integer": "flag"}, "fact_name": "fait"}
        if aggregates and prof.get("colonne_libelle_probable"):
            spec["drop_rows"] = {"column": prof["colonne_libelle_probable"], "values": aggregates}
    return {
        "comprehension": {
            "source": prof["source"].get("fichier", ""),
            "grain": f"une ligne = une valeur de « {prof.get('colonne_libelle_probable') or 'la première colonne'} »",
            "dimensions": list((wide or {}).get("dimensions_detectees", {}).keys()),
            "mesures": (wide or {}).get("indicateurs", [])[:12],
            "resume": (f"{prof['forme']['lignes']} lignes × {prof['forme']['colonnes']} colonnes. "
                       + (f"{wide['colonnes_concernees']} colonnes codent des dimensions dans leur en-tête (format large)."
                          if wide else "Le fichier semble déjà au format long.")),
        },
        "problemes": problemes,
        "structure": ([{"table": "fait", "role": "faits",
                        "colonnes": ids + ["indicateur"] + list(wide["dimensions_detectees"]) + ["valeur"],
                        "description": "Une ligne par entité × indicateur × dimension."}] if wide else []),
        "analyses": [],
        "recommandation": ("Dépivoter les colonnes au format long pour pouvoir filtrer et grouper."
                           if wide else "Vérifier les types et les clés avant l'analyse."),
        "spec": spec,
    }


def _wide_columns(df):
    return [c for c in df.columns if A._strip_dimensions(c)[1]]


def _preview(tables, n=5):
    out = {}
    for name, t in tables.items():
        if name.startswith("_"):
            continue
        out[name] = {"lignes": int(len(t)), "colonnes": [str(c) for c in t.columns],
                     "exemple": json.loads(t.head(n).to_json(orient="values", force_ascii=False, default_handler=str))}
    return out


def propose(df: pd.DataFrame, info: dict, llm_call) -> dict:
    prof = A.profile(df, info)
    proposal, ia, spec_error = None, False, None
    content = llm_call([{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": json.dumps(_compact_profile(prof, df), ensure_ascii=False, default=str)}],
                       max_tokens=32000) if llm_call else None
    data = _parse_json(content)
    if isinstance(data, dict) and data.get("comprehension"):
        proposal, ia = data, True
    else:
        proposal = _fallback(prof, df)
    tables, spec_error = _try_spec(df, proposal.get("spec"))
    if tables is None and ia:  # spec de l'IA inexécutable : on garde l'explication, on bascule sur le spec par règles
        fb = _fallback(prof, df)
        tables, _ = _try_spec(df, fb.get("spec"))
        if tables is not None:
            proposal["spec"] = fb["spec"]
    if tables is None:
        proposal["spec"] = None
    return {
        "ia": ia,
        "proposition": proposal,
        "spec_error": spec_error,
        "apercu": _preview(tables) if tables is not None else None,
        "profil": {k: prof.get(k) for k in ("forme", "cellules_vides_pct", "alerte_majeure", "colonnes_vides",
                                             "colonnes_constantes", "format_large", "lignes", "synthese_alertes")},
    }


def apply(df: pd.DataFrame, spec: dict, out_dir: str, source_name: str) -> dict:
    tables = A.restructure(df, spec)
    stem = re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(source_name))[0])[:40] or "donnees"
    fn = f"modele_{stem}_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
    with pd.ExcelWriter(os.path.join(out_dir, fn)) as xw:
        for name, t in tables.items():
            t.to_excel(xw, sheet_name=name[:31], index=False)
    return {"file": fn, "tables": {n: int(len(t)) for n, t in tables.items() if not n.startswith("_")}}
