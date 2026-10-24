# -*- coding: utf-8 -*-
"""Voix de Mon Prof.

Deux moteurs de synthèse (TTS) :
1. **edge-tts** — voix neuronales naturelles (Microsoft), gratuites, sans clé.
   Utilisé dès que le paquet `edge_tts` est importable (voir README pour l'installation).
2. **SAPI 5** (voix intégrées Windows) — repli automatique, plus robotique.

Reconnaissance (STT) : SAPI 5, dictée via le micro (best effort).

Lecture du MP3 généré : MCI (`winmm.dll`) via ctypes — aucun lecteur externe,
blocage jusqu'à la fin de la phrase.
"""
from __future__ import annotations

import base64
import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time

# Voix neuronale par défaut (fr-FR). Modifiable via MONPROF_VOICE.
DEFAULT_VOICE = "fr-FR-DeniseNeural"          # femme, chaleureuse
# Alternatives naturelles : fr-FR-HenriNeural (homme), fr-FR-EloiseNeural (enfant)
VOICE_NAME = os.environ.get("MONPROF_VOICE", DEFAULT_VOICE)

_MAX_CHARS = 1000   # ne pas lire à voix haute un très long texte
_voice_lock = threading.Lock()
_hint_printed = False
_WHISPER_MODEL_NAME = os.environ.get("MONPROF_WHISPER_MODEL", "base")
_whisper_model = None


def _on_windows() -> bool:
    return sys.platform == "win32"


def _run_ps(script: str, timeout: int = 30) -> tuple[int, str]:
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
            capture_output=True, timeout=timeout,
        )
        return p.returncode, (p.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as e:
        return 1, str(e)


def available() -> bool:
    """Une voix (au moins SAPI) est-elle disponible sur ce poste ?"""
    if not _on_windows():
        return False
    rc, _ = _run_ps("Write-Output 'ok'", timeout=10)
    return rc == 0


def neural_available() -> bool:
    """Le moteur neuronal (edge-tts) est-il importable ?"""
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def whisper_available() -> bool:
    """Le moteur de reconnaissance local (faster-whisper + sounddevice) est-il importable ?"""
    try:
        import sounddevice  # noqa: F401
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _ps_quote(s: str) -> str:
    return s.replace("'", "''")


# ---------------- TTS ----------------

def speak(text: str) -> None:
    """Lit `text` à voix haute (non bloquant). Choisit edge-tts si possible, sinon SAPI."""
    text = (text or "").strip()
    if not text:
        return
    text = text[:_MAX_CHARS]
    threading.Thread(target=_speak_worker, args=(text,), daemon=True).start()


def _speak_worker(text: str) -> None:
    global _hint_printed
    with _voice_lock:  # une seule phrase à la fois
        if not _speak_neural(text):
            if not neural_available() and not _hint_printed:
                _hint_printed = True
                print("\n💡 Voix naturelle : installe edge-tts → "
                      "`uv venv .venv && uv pip install edge-tts`, puis lance avec "
                      "`.venv/Scripts/python.exe -m mon_prof`. (Repli SAPI en attendant.)")
            _speak_sapi(text)


def _speak_neural(text: str) -> bool:
    try:
        import edge_tts
    except ImportError:
        return False
    try:
        mp3 = _synthesize(edge_tts, text, VOICE_NAME)
    except Exception:
        return False
    try:
        ok = _play_mp3(mp3)
    finally:
        try:
            os.remove(mp3)
        except OSError:
            pass
    return ok


def _synthesize(edge_tts, text: str, voice: str) -> str:
    import asyncio

    async def _gen() -> str:
        fd, path = tempfile.mkstemp(suffix=".mp3", prefix="monprof_")
        os.close(fd)
        try:
            await edge_tts.Communicate(text, voice).save(path)
        except Exception:
            os.remove(path)
            raise
        return path

    return asyncio.run(_gen())


def _play_mp3(path: str) -> bool:
    """Joue un MP3 via MCI (winmm) et attend la fin. False si échec."""
    if not _on_windows():
        return False
    winmm = ctypes.windll.winmm
    alias = f"mp{int(time.time() * 1000) % 1000000}"
    if winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}',
                            None, 0, None) != 0:
        return False
    try:
        winmm.mciSendStringW(f"play {alias} wait", None, 0, None)
    finally:
        winmm.mciSendStringW(f"close {alias}", None, 0, None)
    return True


def _speak_sapi(text: str) -> None:
    if not available():
        return
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "try { $s.SelectVoice('Microsoft Hortense Desktop') } catch {};"
        f"$s.Speak('{_ps_quote(text)}')"
    )
    _run_ps(script, timeout=120)


# ---------------- STT ----------------

def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(_WHISPER_MODEL_NAME, device="cpu", compute_type="int8")
    return _whisper_model


def _listen_whisper(timeout_s: int, model) -> str:
    import numpy as np
    import sounddevice as sd
    sr = 16000
    audio = sd.rec(int(timeout_s * sr), samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    segments, _info = model.transcribe(audio.flatten(), language="fr", vad_filter=True)
    return "".join(s.text for s in segments).strip()


def listen(timeout_s: int = 6) -> str:
    """Écoute une phrase via le micro.

    Whisper (local, précis, français) si installé, sinon repli SAPI 5.
    L'enregistrement attend la fin de la lecture en cours (pas de chevauchement voix/prof).
    """
    model = None
    if whisper_available():
        try:
            model = _get_whisper_model()  # chargement hors verrou (lent la 1re fois)
        except Exception:
            model = None
        if model is not None:
            try:
                with _voice_lock:
                    return _listen_whisper(timeout_s, model)
            except Exception:
                pass  # repli SAPI
    with _voice_lock:
        return _listen_sapi(timeout_s)


def _listen_sapi(timeout_s: int = 6) -> str:
    """Repli : dictée SAPI 5 via le micro."""
    if not available():
        return ""
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$engine = $null;"
        "try { $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine("
        "[System.Globalization.CultureInfo]::GetCultureInfo('fr-FR')) } catch {};"
        "if ($null -eq $engine) { $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine };"
        "try { $engine.SetInputToDefaultAudioDevice() } catch { exit 1 };"
        "$g = New-Object System.Speech.Recognition.DictationGrammar;"
        "$engine.LoadGrammar($g);"
        f"$res = $engine.Recognize([TimeSpan]::FromSeconds({int(timeout_s)}));"
        "if ($null -ne $res) { Write-Output $res.Text }"
    )
    rc, out = _run_ps(script, timeout=timeout_s + 15)
    return out if rc == 0 else ""
