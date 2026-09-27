# feedback_speaker.py
"""Speaks the interview questions and the feedback aloud.

Uses pyttsx3 with the built in Windows voices, fully offline.
A neural voice (Coqui TTS) was ruled out because its libraries clash with the pinned speech libraries.

speak() plays audio straight away for the live session. synthesise() writes a WAV file, used by the tests to check the audio is not silent.
The pyttsx3 engine is created on every call, because a reused engine can silently stop working on some Windows builds.

speak_async() reads the live question without blocking, driving the same Windows voice directly so it can be stopped at once when the candidate starts answering.
On a Mac it uses the built in `say` command.
"""

from __future__ import annotations

import logging
import math
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Only one engine may use the Windows voice at a time.
# Two running together can hang, which would freeze the interview.
_SPEAK_LOCK = threading.Lock()

# Words per minute.
# The default of 200 sounds rushed; 175 gives time to take in the question.
SPEECH_RATE_WPM = 175

# Preferred voices, British English first.
# Falls back to what is installed, since voices vary by machine.
_VOICE_PREFERENCE = ("hazel", "zira", "david")
_MAC_VOICE_PREFERENCE = ("daniel", "serena", "samantha")

# Guard against a malformed or hostile input string producing a very long synthesis.
# Feedback passages are a few sentences.
_MAX_CHARS = 4000


def _configure(engine) -> Optional[str]:
    """Apply rate and voice settings.
    Returns the selected voice name, if any.
    """
    engine.setProperty("rate", SPEECH_RATE_WPM)
    try:
        voices = engine.getProperty("voices")
    except Exception:  # driver quirk
        return None
    for wanted in _VOICE_PREFERENCE:
        for voice in voices:
            if wanted in (voice.name or "").lower():
                engine.setProperty("voice", voice.id)
                return voice.name
    return voices[0].name if voices else None


def _new_engine():
    """Create a fresh engine, imported lazily so module import stays cheap."""
    import pyttsx3

    return pyttsx3.init()


def synthesise(text: str, out_path) -> Tuple[dict, List[str]]:
    """Write `text` to a WAV file at `out_path`.

    Returns (result, warnings).
    A failure gives ok=False with a warning instead of raising.
    """
    warnings: List[str] = []
    out_path = Path(out_path)
    t0 = time.perf_counter()

    def _failed(reason: str) -> Tuple[dict, List[str]]:
        warnings.append(reason)
        return (
            {
                "ok": False,
                "path": str(out_path),
                "voice": "",
                "rate_wpm": SPEECH_RATE_WPM,
                "duration_seconds": 0.0,
                "latency_seconds": round(time.perf_counter() - t0, 3),
            },
            warnings,
        )

    if not text or not text.strip():
        return _failed("No feedback text was supplied, so no audio was synthesised.")
    if len(text) > _MAX_CHARS:
        warnings.append(
            f"Feedback text was {len(text)} characters and was truncated to "
            f"{_MAX_CHARS} before synthesis."
        )
        text = text[:_MAX_CHARS]

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with _SPEAK_LOCK:
            engine = _new_engine()
            voice = _configure(engine) or ""
            engine.save_to_file(text, str(out_path))
            engine.runAndWait()
            engine.stop()
    except Exception as exc:  # driver boundary
        return _failed(
            f"Speech synthesis failed ({exc}); the spoken feedback was skipped."
        )

    if not out_path.exists() or out_path.stat().st_size == 0:
        return _failed(
            "Speech synthesis produced no audio file; the spoken feedback was skipped."
        )

    duration = 0.0
    try:
        with wave.open(str(out_path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate() or 1
            duration = frames / float(rate)
    except Exception:
        # Non-WAV container from an odd driver.
        warnings.append(
            "The synthesised audio could not be read as WAV, so its duration is "
            "unknown; the file was still written."
        )

    return (
        {
            "ok": True,
            "path": str(out_path),
            "voice": voice,
            "rate_wpm": SPEECH_RATE_WPM,
            "duration_seconds": round(float(duration), 3),
            "latency_seconds": round(time.perf_counter() - t0, 3),
        },
        warnings,
    )


# The question currently playing, so it can be cut short.
# `stop` asks the speaking thread to stop; `started` is set once the words reach the voice (the tests wait on it).
_CURRENT = {"thread": None, "stop": None, "started": None}

# SpeechVoiceSpeakFlags.
# Async returns as soon as the text is queued; purge discards whatever is being said, including the sentence in progress.
SVSF_ASYNC = 1
SVSF_PURGE = 2

# How often the speaking thread looks up to see whether it should stop.
# This is the worst-case delay between pressing Start and the voice going quiet.
_POLL_MS = 50

# How long stop_speaking waits for the voice to stop.
# Normally one poll.
_STOP_WAIT_S = 1.0


def _sapi_rate(wpm: int) -> int:
    """Convert words per minute to the Windows voice rate (-10 to 10).

    Uses the same curve as pyttsx3, so 175 words per minute is 1.
    """
    return max(-10, min(10, int(math.log(wpm / 156.63, 1.11))))


def _new_voice():
    """A Windows voice for the calling thread, with the interviewer's voice and pace.

    Used directly instead of through pyttsx3, so speech runs in the background and can be cut off within one poll.
    """
    import comtypes.client

    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    chosen = None
    for wanted in _VOICE_PREFERENCE:
        for token in voice.GetVoices():
            if wanted in (token.GetDescription() or "").lower():
                chosen = token
                break
        if chosen is not None:
            break
    if chosen is not None:
        voice.Voice = chosen
    voice.Rate = _sapi_rate(SPEECH_RATE_WPM)
    return voice


def _com_apartment():
    """Enter COM on this thread; returns what to call on the way out."""
    try:
        import comtypes

        comtypes.CoInitialize()
        return comtypes.CoUninitialize
    except Exception:  # already initialised, or not Windows
        return lambda: None


def _speak_mac(text: str, stop: threading.Event, started: threading.Event) -> None:
    """Say `text` with the Mac's own voice, ending it early if `stop` is set."""
    command = ["say", "-r", str(SPEECH_RATE_WPM)]
    listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    installed = [line.split("  ")[0].strip() for line in listing.splitlines()]
    for wanted in _MAC_VOICE_PREFERENCE:
        match = next((v for v in installed if v.lower().startswith(wanted)), None)
        if match:
            command += ["-v", match]
            break
    process = subprocess.Popen(command + [text[:_MAX_CHARS]])
    started.set()
    while process.poll() is None:
        if stop.wait(_POLL_MS / 1000):
            process.terminate()
            process.wait(timeout=_STOP_WAIT_S)
            break


def stop_speaking() -> None:
    """Stop whatever is being said.
    Safe to call when nothing is.

    Waits until the voice is quiet, so the microphone never records the question.
    """
    stop, thread = _CURRENT["stop"], _CURRENT["thread"]
    if stop is not None:
        stop.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=_STOP_WAIT_S)
        if thread.is_alive():
            logger.warning("The spoken question did not stop within %.1fs", _STOP_WAIT_S)
    _CURRENT["thread"] = None
    _CURRENT["stop"] = None


def speak_async(text: str) -> None:
    """Start saying `text` and return at once, so the question shows on screen while it is read."""
    if not text or not text.strip():
        return
    stop_speaking()
    stop, started = threading.Event(), threading.Event()

    def run():
        leave = _com_apartment()
        try:
            with _SPEAK_LOCK:
                if stop.is_set():
                    return
                if sys.platform == "darwin":
                    _speak_mac(text, stop, started)
                    return
                voice = _new_voice()
                if stop.is_set():
                    return
                voice.Speak(text[:_MAX_CHARS], SVSF_ASYNC)
                started.set()
                while not voice.WaitUntilDone(_POLL_MS):
                    if stop.is_set():
                        voice.Speak("", SVSF_ASYNC | SVSF_PURGE)
                        break
        except Exception as exc:
            # The question is on screen anyway.
            logger.debug("Could not play spoken output: %s", exc)
        finally:
            leave()

    thread = threading.Thread(target=run, daemon=True, name="coach-speech")
    _CURRENT["thread"] = thread
    _CURRENT["stop"] = stop
    _CURRENT["started"] = started
    thread.start()


def speak(text: str) -> Tuple[bool, List[str]]:
    """Say `text` aloud and wait until it finishes.

    Run it off the page thread.
    Returns (spoken, warnings) and never raises for expected failures.
    """
    warnings: List[str] = []
    if not text or not text.strip():
        warnings.append("No text was supplied, so nothing was spoken.")
        return False, warnings
    try:
        with _SPEAK_LOCK:
            engine = _new_engine()
            _configure(engine)
            engine.say(text[:_MAX_CHARS])
            engine.runAndWait()
            engine.stop()
    except Exception as exc:  # driver boundary
        warnings.append(
            f"Could not play spoken output ({exc}); the text was shown on screen only."
        )
        return False, warnings
    return True, warnings
