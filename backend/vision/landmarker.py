# landmarker.py
"""MediaPipe's Face Landmarker, wrapped for the camera worker. vision_env only.

Takes one frame and returns gaze.frame_signals(), or None when there is no face, so gaze.py only ever sees numbers.

TensorFlow is hidden while MediaPipe imports, which cuts the import from about 6 s to 1 s.
The model loads on a background thread; until then the worker uses the Haar presence check.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import gaze

# The model file ships in artifacts/models, two levels above backend/vision.
MODEL_PATH = Path(__file__).parents[2] / "artifacts" / "models" / "face_landmarker.task"


def _import_mediapipe():
    hidden = "tensorflow" not in sys.modules
    if hidden:
        sys.modules["tensorflow"] = None  # import raises ModuleNotFoundError
    try:
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions
    finally:
        if hidden:
            del sys.modules["tensorflow"]
    return mp, vision, BaseOptions


def face_lumas(cv2, frame, landmarks):
    """Mean brightness (0-255) of the face's box and of the whole frame."""
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    height, width = grey.shape[:2]
    xs = [p.x for p in landmarks]
    ys = [p.y for p in landmarks]
    x0, x1 = max(0, int(min(xs) * width)), min(width, int(max(xs) * width))
    y0, y1 = max(0, int(min(ys) * height)), min(height, int(max(ys) * height))
    whole = float(grey.mean())
    if x1 - x0 < 4 or y1 - y0 < 4:
        return whole, whole
    return float(grey[y0:y1, x0:x1].mean()), whole


class Landmarker:
    """The Face Landmarker in video mode, loaded in the background."""

    def __init__(self, model_path: Path = MODEL_PATH, background: bool = True):
        self.ready = threading.Event()
        self.error = ""
        self._landmarker = None
        self._mp = None
        self._last_ms = -1
        if background:
            threading.Thread(target=self._load, args=(Path(model_path),), daemon=True).start()
        else:
            self._load(Path(model_path))

    def _load(self, model_path: Path) -> None:
        """Load the face landmark model.
        On failure it records why, and the worker falls back to Haar.
        """
        try:
            if not model_path.exists():
                raise FileNotFoundError(f"no model at {model_path}")
            mp, vision, BaseOptions = _import_mediapipe()
            options = vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(model_path)),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                output_face_blendshapes=True,
                output_facial_transformation_matrixes=True,
            )
            self._landmarker = vision.FaceLandmarker.create_from_options(options)
            self._mp = mp
        except Exception as exc:
            # The worker falls back to Haar.
            self.error = f"{type(exc).__name__}: {exc}"
            self._landmarker = None
        finally:
            self.ready.set()

    def usable(self) -> bool:
        return self.ready.is_set() and self._landmarker is not None

    def signals(self, cv2, frame, seconds: float) -> Optional[Dict[str, Any]]:
        """This frame's gaze signals, or None for no face.
        Never raises.
        """
        if not self.usable():
            return None
        try:
            # Video mode needs strictly increasing timestamps.
            ms = max(int(seconds * 1000), self._last_ms + 1)
            self._last_ms = ms
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
            result = self._landmarker.detect_for_video(image, ms)
            if not result.face_landmarks:
                return None
            landmarks = result.face_landmarks[0]
            transform = None
            if result.facial_transformation_matrixes:
                transform = [[float(v) for v in row] for row in result.facial_transformation_matrixes[0]]
            shapes = None
            if result.face_blendshapes:
                shapes = {c.category_name: float(c.score) for c in result.face_blendshapes[0]}
            face_luma, frame_luma = face_lumas(cv2, frame, landmarks)
            return gaze.frame_signals(landmarks, transform, shapes, face_luma, frame_luma)
        except Exception:
            # A lost frame, never a lost interview.
            return None

    def close(self) -> None:
        # Closed explicitly: left to the garbage collector at interpreter exit it raises from inside MediaPipe's own teardown.
        landmarker, self._landmarker = self._landmarker, None
        if landmarker is not None:
            try:
                landmarker.close()
            except Exception:
                pass
