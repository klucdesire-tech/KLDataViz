# -*- coding: utf-8 -*-
"""Tests du bac à sable du code IA : python -m pytest tests/test_sandbox.py"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sandbox  # noqa: E402

ATTACKS = [
    "open('/etc/passwd').read()",
    "import os\nresult = os.environ",
    "result = pd.read_csv('/data/tenants/autre/x.csv')",
    "data['x'].to_csv('/tmp/vol.csv')",
    "result = ().__class__.__bases__[0].__subclasses__()",
    "result = getattr(pd, 'read_csv')('x')",
    "result = '{0.__class__}'.format(1)",
    "result = eval('1+1')",
    "result = __builtins__",
    "result = np.load('x.npy')",
    "result = pd.io.parsers",
    "result = data._paths",
]


@pytest.mark.parametrize("code", ATTACKS)
def test_attaques_refusees(code):
    with pytest.raises(sandbox.UnsafeCode):
        sandbox.run(code, {})


def test_calcul_normal(tmp_path):
    p = tmp_path / "ventes.parquet"
    pd.DataFrame({"region": ["A", "A", "B"], "montant": [1, 2, 5]}).to_parquet(p)
    code = ("df = data['ventes']\n"
            "result = df.groupby('region')['montant'].sum()\n"
            "chart = {'type': 'bar', 'title': 't', 'labels': list(result.index), 'values': [result.tolist()]}")
    out = sandbox.run(code, {"ventes": str(p)})
    assert "error" not in out, out
    assert out["result"]["rows"] == [["A", "3"], ["B", "5"]]
    assert out["chart"]["labels"] == ["A", "B"]


def test_pas_de_secrets_dans_le_processus():
    os.environ["KLDV_TEST_SECRET"] = "ne-doit-pas-fuiter"
    out = sandbox.run("result = str(dict(data))", {})
    assert "ne-doit-pas-fuiter" not in str(out)


def test_boucle_infinie_arretee(monkeypatch):
    monkeypatch.setattr(sandbox, "TIMEOUT_S", 3)
    out = sandbox.run("x = 0\nwhile True:\n    x += 1", {})
    assert "error" in out and "trop long" in out["error"]


def test_calcul_limite_a_un_fil():
    """Les bibliothèques de calcul ne créent qu'un fil (sinon échec sous RLIMIT_NPROC en ligne)."""
    out = sandbox.run("result = 1", {})
    assert out["result"]["value"] == "1"
    assert sandbox.THREAD_ENV["OPENBLAS_NUM_THREADS"] == "1"


def test_nouvel_essai_si_limite_de_fils(monkeypatch):
    """Échec « KeyboardInterrupt » (fils refusés) → un seul nouvel essai, sans la limite de fils."""
    import subprocess
    calls = []

    def fake_run(args, **kw):
        calls.append(kw)
        if len(calls) == 1:
            return subprocess.CompletedProcess(args, 1, b"", b"OpenBLAS blas_thread_init: pthread_create failed\nKeyboardInterrupt")
        return subprocess.CompletedProcess(args, 0, b'{"result": {"kind": "scalar", "value": "42"}, "chart": null}\n', b"")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)
    monkeypatch.setattr(sandbox, "_user_tasks", lambda: 10)
    monkeypatch.setattr(sandbox.os, "name", "posix")
    monkeypatch.setattr(sandbox, "_make_limits", lambda n: ("limites", n))
    out = sandbox.run("result = 42", {})
    assert out["result"]["value"] == "42" and len(calls) == 2
    assert calls[0]["preexec_fn"] == ("limites", 10 + sandbox.NPROC_MARGIN)
    assert calls[1]["preexec_fn"] == ("limites", None)
    assert calls[0]["env"]["OMP_NUM_THREADS"] == "1"


def test_imports_des_modules_fournis_acceptes(tmp_path):
    """« import numpy as np », « import pandas », « from math import sqrt » : réécrits, pas refusés."""
    p = tmp_path / "t.parquet"
    pd.DataFrame({"age": [20, 30, 40]}).to_parquet(p)
    code = ("import numpy as np\nimport pandas\nfrom math import sqrt\n"
            "df = data['t']\nresult = round(sqrt(float(np.mean(df['age']))) + pandas.Series([0]).sum(), 3)")
    out = sandbox.run(code, {"t": str(p)})
    assert "error" not in out, out
    assert out["result"]["value"] == "5.477"


def test_autres_imports_toujours_refuses():
    for code in ("import os\nresult = 1", "import numpy, os\nresult = 1", "from os import path\nresult = 1",
                 "from numpy import *\nresult = 1"):
        with pytest.raises(sandbox.UnsafeCode):
            sandbox.run(code, {})


# --- Audit de sécurité (octobre 2026) : contournements par nom de méthode en texte et lecteurs oubliés
@pytest.mark.parametrize("code", [
    "result = pd.ExcelFile('autre.xlsx').parse()",
    "data['x'].apply('to_csv', path_or_buf='vol.csv')",
    "data['x'].agg('to_pickle', 'vol.pkl')",
])
def test_contournements_refuses_par_le_controle(code):
    with pytest.raises(sandbox.UnsafeCode):
        sandbox.validate(code)


def test_garde_fou_d_execution_bloque_l_ecriture_et_la_lecture(tmp_path):
    """Même si le contrôle du code est contourné (nom construit morceau par morceau), le processus de calcul
    ne peut ni écrire un fichier ni lire un fichier hors de l'espace."""
    src = tmp_path / "x.csv"
    pd.DataFrame({"a": [1, 2]}).to_csv(src, index=False)
    autre = tmp_path / "autre_espace.csv"
    autre.write_text("secret\nAUTRE_ESPACE\n", encoding="utf-8")
    cible = tmp_path / "vol.csv"
    ecrit = sandbox.run(f"m = 'to' + '_csv'\ndata['x'].apply(m, path_or_buf={str(cible)!r})\nresult = 1",
                        {"x": str(src)})
    assert "error" in ecrit and not cible.exists()
    ok = sandbox.run("result = int(data['x']['a'].sum())", {"x": str(src)})
    assert ok["result"]["value"] == "3"   # les calculs normaux restent possibles
