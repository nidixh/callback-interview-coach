# facial_worker.py
"""Facial analysis worker.
Runs inside vision_env only.

Usage: python facial_worker.py <video_path> [--fps 2.0]

Reads a video, samples frames at a fixed rate, and prints one JSON object with the model used, frames sampled, frames with a face, the face detection rate, the main expression, head steadiness and the sample rate, plus "error" when "ok" is false.

DeepFace adds expression when it is installed.
Otherwise the built in Haar detector still gives presence and steadiness.
The "model" field says which one ran.

Frames are equalised first, because a face in front of a bright window is too dark for the detector.
On one backlit session this raised the frames with a face from 3% to 97%.
The looser detector settings can find false faces, which pick_face filters out.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Force the CPU before any library loads, because TensorFlow would otherwise take all the graphics memory Whisper needs.
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

DEFAULT_FPS = 2.0
# ~5 minutes at 2 fps; bounds worst-case runtime.
MAX_FRAMES = 600

# Detector settings.
# See the module docstring for the measurements behind these.
CASCADE_FILE = "haarcascade_frontalface_alt2.xml"
SCALE_FACTOR = 1.05
MIN_NEIGHBOURS = 3

# A face smaller than this share of the frame height is not the person at the camera.
# Real faces measured about 0.25; a false match was about 0.04.
MIN_FACE_HEIGHT_FRACTION = 0.12


# stdout must carry only the JSON result.
# The vision libraries print to stdout, so it is pointed at stderr during a run.
# Done in main(), so importing this module for tests changes nothing.
_REAL_STDOUT = sys.stdout


def _reserve_stdout() -> None:
    global _REAL_STDOUT
    _REAL_STDOUT = sys.stdout
    sys.stdout = sys.stderr


def pick_face(boxes, frame_height: int):
    """Choose which detected box to trust, or None.

    Takes (x, y, w, h) boxes.
    Boxes too small to be the candidate are dropped, and the largest remaining one is picked.
    """
    minimum = MIN_FACE_HEIGHT_FRACTION * max(frame_height, 1)
    plausible = [b for b in boxes if b[3] >= minimum]
    if not plausible:
        return None
    return max(plausible, key=lambda b: b[2] * b[3])


def _emit(payload: dict) -> None:
    """Write the single JSON result to the reserved stdout handle."""
    _REAL_STDOUT.write(json.dumps(payload))
    _REAL_STDOUT.flush()


def _sample_frames(video_path: str, fps: float):
    """Yield (index, frame) sampled at `fps`, plus the frame size."""
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open video: {video_path}")
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if native_fps <= 0:
        native_fps = 25.0
    step = max(1, int(round(native_fps / max(fps, 0.1))))

    frames = []
    idx = 0
    while len(frames) < MAX_FRAMES:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            frames.append(frame)
        idx += 1
    cap.release()
    return frames


def _stability(centres, width: float, height: float) -> float:
    """Turn how much the face centre moved into a 0 to 1 steadiness score.

    Relative to the frame size, so resolution does not matter.
    This is head steadiness, not gaze.
    """
    if len(centres) < 2:
        return 1.0
    xs = [c[0] / max(width, 1.0) for c in centres]
    ys = [c[1] / max(height, 1.0) for c in centres]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    var = sum((x - mean_x) ** 2 + (y - mean_y) ** 2 for x, y in zip(xs, ys))
    spread = (var / len(xs)) ** 0.5
    # A spread of 0.15 of the frame or more is treated as fully unsteady.
    return max(0.0, min(1.0, 1.0 - spread / 0.15))


def _lift_shadows(frame):
    """Even out the brightness so a backlit face can be detected.

    Only the brightness channel is changed, so skin colour stays the same.
    """
    import cv2

    ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    ycrcb[:, :, 0] = cv2.equalizeHist(ycrcb[:, :, 0])
    return cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)


def _run_deepface(frames):
    """Find the face with the Haar detector and ask DeepFace only for the expression.

    DeepFace's own detection misses most faces in shadow, so the detector finds the face and DeepFace only reads its expression.
    """
    import cv2
    from deepface import DeepFace

    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + CASCADE_FILE)
    detected = 0
    classified = 0
    centres = []
    totals: dict = {}
    h, w = (frames[0].shape[0], frames[0].shape[1]) if frames else (1, 1)

    for frame in frames:
        grey = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        face = pick_face(
            cascade.detectMultiScale(grey, scaleFactor=SCALE_FACTOR,
                                     minNeighbors=MIN_NEIGHBOURS), h)
        if face is None:
            continue
        x, y, fw, fh = face
        detected += 1
        centres.append((x + fw / 2.0, y + fh / 2.0))

        # Classified on the equalised crop: the expression model reads a silhouette no better than the detector did.
        x0, y0 = max(int(x), 0), max(int(y), 0)
        crop = _lift_shadows(frame)[y0:y0 + int(fh), x0:x0 + int(fw)]
        if crop.size == 0:
            continue
        try:
            results = DeepFace.analyze(
                crop,
                actions=["emotion"],
                enforce_detection=False,
                detector_backend="skip",
                silent=True,
            )
        except Exception:
            continue
        if isinstance(results, dict):
            results = [results]
        if not results:
            continue
        emotions = results[0].get("emotion") or {}
        if not emotions:
            continue
        classified += 1
        for emotion, score in emotions.items():
            totals[emotion] = totals.get(emotion, 0.0) + float(score)

    # Averaged over the frames actually classified, not every frame with a face, so a crop the expression model refused does not drag the mean down.
    means = {k: round(v / max(classified, 1) / 100.0, 4) for k, v in totals.items()}
    dominant = max(means, key=means.get) if means else "unavailable"
    return {
        "model": "deepface-fer",
        "frames_with_face": detected,
        "dominant_expression": dominant,
        "expression_mean_probabilities": means,
        "gaze_centre_stability_0to1": round(_stability(centres, w, h), 4),
    }


def _run_opencv(frames):
    """Fallback: face presence and head steadiness with the built in Haar detector.

    Expression is not attempted, and the result says so.
    """
    import cv2

    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + CASCADE_FILE)
    detected = 0
    centres = []
    h, w = (frames[0].shape[0], frames[0].shape[1]) if frames else (1, 1)

    for frame in frames:
        grey = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        faces = cascade.detectMultiScale(grey, scaleFactor=SCALE_FACTOR,
                                         minNeighbors=MIN_NEIGHBOURS)
        face = pick_face(faces, h)
        if face is None:
            continue
        x, y, fw, fh = face
        detected += 1
        centres.append((x + fw / 2.0, y + fh / 2.0))

    return {
        "model": "opencv-haar-frontalface",
        "frames_with_face": detected,
        "dominant_expression": "unavailable",
        "expression_mean_probabilities": {},
        "gaze_centre_stability_0to1": round(_stability(centres, w, h), 4),
    }


def main(argv) -> int:
    """Analyse the faces in one video file and print the result as a JSON line."""
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    parser.add_argument("--backend", choices=["auto", "deepface", "opencv"],
                        default="auto")
    args = parser.parse_args(argv[1:])
    _reserve_stdout()

    try:
        frames = _sample_frames(args.video, args.fps)
    except Exception as exc:
        _emit({"ok": False, "error": f"could not read video: {exc}"})
        # A clean exit with ok=false is easier for the caller than a crash.
        return 0

    if not frames:
        _emit({"ok": False, "error": "video contained no readable frames"})
        return 0

    backend_used = None
    if args.backend in ("auto", "deepface"):
        try:
            payload = _run_deepface(frames)
            backend_used = "deepface"
        except Exception as exc:
            if args.backend == "deepface":
                _emit({"ok": False, "error": f"deepface backend failed: {exc}"})
                return 0
            backend_used = None
    if backend_used is None:
        try:
            payload = _run_opencv(frames)
        except Exception as exc:
            _emit({"ok": False, "error": f"opencv backend failed: {exc}"})
            return 0

    sampled = len(frames)
    payload.update({
        "ok": True,
        "frames_sampled": sampled,
        "face_detection_rate_0to1": round(payload["frames_with_face"] / sampled, 4),
        "sample_rate_fps": args.fps,
    })
    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
