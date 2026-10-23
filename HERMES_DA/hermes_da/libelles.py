# -*- coding: utf-8 -*-
"""Libellés lisibles pour les noms de colonnes bruts (« delai_initiation_jours » → « Délai initiation (jours) »).

Les exports (Excel, DHIS2, formulaires) nomment souvent les colonnes sans accents ni espaces. Les afficher tels
quels dans un tableau de bord fait « brouillon » ; ce module rétablit les accents des mots courants, les sigles
(VIH, CD4, ARV…) et met l'unité finale entre parenthèses. Un nom déjà rédigé (espaces, majuscules, accents) est
laissé tel quel : on ne réécrit pas un libellé choisi par l'utilisateur.
"""
import re

# Mots sans accents (minuscules) → forme correcte
_MOTS = {
    "delai": "délai", "delais": "délais", "meme": "même", "deces": "décès", "annee": "année", "annees": "années",
    "region": "région", "regions": "régions", "periode": "période", "frequence": "fréquence", "categorie": "catégorie",
    "depistage": "dépistage", "depiste": "dépisté", "depistes": "dépistés", "prelevement": "prélèvement",
    "resultat": "résultat", "resultats": "résultats", "numero": "numéro", "eleve": "élève", "eleves": "élèves",
    "generale": "générale", "general": "général", "depart": "départ", "anciennete": "ancienneté",
    "cooperative": "coopérative", "humidite": "humidité", "operation": "opération", "operations": "opérations",
    "depense": "dépense", "depenses": "dépenses", "reservation": "réservation", "reservations": "réservations",
    "arrivee": "arrivée", "annulee": "annulée", "annule": "annulé", "eligible": "éligible", "initie": "initié",
    "inities": "initiés", "initiee": "initiée", "supprimee": "supprimée", "supprime": "supprimé",
    "demarre": "démarré", "therapeutique": "thérapeutique", "ecole": "école", "etablissement": "établissement",
    "sante": "santé", "fidelite": "fidélité", "qualite": "qualité", "quantite": "quantité", "controle": "contrôle",
    "beneficiaire": "bénéficiaire", "beneficiaires": "bénéficiaires", "credit": "crédit", "debit": "débit",
    "unite": "unité", "unites": "unités", "activite": "activité", "entree": "entrée", "entrees": "entrées",
    "assiduite": "assiduité", "electromenager": "électroménager", "employe": "employé", "employes": "employés",
    "agee": "âgée", "age": "âge", "ages": "âges", "frequente": "fréquente", "telephone": "téléphone",
    "prevention": "prévention", "medecin": "médecin", "medical": "médical", "medicale": "médicale",
    "hopital": "hôpital", "duree": "durée", "remuneration": "rémunération", "benefice": "bénéfice",
    "reference": "référence", "date": "date", "naissance": "naissance", "embauche": "embauche",
    "sejour": "séjour", "reussite": "réussite", "theme": "thème", "systeme": "système", "donnees": "données",
    "deja": "déjà", "derniere": "dernière", "dernier": "dernier", "precedent": "précédent", "evolution": "évolution",
    "repartition": "répartition", "visite": "visite", "positivite": "positivité", "negatif": "négatif",
    "realise": "réalisé", "realises": "réalisés", "realisee": "réalisée", "realisees": "réalisées",
    "confirme": "confirmé", "confirmes": "confirmés", "vaccine": "vacciné", "vaccines": "vaccinés",
    "assiste": "assisté", "assistes": "assistés", "teste": "testé", "testes": "testés", "recu": "reçu",
    "recus": "reçus", "prevu": "prévu", "prevus": "prévus", "decede": "décédé", "decedes": "décédés",
    "perdu": "perdu", "refere": "référé", "referes": "référés", "cumule": "cumulé", "evalue": "évalué",
    "livre": "livré", "livres": "livrés", "paye": "payé", "payes": "payés", "achete": "acheté", "vendu": "vendu",
}
# Sigles et unités (minuscules) → forme d'affichage
_SIGLES = {"vih": "VIH", "cd4": "CD4", "arv": "ARV", "tarv": "TARV", "tpt": "TPT", "cv": "CV", "pvvih": "PVVIH",
           "fcfa": "FCFA", "xof": "XOF", "usd": "USD", "eur": "EUR", "tb": "TB", "cpn": "CPN", "pec": "PEC",
           "id": "ID", "imc": "IMC", "ca": "CA", "rh": "RH", "pib": "PIB", "dhis2": "DHIS2", "oev": "OEV", "tdr": "TDR", "cta": "CTA",
           "penta3": "Penta3", "csu": "CSU", "csr": "CSR", "pf": "PF", "ptme": "PTME"}
# Noms complets (exports DHIS2, expressions figées)
_NOMS = {"periodname": "Période", "periodid": "Période", "periodcode": "Période", "organisationunitname": "Unité d'organisation",
         "organisationunitid": "ID unité d'organisation", "organisationunitcode": "Code unité d'organisation",
         "orgunit": "Unité d'organisation", "dataname": "Indicateur", "dataid": "ID indicateur", "tranche_age": "Tranche d'âge",
         "tranche_d_age": "Tranche d'âge", "classe_age": "Classe d'âge", "date_naissance": "Date de naissance",
         "date_embauche": "Date d'embauche", "chiffre_affaires": "Chiffre d'affaires",
         "chiffre_affaires_fcfa": "Chiffre d'affaires (FCFA)"}
_UNITES = {"fcfa": "FCFA", "xof": "XOF", "usd": "USD", "eur": "EUR", "pct": "%", "pourcent": "%", "jours": "jours",
           "j": "jours", "ha": "ha", "kg": "kg", "t": "t", "km": "km", "mois": "mois", "ans": "ans", "h": "h",
           "min": "min", "l": "L"}


def _brut(name: str) -> bool:
    """Nom « technique » (tirets bas, tout en minuscules, sans accent ni espace) : à mettre en forme."""
    s = str(name)
    return ("_" in s or s == s.lower()) and not re.search(r"[À-ÿ]", s) and " " not in s.strip()


def joli(name) -> str:
    """Libellé lisible d'un nom de colonne ; inchangé s'il est déjà rédigé."""
    s = str(name if name is not None else "").strip()
    if not s or not _brut(s):
        return s
    if s.lower() in _NOMS:
        return _NOMS[s.lower()]
    mots = [m for m in re.split(r"[_\s]+", s) if m]
    unite = None
    if len(mots) >= 2 and mots[-1].lower() in _UNITES and not mots[-2].isdigit():
        unite = _UNITES[mots.pop().lower()]
    out = []
    for m in mots:
        low = m.lower()
        m2 = re.fullmatch(r"([a-z]+)(\d+)", low)
        if m2 and m2.group(1) in _SIGLES and low not in _SIGLES:
            out.append(_SIGLES[m2.group(1)] + m2.group(2))
            continue
        out.append(_SIGLES.get(low) or _MOTS.get(low) or low)
    txt = " ".join(out)
    if unite:
        txt += f" ({unite})"
    return txt[:1].upper() + txt[1:]


def joli_min(name) -> str:
    """Même libellé, initiale en minuscule pour l'insérer dans une phrase (« … par région »), sauf sigle."""
    t = joli(name)
    return t if len(t) > 1 and t[:2].isupper() else t[:1].lower() + t[1:]
