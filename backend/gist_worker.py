# gist_worker.py
"""Quick transcription, run in its own Python process.

Usage: python gist_worker.py

Reads one JSON request per line and writes one JSON reply per line. {"cmd": "transcribe", "path": "take_01.wav"} returns {"ok": true, "text": "..."}, and {"cmd": "release"} frees the model.

It runs in its own process because a graphics driver crash kills the whole process, and Python cannot catch it.
If this process dies, the interview carries on and a new worker starts for the next answer.

The process stays alive between answers, because starting torch and Whisper takes several seconds.
Only the model is loaded and freed. stdout carries replies only, so all other output is sent to stderr.
"""

from __future__ import annotations

import gc
import json
import sys

SAMPLE_RATE = 16000
GIST_MODEL_SIZE = "base.en"

_MODELS: dict = {}
_REAL_STDOUT = sys.stdout


def _reserve_stdout() -> None:
    global _REAL_STDOUT
    _REAL_STDOUT = sys.stdout
    sys.stdout = sys.stderr


def _reply(payload: dict) -> None:
    _REAL_STDOUT.write(json.dumps(payload) + "\n")
    _REAL_STDOUT.flush()


def _select_device() -> str:
    try:
        import torch

        if "cuda" in _MODELS:
            return "cuda"
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # no torch is simply CPU
        return "cpu"


def _get_model(device: str):
    if device not in _MODELS:
        import whisper

        _MODELS[device] = whisper.load_model(GIST_MODEL_SIZE, device=device)
    return _MODELS[device]


def transcribe(path: str) -> str:
    """The rough text of one answer, or "" if it could not be produced."""
    import whisper

    audio = whisper.load_audio(str(path), sr=SAMPLE_RATE)
    device = _select_device()
    model = _get_model(device)
    result = model.transcribe(
        audio,
        language="en",
        temperature=0.0,  # deterministic, like every other stage
        condition_on_previous_text=False,
        fp16=(device == "cuda"),
    )
    return str((result or {}).get("text", "")).strip()


def release() -> int:
    """Free the weights, keeping the interpreter.
    Returns bytes reclaimed.
    """
    if not _MODELS:
        return 0
    had_cuda = "cuda" in _MODELS
    _MODELS.clear()
    gc.collect()
    if not had_cuda:
        return 0
    try:
        import torch

        if not torch.cuda.is_available():
            return 0
        before, _total = torch.cuda.mem_get_info()
        torch.cuda.empty_cache()
        after, _total = torch.cuda.mem_get_info()
        return max(after - before, 0)
    except Exception:  # housekeeping is best effort
        return 0


def handle(request: dict) -> dict:
    command = (request or {}).get("cmd")
    if command == "transcribe":
        return {"ok": True, "text": transcribe(request.get("path", ""))}
    if command == "release":
        return {"ok": True, "freed": release()}
    if command == "stop":
        return {"ok": True, "stopping": True}
    return {"ok": False, "error": f"unknown command {command!r}"}


def main() -> int:
    """Transcribe what the parent sends, one JSON line in and one out, until the pipe closes."""
    _reserve_stdout()
    _reply({"ready": True})

    while True:
        line = sys.stdin.readline()
        if not line:  # the parent closed the pipe
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except Exception as exc:  # keep serving
            _reply({"ok": False, "error": f"bad request: {exc}"})
            continue
        try:
            reply = handle(request)
        except Exception as exc:
            # One bad answer, not the session.
            _reply({"ok": False, "error": str(exc)})
            continue
        _reply(reply)
        if reply.get("stopping"):
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
