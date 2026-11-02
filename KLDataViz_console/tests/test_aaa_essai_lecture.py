# -*- coding: utf-8 -*-
"""Expérience temporaire, jamais fusionnée : la lecture parquet se bloque-t-elle sous les limites du serveur ?"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sandbox  # noqa: E402

LIGNE = "df = pd.read_parquet(p, use_threads=False, pre_buffer=False) if p.endswith(\".parquet\") else pd.read_csv(p)"
ANCIENNE = "df = pd.read_parquet(p) if p.endswith(\".parquet\") else pd.read_csv(p)"


@pytest.mark.skipif(os.name == "nt", reason="limites Linux")
def test_experience(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    n = 14050
    df = pd.DataFrame({f"texte_{i}": rng.choice([f"valeur longue numero {k}" for k in range(300)], n) for i in range(12)})
    df["montant"] = rng.normal(1000, 200, n)
    p = tmp_path / "Google Sheets.parquet"
    df.to_parquet(p)
    src = open(sandbox.RUNNER, encoding="utf-8").read()
    assert LIGNE in src
    ancien = tmp_path / "runner_ancien.py"
    ancien.write_text(src.replace(LIGNE, ANCIENNE), encoding="utf-8")
    nouveau = sandbox.RUNNER
    monkeypatch.setattr(sandbox, "TIMEOUT_S", 20)
    out = {"cpu": os.cpu_count(), "pyarrow": __import__("pyarrow").__version__, "pandas": pd.__version__}
    code = "t = data['x']\nresult = len(t)"
    for nom_lecture, runner in (("ancienne", str(ancien)), ("nouvelle", nouveau)):
        for mem in (1024, 768, 512):
            for marge in (8, 3, 1, 0):
                monkeypatch.setattr(sandbox, "RUNNER", runner)
                monkeypatch.setattr(sandbox, "MEMORY_MB", mem)
                monkeypatch.setattr(sandbox, "NPROC_MARGIN", marge)
                d = time.time()
                r = sandbox.run(code, {"x": str(p)})
                out[f"{nom_lecture}/{mem}Mo/marge{marge}"] = [round(time.time() - d, 1), (r.get("error") or (r.get("result") or {}).get("value"))[:90]]
    pytest.fail("EXPERIENCE " + json.dumps(out, ensure_ascii=False))
