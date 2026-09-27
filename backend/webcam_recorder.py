# webcam_recorder.py
"""Controls the webcam worker, which runs in the separate camera environment.

The main program side of vision/webcam_worker.py.
OpenCV lives in vision_env because its libraries clash with the speech libraries.

Usage: cam = WebcamRecorder(); cam.open() once, then cam.start(Path("take_01.mp4")) and cam.stop() for each answer, and cam.close() at the end.

Opening takes about 1.8 s, so the camera opens once per session and shows a preview between answers.
Stopping sends a command and waits for the reply, and only kills the worker if it does not answer. stop() reports problems rather than raising; start() raises when it cannot begin, so the caller records audio only.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
# vision_env is made by setup at the top of the project, one level above backend/.
VISION_PYTHON = HERE.parent / "vision_env" / "Scripts" / "python.exe"
VISION_PYTHON_POSIX = HERE.parent / "vision_env" / "bin" / "python"
WORKER = HERE / "vision" / "webcam_worker.py"

TARGET_FPS = 15.0

# How long to wait for the worker to finish a take after being asked.
STOP_TIMEOUT_S = 10.0
KILL_TIMEOUT_S = 5.0

# Beginning a take is only opening a file against an already-running camera.
RECORD_TIMEOUT_S = 10.0

# How long to wait for the camera to open.
# It usually takes about 1.8 s.
READY_TIMEOUT_S = 15.0

# Calibrating is the worker watching for its seconds, then answering.
# This is how long past those seconds to wait for the answer.
CALIBRATE_GRACE_S = 10.0
CALIBRATE_S = 3.0


def _read_line(proc, timeout: float) -> str:
    """One line from the worker, or "" if it did not arrive in time.

    Windows cannot wait on a pipe with a time limit, so the read runs on a thread that can be abandoned.
    """
    received: List[str] = []

    def _read() -> None:
        try:
            received.append(proc.stdout.readline())
        except Exception as exc:  # treated as nothing said
            logger.debug("Reading from the camera failed: %s", exc)

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        return ""
    return received[0] if received else ""



class WebcamError(RuntimeError):
    """Recording could not be started at all."""


def _interpreter() -> Optional[Path]:
    for candidate in (VISION_PYTHON, VISION_PYTHON_POSIX):
        if candidate.exists():
            return candidate
    return None


def preview_path() -> str:
    """Where the worker saves preview frames for the self view, if anywhere.

    Passed through an environment variable, so the classes in between need not know about it.
    """
    return os.environ.get("COACH_PREVIEW_PATH", "")


def _spawn(interpreter: Path, worker: Path, out_path: Path, target_fps: float):
    return subprocess.Popen(
        [str(interpreter), str(worker), str(out_path), str(target_fps),
         preview_path()],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class WebcamRecorder:
    """One webcam, recording one take at a time, in another process."""

    def __init__(self, target_fps: float = TARGET_FPS):
        self.target_fps = float(target_fps)
        self._proc = None
        self._recording = False
        self.summary: dict = {}
        self.warnings: List[str] = []
        # One command at a time on the pipe, because calibration and recording come from different threads.
        self._talking = threading.Lock()

    def _command(self, line: str, timeout: float):
        """Send one command and read its single reply, or None if none came."""
        with self._talking:
            return self._command_unlocked(line, timeout)

    def _command_unlocked(self, line: str, timeout: float):
        """Send one command to the camera worker and wait for its reply.
        The caller holds the lock.
        """
        proc = self._proc
        if proc is None:
            return None
        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except Exception as exc:
            # A broken pipe means it died.
            logger.warning("The camera stopped listening: %s", exc)
            return None

        raw = _read_line(proc, timeout)
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except Exception:  # anything unparseable is no reply
            logger.warning("The camera said something unreadable: %s", raw[:120])
            return None

    def open(self) -> None:
        """Start the camera and leave it running.
        Safe to call twice.

        Separate from start(), so the camera is on for the whole session and the candidate sees themselves before the first question.
        """
        if self._proc is not None and self._proc.poll() is None:
            return

        interpreter = _interpreter()
        if interpreter is None:
            raise WebcamError(
                "The vision environment was not found at vision_env, so the "
                "camera cannot be used."
            )
        if not Path(WORKER).exists():
            raise WebcamError("The webcam worker script is missing.")

        try:
            # No output path: the camera comes up idle and waits to be told.
            proc = _spawn(interpreter, Path(WORKER), "", self.target_fps)
        except Exception as exc:  # re-raised as our own type
            raise WebcamError(f"The camera process could not be started ({exc}).") from exc

        # Wait until the camera is really sending frames, so the start of each answer is not lost.
        try:
            self._await_ready(proc)
        except WebcamError:
            self._force(proc)
            raise

        self._proc = proc

    def start(self, video_path) -> None:
        """Begin a take.
        Opens the camera first if nobody has yet.
        """
        if self._recording:
            raise WebcamError("This webcam recorder is already recording.")

        self.open()

        video_path = Path(video_path)
        video_path.parent.mkdir(parents=True, exist_ok=True)
        self.summary = {}
        self.warnings = []

        reply = self._command(f"record {video_path}", RECORD_TIMEOUT_S)
        if reply is None or not reply.get("ok"):
            reason = (reply or {}).get("error", "it did not say why")
            raise WebcamError(f"The camera would not start recording ({reason}).")
        self._recording = True

    def calibrate(self, seconds: float = CALIBRATE_S) -> dict:
        """Learn where this person looks when looking at the camera.

        Used for every answer after it.
        Always returns a dictionary: {"ok": True, "baseline": {...}} or {"ok": False, "error": "..."}.
        """
        if self._proc is None or self._proc.poll() is not None:
            return {"ok": False, "error": "The camera is not on yet."}
        reply = self._command(f"calibrate {float(seconds)}",
                              float(seconds) + CALIBRATE_GRACE_S)
        if reply is None:
            return {"ok": False, "error": "The camera did not answer. Try again in a moment."}
        return reply

    def _await_ready(self, proc) -> None:
        """Block until the child says the camera is running, or give up."""
        line = _read_line(proc, READY_TIMEOUT_S).strip()
        if not line:
            raise WebcamError("The camera process ended before it started recording.")

        try:
            message = json.loads(line)
        except Exception:  # anything unparseable is a failure
            raise WebcamError(f"The camera reported something unreadable: {line[:120]}")

        if isinstance(message, dict) and message.get("ready"):
            return
        reason = (message or {}).get("error", "it did not say why")
        raise WebcamError(f"The camera could not start ({reason}).")

    def stop(self) -> None:
        """Finish the take, leaving the camera running for the next answer."""
        if not self._recording:
            return
        self._recording = False

        proc = self._proc
        if proc is None:
            return

        try:
            reply = self._command("pause", STOP_TIMEOUT_S)
        except Exception as exc:  # never worth an exception here
            logger.warning("Finishing the take failed: %s", exc)
            self.warnings.append(f"The camera did not stop cleanly ({exc}).")
            return

        if reply is None:
            # The child owes us a finished file and has not produced one.
            # It is holding the camera the next answer needs, so it goes.
            logger.warning("Webcam worker ignored the pause request; replacing it")
            self.warnings.append(
                "The camera did not finish this answer when asked, so it was "
                "stopped by force; the video for this answer may be unplayable."
            )
            self._force(proc)
            self._proc = None
            return

        self.summary = dict(reply)

    def close(self) -> None:
        """Release the camera.
        Safe when it was never opened.
        """
        proc, self._proc = self._proc, None
        self._recording = False
        if proc is None or proc.poll() is not None:
            return

        try:
            self._ask_to_stop(proc)
            proc.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            logger.warning("Webcam worker ignored the exit request; terminating")
            self.warnings.append(
                "The camera did not release when asked and was stopped by force."
            )
            self._force(proc)
        except Exception as exc:  # releasing is best effort
            logger.debug("Releasing the camera failed: %s", exc)
            self._force(proc)

    # Internals

    def _ask_to_stop(self, proc) -> None:
        try:
            proc.stdin.write("stop\n")
            proc.stdin.flush()
            proc.stdin.close()
        except Exception as exc:
            # The child may already be gone.
            logger.debug("Could not write the stop request: %s", exc)

    def _force(self, proc) -> str:
        try:
            proc.terminate()
            stdout, _ = proc.communicate(timeout=KILL_TIMEOUT_S)
            return stdout or ""
        except Exception:  # last resort
            try:
                proc.kill()
            except Exception:
                pass
            return ""
