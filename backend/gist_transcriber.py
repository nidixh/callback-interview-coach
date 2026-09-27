# gist_transcriber.py
"""A quick, rough transcript used to decide whether to ask a follow-up.

Usage: gist_transcriber.transcribe("take_01.wav")

The work runs in gist_worker.py in a separate process; this module starts and talks to it.

It uses Whisper base.en, which is far faster than the medium model used for the report, so the interviewer can reply without a long silence.
It is less accurate, which is fine for deciding, but never quoted back to the candidate.

Every failure returns "" instead of raising.
No transcript simply means no follow-up.
"""

from __future__ import annotations

import atexit
import json
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

GIST_MODEL_SIZE = "base.en"
SAMPLE_RATE = 16000

HERE = Path(__file__).parent
WORKER = HERE / "gist_worker.py"

# Importing torch and whisper cold was measured at 3.6 to 7.1 s here, so the worker is given room to come up.
# It is only paid once per session.
READY_TIMEOUT_S = 90.0

# Longest wait for one answer's transcript, so a stuck worker cannot freeze the interview.
ANSWER_TIMEOUT_S = 300.0

_PROC: Optional[subprocess.Popen] = None
_LOCK = threading.Lock()


def _worker_command():
    return [sys.executable, str(WORKER)]


def _spawn() -> Optional[subprocess.Popen]:
    """Start the worker and wait for it to say it is ready."""
    try:
        proc = subprocess.Popen(
            _worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=str(HERE),
        )
    except Exception as exc:  # no gist means no follow-up
        logger.warning("Could not start the gist worker: %s", exc)
        return None

    try:
        line = _readline(proc, READY_TIMEOUT_S)
        if line is None:
            raise RuntimeError(
                f"it said nothing within {READY_TIMEOUT_S:.0f}s")
        if not line:
            raise RuntimeError("the worker exited before it was ready")
        if not json.loads(line).get("ready"):
            raise RuntimeError(f"unexpected greeting: {line.strip()!r}")
    except Exception as exc:
        logger.warning("The gist worker did not come up: %s", exc)
        _kill(proc)
        return None
    return proc


def _readline(proc: subprocess.Popen, timeout: float) -> Optional[str]:
    """One line from the worker, or None if it did not arrive in time.

    Windows cannot wait on a pipe with a time limit, so the read runs on a thread the caller can give up on.
    Returns "" when the worker has exited.
    """
    result: List[str] = []

    def read():
        try:
            result.append(proc.stdout.readline())
        except Exception:
            # A closed pipe reads as nothing.
            result.append("")

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        return None
    return result[0] if result else ""


def _kill(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    try:
        proc.kill()
    except Exception:  # it may already be gone
        pass


def _ensure() -> Optional[subprocess.Popen]:
    """The running worker, started or restarted as needed."""
    global _PROC
    if _PROC is not None and _PROC.poll() is None:
        return _PROC
    if _PROC is not None:
        logger.warning("The gist worker had died; starting another")
    _PROC = _spawn()
    return _PROC


def _ask(request: dict, timeout: Optional[float] = None) -> Optional[dict]:
    """Send one request and read its reply, or None if the worker could not answer.

    Requests take turns, because captions and the interviewer both use the same pipe.
    """
    with _LOCK:
        return _exchange(request,
                         ANSWER_TIMEOUT_S if timeout is None else timeout)


def _exchange(request: dict, timeout: float) -> Optional[dict]:
    """Send one request to the gist worker and wait for its reply.
    None if the worker has died.
    """
    proc = _ensure()
    if proc is None:
        return None
    global _PROC
    try:
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        line = _readline(proc, timeout)
    except Exception as exc:
        # A broken pipe means it died.
        logger.warning("The gist worker stopped responding: %s", exc)
        _kill(proc)
        _PROC = None
        return None

    if line is None:
        logger.warning("The gist worker hung on %s; replacing it",
                       request.get("cmd"))
        _kill(proc)
        _PROC = None
        return None

    if not line:
        # No reply and a closed pipe: the process died mid-request, which is what a native CUDA fault looks like from this side.
        logger.warning("The gist worker died while answering %s",
                       request.get("cmd"))
        _kill(proc)
        _PROC = None
        return None

    try:
        return json.loads(line)
    except Exception as exc:
        logger.warning("The gist worker sent something unreadable: %s", exc)
        return None


def warm() -> bool:
    """Start the worker early.
    True when it is ready.

    Called while the questions are being prepared, so the start up delay does not fall after the first answer.
    """
    return _ensure() is not None


def transcribe(audio_path, timeout: Optional[float] = None) -> str:
    """The rough text of one answer, or "" if it could not be produced.

    `timeout` shortens the wait.
    Live captions use it so a stuck worker is replaced quickly.
    """
    path = Path(audio_path)
    if not path.exists():
        logger.debug("No audio at %s to take a gist from", path)
        return ""

    reply = _ask({"cmd": "transcribe", "path": str(path)}, timeout)
    if not reply or not reply.get("ok"):
        if reply:
            logger.warning("Gist transcription failed: %s", reply.get("error"))
        return ""
    return str(reply.get("text") or "").strip()


def release() -> int:
    """Free the quick transcript model and return the graphics memory reclaimed.

    The worker keeps running.
    Freeing the memory lets the follow-up model use the 4 GB card.
    """
    reply = _ask({"cmd": "release"})
    if not reply or not reply.get("ok"):
        return 0
    try:
        return int(reply.get("freed") or 0)
    except (TypeError, ValueError):
        return 0


def shutdown() -> None:
    """Stop the worker.
    Safe to call when there is not one.
    """
    global _PROC
    proc, _PROC = _PROC, None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
        proc.stdin.flush()
        proc.wait(timeout=5)
    except Exception:  # asking nicely is best effort
        _kill(proc)


atexit.register(shutdown)
