# -*- coding: utf-8 -*-
"""Processus isolé lancé par sandbox.run() : lit le travail sur l'entrée standard,
exécute le code avec des fonctions intégrées réduites et écrit le résultat en JSON."""
import time

_T0 = time.perf_counter()  # avant les imports : mesure le coût de chargement des bibliothèques

import json  # noqa: E402
import math  # noqa: E402
import sys  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

_T_IMPORTS = time.perf_counter()
sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # -I ignore PYTHONIOENCODING : jalons lisibles (accents)


def _jalon(texte):
    """Étape franchie, écrite tout de suite sur la sortie d'erreur : si le calcul dépasse le délai, le serveur
    journalise la dernière étape atteinte (bibliothèques, garde-fou, données, calcul)."""
    sys.stderr.write(f"[jalon] {texte} ({time.perf_counter() - _T0:.1f} s)\n")
    sys.stderr.flush()


_jalon("bibliothèques chargées")

SAFE_BUILTINS = {n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n) for n in (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "frozenset", "int",
    "isinstance", "len", "list", "map", "max", "min", "pow", "print", "range", "repr", "reversed", "round",
    "set", "slice", "sorted", "str", "sum", "tuple", "zip", "ValueError", "KeyError", "TypeError",
    "Exception")}


class LazyData(dict):
    """Jeux de données chargés à la demande (seulement ceux de l'organisation)."""

    def __init__(self, paths):
        super().__init__()
        self._paths = paths
        self.temps_chargement = 0.0

    def __missing__(self, key):
        p = self._paths.get(key)
        if p is None:
            for k, v in self._paths.items():
                if str(key).lower() in k.lower() or k.lower() in str(key).lower():
                    p = v
                    break
        if p is None:
            raise KeyError(f"jeu de données inconnu : {key}")
        t = time.perf_counter()
        _jalon(f"lecture des données « {key} »")
        df = pd.read_parquet(p) if p.endswith(".parquet") else pd.read_csv(p)
        self.temps_chargement += time.perf_counter() - t
        _jalon(f"données « {key} » lues ({len(df)} lignes)")
        self[key] = df
        return df

    def keys(self):
        return self._paths.keys()

    def __iter__(self):
        return iter(self._paths)

    def __len__(self):
        return len(self._paths)

    def __contains__(self, key):
        return key in self._paths


def fmt(result):
    if result is None:
        return None
    if isinstance(result, pd.DataFrame):
        return {"kind": "table", "columns": [str(c) for c in result.columns],
                "rows": [[str(v) for v in row] for row in result.head(50).values.tolist()]}
    if isinstance(result, pd.Series):
        if len(result) == 1:
            return {"kind": "scalar", "value": str(result.iloc[0])}
        return {"kind": "table", "columns": [str(result.name or "valeur"), "valeur"],
                "rows": [[str(i), str(v)] for i, v in result.head(50).items()]}
    if isinstance(result, (int, float, str, bool, np.generic)):
        return {"kind": "scalar", "value": str(result)}
    try:
        return {"kind": "scalar", "value": json.dumps(result, ensure_ascii=False, default=str)}
    except Exception:
        return {"kind": "scalar", "value": str(result)}


MAX_SORTIE = 4000
STAT_FUNCS = ("ttest_ind", "ttest_rel", "mannwhitneyu", "chi2_contingency", "fisher_exact", "pearsonr",
              "spearmanr", "linregress", "f_oneway", "kruskal", "shapiro", "wilcoxon")


_WRITE_FLAGS = 0
for _f in ("O_WRONLY", "O_RDWR", "O_APPEND", "O_CREAT", "O_TRUNC"):
    _WRITE_FLAGS |= getattr(__import__("os"), _f, 0)
_BLOCKED_PREFIXES = ("os.", "subprocess.", "socket.", "ctypes.", "shutil.", "_winapi.", "winreg.", "urllib.",
                     "http.", "ftplib.", "smtplib.", "poplib.", "imaplib.", "nntplib.", "telnetlib.", "webbrowser.",
                     "sqlite3.", "glob.", "pty.", "resource.", "signal.", "mmap.", "fcntl.", "syslog.", "_posixsubprocess.",
                     "sys.addaudithook", "sys.settrace", "sys.setprofile", "cpython.", "msvcrt.")


def _install_guard(dataset_paths):
    """Barrière d'exécution, en plus du contrôle du code (sandbox.validate) : un « audit hook » Python,
    impossible à retirer depuis le code exécuté, qui refuse toute écriture de fichier, toute lecture hors
    des fichiers de l'espace et de la bibliothèque Python, tout accès réseau et tout lancement de programme.
    Il ferme les contournements par appel de méthode en texte (df.apply('to_csv', …), pd.ExcelFile(…))."""
    import os
    import site

    def norm(p):
        return os.path.normcase(os.path.realpath(p))

    # Lecture seule, sous Linux : bibliothèques système, fuseaux horaires, /sys et /proc/self (le processus de
    # calcul lui-même, dont l'environnement est vide). Jamais /proc/<autre> : l'environnement du serveur (clés).
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, "/usr/share/zoneinfo", "/usr/lib", "/lib", "/lib64",
             "/usr/local/lib", "/sys", "/proc/self", "/proc/" + str(os.getpid())}
    try:
        roots.update(site.getsitepackages())
    except Exception:
        pass
    roots = tuple(norm(r) + os.sep for r in roots if r)
    files = {norm(p) for p in dataset_paths if p}
    files |= {norm("/etc/localtime"), norm("/dev/urandom"), norm("/dev/null")}

    def readable(path):
        if isinstance(path, int):
            return True
        n = norm(os.fsdecode(path))
        return n in files or n.startswith(roots)

    def hook(event, args):
        if event == "open":
            path, mode, flags = (list(args) + [None, None, 0])[:3]
            writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or bool((flags or 0) & _WRITE_FLAGS)
            if writes or not readable(path):
                raise PermissionError("accès fichier interdit dans le calcul")
            return
        if event in ("os.listdir", "os.scandir"):
            if args and args[0] is not None and not readable(args[0]):
                raise PermissionError("accès fichier interdit dans le calcul")
            return
        if event.startswith(_BLOCKED_PREFIXES):
            raise PermissionError(f"opération interdite dans le calcul : {event}")

    sys.addaudithook(hook)


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # le parent relit en UTF-8 (« Soubré », pas « Soubr� »)
    sys.stdin.reconfigure(encoding="utf-8")
    job = json.loads(sys.stdin.read())
    data = LazyData(job.get("datasets") or {})
    printed = []

    def _print(*args, sep=" ", end="\n", **_):  # la sortie de print() est renvoyée à l'IA
        if sum(len(p) for p in printed) < MAX_SORTIE:
            printed.append(sep.join(str(a) for a in args) + end)

    builtins = dict(SAFE_BUILTINS, print=_print)
    ns = {"__builtins__": builtins, "pd": pd, "np": np, "math": math, "data": data,
          "load_dataset": lambda name: data[name]}
    code = job["code"]
    if "stats" in code or any(f in code for f in STAT_FUNCS):  # scipy.stats, chargé seulement s'il sert
        from scipy import stats
        ns["stats"] = stats
        ns.update({f: getattr(stats, f) for f in STAT_FUNCS})  # aussi utilisables sans « stats. »
    if job.get("garde", True):
        _install_guard(list((job.get("datasets") or {}).values()))
        _jalon("garde-fou installé")
    else:
        _jalon("garde-fou désactivé (KLDV_SANDBOX_GARDE=0)")
    t_exec = time.perf_counter()
    _jalon("calcul lancé")
    try:
        exec(compile(job["code"], "<ia>", "exec"), ns)
        chart = ns.get("chart")
        json.dumps(chart, default=str)
        out = {"result": fmt(ns.get("result")), "chart": chart}
    except Exception as exc:  # erreur du code de l'IA : renvoyée telle quelle
        out = {"error": f"{type(exc).__name__} : {exc}"[:300]}
    if printed:
        out["sortie"] = "".join(printed)[:MAX_SORTIE]
    fin = time.perf_counter()
    out["_temps"] = {"chargement": round(_T_IMPORTS - _T0, 2), "donnees": round(data.temps_chargement, 2),
                     "calcul": round(fin - t_exec - data.temps_chargement, 2)}
    sys.stdout.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")


if __name__ == "__main__":
    main()
