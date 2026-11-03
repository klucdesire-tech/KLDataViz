# -*- coding: utf-8 -*-
"""Un graphique de la discussion ajouté au tableau de bord, recalculé à chaque actualisation — données fictives, IA simulée."""
import json

import pandas as pd
import pytest

from _harness import reset_app_modules

CODE = ('chart = {"type": "bar", "title": "Montant par zone", "labels": [str(z) for z in df["zone"]], '
        '"values": [float(v) for v in df["montant"]]}\nresult = df[["zone", "montant"]]')


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_SOURCES_DIR", str(tmp_path / "sources"))
    reset_app_modules()
    import server
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "x", "model": "m"})
    monkeypatch.setattr(server, "_llm_call", lambda *a, **k: None)  # tableau de bord par règles
    monkeypatch.setattr(server, "_registry_datasets", lambda: [])
    monkeypatch.setattr(server, "_relire_chiffres", lambda q, a, m: (a, None))
    server.T.state().clear()
    chemin = tmp_path / "ventes.csv"
    pd.DataFrame({"zone": ["Nord", "Sud", "Est"], "montant": [100.0, 250.0, 400.0]}).to_csv(chemin, index=False)
    appels = {"n": 0}

    def question(q="Montre le montant par zone.", code=CODE):
        tours = iter([json.dumps({"action": "calcul", "pourquoi": "graphique", "code": code}),
                      json.dumps({"action": "reponse", "answer": "Voici le montant par zone."})])

        def ia(messages, *a, **k):
            appels["n"] += 1
            return next(tours)
        monkeypatch.setattr(server, "_chat_llm", ia)
        return server.llm_chat(q, file_path=str(chemin))
    return server, str(chemin), question, appels


def _section(html):
    return html[html.index('id="ajoutsAdifa"'):] if 'id="ajoutsAdifa"' in html else ""


def test_ajout_recalcul_regeneration_retrait(env):
    server, chemin, question, _ = env
    assert "html" in server.do_dashboard(chemin, "Ventes")
    r = question()
    assert r["graphique_id"] and r["charts"][0]["title"] == "Montant par zone"
    s = server.ajouter_au_tableau(r["graphique_id"])
    sec = _section(s["html"])
    assert "Montant par zone" in sec and "400" in sec and s["ajouts"][0]["titre"] == "Montant par zone"
    # la source change : l'actualisation recalcule le graphique ajouté
    pd.DataFrame({"zone": ["Nord", "Sud", "Est"], "montant": [100.0, 250.0, 999.0]}).to_csv(chemin, index=False)
    maj = server.sources.refresh(s["id"], server._read_df, server.build_dashboard)
    assert maj["changed"] and "999" in _section(maj["html"]) and "400" not in _section(maj["html"])
    # « Régénérer » (nouvelle spec) garde les graphiques ajoutés
    assert "999" in _section(server.do_dashboard(chemin, "Ventes")["html"])
    assert server.sources.get(s["id"])["ajouts"][0]["titre"] == "Montant par zone"
    fin = server.retirer_du_tableau(s["id"], s["ajouts"][0]["id"])
    assert fin["ajouts"] == [] and 'id="ajoutsAdifa"' not in fin["html"]


def test_demande_directe_sans_nouvel_appel_a_l_ia(env):
    server, chemin, question, appels = env
    server.do_dashboard(chemin, "Ventes")
    question()
    avant = appels["n"]
    r = server.llm_chat("Ajoute ce graphique au tableau de bord", file_path=chemin)
    assert "C'est fait" in r["answer"] and r["tableau"]["source_id"] and appels["n"] == avant
    r = server.llm_chat("mets-le dans le tableau de bord", file_path=chemin)  # demandé deux fois : pas de doublon
    assert "déjà dans le tableau de bord" in r["answer"]
    assert len(server.sources.get(r["tableau"]["source_id"])["ajouts"]) == 1


def test_creer_et_ajouter_dans_la_foulee(env):
    server, chemin, question, _ = env
    server.do_dashboard(chemin, "Ventes")
    r = question("Ajoute au tableau de bord un graphique du montant par zone")
    assert "C'est fait" in r["answer"] and r["tableau"]["source_id"]
    assert server.sources.get(r["tableau"]["source_id"])["ajouts"][0]["titre"] == "Montant par zone"


def test_sans_tableau_de_bord(env):
    server, chemin, question, _ = env
    r = question()
    with pytest.raises(ValueError, match="pas encore de tableau de bord"):
        server.ajouter_au_tableau(r["graphique_id"])
    assert "pas encore de tableau de bord" in server.llm_chat("ajoute ce graphique au tableau de bord", file_path=chemin)["answer"]


def test_graphique_inconnu_ou_calcul_non_rejouable(env, monkeypatch):
    server, chemin, question, _ = env
    server.do_dashboard(chemin, "Ventes")
    with pytest.raises(ValueError, match="plus disponible"):
        server.ajouter_au_tableau("inconnu")
    r = question()
    # calcul qui lisait un autre jeu (fichier lié, registre) : il ne se rejoue pas sur le seul fichier du tableau
    monkeypatch.setattr(server, "_rejouer_ajout", lambda a, df: ([], "KeyError: 'un autre jeu'"))
    with pytest.raises(ValueError, match="ne peut pas être recalculé"):
        server.ajouter_au_tableau(r["graphique_id"])
    assert server.sources.get(server.sources.par_chemin(chemin))["ajouts"] == []  # rien d'enregistré


def test_titre_hostile_echappe(env):
    server, chemin, question, _ = env
    server.do_dashboard(chemin, "Ventes")
    code = CODE.replace('"Montant par zone"', '"</script><script>alert(1)</script>"')
    r = question(code=code)
    sec = _section(server.ajouter_au_tableau(r["graphique_id"])["html"])
    assert "<script>alert(1)" not in sec and "&lt;/script&gt;" in sec


def test_six_au_plus(env):
    server, chemin, question, _ = env
    server.do_dashboard(chemin, "Ventes")
    for i in range(6):
        r = question(code=CODE.replace("Montant par zone", f"Graphique {i}") + f"\n# {i}")
        server.ajouter_au_tableau(r["graphique_id"])
    r = question(code=CODE + "\n# septième")
    with pytest.raises(ValueError, match="6 graphiques"):
        server.ajouter_au_tableau(r["graphique_id"])


@pytest.mark.parametrize("q,attendu", [("Ajoute ce graphique au tableau de bord", "dernier"),
                                       ("mets-le dans le TB", "dernier"),
                                       ("Ajoute au tableau de bord un graphique des ventes par mois", "nouveau"),
                                       ("Insère dans le dashboard le graphique des décès par district", "nouveau"),
                                       ("Que montre le tableau de bord ?", None),
                                       ("Ajoute une colonne au fichier", None)])
def test_intention(env, q, attendu):
    assert env[0]._demande_ajout(q) == attendu
