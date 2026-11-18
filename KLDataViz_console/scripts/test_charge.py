# -*- coding: utf-8 -*-
"""Test de charge de KLDataViz : combien d'utilisateurs simultanés le serveur tient-il ?

Des « utilisateurs simulés » ouvrent les pages et/ou lancent des calculs lourds (analyse + tableau de bord) en même
temps, pendant une durée donnée. Le script mesure, action par action : réussites, refus (429 trop de demandes,
503 serveur occupé), erreurs, temps de réponse (médiane, 95e centile, maximum). Avec --pid (serveur lancé sur la même
machine), il mesure aussi le temps de processeur consommé et la mémoire maximale du serveur, puis en déduit ce que
tiendrait une machine Render « Starter » (0,5 processeur, 512 Mo).

Bibliothèque standard seulement (aucune dépendance à installer), sauf --lignes qui utilise pandas pour fabriquer
un fichier FICTIF plus gros à partir du jeu d'exemple.

Exemples (serveur local, sans IA, données dans un dossier jetable) :
    set KLDV_DATA_DIR=C:\\temp\\kldv_charge & set KLDV_LIMITE_CALCULS=100000 & python server.py
    python scripts/test_charge.py --scenario pages --utilisateurs 200 --duree 60
    python scripts/test_charge.py --scenario lourd --utilisateurs 20 --duree 120 --pid 12345 --lignes 20000

Sur le site en ligne : --oui-production est obligatoire, les calculs lourds demandent un jeton de session
(variable d'environnement KLDV_JETON, jamais en argument) et consomment le quota de l'espace. À faire en heure creuse,
avec un compte de test, et en commençant petit.
"""
import argparse
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

LOCAUX = {"127.0.0.1", "localhost", "::1"}
# Pages ouvertes par un visiteur : la console (« / » en local, « /app » en ligne), la bibliothèque de graphiques et le logo
PAGES_LOCAL = ["/", "/chart.umd.min.js", "/logo.png", "/healthz"]
PAGES_EN_LIGNE = ["/", "/app", "/chart.umd.min.js", "/logo.png", "/healthz"]
STARTER = {"cpu": 0.5, "memoire_mo": 512}  # machine Render « Starter » (render.yaml)


# ---------------------------------------------------------------- appels HTTP

def appel(base, methode, chemin, corps=None, jeton=None, delai=120.0):
    """Un appel : (statut, durée en s, réponse JSON ou None, nature de l'échec). Statut 0 = pas de réponse."""
    donnees = json.dumps(corps).encode("utf-8") if corps is not None else None
    req = urllib.request.Request(base + chemin, data=donnees, method=methode)
    if donnees is not None:
        req.add_header("Content-Type", "application/json")
    if jeton:
        req.add_header("Authorization", "Bearer " + jeton)
    debut = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=delai) as r:
            brut = r.read()
            statut = r.status
    except urllib.error.HTTPError as e:
        e.read()
        return e.code, time.perf_counter() - debut, None, None
    except TimeoutError:
        return 0, time.perf_counter() - debut, None, "délai dépassé"
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        raison = getattr(e, "reason", e)
        nature = "délai dépassé" if "timed out" in str(raison) else "connexion refusée ou coupée"
        return 0, time.perf_counter() - debut, None, nature
    duree = time.perf_counter() - debut
    try:
        return statut, duree, json.loads(brut.decode("utf-8")), None
    except ValueError:
        return statut, duree, None, None


class Mesures:
    def __init__(self):
        self.verrou = threading.Lock()
        self.lignes = []  # (action, statut, durée, nature de l'échec)

    def noter(self, action, statut, duree, nature=None):
        with self.verrou:
            self.lignes.append((action, statut, duree, nature))


# ---------------------------------------------------------------- processus serveur (même machine)

class Processus:
    """Temps de processeur et mémoire d'un processus local, sans dépendance (Windows : API psapi ; Linux : /proc)."""

    def __init__(self, pid):
        self.pid = pid
        self.pic_mo = 0.0
        self._stop = threading.Event()
        self._fil = threading.Thread(target=self._boucle, daemon=True)

    def cpu_s(self):
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            h = k32.OpenProcess(0x1000, False, self.pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return None
            try:
                t = [wintypes.FILETIME() for _ in range(4)]
                if not k32.GetProcessTimes(h, *[ctypes.byref(x) for x in t]):
                    return None
                val = lambda ft: (ft.dwHighDateTime << 32 | ft.dwLowDateTime) / 1e7  # noqa: E731
                return val(t[2]) + val(t[3])  # noyau + utilisateur
            finally:
                k32.CloseHandle(h)
        try:
            with open(f"/proc/{self.pid}/stat") as f:
                champs = f.read().rsplit(")", 1)[1].split()
            return (int(champs[11]) + int(champs[12])) / os.sysconf("SC_CLK_TCK")
        except (OSError, IndexError, ValueError):
            return None

    def memoire_mo(self):
        """(mémoire actuelle, pic depuis le démarrage du processus si le système le donne), en Mo."""
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            h = k32.OpenProcess(0x1000 | 0x0010, False, self.pid)  # + PROCESS_VM_READ
            if not h:
                return None, None
            try:
                c = PMC()
                c.cb = ctypes.sizeof(PMC)
                if not psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                    return None, None
                return c.WorkingSetSize / 2**20, c.PeakWorkingSetSize / 2**20
            finally:
                k32.CloseHandle(h)
        try:
            vals = {}
            with open(f"/proc/{self.pid}/status") as f:
                for ligne in f:
                    if ligne.startswith(("VmRSS:", "VmHWM:")):
                        vals[ligne.split(":")[0]] = int(ligne.split()[1]) / 1024
            return vals.get("VmRSS"), vals.get("VmHWM")
        except OSError:
            return None, None

    def _boucle(self):
        while not self._stop.is_set():
            actuelle, _ = self.memoire_mo()
            if actuelle:
                self.pic_mo = max(self.pic_mo, actuelle)
            self._stop.wait(0.25)

    def demarrer(self):
        self.cpu0 = self.cpu_s()
        self.mem0, _ = self.memoire_mo()
        self._fil.start()

    def arreter(self):
        self._stop.set()
        self._fil.join()
        return self.cpu_s()


# ---------------------------------------------------------------- utilisateurs simulés

def fichier_fictif(lignes, dossier):
    """Jeu d'exemple FICTIF agrandi : les 12 centres recopiés sous d'autres noms jusqu'à environ `lignes` lignes."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import pandas as pd
    import exemple
    base = exemple.generer()
    copies = max(1, -(-lignes // len(base)))
    morceaux = []
    for k in range(copies):
        m = base.copy()
        if k:
            m["centre"] = m["centre"] + f" (copie {k})"
            m["district"] = m["district"] + f" {k % 7}"
        morceaux.append(m)
    df = pd.concat(morceaux, ignore_index=True).head(lignes)
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, f"charge_fictif_{len(df)}_lignes.csv")
    df.to_csv(chemin, index=False, encoding="utf-8")
    return chemin, len(df)


def utilisateur(n, args, base, jeton, fin, mesures, fichier, lourd):
    rnd = random.Random(n)
    chemin = fichier
    if lourd and not chemin:
        statut, duree, rep, nature = appel(base, "POST", "/api/exemple", {}, jeton, args.delai)
        mesures.noter("préparer l'exemple", statut, duree, nature)
        chemin = (rep or {}).get("path")
        if not chemin:
            return
    while time.time() < fin:
        if lourd:
            for action, route in (("analyse", "/api/analyze"), ("tableau de bord", "/api/dashboard")):
                if time.time() >= fin:
                    break
                statut, duree, rep, nature = appel(base, "POST", route, {"file_path": chemin}, jeton, args.delai)
                if statut == 200 and isinstance(rep, dict) and rep.get("error"):
                    nature = "erreur métier : " + str(rep["error"])[:80]
                    statut = 299  # réponse 200 qui porte une erreur : comptée à part
                mesures.noter(action, statut, duree, nature)
        else:
            route = rnd.choice(args.pages)
            statut, duree, _, nature = appel(base, "GET", route, None, jeton, args.delai)
            mesures.noter("page " + route, statut, duree, nature)
        time.sleep(rnd.uniform(args.pause * 0.5, args.pause * 1.5))


# ---------------------------------------------------------------- rapport

def centile(valeurs, p):
    if not valeurs:
        return None
    v = sorted(valeurs)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def resumer(mesures, duree_reelle):
    actions = {}
    for action, statut, duree, nature in mesures.lignes:
        a = actions.setdefault(action, {"total": 0, "réussies": 0, "429 trop de demandes": 0, "503 occupé": 0,
                                        "autres erreurs HTTP": 0, "erreurs métier": 0, "sans réponse": 0,
                                        "_durees": [], "_natures": {}})
        a["total"] += 1
        if 200 <= statut < 299:
            a["réussies"] += 1
            a["_durees"].append(duree)
        elif statut == 299:
            a["erreurs métier"] += 1
        elif statut == 429:
            a["429 trop de demandes"] += 1
        elif statut == 503:
            a["503 occupé"] += 1
        elif statut == 0:
            a["sans réponse"] += 1
        else:
            a["autres erreurs HTTP"] += 1
        if nature or (statut >= 300 and statut != 299):
            cle = nature or f"HTTP {statut}"
            a["_natures"][cle] = a["_natures"].get(cle, 0) + 1
    for a in actions.values():
        d = a.pop("_durees")
        a["médiane s"] = round(centile(d, 50), 3) if d else None
        a["95e centile s"] = round(centile(d, 95), 3) if d else None
        a["maximum s"] = round(max(d), 3) if d else None
        a["réussies par minute"] = round(a["réussies"] * 60 / duree_reelle, 1) if duree_reelle else None
        a["détail des échecs"] = a.pop("_natures")
    return actions


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8642", help="adresse du serveur (local par défaut)")
    ap.add_argument("--scenario", choices=("pages", "lourd", "mixte"), default="pages",
                    help="pages : ouverture de pages ; lourd : analyse + tableau de bord ; mixte : 80 %% pages, 20 %% lourd")
    ap.add_argument("--utilisateurs", type=int, default=20, help="utilisateurs simulés en même temps")
    ap.add_argument("--duree", type=float, default=60, help="durée du test en secondes")
    ap.add_argument("--montee", type=float, default=10, help="secondes pour faire arriver tous les utilisateurs")
    ap.add_argument("--pause", type=float, default=2.0, help="pause moyenne entre deux actions d'un utilisateur (s)")
    ap.add_argument("--delai", type=float, default=120, help="délai maximal d'une réponse (s)")
    ap.add_argument("--pid", type=int, help="numéro du processus serveur local : mesure processeur et mémoire")
    ap.add_argument("--lignes", type=int, help="calculs lourds sur un fichier FICTIF d'environ ce nombre de lignes (local)")
    ap.add_argument("--json", help="écrit aussi le résultat dans ce fichier")
    ap.add_argument("--oui-production", action="store_true", help="autorise une adresse qui n'est pas locale")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # accents lisibles dans la console Windows
    except (AttributeError, ValueError):
        pass

    base = args.url.rstrip("/")
    hote = urlparse(base).hostname or ""
    local = hote in LOCAUX
    if not local and not args.oui_production:
        ap.error(f"{hote} n'est pas local : ajoutez --oui-production (en heure creuse, avec un compte de test).")
    args.pages = PAGES_LOCAL if local else PAGES_EN_LIGNE
    jeton = os.environ.get("KLDV_JETON") or None
    if not local and args.scenario != "pages" and not jeton:
        ap.error("calculs lourds en ligne : définissez KLDV_JETON (jeton de session d'un compte de test).")
    if args.lignes and not local:
        ap.error("--lignes ne sert qu'en local : le serveur doit pouvoir lire le fichier sur son disque.")

    fichier = None
    if args.lignes:
        dossier = os.path.join(os.environ.get("KLDV_DATA_DIR") or os.getcwd(), "charge")
        fichier, n = fichier_fictif(args.lignes, dossier)
        print(f"Fichier fictif : {n} lignes ({os.path.getsize(fichier) / 2**20:.1f} Mo)")

    statut, _, _, nature = appel(base, "GET", "/healthz", delai=10)
    if statut != 200:
        sys.exit(f"Le serveur ne répond pas sur {base}/healthz ({nature or statut}).")

    proc = Processus(args.pid) if args.pid else None
    if proc:
        proc.demarrer()
    mesures = Mesures()
    debut = time.time()
    fin = debut + args.duree
    fils = []
    for i in range(args.utilisateurs):
        lourd = args.scenario == "lourd" or (args.scenario == "mixte" and i % 5 == 4)
        t = threading.Thread(target=utilisateur, args=(i, args, base, jeton, fin, mesures, fichier, lourd), daemon=True)
        fils.append(t)
        t.start()
        if args.utilisateurs > 1:
            time.sleep(min(args.montee, args.duree) / args.utilisateurs)
    for t in fils:
        t.join(timeout=max(0.0, fin - time.time()) + args.delai + 5)
    duree_reelle = time.time() - debut

    rapport = {"adresse": base, "scénario": args.scenario, "utilisateurs": args.utilisateurs,
               "durée s": round(duree_reelle, 1), "actions": resumer(mesures, duree_reelle)}
    if proc:
        cpu1 = proc.arreter()
        _, pic_total = proc.memoire_mo()
        srv = {"processeur consommé s": round(cpu1 - proc.cpu0, 1) if cpu1 is not None and proc.cpu0 is not None else None,
               "mémoire au départ Mo": round(proc.mem0 or 0), "mémoire maximale pendant le test Mo": round(proc.pic_mo),
               "pic depuis le lancement du serveur Mo": round(pic_total) if pic_total else None}
        lourdes = sum(a["réussies"] for nom, a in rapport["actions"].items() if nom in ("analyse", "tableau de bord"))
        pages = sum(a["réussies"] for nom, a in rapport["actions"].items() if nom.startswith("page "))
        if srv["processeur consommé s"] and pages and args.scenario == "pages":
            par_page = srv["processeur consommé s"] / pages
            srv["processeur par requête ms"] = round(par_page * 1000, 1)  # page, script, logo ou contrôle
            srv["Starter : requêtes par seconde au mieux"] = round(STARTER["cpu"] / par_page, 1)
        if srv["processeur consommé s"] and lourdes and args.scenario == "lourd":
            par_calcul = srv["processeur consommé s"] / lourdes
            srv["processeur par calcul lourd s"] = round(par_calcul, 2)
            srv["Starter : calculs lourds par minute au mieux"] = round(60 * STARTER["cpu"] / par_calcul, 1)
        srv["Starter : mémoire dépassée"] = bool(proc.pic_mo and proc.pic_mo > STARTER["memoire_mo"])
        rapport["serveur"] = srv

    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rapport, f, ensure_ascii=False, indent=2)
    return rapport


if __name__ == "__main__":
    main()
