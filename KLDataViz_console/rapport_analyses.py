# -*- coding: utf-8 -*-
"""Tests statistiques et prévisions pour les rapports : un seul contenu pour le PDF, l'Excel et la page web.

Le texte est la lecture d'Adifa’ si elle existe, celle du moteur sinon : un rapport sort toujours.
"""
from __future__ import annotations

import interpretation as L

ETATS = {"insuffisant": "Insuffisante, à ne pas utiliser", "indicatif": "Indicative, à confirmer", "utilisable": "Utilisable"}
ENTETES_TESTS = ["Ce qui est comparé", "Résultat", "Force", "p corrigée", "Conclusion"]
MAX_LIGNES_PDF = 20


def _p(p) -> str:
    return "—" if p is None else "< 0,001" if p < 0.001 else L.nombre(round(p, 3))


def texte(experte: dict | None, moteur: dict | None) -> dict:
    if experte:
        return {"source": "Lecture d'Adifa’ à partir des résultats calculés", **experte}
    phrases = (moteur or {}).get("phrases") or []
    return {"source": "Lecture écrite par le calcul", "conclusion": phrases[0] if phrases else "", "lecture": phrases[1:], "prudence": [], "a_verifier": []}


def conclusion_test(x: dict, dependantes: bool) -> str:
    if not x.get("significatif"):
        return "Peut venir du hasard"
    if dependantes or x.get("attendus_faibles"):
        return "À vérifier"
    return "Lien très faible" if x.get("force") == "négligeable" else "Lien probable"


def _ligne_comparaison(x: dict) -> list:
    g = sorted(x["groupes"], key=lambda y: -(y.get("moyenne") or 0))
    u = L.unite(x["num"])
    return [f"{L.nom_lisible(x['num'])} selon {L.nom_lisible(x['cat'])}",
            f"{g[0]['label']} : {L.nombre(g[0].get('moyenne'))}{u} ; {g[-1]['label']} : {L.nombre(g[-1].get('moyenne'))}{u}", x.get("force") or "—"]


def tests(inf: dict | None, experte: dict | None = None) -> dict | None:
    if not inf or not (inf.get("n_tests") or inf.get("n_structure")):
        return None
    dep = bool(inf.get("lignes_dependantes"))
    lignes, structure = [], []
    for x in inf.get("correlations") or []:
        ligne = [f"{L.nom_lisible(x['a'])} et {L.nom_lisible(x['b'])}", f"corrélation {L.nombre(x.get('r'))}", x.get("force") or "—"]
        (structure.append(ligne[0]) if x.get("structurel") else lignes.append((x, ligne)))
    for x in inf.get("associations") or []:
        ligne = [f"{L.nom_lisible(x['a'])} et {L.nom_lisible(x['b'])}", f"V de Cramér {L.nombre(x.get('v_cramer'))}", x.get("force") or "—"]
        (structure.append(ligne[0]) if x.get("structurel") else lignes.append((x, ligne)))
    for x in inf.get("comparaisons") or []:
        (structure.append(f"{L.nom_lisible(x['num'])} et {L.nom_lisible(x['cat'])}") if x.get("structurel") else lignes.append((x, _ligne_comparaison(x))))
    lignes.sort(key=lambda t: (not t[0].get("significatif"), t[0].get("p_ajuste") if t[0].get("p_ajuste") is not None else 1))
    return {"titre": "Tests statistiques", **texte(experte, inf.get("lecture") or L.tests(inf)), "n_tests": inf.get("n_tests") or 0,
            "n_significatifs": inf.get("n_significatifs") or 0, "lignes_dependantes": dep, "structure": structure,
            "colonnes": ENTETES_TESTS, "lignes": [ligne + [_p(x.get("p_ajuste")), conclusion_test(x, dep)] for x, ligne in lignes]}


def _prevision(res: dict, cible) -> dict:
    u, val = L.suffixe(res, cible), res.get("validation") or {}
    bas = 0 if all((v or 0) >= 0 for v in (res.get("historique") or {}).get("valeurs", [])) else None
    metriques = [["Fiabilité", ETATS.get(res.get("etat"), "—")], ["Historique", f"{res.get('n')} mois"]]
    if val.get("erreur_modele") is not None:
        metriques.append([f"Erreur sur les {val.get('periodes_testees')} derniers mois connus",
                          f"{L.nombre(val['erreur_modele'])}{u}, contre {L.nombre(val.get('erreur_reference'))}{u} pour une estimation simple"])
    return {"titre": f"Prévision : {L.nom_prevision(res, cible)}", "metriques": metriques, "colonnes": ["Mois", "Prévision", "Bas", "Haut"],
            "lignes": [[L.mois_clair(p["periode"]), f"{L.nombre(p['valeur'])}{u}", f"{L.nombre(max(p['bas'], bas) if bas is not None else p['bas'])}{u}", f"{L.nombre(p['haut'])}{u}"]
                       for p in res.get("previsions") or []]}


def _regression(res: dict, cible) -> dict:
    u = L.unite(cible)
    metriques = [["Fiabilité", ETATS.get(res.get("etat"), "—")], ["Part expliquée sur les cas mis de côté", f"{L.nombre(round((res.get('r2_test') or 0) * 100, 1))} %"],
                 ["Écart habituel entre le calcul et la réalité", f"{L.nombre(res.get('rmse_test'))}{u}"]]
    return {"titre": f"Ce qui va avec : {L.nom_lisible(cible)}", "metriques": metriques, "colonnes": ["Colonne", "Variation", "Variation associée", "p", "Résultat"],
            "lignes": [[L.nom_lisible(c["variable"]), f"+{L.nombre(pas)}{L.unite(c['variable'])}", f"{'+' if effet > 0 else ''}{L.nombre(effet)}{u}", _p(c.get("p")), genre]
                       for genre, c, pas, effet in L.effets(res)]}


def _logistique(res: dict, cible) -> dict:
    mc = res.get("matrice_confusion") or {}
    metriques = [["Fiabilité", ETATS.get(res.get("etat"), "—")], ["Cas « Oui » retrouvés", f"{res.get('vrais_positifs_test')} sur {res.get('positifs_test')}"],
                 ["Fausses alertes", str(mc.get("faux_positifs", "—"))], ["Capacité à distinguer (0,5 = hasard, 1 = parfait)", L.nombre(res.get("auc_test"))]]
    return {"titre": f"Prévision Oui/Non : {L.nom_lisible(cible)}", "metriques": metriques, "colonnes": ["Colonne", "Quand sa valeur monte"],
            "lignes": [[L.nom_lisible(c["variable"]), "le Oui devient plus probable" if (c.get("coef_standardise") or 0) > 0 else "le Oui devient moins probable"]
                       for c in res.get("coefficients") or [] if c["variable"] != "(constante)"]}


def prediction(entree: dict, experte: dict | None = None, dependantes: bool = False) -> dict | None:
    """Une analyse prédictive lancée sur le fichier du rapport."""
    resultat = entree.get("resultat") or {}
    res, cible = resultat.get("resultat") or {}, resultat.get("cible")
    construire = {"prevision": _prevision, "lineaire": _regression, "logistique": _logistique}.get(res.get("type"))
    if not construire or res.get("erreur"):
        return None
    return {**construire(res, cible), **texte(experte, L.predictif(resultat, dependantes))}


def contenu(result: dict) -> dict:
    """Sections du rapport. Un rapport planifié n'a que les tests : personne n'y a lancé de prévision."""
    if result.get("_analyses"):
        return result["_analyses"]
    t = tests(result.get("inference"))
    return {"tests": t, "predictions": []} if t else {}
