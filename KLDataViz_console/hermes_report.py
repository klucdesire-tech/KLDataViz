"""Rapports planifiés commentés : ce qui a changé depuis l'envoi précédent.

À chaque exécution d'une planification :
1. `snapshot` garde les chiffres clés de l'analyse (lignes, complétude, anomalies, moyennes, valeurs de chaque
   graphique) dans un petit fichier JSON par planification ;
2. `compare` calcule, par règles, les écarts avec l'instantané précédent (dernière valeur d'une évolution,
   changement de tête d'un classement, plus fortes hausses et baisses, moyennes qui bougent) ;
3. `comment` demande à Hermes de rédiger un résumé, les évolutions et les alertes à partir de ces SEULS écarts ;
   sans IA (ou si elle échoue), un résumé par règles est produit.
Le commentaire est repris en tête du rapport PDF (`_evolution`) et dans le corps de l'e-mail.
"""
import datetime as dt
import json
import os
import re

SEUIL_PCT = 5.0  # en dessous, une variation est considérée comme stable


def _f(x):
    try:
        x = float(x)
        return round(x, 4) if x == x else None
    except (TypeError, ValueError):
        return None


def snapshot(result):
    charts = []
    for c in (result.get("smart_charts") or [])[:8]:
        if c.get("kind") not in ("trend", "ranking", "share", "report"):
            continue
        charts.append({"titre": c.get("title"), "kind": c.get("kind"), "sous_titre": c.get("subtitle"),
                       "labels": [str(x) for x in (c.get("labels") or [])[:25]],
                       "series": [str(n) for n in (c.get("seriesNames") or [])][:4],
                       "values": [[_f(v) for v in (vals or [])[:25]] for vals in (c.get("values") or [])[:4]]})
    return {"date": dt.datetime.now().isoformat(timespec="seconds"), "fichier": result.get("file"),
            "lignes": result.get("rows"), "colonnes": result.get("columns"),
            "completude_pct": result.get("completeness_pct"),
            "anomalies": sum(a.get("count", 0) for a in result.get("anomalies") or []),
            "moyennes": {k: _f(v.get("mean")) for k, v in list((result.get("numeric_stats") or {}).items())[:20]},
            "graphiques": charts}


def _pct(a, b):
    if a in (None, 0) or b is None:
        return None
    return (b - a) / abs(a) * 100


def compare(prev, cur):
    """Écarts chiffrés entre deux instantanés (liste de constats, chacun avec ses nombres)."""
    out = []
    if not prev:
        return out
    if prev.get("lignes") != cur.get("lignes"):
        out.append({"sujet": "lignes", "avant": prev.get("lignes"), "apres": cur.get("lignes"),
                    "variation_pct": _f(_pct(prev.get("lignes"), cur.get("lignes")))})
    for k in ("completude_pct", "anomalies"):
        if prev.get(k) is not None and cur.get(k) is not None and prev[k] != cur[k]:
            out.append({"sujet": k, "avant": prev[k], "apres": cur[k]})
    for k, v in (cur.get("moyennes") or {}).items():
        p = _pct((prev.get("moyennes") or {}).get(k), v)
        if p is not None and abs(p) >= SEUIL_PCT:
            out.append({"sujet": f"moyenne de {k}", "avant": prev["moyennes"][k], "apres": v, "variation_pct": _f(p)})
    old = {g["titre"]: g for g in prev.get("graphiques") or []}
    for g in cur.get("graphiques") or []:
        o = old.get(g["titre"])
        if not o or not g["values"] or not o["values"]:
            continue
        if g["kind"] == "trend":
            for i, name in enumerate(g["series"] or ["valeur"]):
                if i >= len(g["values"]) or i >= len(o["values"]) or not g["values"][i] or not o["values"][i]:
                    continue
                last, plast = g["values"][i][-1], o["values"][i][-1]
                new_periods = [l for l in g["labels"] if l not in o["labels"]]
                out.append({"sujet": f"{g['titre']} — {name}", "derniere_periode": g["labels"][-1] if g["labels"] else None,
                            "valeur": last, "valeur_envoi_precedent": plast,
                            "derniere_periode_precedente": o["labels"][-1] if o["labels"] else None,
                            "variation_pct": _f(_pct(plast, last)), "nouvelles_periodes": new_periods[:6]})
        else:
            cur_v = dict(zip(g["labels"], g["values"][0]))
            old_v = dict(zip(o["labels"], o["values"][0]))
            moves = []
            for lab, v in cur_v.items():
                p = _pct(old_v.get(lab), v)
                if p is not None and abs(p) >= SEUIL_PCT:
                    moves.append((lab, old_v[lab], v, p))
            moves.sort(key=lambda m: -abs(m[3]))
            item = {"sujet": g["titre"], "tete": g["labels"][0] if g["labels"] else None,
                    "tete_precedente": o["labels"][0] if o["labels"] else None,
                    "plus_fortes_variations": [{"categorie": m[0], "avant": m[1], "apres": m[2], "variation_pct": _f(m[3])}
                                               for m in moves[:5]],
                    "nouvelles_categories": [l for l in g["labels"] if l not in old_v][:5],
                    "disparues": [l for l in o["labels"] if l not in cur_v][:5]}
            if item["plus_fortes_variations"] or item["nouvelles_categories"] or item["disparues"] or item["tete"] != item["tete_precedente"]:
                out.append(item)
    return out


def _nf(v, d=1):
    if v is None:
        return "—"
    s = f"{float(v):,.{d}f}".replace(",", " ").replace(".", ",")
    return s[:-2] if s.endswith(",0") else s


def rule_comment(prev, cur, diffs):
    """Résumé sans IA, à partir des mêmes écarts."""
    if not prev:
        return {"resume": f"Premier envoi : {_nf(cur.get('lignes'), 0)} lignes, complétude {_nf(cur.get('completude_pct'))} %. "
                          "Les prochains envois indiqueront ce qui a changé.", "evolutions": [], "alertes": [], "ia": False}
    evo, alert = [], []
    for d in diffs:
        p = d.get("variation_pct")
        if "valeur" in d:
            sens = "stable" if p is None or abs(p) < SEUIL_PCT else ("en hausse" if p > 0 else "en baisse")
            evo.append(f"{d['sujet']} : {_nf(d['valeur'])} en {d.get('derniere_periode')}, {sens}"
                       + (f" ({'+' if p >= 0 else ''}{_nf(p)} %)" if p is not None else "") + ".")
            if p is not None and p <= -10:
                alert.append(f"Baisse de {_nf(-p)} % : {d['sujet']}.")
        elif "plus_fortes_variations" in d:
            for m in d["plus_fortes_variations"][:3]:
                evo.append(f"{d['sujet']} — {m['categorie']} : {_nf(m['avant'])} → {_nf(m['apres'])} ({'+' if m['variation_pct'] >= 0 else ''}{_nf(m['variation_pct'])} %).")
            if d.get("tete") != d.get("tete_precedente"):
                evo.append(f"{d['sujet']} : {d.get('tete')} passe en tête (avant : {d.get('tete_precedente')}).")
        elif d["sujet"] == "anomalies" and (d["apres"] or 0) > (d["avant"] or 0):
            alert.append(f"Valeurs hors norme : {d['avant']} → {d['apres']}.")
        elif d["sujet"] == "completude_pct" and (d["apres"] or 0) < (d["avant"] or 0):
            alert.append(f"Complétude en baisse : {_nf(d['avant'])} % → {_nf(d['apres'])} %.")
        elif d["sujet"] == "lignes":
            evo.append(f"Lignes : {_nf(d['avant'], 0)} → {_nf(d['apres'], 0)}.")
    resume = (f"{len(evo)} évolution(s) notable(s) depuis l'envoi du {prev.get('date', '')[:10]}"
              + (f", dont {len(alert)} point(s) d'attention." if alert else ".")) if evo or alert else \
        f"Pas d'évolution notable depuis l'envoi du {prev.get('date', '')[:10]}."
    return {"resume": resume, "evolutions": evo[:10], "alertes": alert[:6], "ia": False}


SYSTEM = (
    "Tu es Hermes, analyste de données. Un rapport est envoyé automatiquement à intervalles réguliers. On te "
    "donne les chiffres clés de l'envoi précédent, ceux de cet envoi et la liste des écarts calculés. Rédige "
    "pour un décideur non technique : un résumé de 2 phrases, les évolutions les plus importantes (au plus 6, "
    "une phrase chacune avec ses chiffres) et les alertes (baisse marquée, hausse anormale, complétude ou "
    "valeurs hors norme qui se dégradent ; au plus 4). Utilise UNIQUEMENT les chiffres fournis, arrondis "
    "simplement, au format français. N'invente aucune cause. S'il n'y a pas d'envoi précédent, décris l'état "
    "actuel et précise que les prochains envois montreront les évolutions.\n"
    "Réponds avec un objet JSON valide et rien d'autre :\n"
    '{"resume":"…","evolutions":["…"],"alertes":["…"]}'
)


def comment(prev, cur, diffs, llm=None, parse=None):
    if llm is not None and parse is not None:
        payload = {"envoi_precedent": prev, "cet_envoi": cur, "ecarts": diffs}
        data = parse(llm([{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)[:60000]}], 8000))
        if isinstance(data, dict) and data.get("resume"):
            clean = lambda xs, n: [re.sub(r"\s+", " ", str(x)).strip()[:400] for x in (xs or []) if str(x).strip()][:n]
            return {"resume": str(data["resume"])[:700], "evolutions": clean(data.get("evolutions"), 6),
                    "alertes": clean(data.get("alertes"), 4), "ia": True}
    return rule_comment(prev, cur, diffs)


def run(job_id, result, snap_dir, llm=None, parse=None):
    """Compare à l'instantané précédent de la planification, commente, puis enregistre le nouvel instantané."""
    path = os.path.join(snap_dir, f"{re.sub(r'[^A-Za-z0-9_-]', '', str(job_id))}.json")
    prev = None
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = None
    cur = snapshot(result)
    diffs = compare(prev, cur)
    out = comment(prev, cur, diffs, llm, parse)
    out["depuis"] = (prev or {}).get("date")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False)
    os.replace(tmp, path)
    return out


def email_text(job_name, result, ev):
    lines = ["Bonjour,", "", f"Voici le rapport « {job_name} » ({_nf(result.get('rows'), 0)} lignes, "
             f"complétude {_nf(result.get('completeness_pct'))} %)."]
    if ev:
        lines += ["", ("Ce qu'il faut retenir (Hermes) :" if ev.get("ia") else "Ce qu'il faut retenir :"), ev.get("resume", "")]
        if ev.get("alertes"):
            lines += ["", "Points d'attention :"] + [f"  • {a}" for a in ev["alertes"]]
        if ev.get("evolutions"):
            lines += ["", "Évolutions :"] + [f"  • {e}" for e in ev["evolutions"]]
    lines += ["", "Le rapport complet est en pièce jointe.", "", "Rapport généré automatiquement par KLDataViz."]
    return "\n".join(lines)
