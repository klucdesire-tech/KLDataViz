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

    class FakePopen:  # sandbox.run lance le calcul avec Popen (sortie partielle récupérable en cas de délai)
        def __init__(self, args, **kw):
            calls.append(kw)
            self.args, self.n = args, len(calls)
            self.returncode = 1 if self.n == 1 else 0

        def communicate(self, data=None, timeout=None):
            if self.n == 1:
                return b"", b"OpenBLAS blas_thread_init: pthread_create failed\nKeyboardInterrupt"
            return b'{"result": {"kind": "scalar", "value": "42"}, "chart": null}\n', b""

    monkeypatch.setattr(sandbox.subprocess, "Popen", FakePopen)
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


def _deux_jeux(tmp_path):
    a, b = tmp_path / "a.parquet", tmp_path / "b.csv"
    pd.DataFrame({"x": [1, 2, 3]}).to_parquet(a)
    pd.DataFrame({"y": [10, 20]}).to_csv(b, index=False)
    return {"premier": str(a), "second": str(b)}


def test_df_est_le_premier_jeu(tmp_path):
    out = sandbox.run("result = int(df['x'].sum())", _deux_jeux(tmp_path))
    assert out["result"]["value"] == "6"


def test_df_non_lu_si_le_code_ne_s_en_sert_pas(tmp_path):
    out = sandbox.run("result = int(data['second']['y'].sum())", dict(_deux_jeux(tmp_path), premier="fichier_absent.parquet"))
    assert out["result"]["value"] == "30"


def test_df_ecrit_par_l_ia_reste_le_sien(tmp_path):
    out = sandbox.run("df = data['second']\nresult = int(df['y'].sum())", _deux_jeux(tmp_path))
    assert out["result"]["value"] == "30"


def test_data_se_parcourt_comme_un_dict(tmp_path):
    """« list(data.values())[0] » levait IndexError : le dict restait vide tant qu'on ne demandait pas une clé."""
    jeux = _deux_jeux(tmp_path)
    assert sandbox.run("result = len(list(data.values()))", jeux)["result"]["value"] == "2"
    assert sandbox.run("result = int(list(data.values())[0]['x'].sum())", jeux)["result"]["value"] == "6"
    code = "total = 0\nfor nom, t in data.items():\n    total += len(t)\nresult = total"
    assert sandbox.run(code, jeux)["result"]["value"] == "5"


def test_data_get(tmp_path):
    jeux = _deux_jeux(tmp_path)
    assert sandbox.run("result = len(data.get('premier'))", jeux)["result"]["value"] == "3"
    assert sandbox.run("result = data.get('absent') is None", jeux)["result"]["value"] == "True"


def test_lecture_parquet_aux_limites_du_serveur(tmp_path, monkeypatch):
    """Mêmes limites qu'en ligne : la lecture doit aboutir bien avant le délai."""
    monkeypatch.setattr(sandbox, "MEMORY_MB", 1024)
    monkeypatch.setattr(sandbox, "TIMEOUT_S", 45)
    p = tmp_path / "operations.parquet"
    pd.DataFrame({"recette_fcfa": range(4000), "poste": ["Épargne", "Commissions"] * 2000, "valide": [True, False] * 2000}).to_parquet(p)
    code = "sub = df[df['recette_fcfa'] > 3000]\nresult = sub['poste'].value_counts()\nprint(sub.nlargest(3, 'recette_fcfa').to_string(index=False))"
    out = sandbox.run(code, {"Google Sheets": str(p)})
    assert "error" not in out, out
    assert out["result"]["kind"] == "table" and "3999" in out["sortie"]


def test_lecture_bloquee_retentee_une_fois(tmp_path, monkeypatch):
    """Premier essai bloqué pendant la lecture : le second aboutit, l'utilisateur ne voit pas l'incident."""
    compteur = tmp_path / "essais.txt"
    faux = tmp_path / "faux_runner.py"
    faux.write_text(
        "import sys, time, os\n"
        f"c = {str(compteur)!r}\n"
        "n = int(open(c).read()) + 1 if os.path.exists(c) else 1\n"
        "open(c, 'w').write(str(n))\n"
        "if n == 1:\n"
        "    sys.stderr.reconfigure(encoding='utf-8'); sys.stderr.write('[jalon] lecture des données « x » (1.6 s)\\n'); sys.stderr.flush(); time.sleep(30)\n"
        "print('{\"result\": {\"kind\": \"scalar\", \"value\": \"ok\"}}')\n", encoding="utf-8")
    monkeypatch.setattr(sandbox, "RUNNER", str(faux))
    monkeypatch.setattr(sandbox, "TIMEOUT_S", 3)
    assert sandbox.run("result = 1", {})["result"]["value"] == "ok"
    assert compteur.read_text() == "2"


def test_lecture_toujours_bloquee_dit_ou(tmp_path, monkeypatch):
    faux = tmp_path / "faux_runner.py"
    faux.write_text("import sys, time\nsys.stderr.reconfigure(encoding='utf-8'); sys.stderr.write('[jalon] lecture des données « x » (1.6 s)\\n'); sys.stderr.flush(); time.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(sandbox, "RUNNER", str(faux))
    monkeypatch.setattr(sandbox, "TIMEOUT_S", 2)
    assert "lecture des données" in sandbox.run("result = 1", {})["error"]


def test_calcul_long_pas_retente(tmp_path, monkeypatch):
    compteur = tmp_path / "essais.txt"
    faux = tmp_path / "faux_runner.py"
    faux.write_text(f"import sys, time\nopen({str(compteur)!r}, 'a').write('x')\nsys.stderr.reconfigure(encoding='utf-8'); sys.stderr.write('[jalon] calcul lancé (1.6 s)\\n'); sys.stderr.flush(); time.sleep(30)\n", encoding="utf-8")
    monkeypatch.setattr(sandbox, "RUNNER", str(faux))
    monkeypatch.setattr(sandbox, "TIMEOUT_S", 2)
    assert "calcul trop long" in sandbox.run("result = 1", {})["error"]
    assert compteur.read_text() == "x"
