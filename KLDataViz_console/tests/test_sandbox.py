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
