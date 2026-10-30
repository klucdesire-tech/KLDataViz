# -*- coding: utf-8 -*-
"""Appel d'outil au format natif DeepSeek (DSML) écrit dans le texte : lu comme un calcul, jamais affiché brut."""
from _harness import reset_app_modules

DSML = """Quelle est la distribution de la charge virale ?
<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="calc">
<｜｜DSML｜｜ parameter name="pourquoi" string="true">Compter les patients non supprimés.</｜｜DSML｜｜ parameter>
<｜｜DSML｜｜ parameter name="code" string="true">d = data["demo"]
result = pd.Series({"n": int((d["cv"] == False).sum())})</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>"""


def test_appel_dsml_lu_comme_un_calcul():
    reset_app_modules()
    import server  # noqa: PLC0415
    obj = server._parse_llm_json(DSML)
    assert obj["action"] == "calcul" and obj["pourquoi"] == "Compter les patients non supprimés."
    assert obj["code"].startswith('d = data["demo"]') and "\n" in obj["code"]


def test_reponse_dsml_et_json_ordinaire():
    reset_app_modules()
    import server  # noqa: PLC0415
    rep = ('<｜｜DSML｜｜ invoke name="reponse"><｜｜DSML｜｜ parameter name="answer" string="true">319 patients.'
           '</｜｜DSML｜｜ parameter><｜｜DSML｜｜ parameter name="montrer_tableau" string="false">true'
           '</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke>')
    obj = server._parse_llm_json(rep)
    assert obj == {"answer": "319 patients.", "montrer_tableau": True, "action": "reponse"}
    assert server._parse_llm_json('{"action":"reponse","answer":"ok"}') == {"action": "reponse", "answer": "ok"}


def test_code_indente_et_guillemets_simples():
    reset_app_modules()
    import server  # noqa: PLC0415
    x = ("<｜｜DSML｜｜ invoke name = 'calc'><｜｜DSML｜｜ parameter name='code'>\n    d = data['demo']\n    result = len(d)\n"
         "</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke>")
    obj = server._parse_llm_json(x)
    assert obj["action"] == "calcul" and obj["code"] == "d = data['demo']\nresult = len(d)"
    compile(obj["code"], "<calc>", "exec")


def test_reponse_json_avec_retours_a_la_ligne_bruts():
    reset_app_modules()
    import server  # noqa: PLC0415
    obj = server._parse_llm_json('{"action":"reponse","answer":"Yopougon-Est.\n\n- 1 862 cas","montrer_tableau":true}')
    assert obj["action"] == "reponse" and obj["answer"].startswith("Yopougon-Est.")
