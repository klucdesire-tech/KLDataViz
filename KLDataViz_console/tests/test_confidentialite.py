# -*- coding: utf-8 -*-
"""Pseudonymisation avant l'IA — personnes fictives uniquement."""
import json
import time

import pandas as pd
import pytest

from _harness import reset_app_modules

PRENOMS = ["Aya", "Koffi", "Mariam", "Yao", "Awa", "Ibrahim", "Fatou", "Serge", "Adjoua", "Moussa"]
NOMS = ["Kouassi", "Traoré", "Konan", "Diabaté", "Koné", "Bamba", "Yéo", "N'Guessan", "Ouattara", "Coulibaly"]


def _patients(n=60):
    return pd.DataFrame({
        "nom_complet": [f"{PRENOMS[i % 10]} {NOMS[(i * 3) % 10]}{i}" for i in range(n)],
        "telephone": [f"07 {i:02d} 45 {i % 90 + 10:02d} 12" for i in range(n)],
        "email": [f"patient{i}@exemple.ci" for i in range(n)],
        "adresse": [f"Lot {i}, quartier Fictif {i % 7}" for i in range(n)],
        "date_naissance": pd.date_range("1980-01-01", periods=n, freq="37D"),
        "numero_dossier": [f"DOS-2025-{i:05d}" for i in range(n)],
        "observations": [f"Patient revu le {i} du mois, suivi par l'agent Kouamé numéro {i}, rien à signaler de particulier." for i in range(n)],
        "district": ["Bouaké", "Korhogo", "Man"] * (n // 3),
        "nom_district": ["Bouaké Nord", "Korhogo", "Man Ouest"] * (n // 3),
        "sexe": ["F", "M"] * (n // 2),
        "age": list(range(20, 20 + n)),
        "cd4": [350.5 + i for i in range(n)],
        "date_visite": [f"2025-{i % 12 + 1:02d}-{i % 27 + 1:02d}" for i in range(n)],
        "campagne": ["2023-2024", "2024-2025"] * (n // 2),
        "client_fidelite": ["Oui", "Non"] * (n // 2),
        "note_client_sur_10": [i % 10 for i in range(n)],
        "indicateur": ["Nombre de personnes testées positives au VIH ce mois-ci"] * n,
        "code_site": [f"S{i % 5}" for i in range(n)],
    })


@pytest.fixture
def C():
    reset_app_modules()
    import confidentialite
    import tenancy as T
    T.state().pop("pseudonymes", None)
    return confidentialite


def test_identifiants_reperes(C):
    trouves = set(C.colonnes_identifiantes(_patients()))
    assert trouves == {"nom_complet", "telephone", "email", "adresse", "date_naissance", "numero_dossier", "observations"}


@pytest.mark.parametrize("colonne", ["district", "nom_district", "sexe", "age", "cd4", "date_visite", "campagne", "client_fidelite",
                                     "note_client_sur_10", "indicateur", "code_site"])
def test_rien_d_autre_n_est_masque(C, colonne):
    """Masquer une date, une catégorie ou un libellé d'indicateur empêcherait Adifa’ d'analyser."""
    assert colonne not in C.colonnes_identifiantes(_patients())


def test_noms_sans_mot_cle_reperes_par_leur_forme(C):
    df = pd.DataFrame({"beneficiaire": [f"{PRENOMS[i % 10]} {NOMS[i % 10]}{i}" for i in range(40)], "montant": range(40)})
    assert C.colonnes_identifiantes(df) == ["beneficiaire"]
    df = pd.DataFrame({"x": [f"{PRENOMS[i % 10]} {NOMS[(i * 7) % 10]}" + chr(65 + i % 26) for i in range(40)]})
    assert C.colonnes_identifiantes(df) == ["x"]


def test_aller_retour(C):
    df = _patients()
    C.enregistrer(df)
    texte = f"Le patient {df.nom_complet[3]} ({df.telephone[3]}, {df.email[3]}) né le {df.date_naissance[3]:%Y-%m-%d}, dossier {df.numero_dossier[3]}."
    masque = C.masquer(texte)
    for v in (df.nom_complet[3], df.telephone[3], df.email[3], f"{df.date_naissance[3]:%Y-%m-%d}", df.numero_dossier[3]):
        assert v not in masque
    assert masque.count("⟦P") == 5 and C.demasquer(masque) == texte


def test_effectifs_et_categories_intacts(C):
    C.enregistrer(_patients())
    texte = "À Bouaké, 12 femmes de 34 ans ont un CD4 moyen de 380,5 au 2025-03-04."
    assert C.masquer(texte) == texte


def test_valeur_dans_un_mot_plus_long_non_touchee(C):
    C.enregistrer(pd.DataFrame({"nom": ["Konan", "Bamba", "Kone"] * 10}))
    assert C.masquer("Konanville et Bambara") == "Konanville et Bambara"
    assert "Konan" not in C.masquer("M. Konan est venu")


def test_telephone_lu_comme_nombre(C):
    C.enregistrer(pd.DataFrame({"telephone": [707080910 + i for i in range(30)]}))
    assert "707080915" not in C.masquer("appeler le 707080915")


def test_isolation_entre_utilisateurs(C):
    import tenancy as T
    with T.use(T.Ctx(key="a")):
        C.enregistrer(pd.DataFrame({"nom": ["Secret Absolu"] * 3}))
        assert "Secret Absolu" not in C.masquer("Secret Absolu")
    with T.use(T.Ctx(key="b")):
        assert C.masquer("Secret Absolu") == "Secret Absolu"
        assert C.demasquer("⟦P1⟧") == "⟦P1⟧"  # un jeton d'un autre utilisateur ne se résout pas ici


def test_rapide_sur_un_gros_fichier(C):
    df = pd.DataFrame({"nom": [f"Personne Fictive{i}" for i in range(50_000)]})
    debut = time.time()
    C.enregistrer(df)
    texte = " ".join(df.nom.head(2000)) * 5
    assert "Fictive12" not in C.masquer(texte)
    assert time.time() - debut < 15


def test_porte_vers_l_ia_masque_et_remet_en_clair(monkeypatch):
    reset_app_modules()
    import server
    df = _patients()
    server.T.state().pop("pseudonymes", None)
    server.confidentialite.enregistrer(df)
    envoye = []

    def fournisseur(messages, *a, **k):
        envoye.append(json.dumps(messages, ensure_ascii=False))
        jeton = server.confidentialite.masquer(df.nom_complet[0])
        return f"Le dossier de {jeton} est complet."
    monkeypatch.setattr(server, "_llm_call_brut", fournisseur)
    sortie = server._llm_call([{"role": "system", "content": "Analyse."}, {"role": "user", "content": f"Que dire de {df.nom_complet[0]} ?"}])
    assert df.nom_complet[0] not in envoye[0] and "⟦P" in envoye[0]
    assert sortie == f"Le dossier de {df.nom_complet[0]} est complet."


def test_chat_ne_laisse_partir_aucun_identifiant(tmp_path, monkeypatch):
    """De bout en bout : contexte, lignes affichées par un calcul, relecture. Le code de l'IA reçoit les vraies valeurs."""
    reset_app_modules()
    import server
    p = tmp_path / "patients.xlsx"
    df = _patients()
    df.to_excel(p, index=False)
    server.T.state().clear()
    envoye, codes = [], []
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "x", "model": "m"})
    monkeypatch.setattr(server, "_registry_datasets", lambda: [])

    def fournisseur(messages, *a, **k):
        envoye.append(json.dumps(messages, ensure_ascii=False))
        if len(envoye) == 1:
            jeton = server.confidentialite.masquer(df.nom_complet[2])
            return json.dumps({"action": "calcul", "pourquoi": "voir", "code": f"print(df.head(10).to_string())\nresult = df[df['nom_complet'] == '{jeton}']"},
                              ensure_ascii=False)
        if len(envoye) == 2:
            return json.dumps({"action": "reponse", "answer": "Vu."})
        return json.dumps({"erreurs": []})
    monkeypatch.setattr(server, "_llm_call_brut", fournisseur)
    vrai = server._run_ai_code
    monkeypatch.setattr(server, "_run_ai_code", lambda code, paths: codes.append(code) or vrai(code, paths))
    r = server.llm_chat("Montre les premières lignes.", file_path=str(p))
    tout = "\n".join(envoye)
    personnelles = [*df.nom_complet, *df.telephone, *df.email, *df.adresse, *df.numero_dossier, *df.observations]
    assert not [v for v in personnelles if v in tout]
    assert df.nom_complet[2] in codes[0]  # le calcul s'exécute sur la vraie valeur
    assert r["etapes"][0]["ok"] and len(envoye) == 3
    assert "Bouaké" in tout and "date_visite" in tout  # l'analyse garde ce qui n'identifie personne
