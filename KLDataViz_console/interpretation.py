# -*- coding: utf-8 -*-
"""Lecture en clair des résultats prédictifs et des tests statistiques.

Les phrases sont écrites ici, sans l'IA : chaque chiffre vient du calcul, et le ton suit la fiabilité du
modèle. Un modèle jugé insuffisant n'annonce ni hausse ni baisse. C'est aussi le socle de la lecture d'Adifa’
(`lecture_experte.py`) et ce que la page garde si celle-ci n'arrive pas.
"""
from __future__ import annotations

import math
import re

SEUIL_TENDANCE = 0.10       # p de la pente : au-delà, le mouvement peut venir du hasard
VARIATION_MIN = 0.02        # moins de 2 % sur l'horizon : niveau stable
P_COEF = 0.05
EFFET_MIN = 0.05            # effet habituel inférieur à 5 % de l'écart-type de la cible : négligeable
ODDS_MIN = 0.2              # coefficient standardisé en dessous duquel on ne cite pas la variable
MAX_PHRASES_TESTS = 5
ORDRE_FORCE = {"forte": 0, "modérée": 1, "faible": 2}
LIEN = {"forte": "fort", "modérée": "modéré", "faible": "faible"}
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")
UNITES = {"fcfa": "FCFA", "xof": "FCFA", "eur": "€", "usd": "$", "pct": "%", "ha": "ha", "kg": "kg", "t": "t"}


def nom_lisible(colonne) -> str:
    """« chiffre_affaires_fcfa » → « chiffre d'affaires »."""
    s = re.sub(r"_(%s)$" % "|".join(UNITES), "", str(colonne), flags=re.I).replace("_", " ").strip()
    return re.sub(r"\bchiffre affaires\b", "chiffre d'affaires", s, flags=re.I) or str(colonne)


def unite(colonne) -> str:
    m = re.search(r"_(%s)$" % "|".join(UNITES), str(colonne), flags=re.I)
    return " " + UNITES[m.group(1).lower()] if m else ""


def _de(nom: str) -> str:
    return ("d'" if nom[:1].lower() in "aàâeéèêëiîïoôuûyh" else "de ") + nom


def _virgule(v: float, decimales: int) -> str:
    s = f"{v:.{decimales}f}"
    return (s.rstrip("0").rstrip(".") if "." in s else s).replace(".", ",")


def nombre(v) -> str:
    """Nombre arrondi pour une phrase : 5 482 792 → « 5,5 millions »."""
    if v is None:
        return "—"
    a, signe = abs(float(v)), "−" if float(v) < 0 else ""
    if a >= 1e9:
        x = a / 1e9
        return f"{signe}{_virgule(x, 1)} milliard{'s' if x >= 2 else ''}"
    if a >= 1e6:
        x = a / 1e6
        return f"{signe}{_virgule(x, 1)} million{'s' if x >= 2 else ''}"
    if a >= 1e4:
        return signe + f"{round(a, 2 - int(math.floor(math.log10(a)))):,.0f}".replace(",", " ")
    if a >= 100:
        return signe + f"{round(a):,.0f}".replace(",", " ")
    if a >= 0.01 or a == 0:
        return signe + _virgule(a, 2)
    return signe + _virgule(a, 1 - int(math.floor(math.log10(a))))


def mois_clair(periode: str) -> str:
    try:
        return f"{MOIS[int(str(periode)[5:7]) - 1]} {str(periode)[:4]}"
    except (ValueError, IndexError):
        return str(periode)


def nom_prevision(res: dict, cible) -> str:
    nom, u = nom_lisible(cible), res.get("unite") or ""
    if "%" in u:
        return f"part de Oui pour {nom}"
    return f"{u} pour {nom}" if u else nom


def suffixe(res: dict, cible) -> str:
    u = res.get("unite") or ""
    return " %" if "%" in u else "" if u else unite(cible)


def niveaux(res: dict) -> dict | None:
    """Niveau récent, niveau prévu et sens de la tendance. Un sens n'est retenu que si la pente est nette."""
    prev = res.get("previsions") or []
    hist = [v for v in (res.get("historique") or {}).get("valeurs", []) if v is not None]
    if not prev or not hist:
        return None
    h = len(prev)
    niveau, prevu = sum(hist[-h:]) / len(hist[-h:]), sum(p["valeur"] for p in prev) / h
    variation = (prevu - niveau) / abs(niveau) if niveau else 0.0
    p_pente, pente = res.get("tendance_p"), res.get("tendance_pente") or 0
    net = p_pente is not None and p_pente < SEUIL_TENDANCE and abs(variation) >= VARIATION_MIN and pente * variation > 0
    return {"horizon": h, "niveau": niveau, "prevu": prevu, "variation": variation, "fin": prev[-1],
            "bas": max(prev[-1]["bas"], 0) if min(hist) >= 0 else prev[-1]["bas"],
            "sens": ("baisse" if variation < 0 else "hausse") if net else None}


def prevision(res: dict, cible) -> dict:
    n = niveaux(res)
    if not n:
        return {"phrases": []}
    h, nom, etat, val = n["horizon"], nom_prevision(res, cible), res.get("etat"), res.get("validation")
    unite_txt = suffixe(res, cible)
    par_mois = " par mois" if res.get("agregation") == "somme" else ""
    teste = bool(val and val.get("erreur_modele") is not None)
    if etat == "insuffisant":
        return {"phrases": [
            f"Ces données ne permettent pas d'annoncer une hausse ou une baisse {_de(nom)}.",
            (f"Sur les {val['periodes_testees']} derniers mois connus, ce calcul n'a pas fait mieux qu'une estimation simple."
             if teste else "L'historique est trop court pour vérifier ce calcul sur le passé.")]}
    if n["sens"]:
        attente = "est attendue" if etat == "utilisable" else "est possible"
        phrases = [f"Si la tendance se poursuit, une {n['sens']} {_de(nom)} {attente} d'ici {h} mois : environ "
                   f"{'+' if n['variation'] > 0 else '−'}{_virgule(abs(n['variation']) * 100, 0)} %, "
                   f"de {nombre(n['niveau'])} à {nombre(n['prevu'])}{unite_txt}{par_mois} en moyenne."]
    else:
        phrases = [f"Aucune hausse ni baisse nette {_de(nom)} n'est attendue d'ici {h} mois : "
                   f"le niveau reste proche de {nombre(n['niveau'])}{unite_txt}{par_mois}."]
    phrases.append(f"Pour {mois_clair(n['fin']['periode'])}, le calcul donne environ {nombre(n['fin']['valeur'])}{unite_txt}, "
                   f"entre {nombre(n['bas'])} et {nombre(n['fin']['haut'])}{unite_txt}.")
    if etat == "utilisable":
        phrases.append(f"Sur les {val['periodes_testees']} derniers mois connus, ce calcul a fait nettement mieux qu'une estimation simple.")
    elif teste:
        phrases.append(f"À prendre comme une indication : sur les {val['periodes_testees']} derniers mois connus, "
                       "ce calcul fait mieux qu'une estimation simple, sans grand écart.")
    else:
        phrases.append("À prendre comme une indication : l'historique est trop court pour vérifier ce calcul sur le passé.")
    phrases.append("Cette prévision prolonge le passé : elle ne tient pas compte d'un changement à venir.")
    return {"phrases": phrases}


def _pas(ecart_type: float) -> float:
    """Variation ronde et réaliste d'une colonne : 87 → 100, 0,3 → 0,1."""
    return 10.0 ** round(math.log10(ecart_type))


def effets(res: dict) -> list:
    """(« net », « net mais faible » ou « non net », coefficient, variation ronde de la colonne, variation associée), du plus fort au plus faible."""
    ecart_y, out = res.get("ecart_type_cible") or 0, []
    for c in res.get("coefficients") or []:
        sd = c.get("ecart_type")
        if c["variable"] == "(constante)" or not sd or c.get("coef") is None or c.get("p") is None:
            continue
        genre = "non net" if c["p"] >= P_COEF else "net" if abs(c["coef"] * sd) >= EFFET_MIN * ecart_y else "net mais faible"
        pas = _pas(sd)
        out.append((genre, c, pas, c["coef"] * pas))
    return sorted(out, key=lambda x: -abs(x[1]["coef"] * x[1]["ecart_type"]))


def regression(res: dict, cible, lignes_dependantes: bool = False) -> dict:
    nom, r2, etat = nom_lisible(cible), res.get("r2_test"), res.get("etat")
    if etat == "insuffisant":
        return {"phrases": [
            f"Les colonnes utilisées expliquent mal « {nom} » : ne vous appuyez pas sur ce modèle pour décider."
            + (" Il prédit moins bien qu'une simple moyenne." if r2 is not None and r2 < 0 else "")]}
    phrases = [f"Les colonnes utilisées expliquent environ {_virgule(r2 * 100, 0)} % des variations {_de(nom)}."
               + ("" if etat == "utilisable" else " C'est une indication à confirmer.")]
    if res.get("rmse_test") is not None:
        phrases.append(f"Sur les cas mis de côté, l'écart habituel entre le calcul et la réalité est d'environ {nombre(res['rmse_test'])}{unite(cible)}.")
    liste = effets(res)
    for _, c, pas, effet in [e for e in liste if e[0] == "net"][:3]:
        v = c["variable"]
        phrases.append(f"+{nombre(pas)}{unite(v)} sur « {nom_lisible(v)} » va avec environ {'+' if effet > 0 else ''}{nombre(effet)}"
                       f"{unite(cible)} sur « {nom} », les autres colonnes restant égales.")
    for genre, debut in (("net mais faible", "Effet net mais faible de"), ("non net", "Aucun effet net de")):
        noms = ", ".join(f"« {nom_lisible(e[1]['variable'])} »" for e in liste if e[0] == genre)
        if noms:
            phrases.append(f"{debut} {noms} une fois les autres colonnes prises en compte.")
    if lignes_dependantes:
        phrases.append("Ce fichier semble compter une ligne par groupe ou par période : ces chiffres sont à vérifier sur les données détaillées.")
    phrases.append("Un lien n'est pas une cause : ces chiffres disent ce qui va ensemble, pas ce qui provoque quoi.")
    return {"phrases": phrases}


def logistique(res: dict, cible) -> dict:
    nom, positifs, vrais = nom_lisible(cible), res.get("positifs_test"), res.get("vrais_positifs_test")
    if res.get("etat") == "insuffisant" or not vrais:
        return {"phrases": [
            f"Ce modèle ne permet pas de prévoir « {nom} » : ne vous appuyez pas sur lui pour décider."]}
    fausses = (res.get("matrice_confusion") or {}).get("faux_positifs", 0)
    phrases = [f"Sur {res.get('n_test')} cas mis de côté, le modèle a retrouvé {vrais} cas « Oui » de « {nom} » sur {positifs}, "
               f"avec {fausses} fausse{'s' if fausses > 1 else ''} alerte{'s' if fausses > 1 else ''}."
               + ("" if res.get("etat") == "utilisable" else " C'est une indication à confirmer.")]
    cites = [c for c in res.get("coefficients") or [] if c["variable"] != "(constante)" and abs(c.get("coef_standardise") or 0) >= ODDS_MIN]
    for c in sorted(cites, key=lambda x: -abs(x["coef_standardise"]))[:3]:
        phrases.append(f"Plus la valeur de « {nom_lisible(c['variable'])} » est haute, "
                       f"{'plus' if c['coef_standardise'] > 0 else 'moins'} le « Oui » est probable.")
    phrases.append("Un lien n'est pas une cause : ces chiffres disent ce qui va ensemble, pas ce qui provoque quoi.")
    return {"phrases": phrases}


def predictif(resultat: dict, lignes_dependantes: bool = False) -> dict:
    """Lecture d'un résultat de `predictive.analyse`."""
    res, cible = resultat.get("resultat") or {}, resultat.get("cible")
    if res.get("erreur"):
        return {"phrases": []}
    if res.get("type") == "prevision":
        return prevision(res, cible)
    if res.get("type") == "logistique":
        return logistique(res, cible)
    if res.get("type") == "lineaire":
        return regression(res, cible, lignes_dependantes)
    return {"phrases": []}


def _force(x) -> int:
    return ORDRE_FORCE.get(x.get("force"), 3)


def _phrase_comparaison(x) -> str:
    g = sorted(x["groupes"], key=lambda y: -(y.get("moyenne") or 0))
    hi, lo, u = g[0], g[-1], unite(x["num"])
    phrase = (f"« {nom_lisible(x['num'])} » change selon « {nom_lisible(x['cat'])} » : en moyenne, {hi['label']} est le plus haut "
              f"({nombre(hi['moyenne'])}{u}) et {lo['label']} le plus bas ({nombre(lo['moyenne'])}{u})")
    if (lo.get("moyenne") or 0) > 0 and hi["moyenne"] / lo["moyenne"] >= 1.5:
        phrase += f", soit {_virgule(hi['moyenne'] / lo['moyenne'], 1)} fois plus"
    return phrase + "."


def _phrase_correlation(x) -> str:
    a, b = nom_lisible(x["a"]), nom_lisible(x["b"])
    if (x.get("r") or 0) > 0:
        return f"« {a} » et « {b} » montent et descendent ensemble (lien {LIEN[x['force']]})."
    return f"Quand « {a} » monte, « {b} » a tendance à baisser (lien {LIEN[x['force']]})."


def _phrase_association(x) -> str:
    return f"« {nom_lisible(x['a'])} » et « {nom_lisible(x['b'])} » sont liés : la répartition de l'un change selon l'autre (lien {LIEN[x['force']]})."


def tests(inf: dict) -> dict:
    """Lecture d'un résultat de `inferential.analyse` : les résultats les plus nets, en phrases."""
    familles = (("comparaisons", _phrase_comparaison), ("correlations", _phrase_correlation), ("associations", _phrase_association))
    retenus = [(x, fn) for cle, fn in familles for x in inf.get(cle) or []
               if x.get("significatif") and not x.get("attendus_faibles") and _force(x) < 3]
    retenus.sort(key=lambda t: _force(t[0]))
    phrases = [fn(x) for x, fn in retenus[:MAX_PHRASES_TESTS]]
    if not inf.get("n_significatifs") and inf.get("n_tests"):
        phrases.append("Aucun lien net dans ce fichier : les écarts observés peuvent venir du hasard.")
    elif len(retenus) > MAX_PHRASES_TESTS:
        reste = len(retenus) - MAX_PHRASES_TESTS
        phrases.append(f"{reste} autre{'s' if reste > 1 else ''} résultat{'s' if reste > 1 else ''} dans les tableaux ci-dessous.")
    return {"phrases": phrases}
