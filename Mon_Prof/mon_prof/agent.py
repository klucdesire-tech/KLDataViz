# -*- coding: utf-8 -*-
"""Agent « Mon Prof » : séance de tutorat pilotée par l'IA (DeepSeek).

Le comportement pédagogique (méthode socratique, vérification du « j'ai compris »,
bienveillance, etc.) est entièrement défini par `PROMPT.md`, lu au démarrage : la
spécification reste la source unique de vérité, ce module ne fait qu'exécuter la boucle.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config, profile, voice

BASE = Path(__file__).resolve().parent.parent
PROMPT_PATH = BASE / "PROMPT.md"


def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _llm_call(cfg: dict, messages: list[dict], model: str, max_tokens: int,
              temperature: float = 0.3, timeout: int = 180, attempts: int = 2) -> str:
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    body = json.dumps(payload).encode("utf-8")
    last: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(
            cfg["base_url"], data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + cfg["api_key"]},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode())
            return data["choices"][0]["message"]["content"]
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError,
                json.JSONDecodeError) as e:
            last = e
            if attempt < attempts - 1:
                time.sleep(2)
    raise RuntimeError(f"Appel LLM échoué : {last}")


def _hint_config() -> None:
    print("Aucune clé LLM configurée pour Mon Prof.")
    print("  • copie `Mon_Prof/llm_config.example.json` en `Mon_Prof/llm_config.json` "
          "et renseigne ta clé DeepSeek ;")
    print("  • ou exporte DEEPSEEK_API_KEY ;")
    print("  • ou laisse `KLDataViz_console/llm_config.json` renseigné (clé partagée).")


def _transcript(messages: list[dict], limit: int = 20000) -> str:
    parts = []
    for m in messages:
        if m.get("role") in ("user", "assistant"):
            parts.append(f"[{m['role']}] {m.get('content', '')}")
    text = "\n\n".join(parts)
    return text[-limit:]


def _ask_statut() -> str:
    while True:
        ans = input("Statut de la notion ? (acquise / en_cours / a_revoir) : ").strip().lower()
        if ans in profile.STATUTS:
            return ans
        print("  → choisis parmi : acquise, en_cours, a_revoir")


def _generate_fiche(cfg: dict, messages: list[dict], model: str, max_tokens: int) -> str | None:
    """Fiche de synthèse de fin de séance : l'essentiel à retenir + 2-3 exercices."""
    instruct = (
        "La séance se termine. Rédige la FICHE DE SYNTHÈSE (en français, concise) :\n"
        "1. L'ESSENTIEL À RETENIR (5 puces maximum) ;\n"
        "2. 2 ou 3 EXERCICES d'entraînement (sans corrigé, pour que l'élève s'exerce seul).\n"
        "Ne salue pas, ne fais pas de conclusion personnelle : uniquement la fiche."
    )
    try:
        return _llm_call(cfg, messages + [{"role": "user", "content": instruct}],
                         model, max_tokens, temperature=0.2, timeout=180, attempts=1)
    except RuntimeError:
        return None


def run_session(matiere: str | None = None, notion: str | None = None,
                mode: str = "ecrit", model: str | None = None, cfg: dict | None = None,
                voix: bool = False) -> int:
    cfg = cfg or config.load_config()
    if not cfg:
        _hint_config()
        return 1
    profile.init_schema()
    model = model or config.model_of(cfg)
    max_tokens = config.max_tokens_for(model)

    if not matiere:
        matiere = input("Matière ? ").strip()
    if not notion:
        notion = input("Notion à travailler ? ").strip()
    if not matiere or not notion:
        print("Matière et notion sont nécessaires pour démarrer une séance.")
        return 1

    sys_content = system_prompt() + "\n\n## PROFIL DE L'APPRENANT\n" + json.dumps(
        {"eleve": profile.get_eleve(), "progression": profile.bilan()},
        ensure_ascii=False, indent=2,
    )
    messages: list[dict] = [
        {"role": "system", "content": sys_content},
        {"role": "user", "content":
            f"Nouvelle séance. Matière : {matiere}. Notion : {notion}. Mode : {mode}. "
            "Commence par évaluer mon niveau de départ sur cette notion."},
    ]
    session_id = profile.start_session(matiere, notion, mode)

    print()
    print("=" * 72)
    print("  MON PROF — séance en cours")
    print(f"  Matière : {matiere}   ·   Notion : {notion}   ·   Mode : {mode}")
    print("=" * 72)
    cmds = "/fin pour clôturer, /bilan pour la progression, /quit pour quitter"
    if voix:
        cmds += ", /parle pour répondre à la voix"
    print(f"Tape {cmds}.\n")

    while True:
        try:
            content = _llm_call(cfg, messages, model, max_tokens)
        except RuntimeError as e:
            print(f"\n(IA indisponible — {e})")
            break
        messages.append({"role": "assistant", "content": content})
        print("\n🧑‍🏫 " + content + "\n")
        if voix:
            voice.speak(content)

        user = input("👉 ").strip()
        if user in ("/fin", "/quit"):
            if user == "/fin":
                fiche = _generate_fiche(cfg, messages, model, max_tokens)
                statut = _ask_statut()
                profile.finish_session(session_id, _transcript(messages), statut, fiche)
                profile.upsert_notion(matiere, notion, statut)
                if fiche:
                    print("\n" + "-" * 72 + "\n📋 FICHE DE SYNTHÈSE\n" + fiche)
                print(f"\n✅ Séance enregistrée — notion « {notion} » : {statut}.")
                return 0
            profile.finish_session(session_id, _transcript(messages), "en_cours", None)
            profile.upsert_notion(matiere, notion, "en_cours")
            print("\nSéance enregistrée comme « en cours ». À bientôt !")
            return 0
        if user == "/bilan":
            print("\n" + _format_bilan())
            continue
        if user == "/parle":
            if not voice.available():
                print("(voix indisponible sur ce poste — tape ta réponse)")
                continue
            print("🎤 Je t'écoute…")
            heard = voice.listen()
            if heard:
                print(f"→ entendu : « {heard} »")
                messages.append({"role": "user", "content": heard})
            else:
                print("(rien entendu — tape ta réponse)")
            continue
        if not user:
            continue
        messages.append({"role": "user", "content": user})
    return 1


def _format_bilan() -> str:
    b = profile.bilan()
    lines = [f"Séances effectuées : {b['nb_seances']}"]
    for label, key in (("✅ Acquises", "acquises"), ("🔄 En cours", "en_cours"),
                       ("⚠️ À revoir", "a_revoir")):
        lines.append(f"{label} ({len(b[key])})")
        for n in b[key]:
            lines.append(f"    - {n['matiere']} : {n['notion']}")
    return "\n".join(lines)


def show_bilan() -> int:
    profile.init_schema()
    print(_format_bilan())
    return 0


def show_profil() -> int:
    profile.init_schema()
    e = profile.get_eleve()
    print(f"Prénom    : {e['prenom']}")
    print(f"Classe    : {e['classe'] or '—'}")
    print(f"Programme : {e['programme'] or '—'}")
    return 0
