# webcam_worker.py
"""Watches the webcam during answers, inside the separate camera environment.

Usage: python webcam_worker.py [output.mp4] [target_fps] [preview.jpg] [max_seconds]

Started by webcam_recorder.py.
The camera opens once and stays open.
Commands come in on stdin, one per line: "record <path>" starts a take, "pause" finishes it, "stop" exits.
Each gets one JSON reply on stdout.

If the main program dies, stdin closes, which counts as a stop, so the camera is always released.

The camera reports a frame rate of 0 on Windows, so the loop keeps its own steady pace and reports the rate it achieved.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time

TARGET_FPS = 15.0

# Frames per second for the self view preview.
PREVIEW_FPS = 12.0
PREVIEW_QUALITY = 70

# How often the face check runs.
# Presence does not change from frame to frame.
FACE_CHECK_FPS = 2.0

# How often gaze is measured once the landmarker has loaded.
# About a tenth of one CPU core.
GAZE_FPS = 10.0
CALIBRATION_S = 3.0
LIVE_WINDOW_S = 1.0

MODEL_LANDMARKS = "mediapipe-face-landmarker"
MODEL_HAAR = "live-haar-frontalface"


def _face_cascade(cv2):
    """The same face detector the analyser uses, so the live warning matches the report.
        
    """
    import facial_worker

    return facial_worker, cv2.CascadeClassifier(
        cv2.data.haarcascades + facial_worker.CASCADE_FILE)


def _stability(centres, width, height):
    """Head steadiness, from the analyser so both give the same number.

    Falls back to fully steady only when the analyser cannot be imported, in which case no face was seen and the channel is left out.
    """
    try:
        import facial_worker

        return facial_worker._stability(centres, width, height)
    except Exception:
        # Never at the cost of the interview.
        return 1.0


def _find_face(cv2, facial_worker, cascade, frame):
    """The centre of the face in this frame, or None.
    Never raises.

    One detection feeds both the live warning and the presence and steadiness in the report.
    """
    try:
        grey = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        boxes = cascade.detectMultiScale(
            grey, scaleFactor=facial_worker.SCALE_FACTOR,
            minNeighbors=facial_worker.MIN_NEIGHBOURS)
        box = facial_worker.pick_face(boxes, frame.shape[0])
        if box is None:
            return None
        x, y, w, h = box[0], box[1], box[2], box[3]
        return (x + w / 2.0, y + h / 2.0)
    except Exception:
        # Never at the cost of the interview.
        return None


def _write_face_status(status, path: str) -> None:
    """Save whether the candidate is visible right now, and how they look.

    Written beside the preview so the page can warn about lighting in time.
    `status` is _live_status()'s dictionary; a plain bool still means face or no face.
    """
    if not path:
        return
    if not isinstance(status, dict):
        status = {"face": bool(status)}
    try:
        temporary = f"{path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(status, handle)
        os.replace(temporary, path)
    except Exception:
        # A mirror is never worth the interview.
        pass


def _live_status(signals, looking, calibrated: bool, gaze_on: bool, seen=None) -> dict:
    """What the page shows about the camera right now.

    Always includes `face`.
    With the landmarker running it also says whether the candidate is looking at the camera (over the last second, so a blink does not flicker it), and the framing and lighting verdicts.
    """
    import gaze

    face = (signals is not None) if seen is None else bool(seen)
    framing = light = None
    if signals:
        framing = gaze.framing_verdict(signals)
        if "face_luma" in signals and "frame_luma" in signals:
            light = gaze.lighting_verdict(signals["face_luma"], signals["frame_luma"])
    # Where the face is: centre and height as shares of the frame, so the self-view's brackets can close around the face the coach is measuring.
    box = None
    if signals:
        box = [round(signals["centre"][0], 3), round(signals["centre"][1], 3),
               round(signals["face_size"], 3)]
    return {"face": face, "looking": looking if gaze_on else None, "framing": framing,
            "light": light, "calibrated": bool(calibrated), "gaze": bool(gaze_on), "box": box}


class _LiveGaze:
    """Eye contact over the last second, for the live light."""

    def __init__(self, window_s: float = LIVE_WINDOW_S):
        self.window_s = window_s
        self._states = []

    def add(self, now: float, state: str) -> None:
        self._states.append((now, state))
        cutoff = now - self.window_s
        while self._states and self._states[0][0] < cutoff:
            self._states.pop(0)

    def looking(self, now: float):
        """True, False, or None when nothing recent was seen (or all blinks)."""
        recent = [s for t, s in self._states if t >= now - self.window_s]
        contact = sum(1 for s in recent if s == "contact")
        away = sum(1 for s in recent if s in ("away", "absent"))
        if not contact and not away:
            return None
        return contact >= away


class _Calibration:
    """Three seconds of "look at the camera", collected as the loop runs."""

    def __init__(self, seconds: float, now: float):
        self.until = now + seconds
        self.samples = []

    def add(self, signals) -> None:
        if signals:
            self.samples.append(signals)

    def due(self, now: float) -> bool:
        return now >= self.until

    def result(self) -> dict:
        import gaze

        baseline = gaze.calibrate(self.samples)
        if baseline is None:
            return {"ok": False, "frames": len(self.samples),
                    "error": "I could not see your eyes clearly enough. Face the "
                             "camera with the light in front of you and try again."}
        return {"ok": True, "baseline": baseline}


def _write_preview(cv2, frame, path: str) -> None:
    """Save the latest frame for the page's self view.

    Written to a temporary name and renamed into place, so the page never shows half a frame.
    Failures are ignored, because the preview is never worth stopping for.
    """
    if not path:
        return
    try:
        ok, buffer = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), PREVIEW_QUALITY])
        if not ok:
            return
        temporary = f"{path}.tmp"
        with open(temporary, "wb") as handle:
            handle.write(buffer.tobytes())
        os.replace(temporary, path)
    except Exception:
        # Never at the cost of the recording.
        pass


def _start_landmarker():
    """MediaPipe's Face Landmarker, loading in the background, or None.

    None when turned off (CALLBACK_GAZE=0) or when MediaPipe cannot be imported.
    """
    if os.environ.get("CALLBACK_GAZE", "1") == "0":
        return None
    try:
        import landmarker

        return landmarker.Landmarker()
    except Exception:
        # Presence without gaze is still a channel.
        return None


# A backstop against an orphan; far longer than any answer.
MAX_SECONDS = 900.0


def _reply(payload: dict) -> None:
    """One JSON object per line: the protocol the parent reads."""
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _read_commands(commands: "queue.Queue[str]",
                   closed: threading.Event) -> None:
    """Pass the main program's commands to the frame loop until the pipe closes.

    Uses readline(), because iterating over stdin reads ahead and can delay a command.
    """
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                # The parent closed the pipe, or died.
                break
            commands.put(line.strip())
    except Exception:
        # A closed pipe is a stop like any other.
        pass
    closed.set()


class _Take:
    """One answer being watched while the camera stays open.

    No video is saved.
    Presence and steadiness are measured live, which is all the report needs.
    """

    def __init__(self, cv2, path: str, target_fps: float, size):
        # `path` is kept only so the parent's per-take bookkeeping still lines up.
        # Nothing is written to it.
        self.path = path
        self.frames = 0
        self.sampled = 0
        self.with_face = 0
        self.centres = []
        # Landmarker frames: {"t": seconds into the answer, "signals": ...}.
        self.gaze_samples = []
        self.started = time.perf_counter()

    def opened(self) -> bool:
        return True  # nothing to open

    def write(self, frame) -> None:
        self.frames += 1

    def observe(self, centre) -> None:
        """One face check during this answer.
        `centre` is None for no face.
        """
        self.sampled += 1
        if centre is not None:
            self.with_face += 1
            self.centres.append(centre)

    def observe_frame(self, seconds: float, signals, size) -> None:
        """Record one landmarker frame, `seconds` into the answer.
        None means no face.

        Counts toward presence and steadiness the same way as a Haar check.
        """
        centre = None
        if signals:
            centre = (signals["centre"][0] * size[0], signals["centre"][1] * size[1])
        self.observe(centre)
        self.gaze_samples.append({"t": float(seconds), "signals": signals})

    def finish(self, target_fps: float, size, baseline=None) -> dict:
        """Summarise the recording: frames sampled, how often a face was found, and the gaze measures."""
        elapsed = time.perf_counter() - self.started
        rate = (self.with_face / self.sampled) if self.sampled else 0.0
        faces = {
            "model": MODEL_LANDMARKS if self.gaze_samples else MODEL_HAAR,
            "frames_sampled": self.sampled,
            "frames_with_face": self.with_face,
            "face_detection_rate_0to1": round(rate, 4),
            "gaze_centre_stability_0to1": round(
                _stability(self.centres, size[0], size[1]), 4),
        }
        if self.gaze_samples:
            import gaze

            last = self.gaze_samples[-1]["t"]
            faces["gaze"] = gaze.summarise(self.gaze_samples, baseline,
                                           duration=max(elapsed, last))
        return {
            "ok": True,
            "frames": self.frames,
            "seconds": round(elapsed, 3),
            "target_fps": target_fps,
            "achieved_fps": round(self.frames / elapsed, 2) if elapsed > 0 else 0.0,
            "width": size[0],
            "height": size[1],
            "faces": faces,
        }


def main() -> int:
    """Record the camera to a file, taking commands from the parent on stdin until told to stop."""
    if len(sys.argv) < 2:
        _reply({"ok": False, "error": "no output path given"})
        return 2

    out_path = sys.argv[1]
    target_fps = float(sys.argv[2]) if len(sys.argv) > 2 else TARGET_FPS
    # The optional time limit is argv[4]; argv[3] is the preview path.
    preview_path = sys.argv[3] if len(sys.argv) > 3 else ""
    max_seconds = float(sys.argv[4]) if len(sys.argv) > 4 else MAX_SECONDS

    try:
        import cv2
    except Exception as exc:
        # Reported to the parent as data.
        _reply({"ok": False, "error": f"OpenCV unavailable: {exc}"})
        return 1

    commands: "queue.Queue[str]" = queue.Queue()
    closed = threading.Event()
    threading.Thread(target=_read_commands, args=(commands, closed),
                     daemon=True).start()

    # CAP_DSHOW is markedly faster to open than the default backend on Windows, and the candidate is waiting for the question while this happens.
    capture = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not capture.isOpened():
        capture.release()
        capture = cv2.VideoCapture(0)
    if not capture.isOpened():
        _reply({"ok": False, "error": "no camera could be opened"})
        return 1

    ok, frame = capture.read()
    if not ok or frame is None:
        capture.release()
        _reply({"ok": False, "error": "the camera opened but returned no frames"})
        return 1

    height, width = frame.shape[:2]
    size = (width, height)
    take = None

    # An output path on the command line means start recording at once.
    # Without one, the worker waits with the camera open, showing a preview.
    if out_path:
        take = _Take(cv2, out_path, target_fps, size)
        if not take.opened():
            capture.release()
            _reply({"ok": False,
                    "error": "the video file could not be opened for writing"})
            return 1
        take.write(frame)

    # Only now is the camera sending frames.
    # The main program waits for this line before starting the microphone, so no answer loses its opening seconds.
    _reply({"ready": True})

    interval = 1.0 / max(target_fps, 1.0)
    preview_interval = 1.0 / PREVIEW_FPS
    face_interval = 1.0 / FACE_CHECK_FPS
    gaze_interval = 1.0 / GAZE_FPS
    started = time.perf_counter()
    next_frame_at = started
    next_preview_at = started
    next_face_at = started

    face_path = f"{preview_path}.json" if preview_path else ""
    # Set up whether or not a preview was asked for: these detections are the facial channel of the report now, not just the mirror's warning light.
    try:
        facial_worker, cascade = _face_cascade(cv2)
    except Exception:
        # The interview survives without a face channel.
        facial_worker = cascade = None

    # The landmarker loads on a thread, now that the camera is up.
    # Until it is ready, and for good if it cannot load, the Haar check above carries on.
    landmarker = _start_landmarker()
    baseline = None  # this session's calibration, once taken
    calibration = None  # a calibration in progress
    live = _LiveGaze()

    _write_preview(cv2, frame, preview_path)

    while not closed.is_set():
        now = time.perf_counter()
        if now - started >= max_seconds:
            break

        # A calibration is three seconds of watching; commands wait for it.
        command = None
        if calibration is None:
            try:
                command = commands.get_nowait()
            except queue.Empty:
                command = None
        elif calibration.due(now):
            reply = calibration.result()
            if reply.get("ok"):
                baseline = reply["baseline"]
            _reply(reply)
            calibration = None

        if command is not None:
            if command == "stop" or command.startswith("stop"):
                break
            if command.startswith("record"):
                path = command[len("record"):].strip()
                if take is not None:
                    # A take left open is still finished.
                    _reply(take.finish(target_fps, size, baseline))
                take = _Take(cv2, path, target_fps, size)
                if not take.opened():
                    take = None
                    _reply({"ok": False,
                            "error": f"could not open {path} for writing"})
                else:
                    _reply({"ok": True, "recording": True})
                continue
            if command == "pause":
                if take is None:
                    _reply({"ok": True, "frames": 0, "seconds": 0.0,
                            "target_fps": target_fps, "achieved_fps": 0.0,
                            "width": width, "height": height})
                else:
                    _reply(take.finish(target_fps, size, baseline))
                    take = None
                continue
            if command.startswith("calibrate"):
                if landmarker is None or (landmarker.ready.is_set() and not landmarker.usable()):
                    reason = landmarker.error if landmarker is not None else "gaze is switched off"
                    _reply({"ok": False, "error": f"gaze is not available ({reason})"})
                    continue
                try:
                    seconds = float(command[len("calibrate"):].strip() or CALIBRATION_S)
                except ValueError:
                    seconds = CALIBRATION_S
                calibration = _Calibration(max(1.0, min(seconds, 10.0)), now)
                continue
            if command == "uncalibrate":
                baseline = None
                _reply({"ok": True, "calibrated": False})
                continue
            if command:
                _reply({"ok": False, "error": f"unknown command {command!r}"})
                continue

        if now < next_frame_at:
            time.sleep(min(next_frame_at - now, interval))
            continue
        next_frame_at += interval

        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        if take is not None:
            take.write(frame)

        # The preview runs whether or not anything is recorded, so the candidate can fix their framing before the first question.
        if preview_path and now >= next_preview_at:
            next_preview_at = now + preview_interval
            _write_preview(cv2, frame, preview_path)

        gaze_on = landmarker is not None and landmarker.usable()
        if gaze_on and now >= next_face_at:
            # Keep a steady beat.
            # If the loop falls more than one step behind, it restarts the beat from now.
            next_face_at += gaze_interval
            if next_face_at < now:
                next_face_at = now + gaze_interval
            signals = landmarker.signals(cv2, frame, now - started)
            import gaze

            live.add(now, gaze.classify(signals, baseline))
            _write_face_status(_live_status(signals, live.looking(now), baseline is not None, True),
                               face_path)
            if take is not None:
                take.observe_frame(now - take.started, signals, size)
            if calibration is not None:
                calibration.add(signals)
        elif not gaze_on and cascade is not None and now >= next_face_at:
            next_face_at = now + face_interval
            centre = _find_face(cv2, facial_worker, cascade, frame)
            _write_face_status(_live_status(None, None, False, False, seen=centre is not None),
                               face_path)
            if take is not None:
                take.observe(centre)

    # Whatever was still being written is finished properly, so that stopping mid-answer leaves a playable file rather than an unopenable one.
    if take is not None:
        _reply(take.finish(target_fps, size, baseline))
    if landmarker is not None:
        landmarker.close()
    capture.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
