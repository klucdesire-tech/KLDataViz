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
import subprocess
import sys
import tempfile

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox_runner.py")
TIMEOUT_S = int(os.environ.get("KLDV_SANDBOX_TIMEOUT", "20"))
MEMORY_MB = int(os.environ.get("KLDV_SANDBOX_MEMORY_MB", "1536"))

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
    "pipe", "ExcelWriter", "HDFStore", "describe_option",
}
SAFE_TO = {"to_dict", "to_list", "to_numpy", "to_frame", "to_period", "to_timestamp", "to_datetime",
           "to_numeric", "to_string", "to_records", "to_flat_index", "to_series", "to_timedelta",
           "to_pydatetime", "to_offset", "tolist"}


class UnsafeCode(ValueError):
    pass


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
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            raise UnsafeCode("chaîne suspecte")


def _limits():  # exécuté dans le sous-processus, sous Linux uniquement
    import resource
    mem = MEMORY_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_S, TIMEOUT_S + 1))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))  # aucun nouveau processus


def run(code: str, datasets: dict) -> dict:
    """Exécute `code` avec `data` = {nom: chemin de fichier parquet/csv}.

    Renvoie {"result": …, "chart": …} ou {"error": "…"}.
    """
    validate(code)
    env = {}
    if os.name == "nt":  # Python a besoin de ces variables pour démarrer sous Windows
        for k in ("SYSTEMROOT", "TEMP", "TMP"):
            if os.environ.get(k):
                env[k] = os.environ[k]
    job = json.dumps({"code": code, "datasets": {str(k): str(v) for k, v in datasets.items()}})
    with tempfile.TemporaryDirectory(prefix="kldv-sbx-") as tmp:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", RUNNER], input=job.encode("utf-8"), capture_output=True,
                cwd=tmp, env=env, timeout=TIMEOUT_S,
                preexec_fn=_limits if os.name != "nt" else None,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"calcul trop long (plus de {TIMEOUT_S} s), arrêté"}
    out = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    if not out:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return {"error": "échec du calcul" + (f" : {err[-1][:200]}" if err else "")}
    try:
        return json.loads(out[-1])
    except Exception:
        return {"error": "réponse du calcul illisible"}
