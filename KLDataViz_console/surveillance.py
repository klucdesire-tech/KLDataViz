# -*- coding: utf-8 -*-
"""Surveillance du service en ligne : incidents relevés, bilan de santé, alertes aux administrateurs.

- Incidents : erreur interne (500), tâche de fond plantée, e-mail non envoyé, IA en panne, contrôle de santé en échec,
  redémarrage sans nouvelle version. Gardés en mémoire et dans <DATA_DIR>/surveillance.jsonl (borné). Pour une erreur
  interne, seuls le type, l'endroit du code et la référence sont gardés : le message d'une exception peut citer une
  valeur d'un fichier.
- Bilan : base, disque, mémoire, IA, e-mail, boucles d'envoi. Lu par l'administration. /healthz reste une simple preuve
  de vie : si elle échouait quand la base tombe, Render redémarrerait le service en boucle sans rien réparer.
- Alertes : un e-mail aux administrateurs (KLDV_ALERTES, sinon ADMIN_EMAILS) dans la minute qui suit le premier
  incident, puis au plus un résumé toutes les 30 minutes tant qu'il en arrive de nouveaux, et un message quand un
  contrôle redevient bon. Si le service entier est arrêté, il ne peut rien envoyer : une surveillance extérieure de
  /healthz reste nécessaire.
"""
import collections
import datetime as dt
import json
import os
import shutil
import sys
import threading
import time
import traceback

import cloud
import tenancy as T

GENRES = {"erreur": "Erreur interne", "tache": "Tâche de fond en échec", "envoi": "E-mail non envoyé",
          "ia": "Intelligence artificielle", "sante": "Contrôle de santé"}
INTERVALLE_ALERTES = 30 * 60   # secondes entre deux résumés
INTERVALLE_BILAN = 5 * 60
# Âge maximal du dernier passage : elles repassent toutes les 30 à 60 s, mais un rapport ou une actualisation peut
# durer plusieurs minutes.
BOUCLES = {"planifications": 20 * 60, "tableaux": 20 * 60}
DISQUE_MIN = 0.05              # part libre minimale du disque persistant
MEMOIRE_MAX = 0.90
MAX_INCIDENTS = 300
MAX_LIGNES = 2000
MAX_GROUPES = 40               # incidents distincts détaillés dans un résumé

_lock = threading.RLock()
_incidents = collections.deque(maxlen=MAX_INCIDENTS)
_en_attente = {}               # (genre, résumé) -> {"n", "refs", "premier", "dernier"} : pas encore envoyé
_retablis = []
_battements = {}
_controles = {}                # nom -> fonction () -> (ok, détail) ; branchées par server.py
_etat = {"derniere_alerte": 0.0, "ko": {}, "charge": False, "demarre": time.time(),
         "ia_ok": None, "ia_echec": None, "envoi_ok": None, "envoi_echec": None}


def _dossier():
    d = os.environ.get("KLDV_SURVEILLANCE_DIR") or (T.DATA_DIR if T.CLOUD else None)
    if d:
        os.makedirs(d, exist_ok=True)
    return d


def _maintenant():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _charger():
    """Les incidents d'avant un redémarrage restent visibles dans l'administration."""
    if _etat["charge"]:
        return
    _etat["charge"] = True
    d = _dossier()
    p = d and os.path.join(d, "surveillance.jsonl")
    if p and os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                for ligne in f.readlines()[-MAX_INCIDENTS:]:
                    _incidents.append(json.loads(ligne))
        except Exception:
            pass


def _ecrire(inc):
    d = _dossier()
    if not d:
        return
    p = os.path.join(d, "surveillance.jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(inc, ensure_ascii=False) + "\n")
    if os.path.getsize(p) > MAX_LIGNES * 300:
        with open(p, encoding="utf-8") as f:
            garde = f.readlines()[-MAX_LIGNES // 2:]
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(garde)
        os.replace(p + ".tmp", p)


def signaler(genre: str, resume: str, ref: str = "", espace: str = "") -> None:
    """Relève un incident ; ne lève jamais (la surveillance ne doit pas casser ce qu'elle surveille)."""
    try:
        resume = " ".join(str(resume or "").split())[:300]
        if not espace:
            try:
                espace = T.current().org_id or ""
            except Exception:
                espace = ""
        inc = {"date": _maintenant(), "genre": genre, "resume": resume, "ref": ref or "", "espace": str(espace or "")}
        with _lock:
            _charger()
            _incidents.append(inc)
            cle = (genre, resume)
            if cle not in _en_attente and len(_en_attente) >= MAX_GROUPES:  # une avalanche d'erreurs différentes
                cle = ("sante", "Autres incidents : voir la page Admin → Santé du service")
            g = _en_attente.setdefault(cle, {"n": 0, "refs": [], "premier": inc["date"], "espaces": set()})
            g["n"] += 1
            g["dernier"] = inc["date"]
            if ref and len(g["refs"]) < 5:
                g["refs"].append(ref)
            if inc["espace"]:
                g["espaces"].add(inc["espace"])
            _ecrire(inc)
        sys.stderr.write(f"[surveillance] {GENRES.get(genre, genre)} : {resume}{' (réf. ' + ref + ')' if ref else ''}\n")
    except Exception:
        pass


def _lieu(exc: BaseException) -> str:
    """Dernier endroit de notre code traversé par l'exception : « server.py:1234 dans _route »."""
    base = os.path.dirname(os.path.abspath(__file__))
    lieu = ""
    for fr in traceback.extract_tb(exc.__traceback__):
        if os.path.abspath(fr.filename).startswith(base):
            lieu = f"{os.path.basename(fr.filename)}:{fr.lineno} dans {fr.name}"
    return lieu


def _chemin_sans_donnee(path: str) -> str:
    """Les liens de partage et de téléchargement portent un jeton : il ne part pas dans une alerte."""
    for prefixe in ("/t/", "/download/"):
        if path.startswith(prefixe):
            return prefixe + "…"
    return path[:120]


def erreur_interne(ref: str, method: str, path: str, exc: BaseException, genre: str = "erreur", espace: str = "") -> None:
    lieu = _lieu(exc)
    signaler(genre, f"{method} {_chemin_sans_donnee(path)} : {type(exc).__name__}" + (f" ({lieu})" if lieu else ""), ref, espace)


def battement(nom: str) -> None:
    """Une boucle de fond signale qu'elle tourne encore."""
    _battements[nom] = time.time()


def controle(nom: str, fn) -> None:
    _controles[nom] = fn


def ia(ok: bool, motif: str = "") -> None:
    with _lock:
        _etat["ia_ok" if ok else "ia_echec"] = (_maintenant(), motif)
    if not ok and motif:
        signaler("ia", motif)


def envoi(ok: bool, motif: str = "", quoi: str = "") -> None:
    with _lock:
        _etat["envoi_ok" if ok else "envoi_echec"] = (_maintenant(), motif)
    if not ok:
        signaler("envoi", (quoi + " : " if quoi else "") + str(motif or "échec"))


# ---------------------------------------------------------------- bilan
def _base():
    if not cloud.DATABASE_URL:
        return False, "DATABASE_URL non renseignée."
    d = time.time()
    cloud.q("select 1")
    return True, f"répond en {round((time.time() - d) * 1000)} ms"


def _disque():
    d = T.DATA_DIR
    if not os.path.isdir(d):
        return False, f"{d} introuvable : disque persistant non monté ?"
    sonde = os.path.join(d, ".sonde_surveillance")
    with open(sonde, "w", encoding="utf-8") as f:
        f.write("ok")
    os.remove(sonde)
    u = shutil.disk_usage(d)
    libre = u.free / u.total if u.total else 1
    detail = f"{u.free / 2**30:.1f} Go libres sur {u.total / 2**30:.1f} Go ({libre:.0%})"
    return libre >= DISQUE_MIN, detail


def _lire_entier(p):
    with open(p, encoding="utf-8") as f:
        v = f.read().strip()
    return None if v == "max" else int(v)


def _cache_inactif(stat, cle):
    try:
        with open(stat, encoding="utf-8") as f:
            for ligne in f:
                nom, _, v = ligne.partition(" ")
                if nom == cle:
                    return int(v)
    except (OSError, ValueError):
        pass
    return 0


def _memoire():
    """Mémoire du conteneur (Linux, cgroup) ; ignorée ailleurs. Le cache de fichiers inactif est retiré, comme le
    fait Docker : le système le libère de lui-même, il ne menace pas le service."""
    for usage, limite, stat, cle in (
            ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.stat", "inactive_file"),
            ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.limit_in_bytes",
             "/sys/fs/cgroup/memory/memory.stat", "total_inactive_file")):
        if os.path.exists(usage) and os.path.exists(limite):
            u, lim = _lire_entier(usage), _lire_entier(limite)
            u = max(0, u - _cache_inactif(stat, cle))
            if not lim or lim > 2**50:
                return True, f"{u / 2**20:.0f} Mo utilisés (pas de limite lue)"
            return u / lim < MEMOIRE_MAX, f"{u / 2**20:.0f} Mo utilisés sur {lim / 2**20:.0f} Mo ({u / lim:.0%})"
    return None, "non mesurée sur ce système"


def _envoi():
    if not cloud.mail_ready():
        return False, "Envoi non configuré (BREVO_API_KEY, MAIL_FROM)."
    ok, echec = _etat["envoi_ok"], _etat["envoi_echec"]
    if echec and (not ok or echec[0] > ok[0]):
        return False, f"dernier envoi raté le {echec[0][:16].replace('T', ' à ')} : {echec[1]}"[:300]
    return True, f"dernier envoi réussi le {ok[0][:16].replace('T', ' à ')}" if ok else "configuré, aucun envoi depuis le démarrage"


def _boucles():
    if time.time() - _etat["demarre"] < 3 * 60:
        return None, "service démarré il y a moins de 3 minutes"
    vieilles = [f"{nom} (dernier passage il y a {round((time.time() - _battements[nom]) / 60)} min)" if nom in _battements
                else f"{nom} (aucun passage)" for nom, age in BOUCLES.items()
                if time.time() - _battements.get(nom, 0) > age]
    return (False, "arrêtées : " + ", ".join(vieilles)) if vieilles else (True, "rapports planifiés et tableaux de bord tournent")


def bilan() -> dict:
    """Chaque contrôle : ok (True, False, ou None s'il ne s'applique pas), détail, durée."""
    out = []
    for nom, fn in [("Base de données", _base), ("Disque", _disque), ("Mémoire", _memoire), ("E-mail", _envoi),
                    ("Boucles d'envoi", _boucles), *_controles.items()]:
        d = time.time()
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__} : {str(exc).splitlines()[0][:200] if str(exc) else ''}"
        out.append({"nom": nom, "ok": ok, "detail": detail, "ms": round((time.time() - d) * 1000)})
    with _lock:
        _charger()
        recents = list(_incidents)[-100:][::-1]
    return {"ok": all(c["ok"] is not False for c in out), "controles": out, "incidents": recents,
            "destinataires": len(destinataires()), "demarre": dt.datetime.fromtimestamp(_etat["demarre"], dt.timezone.utc).isoformat(timespec="seconds")}


def _suivre(b: dict) -> None:
    """Un contrôle qui tombe devient un incident ; un contrôle qui revient est annoncé dans l'alerte suivante."""
    for c in b["controles"]:
        avant = _etat["ko"].get(c["nom"])
        if c["ok"] is False and not avant:
            _etat["ko"][c["nom"]] = c["detail"]
            signaler("sante", f"{c['nom']} : {c['detail']}", espace="-")
        elif c["ok"] is not False and avant:
            del _etat["ko"][c["nom"]]
            with _lock:
                _retablis.append(f"{c['nom']} : de nouveau bon ({c['detail']})")


# ---------------------------------------------------------------- alertes
def destinataires() -> list:
    brut = os.environ.get("KLDV_ALERTES")
    if brut is None:
        return sorted(cloud.ADMIN_EMAILS)
    return [e.strip().lower() for e in brut.split(",") if "@" in e]


def _texte(groupes, retablis, base_url):
    lignes = []
    for (genre, resume), g in sorted(groupes.items(), key=lambda kv: kv[1]["premier"]):
        refs = f" — réf. {', '.join(g['refs'])}" if g["refs"] else ""
        esp = sorted(e for e in g["espaces"] if e and e != "-")
        lignes.append(f"• {g['n']} × {GENRES.get(genre, genre)} : {resume}{refs}"
                      + (f"\n  espaces : {', '.join(esp[:5])}" if esp else "")
                      + f"\n  du {g['premier'][:16].replace('T', ' à ')} au {g['dernier'][:16].replace('T', ' à ')} (UTC)")
    corps = "Bonjour,\n\nLa surveillance de KLDataViz a relevé :\n\n" + "\n".join(lignes)
    if retablis:
        corps += "\n\nRevenu à la normale :\n" + "\n".join("• " + r for r in retablis)
    corps += ("\n\nDétail et bilan de santé : " + (base_url.rstrip("/") + "/admin" if base_url else "page Admin → Santé du service")
              + "\nLes références se retrouvent dans les journaux Render.\n\nKLDataViz")
    return corps


def alerter(force=False, envoyer=None) -> dict:
    """Envoie le résumé des incidents en attente si le délai est passé. `envoyer` : pour les tests."""
    with _lock:
        if not _en_attente and not _retablis:
            return {"envoye": False, "raison": "rien de nouveau"}
        if not force and _etat["derniere_alerte"] and time.time() - _etat["derniere_alerte"] < INTERVALLE_ALERTES and _en_attente:
            return {"envoye": False, "raison": "résumé déjà envoyé il y a moins de 30 minutes"}
        groupes, retablis = dict(_en_attente), list(_retablis)
    dest = destinataires()
    if not dest:
        return {"envoye": False, "raison": "aucun destinataire (KLDV_ALERTES ou ADMIN_EMAILS)"}
    n = sum(g["n"] for g in groupes.values())
    sujet = (f"KLDataViz : {n} incident{'s' if n > 1 else ''} à regarder" if groupes else "KLDataViz : service revenu à la normale")
    try:
        (envoyer or cloud.envoyer_brevo)(dest, sujet, _texte(groupes, retablis, cloud.APP_URL))
    except Exception as exc:  # pas de signaler() ici : une panne d'envoi ne doit pas s'alimenter elle-même
        sys.stderr.write(f"[surveillance] alerte non envoyée : {exc}\n")
        return {"envoye": False, "raison": str(exc)[:300]}
    with _lock:
        for k in groupes:
            _en_attente.pop(k, None)
        del _retablis[:len(retablis)]
        _etat["derniere_alerte"] = time.time()
    return {"envoye": True, "destinataires": len(dest), "incidents": n}


def essai() -> dict:
    """Alerte d'essai demandée depuis l'administration : prouve que la chaîne d'envoi fonctionne."""
    dest = destinataires()
    if not dest:
        return {"envoye": False, "raison": "aucun destinataire (KLDV_ALERTES ou ADMIN_EMAILS)"}
    try:
        cloud.send_mail(dest, "KLDataViz : alerte d'essai",
                        "Bonjour,\n\nCeci est une alerte d'essai demandée depuis l'administration. Si vous la lisez, les "
                        "alertes de surveillance vous parviendront.\n\nKLDataViz")
    except Exception as exc:  # déjà noté comme envoi raté par cloud.send_mail
        return {"envoye": False, "raison": str(exc)[:300]}
    return {"envoye": True, "destinataires": len(dest)}


def _redemarrage():
    """Un redémarrage sans nouvelle version trahit un plantage ou une mémoire saturée."""
    d = _dossier()
    if not d:
        return
    p = os.path.join(d, "surveillance_etat.json")
    version = os.environ.get("RENDER_GIT_COMMIT", "")
    try:
        with open(p, encoding="utf-8") as f:
            avant = json.load(f)
    except Exception:
        avant = {}
    if version and avant.get("version") == version:
        signaler("sante", "Service redémarré sans nouvelle version : plantage, mémoire saturée ou redémarrage manuel.",
                 espace="-")
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"version": version, "demarre": _maintenant()}, f)
    except Exception:
        pass


def demarrer():
    def boucle():
        dernier_bilan = 0.0
        while True:
            try:
                if time.time() - dernier_bilan >= INTERVALLE_BILAN:
                    dernier_bilan = time.time()
                    _suivre(bilan())
                alerter()
            except Exception as exc:
                sys.stderr.write(f"[surveillance] {type(exc).__name__} : {exc}\n")
            time.sleep(60)
    _redemarrage()
    threading.Thread(target=boucle, daemon=True).start()
