# facial_analyser.py
"""Facial analysis, run in the separate camera environment.

The camera libraries live in vision_env and run as a child process that prints JSON.
Nothing from that environment is imported here, so the two sets of libraries never clash.
If vision_env is missing or the worker fails, the result is marked unreliable and the report is built from speech and content alone.

Readings describe what was visible, not how the candidate felt (Barrett et al., 2019), and the channel carries little weight in the score.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import List, Tuple

from schema import FacialAnalysis

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
# vision_env is made by setup at the top of the project, one level above backend/.
VISION_PYTHON = HERE.parent / "vision_env" / "Scripts" / "python.exe"
VISION_PYTHON_POSIX = HERE.parent / "vision_env" / "bin" / "python"
WORKER = HERE / "vision" / "facial_worker.py"

# Two frames per second.
# Expression and posture change far more slowly than video frame rate, so sampling costs little and bounds runtime on long answers.
SAMPLE_FPS = 2.0

# Below this share of frames containing a detectable face the channel is not trustworthy: the candidate may be off-camera, badly lit, or out of frame.
RELIABILITY_MIN = 0.60

# A minute-long answer at 2 fps is 120 frames; 300 s is generous even on CPU.
TIMEOUT_S = 300


def _empty(model: str = "") -> FacialAnalysis:
    return FacialAnalysis(
        model=model,
        frames_sampled=0,
        frames_with_face=0,
        face_detection_rate_0to1=0.0,
        dominant_expression="unavailable",
        expression_mean_probabilities={},
        gaze_centre_stability_0to1=0.0,
        sample_rate_fps=SAMPLE_FPS,
        reliable=False,
        latency_seconds=0.0,
        gaze=None,
    )


def from_live(summary, sample_fps: float = 0.0) -> FacialAnalysis:
    """Build the facial result from what the camera measured during the answer.

    The camera worker measures while the candidate speaks, so no video needs to be kept.
    Presence and steadiness are the only two values the score uses.
    Expression is left out on purpose: it is not graded (Barrett et al., 2019).
    """
    summary = summary or {}
    sampled = int(summary.get("frames_sampled", 0) or 0)
    with_face = int(summary.get("frames_with_face", 0) or 0)
    rate = float(summary.get("face_detection_rate_0to1", 0.0) or 0.0)
    # Named by the worker since gaze was added; an answer measured before that was always the Haar presence check.
    model = str(summary.get("model") or "live-haar-frontalface")
    gaze = summary.get("gaze") if isinstance(summary.get("gaze"), dict) else None
    if not sampled:
        return _empty(model)
    return FacialAnalysis(
        model=model,
        frames_sampled=sampled,
        frames_with_face=with_face,
        face_detection_rate_0to1=round(rate, 4),
        dominant_expression="unavailable",
        expression_mean_probabilities={},
        gaze_centre_stability_0to1=round(
            float(summary.get("gaze_centre_stability_0to1", 0.0) or 0.0), 4),
        sample_rate_fps=sample_fps,
        reliable=rate >= RELIABILITY_MIN,
        latency_seconds=0.0,
        # Eye contact, look-aways, steadiness, framing and light, measured by the landmarker during the answer.
        # None when only presence was.
        gaze=dict(gaze) if gaze else None,
    )


def _interpreter() -> Path | None:
    for candidate in (VISION_PYTHON, VISION_PYTHON_POSIX):
        if candidate.exists():
            return candidate
    return None


def analyse_video(video_path, fps: float = SAMPLE_FPS) -> Tuple[FacialAnalysis, List[str]]:
    """Check a video for a visible face.

    Returns (result, warnings).
    Problems give an unreliable result with a warning and never raise.
    """
    warnings: List[str] = []
    t0 = time.perf_counter()

    def _degraded(reason: str, model: str = "") -> Tuple[FacialAnalysis, List[str]]:
        warnings.append(reason)
        result = _empty(model)
        result["latency_seconds"] = round(time.perf_counter() - t0, 3)
        return result, warnings

    video_path = Path(video_path)
    if not video_path.exists():
        return _degraded(
            f"No video was found at {video_path}, so the facial channel was skipped."
        )

    interpreter = _interpreter()
    if interpreter is None:
        return _degraded(
            "The facial-analysis environment was not found at vision_env, so the "
            "facial channel was skipped; install it to enable this channel."
        )
    if not WORKER.exists():
        return _degraded(
            "The facial-analysis worker script is missing, so the facial channel "
            "was skipped."
        )

    # Clear CUDA for the child so its framework cannot allocate on the GPU the transcription model owns.
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["TF_CPP_MIN_LOG_LEVEL"] = "3"

    try:
        proc = subprocess.run(
            [str(interpreter), str(WORKER), str(video_path), "--fps", str(fps)],
            capture_output=True, text=True, timeout=TIMEOUT_S, env=env,
            cwd=str(HERE),
        )
    except subprocess.TimeoutExpired:
        return _degraded(
            f"Facial analysis exceeded the {TIMEOUT_S}-second limit and was "
            "abandoned; the facial channel was skipped."
        )
    except Exception as exc:  # process boundary
        return _degraded(
            f"Facial analysis could not be started ({exc}); the facial channel "
            "was skipped."
        )

    if proc.returncode != 0:
        return _degraded(
            f"The facial-analysis worker exited with code {proc.returncode}; the "
            "facial channel was skipped."
        )

    try:
        payload = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return _degraded(
            "Facial analysis returned output that could not be read; the facial "
            "channel was skipped."
        )

    if not payload.get("ok"):
        return _degraded(
            f"Facial analysis reported a failure ({payload.get('error', 'unknown')}); "
            "the facial channel was skipped."
        )

    rate = float(payload.get("face_detection_rate_0to1", 0.0))
    reliable = rate >= RELIABILITY_MIN

    result = FacialAnalysis(
        model=str(payload.get("model", "")),
        frames_sampled=int(payload.get("frames_sampled", 0)),
        frames_with_face=int(payload.get("frames_with_face", 0)),
        face_detection_rate_0to1=round(rate, 4),
        dominant_expression=str(payload.get("dominant_expression", "unavailable")),
        expression_mean_probabilities={
            str(k): float(v)
            for k, v in (payload.get("expression_mean_probabilities") or {}).items()
        },
        gaze_centre_stability_0to1=round(
            float(payload.get("gaze_centre_stability_0to1", 0.0)), 4
        ),
        sample_rate_fps=float(payload.get("sample_rate_fps", fps)),
        reliable=reliable,
        latency_seconds=round(time.perf_counter() - t0, 3),
        # A video file is only ever checked for presence.
        gaze=None,
    )

    if not reliable:
        warnings.append(
            f"A face was detected in only {rate:.0%} of sampled frames, below the "
            f"{RELIABILITY_MIN:.0%} threshold, so the facial channel was excluded "
            "from scoring."
        )
    if result["dominant_expression"] == "unavailable" and result["frames_with_face"]:
        warnings.append(
            "The facial backend detected faces but could not classify expression, "
            "so only presence and head steadiness were recorded."
        )

    return result, warnings
