# -*- coding: utf-8 -*-
"""Résolution de la configuration LLM (DeepSeek). Jamais de clé en dur.

Ordre de résolution :
1. variables d'environnement (DEEPSEEK_API_KEY / MONPROF_API_KEY) ;
2. `Mon_Prof/llm_config.json` (config propre à l'agent) ;
3. `KLDataViz_console/llm_config.json` (clé partagée déjà utilisée par la console).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent          # Mon_Prof/
OWN_CONFIG = BASE / "llm_config.json"
CONSOLE_CONFIG = BASE.parent / "KLDataViz_console" / "llm_config.json"

DEFAULT_BASE_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-pro"


def _valid(cfg: dict | None) -> bool:
    return bool(cfg and cfg.get("api_key") and cfg["api_key"] != "VOTRE_CLE_DEEPSEEK")


def load_config() -> dict | None:
    """Configuration LLM disponible, ou None (et l'appelant guide l'utilisateur)."""
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("MONPROF_API_KEY")
    if key:
        return {
            "api_key": key,
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
            "model": os.environ.get("MONPROF_MODEL")
            or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL),
        }
    for path in (OWN_CONFIG, CONSOLE_CONFIG):
        if path.exists():
            try:
                cfg = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if _valid(cfg):
                cfg.setdefault("base_url", DEFAULT_BASE_URL)
                cfg.setdefault("model", DEFAULT_MODEL)
                return cfg
    return None


def model_of(cfg: dict | None) -> str:
    """Modèle à utiliser : env > chat_model > model > défaut."""
    return (os.environ.get("MONPROF_MODEL")
            or (cfg or {}).get("chat_model")
            or (cfg or {}).get("model")
            or DEFAULT_MODEL)


def max_tokens_for(model: str) -> int:
    """deepseek-v4-pro est un modèle à raisonnement : un max_tokens trop bas renvoie
    un contenu vide (finish_reason=length). On garde 8000 pour lui, 4000 pour les autres."""
    return 8000 if "pro" in (model or "") else 4000
