# -*- coding: utf-8 -*-
"""Surveillance : incidents relevés sans donnée, alertes regroupées et espacées, bilan de santé (sans réseau)."""
import pytest

from _harness import reset_app_modules


@pytest.fixture
def S(tmp_path, monkeypatch):
    monkeypatch.setenv("KLDV_SURVEILLANCE_DIR", str(tmp_path))
    monkeypatch.setenv("KLDV_ALERTES", "admin@exemple.ci")
    reset_app_modules()
    import surveillance
    surveillance.envois = []
    surveillance.facteur = lambda dest, sujet, corps, attachment=None: surveillance.envois.append((dest, sujet, corps))
    return surveillance


def _boom(message):
    try:
        raise KeyError(message)
    except KeyError as e:
        return e


def test_erreur_interne_sans_valeur_du_fichier(S):
    S.erreur_interne("a1b2c3", "POST", "/api/chat", _boom("Aya Kouassi 07 45 12 33"))
    inc = S.bilan()["incidents"][0]
    assert inc["genre"] == "erreur" and inc["ref"] == "a1b2c3"
    assert "KeyError" in inc["resume"] and "/api/chat" in inc["resume"] and "test_surveillance.py" in inc["resume"]
    assert "Aya" not in inc["resume"] and "07 45" not in inc["resume"]


def test_jeton_de_partage_jamais_dans_une_alerte(S):
    S.erreur_interne("r", "GET", "/t/jeton-secret-123", _boom("x"))
    S.erreur_interne("r", "GET", "/download/jeton-secret-456", _boom("x"))
    assert not any("jeton-secret" in i["resume"] for i in S.bilan()["incidents"])


def test_alerte_tout_de_suite_puis_resume_espace(S, monkeypatch):
    S.signaler("envoi", "Envoi refusé par Brevo (401)", espace="org1")
    r = S.alerter(envoyer=S.facteur)
    assert r["envoye"] and S.envois[0][0] == ["admin@exemple.ci"] and "Brevo (401)" in S.envois[0][2]
    for _ in range(3):
        S.signaler("envoi", "Envoi refusé par Brevo (401)", espace="org2")
    assert S.alerter(envoyer=S.facteur)["envoye"] is False  # moins de 30 minutes après le premier
    monkeypatch.setitem(S._etat, "derniere_alerte", S._etat["derniere_alerte"] - S.INTERVALLE_ALERTES - 1)
    assert S.alerter(envoyer=S.facteur)["envoye"]
    corps = S.envois[1][2]
    assert "3 × E-mail non envoyé" in corps and "org2" in corps
    assert S.alerter(envoyer=S.facteur)["raison"] == "rien de nouveau"


def test_alerte_ratee_reste_en_attente(S):
    S.signaler("ia", "DeepSeek refuse le compte (HTTP 402)")

    def panne(*a, **k):
        raise RuntimeError("Brevo injoignable")
    assert S.alerter(envoyer=panne)["envoye"] is False
    assert S.alerter(envoyer=S.facteur)["envoye"] and "HTTP 402" in S.envois[0][2]


def test_avalanche_d_erreurs_differentes_bornee(S):
    for i in range(S.MAX_GROUPES + 30):
        S.signaler("erreur", f"GET /api/x{i} : KeyError")
    assert len(S._en_attente) == S.MAX_GROUPES + 1
    S.alerter(envoyer=S.facteur)
    assert "Autres incidents" in S.envois[0][2]


def test_sans_destinataire(S, monkeypatch):
    monkeypatch.setenv("KLDV_ALERTES", "")
    S.signaler("erreur", "GET /api/x : KeyError")
    assert "aucun destinataire" in S.alerter(envoyer=S.facteur)["raison"]


def test_controle_qui_tombe_puis_revient(S):
    etat = {"ok": False}
    S.controle("Essai", lambda: (etat["ok"], "détail"))
    S._suivre(S.bilan())
    S._suivre(S.bilan())  # toujours en panne : un seul incident
    assert [i["resume"] for i in S.bilan()["incidents"] if i["resume"].startswith("Essai")] == ["Essai : détail"]
    etat["ok"] = True
    S._suivre(S.bilan())
    assert [r for r in S._retablis if r.startswith("Essai")] == ["Essai : de nouveau bon (détail)"]
    S.alerter(envoyer=S.facteur)
    assert "Revenu à la normale" in S.envois[0][2]


def test_bilan_base_disque_boucles(S, monkeypatch, tmp_path):
    import cloud
    import tenancy as T
    monkeypatch.setattr(cloud, "DATABASE_URL", "postgres://x")
    monkeypatch.setattr(cloud, "q", lambda sql, *a, **k: [{"?column?": 1}])
    monkeypatch.setattr(T, "DATA_DIR", str(tmp_path))
    c = {x["nom"]: x for x in S.bilan()["controles"]}
    assert c["Base de données"]["ok"] and c["Disque"]["ok"] and "Go libres" in c["Disque"]["detail"]
    assert c["Boucles d'envoi"]["ok"] is None  # service tout juste démarré

    def injoignable(*a, **k):
        raise T.Unavailable("La base de données est momentanément injoignable.")
    monkeypatch.setattr(cloud, "q", injoignable)
    monkeypatch.setitem(S._etat, "demarre", 0)
    S.battement("planifications")
    b = S.bilan()
    c = {x["nom"]: x for x in b["controles"]}
    assert b["ok"] is False and c["Base de données"]["ok"] is False and "injoignable" in c["Base de données"]["detail"]
    assert c["Boucles d'envoi"]["ok"] is False and "tableaux (aucun passage)" in c["Boucles d'envoi"]["detail"]
    assert "planifications" not in c["Boucles d'envoi"]["detail"]


def test_memoire_sans_le_cache_de_fichiers(S, monkeypatch):
    mo = 2**20
    monkeypatch.setattr(S.os.path, "exists", lambda p: p.startswith("/sys/fs/cgroup/memory."))
    monkeypatch.setattr(S, "_lire_entier", lambda p: 500 * mo if p.endswith("current") else 512 * mo)
    monkeypatch.setattr(S, "_cache_inactif", lambda stat, cle: 300 * mo)
    assert S._memoire() == (True, "200 Mo utilisés sur 512 Mo (39%)")
    monkeypatch.setattr(S, "_cache_inactif", lambda stat, cle: 0)
    assert S._memoire()[0] is False


def test_envoi_rate_note_par_cloud(S, monkeypatch):
    import cloud
    monkeypatch.setattr(cloud, "BREVO_API_KEY", "")
    with pytest.raises(RuntimeError):
        cloud.send_mail(["x@exemple.ci"], "s", "b")
    assert S.bilan()["incidents"][0]["genre"] == "envoi"
    monkeypatch.setattr(cloud, "envoyer_brevo", lambda *a, **k: None)
    cloud.send_mail(["x@exemple.ci"], "s", "b")
    monkeypatch.setattr(cloud, "BREVO_API_KEY", "k")
    monkeypatch.setattr(cloud, "MAIL_FROM", "a@exemple.ci")
    assert next(c for c in S.bilan()["controles"] if c["nom"] == "E-mail")["ok"]


def test_ia_compte_refuse_et_coupe_circuit(monkeypatch, tmp_path):
    monkeypatch.setenv("KLDV_SURVEILLANCE_DIR", str(tmp_path))
    reset_app_modules()
    import server
    server._ia_resultat(False, "DeepSeek refuse le compte (HTTP 402) : solde épuisé ou clé invalide")
    for _ in range(server.IA_SEUIL):
        server._ia_resultat(False)
    resumes = [i["resume"] for i in server.surveillance.bilan()["incidents"] if i["genre"] == "ia"]
    assert any("HTTP 402" in r for r in resumes) and any("mise en pause" in r for r in resumes)
    monkeypatch.setattr(server, "_llm_config", lambda: {"api_key": "x", "model": "m"})
    ia = next(c for c in server.surveillance.bilan()["controles"] if c["nom"] == "Intelligence artificielle")
    assert ia["ok"] is False and "en pause" in ia["detail"]


def test_redemarrage_sans_nouvelle_version(S, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc")
    S._redemarrage()
    assert not S.bilan()["incidents"]          # premier démarrage de cette version
    S._redemarrage()
    assert "sans nouvelle version" in S.bilan()["incidents"][0]["resume"]
    monkeypatch.setenv("RENDER_GIT_COMMIT", "def")
    S._incidents.clear()
    S._redemarrage()
    assert not S.bilan()["incidents"]          # déploiement : rien à signaler


def test_incidents_gardes_apres_redemarrage(S, monkeypatch, tmp_path):
    S.signaler("erreur", "GET /api/x : KeyError", "r1")
    reset_app_modules()
    import surveillance
    assert surveillance.bilan()["incidents"][0]["ref"] == "r1"
