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
    monkeypatch.setattr(server, "_rejouer_ajout", lambda a, df, filtres=None: ({}, "KeyError: 'un autre jeu'", True))
    with pytest.raises(ValueError, match="ne peut pas être recalculé"):
        server.ajouter_au_tableau(r["graphique_id"])
    assert server.sources.get(server.sources.par_chemin(chemin))["ajouts"] == []  # rien d'enregistré


def test_titre_hostile_echappe(env):
    server, chemin, question, _ = env
    server.do_dashboard(chemin, "Ventes")
    code = CODE.replace('"Montant par zone"', '"</script><script>alert(1)</script>"')
    r = question(code=code)
    sec = _section(server.ajouter_au_tableau(r["graphique_id"])["html"])
    assert "<script>alert(1)" not in sec and "\\u003c/script>" in sec  # dans les données ; la page l'affiche via esc()


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


FILTRES = [{"col": "zone", "values": ["Est", "Nord", "Sud"]}, {"col": "canal", "values": ["Boutique", "Web"]}]


def _ventes_canal():
    return pd.DataFrame({"zone": ["Nord", "Sud", "Est", "Nord"], "canal": ["Web", "Web", "Boutique", "Boutique"],
                         "montant": [100.0, 250.0, 400.0, 50.0]})


def test_cle_comme_la_page(env):
    server = env[0]
    assert server._cle_filtres([("zone", "Nord"), ("canal", "Web")]) == '[["canal","Web"],["zone","Nord"]]'
    assert server._cle_filtres([]) == "[]"


def test_combinaisons_presentes_seulement(env, monkeypatch):
    server = env[0]
    combos, complet = server._combinaisons(_ventes_canal(), FILTRES + [{"col": "absente", "values": ["x"]}])
    # 3 zones + 2 canaux + 4 couples zone × canal présents dans les données (pas Sud × Boutique)
    assert complet and len(combos) == 3 + 2 + 4
    monkeypatch.setattr(server, "MAX_VARIANTES", 4)
    assert server._combinaisons(_ventes_canal(), FILTRES) == (server._combinaisons(_ventes_canal(), FILTRES)[0], False)
    assert server._combinaisons(_ventes_canal(), []) == ([], True)


def test_variantes_par_filtre_en_un_passage(env, monkeypatch):
    server = env[0]
    passages = []
    vrai = server._run_ai_code
    monkeypatch.setattr(server, "_run_ai_code", lambda code, paths: passages.append(1) or vrai(code, paths))
    ajout = {"code": "import numpy as np\n" + CODE, "jeu": "ventes.csv"}  # un import en tête reste accepté
    variantes, erreur, complet = server._rejouer_ajout(ajout, _ventes_canal(), FILTRES)
    assert erreur is None and complet and len(passages) == 1
    assert variantes["[]"][0]["values"] == [[100.0, 250.0, 400.0, 50.0]]
    assert variantes['[["zone","Nord"]]'][0]["labels"] == ["Nord", "Nord"]
    assert variantes['[["canal","Web"],["zone","Sud"]]'][0]["values"] == [[250.0]]
    assert '[["canal","Boutique"],["zone","Sud"]]' not in variantes  # aucune ligne : pas de graphique


def test_page_suit_les_filtres(env):
    server, chemin, question, _ = env
    _ventes_canal().to_csv(chemin, index=False)
    server.do_dashboard(chemin, "Ventes")
    r = question()
    html = server.ajouter_au_tableau(r["graphique_id"])["html"]
    sec = _section(html)
    assert '[[\\"zone\\",\\"Nord\\"]]' in sec or '[["zone","Nord"]]' in sec
    assert "window.render" in sec and "suit les filtres" in sec
    # dessinés avec les outils du tableau de bord, dans sa grille de graphiques
    assert "getElementById('row3')" in sec and "card(corps(" in sec and "navyTip" in sec
    assert html.index('<section class="row3" id="row3"></section>') < html.index('id="ajoutsAdifa"')


CODE_PAR_SEXE = ('p = df.pivot_table(index="mois", columns="sexe", values="depistes", aggfunc="sum")\n'
                 'chart = {"type": "line", "title": "Dépistages par sexe", "labels": list(p.index), '
                 '"values": [p["F"].tolist(), p["M"].tolist()], "seriesNames": ["Femmes", "Hommes"]}')


def test_filtre_qui_casse_le_calcul_signale(env):
    """Un graphique « par sexe » filtré sur un seul sexe ne se calcule plus : c'est noté, pas confondu avec « non précalculé »."""
    server = env[0]
    df = pd.DataFrame({"mois": ["2025-01", "2025-01", "2025-02", "2025-02"], "sexe": ["F", "M", "F", "M"],
                       "region": ["Nord", "Sud", "Nord", "Sud"], "depistes": [10, 12, 9, 14]})
    filtres = [{"col": "sexe", "values": ["F", "M"]}, {"col": "region", "values": ["Nord", "Sud"]}]
    variantes, erreur, complet = server._rejouer_ajout({"code": CODE_PAR_SEXE, "jeu": "x"}, df, filtres)
    assert erreur is None and complet
    assert variantes['[["sexe","M"]]'] is None                       # échec noté
    assert variantes["[]"][0]["values"] == [[10.0, 9.0], [12.0, 14.0]]
    page = server._avec_ajouts('<section class="row3" id="row3"></section></body>', [{"code": CODE_PAR_SEXE, "jeu": "x"}], df, filtres)
    assert '"compare": ["sexe"]' in page and "il compare les" in page and "intersect:false" in page


def test_retirer_au_survol(env):
    """« Retirer » vit dans le graphique ajouté ; il n'apparaît que si l'application l'autorise (jamais dans l'e-mail
    ni le lien partagé) ; l'application exécute le retrait."""
    import os
    server, chemin, question, _ = env
    server.do_dashboard(chemin, "Ventes")
    s = server.ajouter_au_tableau(question()["graphique_id"])
    sec = _section(s["html"])
    assert f'"id": "{s["ajouts"][0]["id"]}"' in sec
    assert "MOD=false" in sec and "e.data.kldvModifiable" in sec and "kldvRetirer" in sec and "#DC2626" in sec
    page = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html"), encoding="utf-8").read()
    assert 'id="dbAjouts"' not in page                         # plus de liste au-dessus du tableau
    assert "e.data.kldvRetirer" in page
    # les lecteurs reçoivent seulement kldvApp (calcul à la demande), pas kldvModifiable
    assert "postMessage(!CLOUD||state.me?.role!=='viewer'?{kldvModifiable:true}:{kldvApp:true},'*')" in page


def _ventes_datees():
    return pd.DataFrame({"date_vente": ["2025-01-10", "2025-01-20", "2025-02-05", "2025-02-25", "2025-03-03", "2025-03-30"],
                         "zone": ["Nord", "Sud", "Nord", "Sud", "Nord", "Sud"], "montant": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0]})


def test_variante_a_la_demande_filtres_et_periode(env):
    """Combinaison non précalculée ou période : le serveur recalcule le graphique ajouté sur les seules lignes retenues."""
    server, chemin, question, _ = env
    _ventes_datees().to_csv(chemin, index=False)
    server.do_dashboard(chemin, "Ventes")
    s = server.ajouter_au_tableau(question()["graphique_id"])
    sid, aid = s["id"], s["ajouts"][0]["id"]
    r = server.variante_ajout(sid, aid, [["zone", "Nord"]], "2025-02", "2025-03")
    assert r["lignes"] == 2 and r["charts"][0]["values"] == [[300.0, 500.0]]
    assert server.variante_ajout(sid, aid, [], "2025-03", None)["charts"][0]["values"] == [[500.0, 600.0]]
    assert server.variante_ajout(sid, aid, [["zone", "Ouest"]], None, None) == {"charts": [], "lignes": 0}
    with pytest.raises(ValueError, match="Période invalide"):
        server.variante_ajout(sid, aid, [], "mars", None)
    with pytest.raises(ValueError, match="Filtre invalide"):
        server.variante_ajout(sid, aid, [["inconnue", "x"]], None, None)
    with pytest.raises(KeyError):
        server.variante_ajout(sid, "autre", [], None, None)


def test_page_filtre_de_periode_et_suivi(env):
    server, chemin, question, _ = env
    _ventes_datees().to_csv(chemin, index=False)
    server.do_dashboard(chemin, "Ventes")
    html = server.ajouter_au_tableau(question()["graphique_id"])["html"]
    # le tableau de bord a son filtre « Du … Au … », appliqué à toutes ses lignes
    assert "function boitePeriode()" in html and "return inPer(r)&&FCOLS.every" in html
    sec = _section(html)
    # graphiques ajoutés : coupe des courbes dans le temps, série gardée, calcul à la demande
    assert "function rogner(" in sec and "function garder(" in sec and "kldvVariante" in sec and "window.PER" in sec


def test_couleurs_demandees(env):
    server = env[0]
    assert server._couleurs(["rouge", "#1d7a4c", "bleu foncé", "chartreuse"]) == ["#DC2626", "#1D7A4C", "#1E3A8A", None]
    assert server._couleurs("vert") == ["#16A34A"] and server._couleurs(["inconnue"]) is None and server._couleurs(None) is None
    c = server._chart_propre({"type": "bar", "labels": ["F", "M"], "values": [[1, 2]], "colors": ["rose", "bleu"]})
    assert c["colors"] == ["#DB2777", "#2563EB"]
    assert "colors" not in server._chart_propre({"type": "bar", "labels": ["F"], "values": [[1]], "colors": ["???"]})
    assert "COULEURS : si l'utilisateur en demande" in server.CHAT_METHODE or "COULEURS : si l'utilisateur en demande" in open(
        server.__file__, encoding="utf-8").read()
