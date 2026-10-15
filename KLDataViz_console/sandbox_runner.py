# -*- coding: utf-8 -*-
"""Processus isolé lancé par sandbox.run() : lit le travail sur l'entrée standard,
exécute le code avec des fonctions intégrées réduites et écrit le résultat en JSON."""
import json
import math
import sys

import numpy as np
import pandas as pd

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

    def __missing__(self, key):
        p = self._paths.get(key)
        if p is None:
            for k, v in self._paths.items():
                if str(key).lower() in k.lower() or k.lower() in str(key).lower():
                    p = v
                    break
        if p is None:
            raise KeyError(f"jeu de données inconnu : {key}")
        df = pd.read_parquet(p) if p.endswith(".parquet") else pd.read_csv(p)
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
    try:
        exec(compile(job["code"], "<ia>", "exec"), ns)
        chart = ns.get("chart")
        json.dumps(chart, default=str)
        out = {"result": fmt(ns.get("result")), "chart": chart}
    except Exception as exc:  # erreur du code de l'IA : renvoyée telle quelle
        out = {"error": f"{type(exc).__name__} : {exc}"[:300]}
    if printed:
        out["sortie"] = "".join(printed)[:MAX_SORTIE]
    sys.stdout.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")


if __name__ == "__main__":
    main()
