# -*- coding: utf-8 -*-
"""
Exécution isolée du code pandas écrit par l'IA (chat).

Deux barrières :
1. `validate()` : contrôle de l'arbre syntaxique — pas d'import, pas d'attribut « _… »,
   pas de fonctions d'entrée/sortie (open, read_*, to_csv…), pas d'introspection.
2. `run()` : exécution dans un sous-processus séparé (sandbox_runner.py), sans variables
   d'environnement (donc sans clés ni mots de passe), avec des fonctions intégrées
   réduites, un délai maximal et, sous Linux, une limite de mémoire et de CPU.
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_runner.py")
# En ligne (Render Starter : une demi-CPU), relancer Python + pandas + NumPy + PyArrow pour chaque calcul prend
# à lui seul plusieurs secondes ; 20 s ne suffisaient plus (calculs simples interrompus). En local, 20 s.
_CLOUD = os.environ.get("KLDV_MODE", "").strip().lower() == "cloud"
TIMEOUT_S = int(os.environ.get("KLDV_SANDBOX_TIMEOUT", "60" if _CLOUD else "20"))
# En ligne (Render Starter : 512 Mo pour tout le service), un calcul ne doit jamais pouvoir faire tomber le serveur
# Limite d'espace VIRTUEL (RLIMIT_AS) : NumPy/OpenBLAS en réservent à eux seuls plusieurs centaines de Mo sans les
# utiliser ; trop bas, tous les calculs échoueraient. La vraie protection de la mémoire du service est la garde
# « 2 traitements lourds au plus » du serveur.
MEMORY_MB = int(os.environ.get("KLDV_SANDBOX_MEMORY_MB",
                               "1024" if os.environ.get("KLDV_MODE", "").strip().lower() == "cloud" else "1536"))

BANNED_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "getattr", "setattr", "delattr", "hasattr",
    "globals", "locals", "vars", "dir", "input", "breakpoint", "help", "memoryview", "type",
    "object", "super", "classmethod", "staticmethod", "property", "id", "exit", "quit",
    "copyright", "credits", "license", "os", "sys", "subprocess", "builtins", "importlib",
}
# Attributs interdits : entrées/sorties, introspection, sous-modules à risque.
BANNED_ATTRS = {
    "eval", "load", "loads", "save", "savez", "savez_compressed", "savetxt", "loadtxt", "genfromtxt",
    "fromfile", "tofile", "memmap", "fromregex", "dump", "dumps", "io", "lib", "ctypeslib", "f2py",
    "distutils", "testing", "os", "sys", "system", "popen", "format", "format_map", "plot", "plotting",
    "style", "api", "core", "compat", "util", "errors", "options", "set_option", "get_option",
    "pipe", "ExcelWriter", "HDFStore", "describe_option", "ExcelFile", "DataSource",
    # chaînes de sous-modules menant aux fonctions intégrées ou au chargement de modules
    "builtins", "importlib", "inspect", "overrides", "getattr", "setattr", "delattr", "modules",
    "char", "ctypes", "typing", "functools", "operator", "warnings", "linecache", "pathlib", "shutil",
    "subprocess", "socket", "pickle", "marshal",
}
# Méthodes appelables par leur NOM en texte (df.apply('to_csv', …), df.agg('eval', …)) : refusées aussi
# quand elles apparaissent comme chaîne de caractères.
BANNED_STRINGS = {  # seulement des noms de MÉTHODES (les valeurs de données ordinaires restent permises)
    "eval", "load", "loads", "save", "savez", "savez_compressed", "savetxt", "loadtxt", "genfromtxt", "fromfile",
    "tofile", "memmap", "fromregex", "dump", "dumps", "system", "popen", "pipe", "ExcelFile", "ExcelWriter",
    "HDFStore", "DataSource", "open", "exec", "compile", "getattr", "setattr", "delattr", "globals", "locals", "vars",
}
SAFE_TO = {"to_dict", "to_list", "to_numpy", "to_frame", "to_period", "to_timestamp", "to_datetime",
           "to_numeric", "to_string", "to_records", "to_flat_index", "to_series", "to_timedelta",
           "to_pydatetime", "to_offset", "tolist"}


class UnsafeCode(ValueError):
    pass


# Modules déjà fournis au calcul, sous ce nom : leurs imports sont réécrits en simples
# affectations au lieu d'être refusés (l'IA écrit souvent « import numpy as np »).
PROVIDED_MODULES = {"pandas": "pd", "numpy": "np", "math": "math", "scipy.stats": "stats"}


def strip_provided_imports(code: str) -> str:
    """Remplace `import numpy as np`, `from math import sqrt`… par des affectations vers les
    modules déjà fournis. Tout autre import est laissé tel quel (et sera refusé par validate)."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return code  # validate() renverra l'erreur de syntaxe
    changed = False
    body = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "scipy" and node.level == 0 \
                and all(a.name == "stats" for a in node.names):  # from scipy import stats
            body += [ast.parse(f"{a.asname or 'stats'} = stats").body[0] for a in node.names if a.asname]
            changed = True
            continue
        if isinstance(node, ast.Import) and all(a.name in PROVIDED_MODULES and (a.asname or "." not in a.name)
                                                for a in node.names):
            for a in node.names:
                name, provided = a.asname or a.name, PROVIDED_MODULES[a.name]
                if name != provided:
                    body.append(ast.parse(f"{name} = {provided}").body[0])
            changed = True
            continue
        if (isinstance(node, ast.ImportFrom) and node.module in PROVIDED_MODULES and node.level == 0
                and all(a.name != "*" and not a.name.startswith("_") for a in node.names)):
            for a in node.names:
                body.append(ast.parse(f"{a.asname or a.name} = {PROVIDED_MODULES[node.module]}.{a.name}").body[0])
            changed = True
            continue
        body.append(node)
    if not changed:
        return code
    tree.body = body
    return ast.unparse(tree)


def validate(code: str) -> None:
    """Lève UnsafeCode si le code utilise une construction interdite."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise UnsafeCode(f"code invalide : {exc.msg}")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise UnsafeCode("import interdit")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise UnsafeCode("global/nonlocal interdit")
        if isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("_")):
            raise UnsafeCode(f"nom interdit : {node.id}")
        if isinstance(node, ast.Attribute):
            a = node.attr
            if a.startswith("_") or a in BANNED_ATTRS:
                raise UnsafeCode(f"attribut interdit : {a}")
            if (a.startswith("read_") or (a.startswith("to_") and a not in SAFE_TO)):
                raise UnsafeCode(f"lecture/écriture de fichier interdite : {a}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value.strip()
            if "__" in v:
                raise UnsafeCode("chaîne suspecte")
            if v in BANNED_STRINGS or (re.fullmatch(r"(to|read)_\w+", v) and v not in SAFE_TO):
                raise UnsafeCode(f"méthode interdite : {v}")


# Bibliothèques de calcul limitées à un fil : sinon OpenBLAS/pyarrow tentent d'en créer
# plusieurs au démarrage, échouent sous la limite RLIMIT_NPROC et interrompent le calcul
# (« échec du calcul : KeyboardInterrupt » vu en ligne).
THREAD_ENV = {k: "1" for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
                               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "ARROW_IO_THREADS")}
NPROC_MARGIN = 8  # fils que le calcul peut encore créer (pyarrow, numpy…)
THREAD_FAILURE = re.compile(r"KeyboardInterrupt|pthread_create|can't start new thread|Resource temporarily unavailable")


def _user_tasks():
    """Fils d'exécution déjà ouverts par l'utilisateur courant (RLIMIT_NPROC les compte tous,
    serveur compris). None si /proc n'est pas lisible."""
    uid, total = os.getuid(), 0
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/status") as f:
                    fields = dict(line.split(":", 1) for line in f if ":" in line)
            except OSError:
                continue
            if int(fields.get("Uid", "-1").split()[0]) == uid:
                total += int(fields.get("Threads", "1"))
    except OSError:
        return None
    return total


def _make_limits(nproc):
    def _limits():  # exécuté dans le sous-processus, sous Linux uniquement
        import resource
        mem = MEMORY_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_S, TIMEOUT_S + 1))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        if nproc is not None and hasattr(resource, "RLIMIT_NPROC"):
            # Pas de rafale de processus : seulement une petite marge au-delà de l'existant.
            resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
    return _limits


def run(code: str, datasets: dict) -> dict:
    """Exécute `code` avec `data` = {nom: chemin de fichier parquet/csv}.

    Renvoie {"result": …, "chart": …} ou {"error": "…"}.
    """
    code = strip_provided_imports(code)
    validate(code)
    env = dict(THREAD_ENV, PYTHONIOENCODING="utf-8")  # aucune autre variable : ni clés ni mots de passe
    if os.name == "nt":  # Python a besoin de ces variables pour démarrer sous Windows
        for k in ("SYSTEMROOT", "TEMP", "TMP"):
            if os.environ.get(k):
                env[k] = os.environ[k]
    used = _user_tasks() if os.name != "nt" else None
    nproc = used + NPROC_MARGIN if used is not None else None
    # Interrupteur d'urgence (variable Render) : KLDV_SANDBOX_GARDE=0 désactive le garde-fou d'exécution sans
    # redéployer ; le contrôle du code, l'environnement vide et les limites restent actifs.
    garde = os.environ.get("KLDV_SANDBOX_GARDE", "1").strip() != "0"
    job = json.dumps({"code": code, "datasets": {str(k): str(v) for k, v in datasets.items()}, "garde": garde})

    def attempt(nproc_limit):
        with tempfile.TemporaryDirectory(prefix="kldv-sbx-") as tmp:
            proc = subprocess.Popen(
                [sys.executable, "-I", RUNNER], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=tmp, env=env, preexec_fn=_make_limits(nproc_limit) if os.name != "nt" else None)
            try:
                out, err = proc.communicate(job.encode("utf-8"), timeout=TIMEOUT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate()  # sortie partielle : les jalons disent où le calcul s'est bloqué
                raise subprocess.TimeoutExpired(proc.args, TIMEOUT_S, output=out, stderr=err)
            return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

    try:
        proc = attempt(nproc)
        err = proc.stderr.decode("utf-8", "replace")
        if nproc is not None and not proc.stdout.strip() and THREAD_FAILURE.search(err):
            # Le calcul n'a pas pu créer ses fils malgré la marge : on relance sans cette seule limite
            # (mémoire, CPU, fichiers, environnement vide et contrôle du code restent en place).
            sys.stderr.write("[sandbox] limite de fils atteinte, nouvel essai sans RLIMIT_NPROC\n")
            proc = attempt(None)
    except subprocess.TimeoutExpired as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        jalons = [l for l in err.splitlines() if l.startswith("[jalon]")]
        sys.stderr.write(f"[sandbox] délai de {TIMEOUT_S} s dépassé ; dernière étape : "
                         f"{jalons[-1] if jalons else 'aucune (blocage pendant le chargement des bibliothèques)'} "
                         f"— KLDV_SANDBOX_TIMEOUT pour le délai, KLDV_SANDBOX_GARDE=0 pour tester sans garde-fou\n")
        return {"error": f"calcul trop long (plus de {TIMEOUT_S} s), arrêté"}
    out = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        t = json.loads(out[-1]).get("_temps") if out else None
        if t:
            sys.stderr.write(f"[sandbox] chargement {t.get('chargement')} s, données {t.get('donnees')} s, "
                             f"calcul {t.get('calcul')} s (limite {TIMEOUT_S} s)\n")
    except Exception:
        pass
    if not out:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        sys.stderr.write("[sandbox] échec : " + " | ".join(err[-5:])[:800] + "\n")
        return {"error": "échec du calcul" + (f" : {err[-1][:200]}" if err else "")}
    try:
        return json.loads(out[-1])
    except Exception:
        return {"error": "réponse du calcul illisible"}
