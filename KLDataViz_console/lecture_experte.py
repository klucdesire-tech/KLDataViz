# -*- coding: utf-8 -*-
"""Lecture d'experte par Adifa’ : relier les résultats, dire ce qu'on peut conclure et ce qu'on ne peut pas conclure.

Adifa’ raisonne mieux qu'un texte à trous : elle repère une colonne dérivée, une contradiction, un résultat net mais
sans portée. Elle peut aussi arrondir, supposer ou affirmer une cause. Elle ne reçoit donc que des résultats calculés,
jamais une ligne du fichier, et sa réponse est contrôlée ici. Une phrase qui contient un chiffre absent du calcul, une
cause affirmée ou une supposition présentée comme un fait est retirée. Si la conclusion tombe, la page garde la lecture du moteur.
"""
from __future__ import annotations

import json
import re

import interpretation as L

MAX_JETONS = 8000
MAX_LECTURE, MAX_PRUDENCE, MAX_ACTIONS = 3, 2, 2
MAX_CARACTERES = 420
TOLERANCE = 0.006           # 1 274 peut s'écrire 1 270 ; pas 1 300
USUELS = {80.0, 95.0, 100.0}

SYSTEM = """Tu es Adifa’, statisticienne senior. Tu expliques à un responsable non statisticien des résultats déjà calculés.
Tu raisonnes comme une experte : tu relies les résultats entre eux, tu dis ce qu'on peut conclure et ce qu'on ne peut pas conclure.

Règles :
- Tu reprends les chiffres tels qu'ils sont écrits dans les faits. Tu n'en calcules aucun autre, même un pourcentage ou un rapport.
- La fiabilité commande le ton. « insuffisant » : tu n'annonces ni hausse ni baisse. « indicatif » : possible, à confirmer. « utilisable » : attendu, jamais certain.
- Tu n'affirmes jamais une cause. Une explication possible commence par « hypothèse à vérifier ».
- Tu n'inventes aucun fait sur l'entreprise, le marché, la saison ou le pays.
- La façon dont une colonne est construite est une supposition : écris « semble calculée à partir de », jamais « est calculée ».
- Tu signales les pièges visibles : colonnes qui semblent se déduire l'une de l'autre, sens de lecture douteux (un effet ne va pas forcément dans le sens du modèle),
  résultat net mais faible, résultat non significatif qui ne prouve pas l'absence de lien.
- Pour une prévision, les chiffres portent sur des totaux ou moyennes par mois : ne parle pas d'indépendance des lignes.
- Pour des tests, seules les colonnes absentes de « colonnes_testees » n'ont pas été testées.
- Une valeur p n'est jamais la probabilité que ce soit le hasard. Une p faible veut dire qu'un tel écart serait rare si rien ne se passait réellement.
- Tu n'écris ni « levier » ni « facteur » : ces mots supposent qu'on peut agir sur la colonne.
- Tu n'écris ni nom de champ technique, ni formule. Tu expliques tout terme de statistique.
- Chaque phrase fait moins de 45 mots. Tu entres directement dans le sujet, sans annoncer « le message principal ».

Réponds uniquement en JSON :
{"conclusion": "une ou deux phrases : le message principal",
 "lecture": ["au plus 3 phrases qui relient les résultats"],
 "prudence": ["au plus 2 limites concrètes"],
 "a_verifier": ["au plus 2 vérifications ou actions concrètes"]}"""

CAUSE = re.compile(r"\bà cause d|\bgrâce à|\bprovoqu|\bentraîn|\bdue? à\b|\bs'explique par\b|\bs’explique par\b|\blevier|\bfacteur", re.I)
CERTITUDE = re.compile(r"\b(est|sont|sera|seront)\s+(\w+\s+)?(certaine?s?|sûre?s?|acquise?s?)\b|\bcertainement\b|\bà coup sûr\b|\bgaranti", re.I)
P_FAUSSE = re.compile(r"(probabilité|chance|risque)s?\b[^.]{0,30}\bhasard", re.I)     # la définition fausse d'une valeur p
CONSTRUCTION = re.compile(r"\b(calculée?s?|dérivée?s?)\b", re.I)
HYPOTHESE = re.compile(r"sembl|probabl|peut-être|sans doute|vérifi|hypothèse|supposi|si ", re.I)
ANNONCE = re.compile(r"\b(devrai(t|ent)|va|vont)\s+(\w+\s+)?(baisser|diminuer|chuter|reculer|augmenter|monter|progresser)|"
                     r"\b(baisse|hausse)\s+(est\s+)?(attendue|prévue|probable)", re.I)
NOMBRE = re.compile(r"(?<![\wÀ-ÿ])\d{1,3}(?: \d{3})+(?:,\d+)?(?![\wÀ-ÿ])|(?<![\wÀ-ÿ])\d+(?:[.,]\d+)?(?![\wÀ-ÿ])")


def _nb(v, unite="") -> str | None:
    return None if v is None else f"{L.nombre(v)}{unite}"


def _p(p) -> str | None:
    if p is None:
        return None
    return "inférieure à 0,001" if p < 0.001 else L.nombre(round(p, 3))


def _pct(v) -> str | None:
    return None if v is None else f"{L.nombre(round(v * 100, 1))} %"


def faits_prevision(res: dict, cible, colonnes=()) -> dict | None:
    n = L.niveaux(res)
    if not n:
        return None
    unite = L.suffixe(res, cible)
    fin, val = n["fin"], res.get("validation") or {}
    testee = res.get("n", 0) - (val.get("periodes_testees") or 0) >= 24      # il faut 24 mois avant la période de test
    saison = ("retenue, car elle améliore le test sur le passé" if res.get("saisonnalite") else
              "écartée, car elle n'améliore pas le test sur le passé" if testee else "non testée : historique trop court")
    return {"analyse": "prévision mensuelle", "mesure": L.nom_prevision(res, cible), "fiabilite": res.get("etat"),
            "mois_d_historique": res.get("n"), "calcul_par_mois": {"somme": "total du mois"}.get(res.get("agregation"), res.get("agregation")),
            "horizon_mois": n["horizon"], "niveau_recent": _nb(n["niveau"], unite), "prevision_moyenne": _nb(n["prevu"], unite),
            "variation": f"{'+' if n['variation'] > 0 else '−'}{L.nombre(round(abs(n['variation']) * 100))} %",
            "dernier_mois_prevu": {"mois": L.mois_clair(fin["periode"]), "valeur": _nb(fin["valeur"], unite), "bas": _nb(n["bas"], unite), "haut": _nb(fin["haut"], unite)},
            "tendance": {"par_mois": _nb(res.get("tendance_pente"), unite), "p": _p(res.get("tendance_p")), "nette": n["sens"] is not None},
            "ecart_habituel_autour_de_la_tendance": _nb(res.get("residu_ecart_type"), unite),
            "test_sur_le_passe": {"mois_testes": val.get("periodes_testees"), "erreur_du_calcul": _nb(val.get("erreur_modele"), unite),
                                  "erreur_d_une_estimation_simple": _nb(val.get("erreur_reference"), unite), "estimation_simple": val.get("reference")} if val else None,
            "saisonnalite": saison, "mois_ecarte_car_incomplet": res.get("mois_ecarte"), "autres_colonnes_du_fichier": list(colonnes)}


def faits_regression(res: dict, cible, colonnes=(), lignes_dependantes=False) -> dict:
    variables = []
    for genre, c, pas, effet in L.effets(res):
        variables.append({"colonne": L.nom_lisible(c["variable"]), "resultat": genre, "variation_de_la_colonne": _nb(pas, L.unite(c["variable"])),
                          "variation_associee": _nb(effet, L.unite(cible)), "p": _p(c.get("p"))})
    return {"analyse": "régression linéaire", "mesure_expliquee": L.nom_lisible(cible), "fiabilite": res.get("etat"), "lignes": res.get("n"),
            "part_expliquee_sur_cas_mis_de_cote": _pct(res.get("r2_test")), "part_expliquee_sur_cas_d_apprentissage": _pct(res.get("r2_train")),
            "ecart_habituel_calcul_realite": _nb(res.get("rmse_test"), L.unite(cible)), "dispersion_habituelle_de_la_mesure": _nb(res.get("ecart_type_cible"), L.unite(cible)),
            "colonnes_du_modele": variables, "colonnes_ecartees_car_redondantes": [L.nom_lisible(x) for x in res.get("predicteurs_ecartes") or []],
            "lignes_non_independantes": bool(lignes_dependantes), "colonnes_du_fichier": list(colonnes)}


def faits_logistique(res: dict, cible, colonnes=()) -> dict:
    mc = res.get("matrice_confusion") or {}
    return {"analyse": "prévision d'un Oui ou d'un Non", "mesure": L.nom_lisible(cible), "fiabilite": res.get("etat"), "cas_mis_de_cote": res.get("n_test"),
            "oui_reels": res.get("positifs_test"), "oui_retrouves": res.get("vrais_positifs_test"), "fausses_alertes": mc.get("faux_positifs"),
            "oui_manques": mc.get("faux_negatifs"), "capacite_a_distinguer_de_0_5_a_1": _nb(res.get("auc_test")),
            "colonnes": [{"colonne": L.nom_lisible(c["variable"]), "sens": "plus elle est haute, plus le Oui est probable" if (c.get("coef_standardise") or 0) > 0 else "plus elle est haute, moins le Oui est probable",
                          "poids": _nb(abs(c.get("coef_standardise") or 0))} for c in res.get("coefficients") or [] if c["variable"] != "(constante)"],
            "colonnes_du_fichier": list(colonnes)}


def faits_tests(inf: dict, colonnes=()) -> dict | None:
    if not inf or not (inf.get("n_tests") or inf.get("n_structure")):
        return None
    verdict = lambda x: "significatif" if x.get("significatif") else "non significatif"  # noqa: E731
    tests, liens, testees = [], [], set()
    for x in inf.get("correlations") or []:
        testees |= {x["a"], x["b"]}
        if x.get("structurel"):
            liens.append(f"{L.nom_lisible(x['a'])} et {L.nom_lisible(x['b'])}")
        else:
            tests.append({"lien_entre": [L.nom_lisible(x["a"]), L.nom_lisible(x["b"])], "correlation": _nb(x.get("r")), "force": x.get("force"), "p_corrigee": _p(x.get("p_ajuste")), "verdict": verdict(x)})
    for x in inf.get("associations") or []:
        testees |= {x["a"], x["b"]}
        if x.get("structurel"):
            liens.append(f"{L.nom_lisible(x['a'])} et {L.nom_lisible(x['b'])}")
        else:
            tests.append({"lien_entre": [L.nom_lisible(x["a"]), L.nom_lisible(x["b"])], "force": x.get("force"), "p_corrigee": _p(x.get("p_ajuste")), "verdict": verdict(x), "effectifs_faibles": bool(x.get("attendus_faibles"))})
    for x in inf.get("comparaisons") or []:
        testees |= {x["num"], x["cat"]}
        g = sorted(x["groupes"], key=lambda y: -(y.get("moyenne") or 0))
        u = L.unite(x["num"])
        tests.append({"mesure": L.nom_lisible(x["num"]), "selon": L.nom_lisible(x["cat"]), "moyenne_la_plus_haute": f"{g[0]['label']} : {_nb(g[0].get('moyenne'), u)}",
                      "moyenne_la_plus_basse": f"{g[-1]['label']} : {_nb(g[-1].get('moyenne'), u)}", "force_de_l_ecart": x.get("force"), "p_corrigee": _p(x.get("p_ajuste")), "verdict": verdict(x)})
    return {"analyse": "tests statistiques", "tests_faits": inf.get("n_tests"), "resultats_significatifs": inf.get("n_significatifs"),
            "seuil": "p corrigée inférieure à 0,05", "lignes_non_independantes": bool(inf.get("lignes_dependantes")),
            "liens_mis_a_part_car_une_colonne_decoule_de_l_autre": liens, "colonnes_testees": sorted(L.nom_lisible(c) for c in testees),
            "colonnes_du_fichier": list(colonnes), "tests": tests}


def faits_predictif(resultat: dict, colonnes=(), lignes_dependantes=False) -> dict | None:
    res, cible = resultat.get("resultat") or {}, resultat.get("cible")
    colonnes = [L.nom_lisible(c) for c in colonnes]
    if res.get("erreur"):
        return None
    if res.get("type") == "prevision":
        return faits_prevision(res, cible, colonnes)
    if res.get("type") == "logistique":
        return faits_logistique(res, cible, colonnes)
    if res.get("type") == "lineaire":
        return faits_regression(res, cible, colonnes, lignes_dependantes)
    return None


def _valeurs(texte: str) -> list:
    out = []
    for x in NOMBRE.findall(re.sub(r"[  ]", " ", texte)):
        try:
            out.append(float(x.replace(" ", "").replace(",", ".")))
        except ValueError:
            pass
    return out


def chiffres_connus(faits: dict) -> set:
    return set(_valeurs(json.dumps(faits, ensure_ascii=False))) | USUELS


def _chiffres_justes(phrase: str, connus: set) -> bool:
    return all(any(c == v or (c and abs(c - v) / c < TOLERANCE) for c in connus) for v in _valeurs(phrase))


def _phrase_sure(phrase, connus: set, fiabilite=None, affirmative=True) -> bool:
    if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > MAX_CARACTERES or not _chiffres_justes(phrase, connus):
        return False
    if CERTITUDE.search(phrase) or P_FAUSSE.search(phrase):
        return False
    if CONSTRUCTION.search(phrase) and not HYPOTHESE.search(phrase):
        return False
    if affirmative and CAUSE.search(phrase):
        return False
    return not (affirmative and fiabilite == "insuffisant" and ANNONCE.search(phrase))


def lire(faits: dict, llm, parse) -> dict | None:
    """Lecture contrôlée, ou None : la page garde alors la lecture du moteur."""
    if not faits:
        return None
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(faits, ensure_ascii=False)}]
    obj = parse(llm(messages, MAX_JETONS) or "")
    if not isinstance(obj, dict):
        return None
    connus, fiabilite = chiffres_connus(faits), faits.get("fiabilite")
    if not _phrase_sure(obj.get("conclusion"), connus, fiabilite):
        return None
    garde = lambda cle, maxi, aff: [p.strip() for p in (obj.get(cle) or []) if _phrase_sure(p, connus, fiabilite, aff)][:maxi] if isinstance(obj.get(cle), list) else []  # noqa: E731
    return {"conclusion": obj["conclusion"].strip(), "lecture": garde("lecture", MAX_LECTURE, True),
            "prudence": garde("prudence", MAX_PRUDENCE, False), "a_verifier": garde("a_verifier", MAX_ACTIONS, False)}
