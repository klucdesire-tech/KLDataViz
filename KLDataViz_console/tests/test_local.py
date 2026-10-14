# -*- coding: utf-8 -*-
"""Non-régression de la console locale : python -m pytest tests/test_local.py"""
import os
import time

import pandas as pd
import pytest

os.environ.pop("KLDV_MODE", None)
from _harness import call, reset_app_modules, start  # noqa: E402
reset_app_modules()
import server  # noqa: E402


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    httpd, url = start(server)
    yield url
    httpd.shutdown()


@pytest.fixture(scope="module")
def csv(tmp_path_factory):
    p = tmp_path_factory.mktemp("d") / "ventes_test.csv"
    pd.DataFrame({"region": ["A", "B", "A", "C"] * 10, "montant": range(40)}).to_csv(p, index=False)
    return str(p)


def test_pages_publiques(base):
    assert call(base, "/", raw=True)[0] == 200
    assert call(base, "/classic", raw=True)[0] == 200
    assert call(base, "/healthz")[1] == {"ok": True}


def test_parcours_analyse_export(base, csv):
    code, a = call(base, "/api/analyze", {"file_path": csv})
    assert code == 200 and a["rows"] == 40, a
    code, c = call(base, "/api/clean", {"file_path": csv})
    assert code == 200 and "cleaned_path" in c
    code, e = call(base, "/api/export/html", {})
    assert code == 200 and e["url"].startswith("/download/")
    code, data = call(base, e["url"], raw=True)
    assert code == 200 and len(data) > 100
    code, files = call(base, "/api/exports")
    assert any(f["file"] == e["file"] for f in files)
    assert call(base, "/api/exports/delete", {"file": e["file"]})[0] == 200
    os.remove(c["cleaned_path"])


def test_architecte_format_large(base, tmp_path, monkeypatch):
    """Export type DHIS2 (« ; », dimensions dans les en-têtes, ligne d'agrégat) → format long."""
    monkeypatch.setattr(server, "_llm_config", lambda: None)  # repli par règles, sans appel réseau
    ages = ["00-04 ans", "05-09 ans", "50 ans et plus"]
    cols = {f"Mars 2026 SIG - Nombre de {i} {a}": [3, 5, 7, 3] for i in ("consultations", "consultants") for a in ages}
    p = tmp_path / "dhis2.csv"
    pd.DataFrame({"orgid": ["a1", "b2", "c3", "d4"], "nom": ["CSR-D A", "CSR-D B", "CSU C", "DISTRICT"], **cols}) \
        .to_csv(p, sep=";", index=False, encoding="utf-8-sig")
    code, job = call(base, "/api/architect", {"file_path": str(p)})
    assert code == 200 and job.get("job"), job
    for _ in range(100):  # tâche de fond
        code, j = call(base, f"/api/architect/job?id={job['job']}")
        if j.get("status") == "done":
            break
        time.sleep(0.1)
    r = j["result"]
    assert not r.get("error"), r
    assert call(base, "/api/architect/job?id=inconnu")[1].get("error")
    assert r["ia"] is False and set(r["proposition"]["comprehension"]["dimensions"]) == {"periode", "tranche_age"}
    assert r["apercu"]["fait"]["lignes"] == 4 * len(cols)
    code, a = call(base, "/api/architect/apply", {"file_path": str(p), "spec": r["proposition"]["spec"]})
    assert code == 200 and a["url"].startswith("/download/"), a
    # La structure est analysable directement : vue à plat de la table de faits
    vue = a["vues"][0]
    assert vue["lignes"] == r["apercu"]["fait"]["lignes"]
    code, an = call(base, "/api/analyze", {"file_path": vue["path"]})
    assert code == 200 and an["rows"] == vue["lignes"], an
    os.remove(vue["path"])
    assert call(base, "/api/exports/delete", {"file": a["file"]})[0] == 200


def test_detail_des_corrections(base, tmp_path):
    """Chaque correction proposée est détaillée : lignes, avant → après, valeurs non convertibles."""
    p = tmp_path / "sale.csv"
    pd.DataFrame({"client": ["A", "B", "A"] + list("CDEFGHIJKLMNOPQRSTUVW"),
                  "montant": ["1 200,5", "30", "1 200,5", "inconnu"] + [str(i) for i in range(20)]}).to_csv(p, index=False)
    code, c = call(base, "/api/clean", {"file_path": str(p)})
    assert code == 200, c
    d = c["rapport"]["details"]
    assert d["doublons"]["groupes"][0]["lignes"] == [2, 4]  # n° de ligne du fichier, en-tête = 1
    assert d["type:montant"]["non_convertibles"] == 1
    assert d["type:montant"]["exemples_non_convertibles"][0] == {"ligne": 5, "valeur": "inconnu"}
    assert d["imput:montant"]["lignes"] == [5]
    os.remove(c["cleaned_path"])


def test_architecte_fichier_long_sans_ia(base, tmp_path, monkeypatch):
    """Fichier déjà « long » et IA indisponible : une structure exécutable est quand même proposée."""
    monkeypatch.setattr(server, "_llm_config", lambda: None)
    p = tmp_path / "prelevements.csv"
    pd.DataFrame({"patient": [f"P{i}" for i in range(30)], "site": ["Buyo", "Soubré", "Méagui"] * 10,
                  "resultat": range(30), "vide": [None] * 30}).to_csv(p, index=False)
    r = _wait_job(base, call(base, "/api/architect", {"file_path": str(p)})[1])
    assert r["source_structure"] == "regles" and r["proposition"]["spec"], r
    assert r["apercu"]["fait"]["colonnes"] == ["patient", "site", "resultat"]  # colonne vide retirée


def test_architecte_ia_corrige_sa_recette():
    """Recette de l'IA inexécutable → l'erreur lui est renvoyée, la recette corrigée est retenue."""
    import json as _json
    import architect
    df = pd.DataFrame({"site": ["A", "B"], "valeur": [1, 2]})
    good = {"comprehension": {"resume": "ok"}, "problemes": [], "structure": [],
            "spec": {"fact_name": "fait", "drop_columns": ["colonne_inexistante"],
                     "dimensions": {"dim_site": {"key": "SITE"}}}}
    answers = iter([_json.dumps(good), _json.dumps({"spec": {"fact_name": "fait",
                                                              "dimensions": {"dim_site": {"key": "site"}}}})])
    r = architect.propose(df, {}, lambda messages, max_tokens: next(answers))
    assert r["source_structure"] == "ia_corrigee" and "SITE" in r["spec_error"], r
    assert set(r["apercu"]) == {"fait", "dim_site"}


def test_csv_point_virgule(base, tmp_path):
    """Exports français / DHIS2 : séparateur « ; » et encodage Windows détectés à l'analyse."""
    p = tmp_path / "export_fr.csv"
    pd.DataFrame({"établissement": ["CSR Buyo", "CSU Méagui"] * 10, "consultations": range(20)}) \
        .to_csv(p, sep=";", index=False, encoding="cp1252")
    code, a = call(base, "/api/analyze", {"file_path": str(p)})
    assert code == 200 and a["columns"] == 2, a


def test_reparation_encodage():
    """« contrÃ´le » → « contrôle », détaillé, et refusable par l'utilisateur."""
    import modeling
    df = pd.DataFrame({"motif": ["sous contrÃ´le ARV", "DÃ©cÃ¨s", "Soubré", None] * 5})
    out, r = modeling.clean_dataframe(df)
    assert r["encodage_corrige"] == {"motif": 2}  # après dédoublonnage des 5 répétitions
    assert out["motif"].iloc[0] == "sous contrôle ARV" and out["motif"].iloc[2] == "Soubré"
    assert r["details"]["encodage:motif"]["exemples"][0] == {"ligne": 2, "avant": "sous contrÃ´le ARV",
                                                            "apres": "sous contrôle ARV"}
    out2, r2 = modeling.clean_dataframe(df, skip=["encodage:motif"])
    assert r2["encodage_corrige"] == {} and out2["motif"].iloc[0] == "sous contrÃ´le ARV"


def test_pas_de_fausse_correction_trim():
    """Une colonne texte avec des cases vides mais sans espaces superflus n'est pas « à corriger »."""
    import modeling
    assert modeling._coerce_series(pd.Series(["a", None, "b"]))[1] is None
    out, t = modeling._coerce_series(pd.Series([" a", None, "b "]))
    assert t == "texte (trim)" and out.iloc[0] == "a" and pd.isna(out.iloc[1])


def _wait_job(base, job):
    for _ in range(100):
        j = call(base, f"/api/architect/job?id={job['job']}")[1]
        if j.get("status") == "done":
            return j["result"]
        time.sleep(0.1)
    raise AssertionError("tâche non terminée")


def test_architecte_plusieurs_fichiers(base, tmp_path, monkeypatch):
    """Deux fichiers reliés par un identifiant de nom différent → relation détectée, modèle commun."""
    monkeypatch.setattr(server, "_llm_config", lambda: None)
    ventes = tmp_path / "ventes.csv"
    clients = tmp_path / "clients.xlsx"
    pd.DataFrame({"id_client": ["C1", "C2", "C3", "C1"] * 5, "montant": range(20)}).to_csv(ventes, index=False)
    pd.DataFrame({"CODE CLIENT": ["C1", "C2", "C3", "C4"], "ville": ["Abidjan", "Soubré", "Méagui", "Buyo"]}) \
        .to_excel(clients, index=False)
    paths = [str(ventes), str(clients)]
    r = _wait_job(base, call(base, "/api/architect", {"file_paths": paths})[1])
    assert not r.get("error"), r
    rel = r["relations_mesurees"][0]
    assert {rel["gauche"], rel["droite"]} == {"ventes.csv :: id_client", "clients.xlsx :: CODE CLIENT"}
    assert rel["cardinalite"] in ("N:1", "1:N")
    a = call(base, "/api/architect/apply", {"file_paths": paths, "spec": r["proposition"]["spec"]})[1]
    assert a.get("url") and len(a["tables"]) == 2, a
    assert call(base, "/api/exports/delete", {"file": a["file"]})[0] == 200


def test_chat_seconde_chance_code_ia(base, tmp_path, monkeypatch):
    """Code de l'IA invalide (apostrophe dans un nom de colonne) → erreur renvoyée à l'IA, code corrigé exécuté."""
    import json as _json
    p = tmp_path / "utilisateurs.csv"
    pd.DataFrame({"Nom d'utilisateur": ["awa", "koffi", "awa"], "Age (ans)": [30, 40, 50]}).to_csv(p, index=False)
    assert call(base, "/api/analyze", {"file_path": str(p)})[0] == 200
    broken = "df = data['utilisateurs']\nresult = df.groupby('Nom d'utilisateur')['Age (ans)'].mean()"
    fixed = "df = data['utilisateurs']\nresult = df.groupby(\"Nom d'utilisateur\")[\"Age (ans)\"].mean()"
    answers = iter([_json.dumps({"answer": "Âge moyen", "code": broken}),
                    _json.dumps({"answer": "Âge moyen par utilisateur", "code": fixed})])
    seen = []
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "test"})
    monkeypatch.setattr(server, "_llm_call", lambda messages, *a, **k: (seen.append(messages), next(answers))[1])
    code, r = call(base, "/api/chat", {"question": "âge moyen par Nom d'utilisateur"})
    assert code == 200 and r["type"] == "table", r
    assert r["table"]["rows"] == [["awa", "40.0"], ["koffi", "40.0"]]
    assert "unterminated string" in seen[1][-1]["content"]  # l'erreur a bien été transmise à l'IA
