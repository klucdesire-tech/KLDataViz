# -*- coding: utf-8 -*-
"""Le chat calcule sur toutes les sources, par le même chemin — données fictives, IA simulée."""
import json
import os

import pandas as pd
import pytest

from _harness import reset_app_modules

MONTANTS = [100.0, 250.0, 400.0]
TOTAL = "750"


def _table():
    return pd.DataFrame({"zone": ["Nord", "Sud", "Est"], "montant": MONTANTS, "valide": ["Oui", "Oui", "Non"]})


@pytest.fixture
def chat(monkeypatch):
    """L'IA simulée lance le calcul demandé, puis conclut avec ce qu'elle a reçu."""
    reset_app_modules()
    import server
    envoye = {"messages": []}

    def question(chemin, code="result = df[\"montant\"].sum()", liens=None):
        tours = iter([json.dumps({"action": "calcul", "pourquoi": "somme", "code": code}), json.dumps({"action": "reponse", "answer": "Terminé."})])

        def ia(messages, *a, **k):
            envoye["messages"] = messages
            return next(tours)
        monkeypatch.setattr(server, "_chat_llm", ia)
        server.T.state()["current_path"] = chemin
        return server.llm_chat("Quelle est la somme ?", linked=liens)
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "x", "model": "m"})
    monkeypatch.setattr(server, "_registry_datasets", lambda: [])
    return server, question, envoye


def _resultat(r):
    return r["etapes"][0]


def _ok(r, attendu=TOTAL):
    e = _resultat(r)
    assert e["ok"], e["resultat"]
    assert attendu in e["resultat"], e["resultat"]


def test_source_csv_francais(chat, tmp_path):
    _, q, _ = chat
    p = tmp_path / "ventes.csv"
    _table().to_csv(p, sep=";", index=False, encoding="cp1252")
    _ok(q(str(p)))


def test_source_excel(chat, tmp_path):
    _, q, _ = chat
    p = tmp_path / "ventes.xlsx"
    _table().to_excel(p, index=False)
    _ok(q(str(p)))


def test_source_json(chat, tmp_path):
    _, q, _ = chat
    p = tmp_path / "ventes.json"
    _table().to_json(p, orient="records")
    _ok(q(str(p)))


def test_source_parquet(chat, tmp_path):
    _, q, _ = chat
    p = tmp_path / "ventes.parquet"
    _table().to_parquet(p)
    _ok(q(str(p)))


def test_source_google_sheets(chat, monkeypatch):
    server, q, _ = chat
    monkeypatch.setattr(server, "_telecharger", lambda url, **k: (_table().to_csv(index=False).encode("utf-8"), "text/csv"))
    _ok(q("https://docs.google.com/spreadsheets/d/abcdefgh12345/edit"))


def test_source_dhis2(chat, monkeypatch):
    server, q, _ = chat
    monkeypatch.setattr(server.dhis2, "read_dhis2", lambda uri, **k: (_table(), None))
    _ok(q("dhis2://instance/essai"))


def test_source_illisible_ne_repond_pas_sur_un_autre_jeu(chat, monkeypatch):
    server, q, _ = chat
    monkeypatch.setattr(server, "_telecharger", lambda url, **k: (_ for _ in ()).throw(OSError("réseau coupé")))
    r = q("https://docs.google.com/spreadsheets/d/abcdefgh12345/edit")
    assert "n'ai pas pu relire" in r["answer"] and r["etapes"] == []


def test_colonne_mixte_ne_transforme_pas_tout_en_texte(chat, tmp_path):
    """Une seule colonne mêlée faisait passer tout le tableau en texte : plus aucun calcul numérique possible."""
    _, q, envoye = chat
    p = tmp_path / "mixte.xlsx"
    _table().assign(code=[12, "A7", 3.5]).to_excel(p, index=False)
    _ok(q(str(p)))
    assert "colonnes_mixtes" in envoye["messages"][0]["content"]


def test_colonnes_en_double(chat, tmp_path):
    server, q, envoye = chat
    p = tmp_path / "doublon.csv"
    p.write_text("zone,montant,montant\nNord,100,1\nSud,250,2\nEst,400,3\n", encoding="utf-8")
    _ok(q(str(p)))
    assert server._noms_uniques(["a", "a", 3]) == ["a", "a (2)", "3"]


def test_colonnes_numeriques_nommees_par_des_nombres(chat, tmp_path):
    _, q, _ = chat
    p = tmp_path / "annees.xlsx"
    pd.DataFrame({"zone": ["Nord", "Sud"], 2024: [1, 2], 2025: [3, 4]}).to_excel(p, index=False)
    _ok(q(str(p), code="result = df[\"2025\"].sum()"), "7")


def test_fichier_lie_par_lien(chat, tmp_path, monkeypatch):
    server, q, _ = chat
    p = tmp_path / "ventes.csv"
    _table().to_csv(p, index=False)
    monkeypatch.setattr(server, "_telecharger", lambda url, **k: (pd.DataFrame({"prix": [5, 6]}).to_csv(index=False).encode("utf-8"), "text/csv"))
    r = q(str(p), code="result = data[\"tarifs\"][\"prix\"].sum()", liens=[{"path": "https://exemple.org/tarifs.csv", "name": "tarifs"}])
    _ok(r, "11")


def test_fichier_en_cours_avant_les_jeux_enregistres(chat, tmp_path, monkeypatch):
    """Un ancien jeu enregistré passait devant le fichier ouvert."""
    server, q, envoye = chat
    p = tmp_path / "ventes.csv"
    _table().to_csv(p, index=False)
    ancien = tmp_path / "Google Sheets.parquet"
    pd.DataFrame({"autre": [1]}).to_parquet(ancien)
    monkeypatch.setattr(server, "_registry_datasets", lambda: [{"dataset_name": "Google Sheets", "dataset_id": "1", "work_path": str(ancien), "row_count": 1}])
    r = q(str(p), code="result = len(list(data.keys())[0])\nprint(list(data.keys()))\nresult = df[\"montant\"].sum()")
    _ok(r)
    assert _resultat(r)["resultat"].index("ventes") < _resultat(r)["resultat"].index("Google Sheets")


def test_jeux_enregistres_ni_relus_ni_montres_a_l_ia(chat, tmp_path, monkeypatch):
    server, q, envoye = chat
    p = tmp_path / "ventes.csv"
    _table().to_csv(p, index=False)
    ancien = tmp_path / "ancien.parquet"
    pd.DataFrame({"colonne_secrete": ["valeur_privee"]}).to_parquet(ancien)
    monkeypatch.setattr(server, "_registry_datasets", lambda: [{"dataset_name": "ancien", "dataset_id": "1", "work_path": str(ancien), "row_count": 1}])
    lus = []
    vrai = server._chat_load_work
    monkeypatch.setattr(server, "_chat_load_work", lambda chemin: lus.append(chemin) or vrai(chemin))
    q(str(p))
    consigne = envoye["messages"][0]["content"]
    assert "ancien" in consigne and "autre_jeu_enregistre" in consigne
    assert "valeur_privee" not in consigne and "colonne_secrete" not in consigne and lus == []


def test_sans_fichier_ouvert_les_jeux_restent_decrits(chat, tmp_path, monkeypatch):
    server, q, envoye = chat
    ancien = tmp_path / "ancien.parquet"
    pd.DataFrame({"montant": [5.0]}).to_parquet(ancien)
    monkeypatch.setattr(server, "_registry_datasets", lambda: [{"dataset_name": "ancien", "dataset_id": "1", "work_path": str(ancien), "row_count": 1}])
    _ok(q(None), "5")
    assert "\"montant\"" in envoye["messages"][0]["content"]


def test_consigne_impose_la_methode():
    reset_app_modules()
    import server
    for mot in ("population", "sous-totaux", "à l'intérieur de chaque groupe", "`df` est DÉJÀ"):
        assert mot in server.CHAT_METHODE


def test_save_table_garde_les_types_des_autres_colonnes(tmp_path):
    reset_app_modules()
    import server
    p = tmp_path / "t.parquet"
    df = pd.DataFrame({"montant": [1.5, 2.5], "code": [12, "A7"]})
    df.attrs["feuille"] = "x"
    server._save_table(df, str(p))
    lu = pd.read_parquet(p)
    assert lu["montant"].dtype == float and lu["code"].tolist() == ["12", "A7"]
