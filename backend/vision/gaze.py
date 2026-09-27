# gaze.py
"""Where the candidate is looking, worked out from face landmarks.
Plain arithmetic only.

The camera worker runs MediaPipe's Face Landmarker and passes this module 478 face points, the head's rotation and some expression values.
No numpy or OpenCV is used here, so it runs and is tested in the main environment.

frame_signals() handles one frame; summarise() handles one answer.

Gaze is the head direction plus the eye direction.
The head angles come from the landmarker's transform.
The eye angle comes from where the iris sits between the eye corners.
Looking at the camera means gaze within a tolerance of a baseline, which is straight ahead until the candidate calibrates.

A blink counts as unknown.
Eyes that stay narrowed longer than a blink count as looking down, such as reading notes.
Directions are "down", "up", "to the side" and "out of frame"; left and right are not named, because the camera image is not mirrored.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

# Landmark numbers (MediaPipe face mesh, 478 points).
# Right and left are the candidate's own.
RIGHT_EYE = {"outer": 33, "inner": 133, "upper": 159, "lower": 145, "iris": 468}
LEFT_EYE = {"outer": 263, "inner": 362, "upper": 386, "lower": 374, "iris": 473}
FOREHEAD, CHIN = 10, 152

# The eye model
EYE_WIDTH_MM = 30.0
EYEBALL_RADIUS_MM = 12.0

# Lid gap over eye width below which the eyes count as narrowed: a blink, or looking down.
EYES_NARROW = 0.14
# A blink lasts 0.1 to 0.4 s.
# Narrowed for longer than this is looking down.
BLINK_MAX_S = 0.5
# Looking down at notes mostly narrows the lids rather than tilting the head.
# Below this share of the person's normal lid gap, the eyes count as lowered.
EYES_LOWERED = 0.70
# Fewer open-eyed frames than this and an answer's own median gap is no guide.
MIN_OPEN_FRAMES = 10

# Degrees from the baseline that still count as looking at the camera.
# Wider below than above, because on a laptop the screen sits under the lens and glancing at it is not breaking eye contact.
YAW_TOLERANCE = 10.0
PITCH_DOWN_TOLERANCE = 14.0
PITCH_UP_TOLERANCE = 5.0
# Wider limits used before calibration, so nobody is marked as looking away because of the shape of their face.
UNCALIBRATED_TOLERANCE = {"yaw": 15.0, "down": 14.0, "up": 10.0}

# Look-aways.
# Short glances to think are normal, so a look-away must last at least a second, and brief returns inside one are merged.
LOOK_AWAY_MIN_S = 1.0
MERGE_GAP_S = 0.4
# Unknown stretches (blinks, a dropped frame) up to this long take the state on either side.
FILL_UNKNOWN_S = 0.4
# Samples further apart than this are a gap in the record, not a movement.
MAX_STEP_S = 0.6

# Framing and light Face height (forehead to chin) as a share of the frame height.
FACE_TOO_SMALL = 0.18
FACE_TOO_LARGE = 0.60
# Face centre further than this from the middle, as a share of the frame.
OFF_CENTRE = 0.20
# Mean brightness, 0-255.
# A face darker than this is hard to read, and a surround this much brighter than the face is a window behind the candidate.
FACE_DARK = 70.0
BACKLIT_MARGIN = 35.0

# Steadiness.
# Median head turning speed, in degrees per second, at which steadiness reaches 0.
MOTION_FULL_SCALE = 30.0

TIMELINE_STEP_S = 0.5

CONTACT, AWAY, CLOSED, ABSENT = "contact", "away", "closed", "absent"


# Small helpers

def _xy(point) -> tuple:
    """(x, y) from a landmark given as an object with .x/.y or a sequence."""
    if hasattr(point, "x"):
        return float(point.x), float(point.y)
    return float(point[0]), float(point[1])


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _matrix(rows) -> List[List[float]]:
    """A 4x4 transform as nested lists, from nested rows or 16 numbers."""
    try:
        flat = [float(v) for row in rows for v in row]
    except TypeError:  # already flat
        flat = [float(v) for v in rows]
    if len(flat) != 16:
        raise ValueError("a 4x4 transform needs 16 numbers")
    return [flat[i:i + 4] for i in range(0, 16, 4)]


# One frame

def head_angles(transform) -> Dict[str, float]:
    """Yaw, pitch and roll in degrees from the landmarker's 4x4 transform.

    Yaw is positive when the face turns toward the right of the image; pitch is positive when the head tilts up.
    """
    r = _matrix(transform)
    yaw = math.degrees(math.asin(_clamp(-r[2][0], -1.0, 1.0)))
    # The matrix's own pitch is positive with the head tipped down.
    pitch = -math.degrees(math.atan2(r[2][1], r[2][2]))
    roll = math.degrees(math.atan2(r[1][0], r[0][0]))
    return {"yaw": yaw, "pitch": pitch, "roll": roll}


def _eye(landmarks, eye) -> Dict[str, float]:
    """Where one iris sits in its eye, and how open the eye is.

    `across` is the sideways offset from the eye's centre as a share of its width.
    `along` is the offset below the middle of the lids.
    `open` is the lid gap over the width.
    Measured from the lids, because the iris naturally sits above the corner line.
    """
    ox, oy = _xy(landmarks[eye["outer"]])
    ix, iy = _xy(landmarks[eye["inner"]])
    ux, uy = _xy(landmarks[eye["upper"]])
    lx, ly = _xy(landmarks[eye["lower"]])
    cx, cy = _xy(landmarks[eye["iris"]])

    # The corners in image order, so "across" means the same thing for both eyes.
    (ax, ay), (bx, by) = sorted([(ox, oy), (ix, iy)])
    width = math.hypot(bx - ax, by - ay)
    if width <= 1e-9:
        return {"across": 0.0, "along": 0.0, "open": 0.0, "width": 0.0}
    ux_, uy_ = (bx - ax) / width, (by - ay) / width  # along the eye
    nx, ny = -uy_, ux_  # at right angles, downward
    mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
    across = ((cx - mx) * ux_ + (cy - my) * uy_) / width
    lid_x, lid_y = (ux + lx) / 2.0, (uy + ly) / 2.0
    along = ((cx - lid_x) * nx + (cy - lid_y) * ny) / width
    gap = math.hypot(lx - ux, ly - uy)
    return {"across": across, "along": along, "open": gap / width, "width": width}


def _eye_degrees(offset: float) -> float:
    """An iris offset (share of eye width) as a rotation of the eye, degrees."""
    return math.degrees(math.asin(_clamp(offset * EYE_WIDTH_MM / EYEBALL_RADIUS_MM, -1.0, 1.0)))


def frame_signals(landmarks, transform=None, blendshapes: Optional[Dict[str, float]] = None,
                  face_luma: Optional[float] = None,
                  frame_luma: Optional[float] = None) -> Dict[str, Any]:
    """Everything one frame says about the candidate, as plain numbers.

    `landmarks` are 0 to 1 points.
    `transform` is the 4x4 face matrix; without it the head is taken as facing the camera.
    """
    right, left = _eye(landmarks, RIGHT_EYE), _eye(landmarks, LEFT_EYE)
    across = (right["across"] + left["across"]) / 2.0
    along = (right["along"] + left["along"]) / 2.0
    openness = (right["open"] + left["open"]) / 2.0

    head = head_angles(transform) if transform is not None else {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}
    eye_yaw = _eye_degrees(across)
    # Iris lower in the eye is looking down.
    eye_pitch = -_eye_degrees(along)

    xs = [_xy(p)[0] for p in landmarks]
    ys = [_xy(p)[1] for p in landmarks]
    top, bottom = _xy(landmarks[FOREHEAD])[1], _xy(landmarks[CHIN])[1]
    signals: Dict[str, Any] = {
        "yaw": round(head["yaw"], 2),
        "pitch": round(head["pitch"], 2),
        "roll": round(head["roll"], 2),
        "gaze_yaw": round(head["yaw"] + eye_yaw, 2),
        "gaze_pitch": round(head["pitch"] + eye_pitch, 2),
        "eyes_open": round(openness, 3),
        "face_size": round(abs(bottom - top), 4),
        "centre": [round((min(xs) + max(xs)) / 2.0, 4), round((min(ys) + max(ys)) / 2.0, 4)],
    }
    if face_luma is not None:
        signals["face_luma"] = round(float(face_luma), 1)
    if frame_luma is not None:
        signals["frame_luma"] = round(float(frame_luma), 1)
    if blendshapes:
        smile = (float(blendshapes.get("mouthSmileLeft", 0.0)) + float(blendshapes.get("mouthSmileRight", 0.0))) / 2.0
        brow = max(float(blendshapes.get("browInnerUp", 0.0)),
                   (float(blendshapes.get("browOuterUpLeft", 0.0)) + float(blendshapes.get("browOuterUpRight", 0.0))) / 2.0)
        signals["smile"] = round(smile, 3)
        signals["brow"] = round(brow, 3)
    return signals


# Calibration

DEFAULT_BASELINE = {"gaze_yaw": 0.0, "gaze_pitch": 0.0, "calibrated": False}


def calibrate(samples: Iterable[Dict[str, Any]], minimum: int = 8) -> Optional[Dict[str, Any]]:
    """This person's "looking at the camera", from a few seconds of doing it.

    The median of open eyed frames, so a blink does not move it.
    None when too few frames were usable.
    """
    usable = [s for s in samples if s and s.get("eyes_open", 0.0) >= EYES_NARROW]
    if len(usable) < minimum:
        return None
    return {
        "gaze_yaw": round(_median([s["gaze_yaw"] for s in usable]), 2),
        "gaze_pitch": round(_median([s["gaze_pitch"] for s in usable]), 2),
        "eyes_open": round(_median([s["eyes_open"] for s in usable]), 3),
        "calibrated": True,
        "frames": len(usable),
    }


def classify(signals: Optional[Dict[str, Any]], baseline: Optional[Dict[str, Any]] = None) -> str:
    """contact, away, closed (eyes narrowed) or absent (no face) for one frame."""
    if not signals:
        return ABSENT
    base = baseline or DEFAULT_BASELINE
    narrow = EYES_NARROW
    if base.get("eyes_open"):
        narrow = max(narrow, EYES_LOWERED * base["eyes_open"])
    if signals.get("eyes_open", 1.0) < narrow:
        return CLOSED
    dy = signals["gaze_yaw"] - base["gaze_yaw"]
    dp = signals["gaze_pitch"] - base["gaze_pitch"]
    yaw, down, up = _tolerances(base)
    if abs(dy) > yaw or dp < -down or dp > up:
        return AWAY
    return CONTACT


def _tolerances(base) -> tuple:
    """Yaw, down and up tolerances: tight once calibrated, wide on a guess."""
    if base.get("calibrated"):
        return YAW_TOLERANCE, PITCH_DOWN_TOLERANCE, PITCH_UP_TOLERANCE
    t = UNCALIBRATED_TOLERANCE
    return t["yaw"], t["down"], t["up"]


def _direction(frames: List[Dict[str, Any]], baseline) -> str:
    """Which way a look-away went, named from the candidate's side."""
    faces = [f for f in frames if f.get("signals")]
    if not faces:
        return "out of frame"
    base = baseline or DEFAULT_BASELINE
    if all(f["state"] == CLOSED for f in faces):
        return "down"
    yaw = _median([abs(f["signals"]["gaze_yaw"] - base["gaze_yaw"]) for f in faces])
    pitch = _median([f["signals"]["gaze_pitch"] - base["gaze_pitch"] for f in faces])
    yaw_tol, down_tol, up_tol = _tolerances(base)
    if pitch < 0 and -pitch / down_tol >= yaw / yaw_tol:
        return "down"
    if pitch > 0 and pitch / up_tol >= yaw / yaw_tol:
        return "up"
    return "to the side"


# One answer

def _states(samples: List[Dict[str, Any]], baseline) -> List[Dict[str, Any]]:
    """Each sample with its state, with blinks resolved.

    Narrowed eyes held longer than a blink become "away".
    Short unknown stretches take the state around them.
    """
    base = dict(baseline or DEFAULT_BASELINE)
    if not base.get("eyes_open"):
        gaps = [s["signals"]["eyes_open"] for s in samples
                if s.get("signals") and s["signals"].get("eyes_open", 0.0) >= EYES_NARROW]
        if len(gaps) >= MIN_OPEN_FRAMES:
            base["eyes_open"] = _median(gaps)
    baseline = base
    frames = [{"t": float(s["t"]), "signals": s.get("signals"),
               "state": classify(s.get("signals"), baseline)} for s in samples]

    # Runs of narrowed eyes: long ones are eyes lowered, short ones are blinks.
    i = 0
    while i < len(frames):
        if frames[i]["state"] != CLOSED:
            i += 1
            continue
        j = i
        while j + 1 < len(frames) and frames[j + 1]["state"] == CLOSED:
            j += 1
        if frames[j]["t"] - frames[i]["t"] >= BLINK_MAX_S:
            for k in range(i, j + 1):
                frames[k]["state"] = AWAY
                frames[k]["lowered"] = True
        i = j + 1

    # Short blinks take their neighbours' state when both sides agree, else the earlier one (a blink does not end a look-away or start one).
    i = 0
    while i < len(frames):
        if frames[i]["state"] != CLOSED:
            i += 1
            continue
        j = i
        while j + 1 < len(frames) and frames[j + 1]["state"] == CLOSED:
            j += 1
        before = frames[i - 1]["state"] if i > 0 else None
        after = frames[j + 1]["state"] if j + 1 < len(frames) else None
        fill = before or after
        span = frames[j]["t"] - frames[i]["t"]
        if fill in (CONTACT, AWAY) and span <= FILL_UNKNOWN_S:
            for k in range(i, j + 1):
                frames[k]["state"] = fill
        i = j + 1
    return frames


def _spans(frames: List[Dict[str, Any]], baseline, step: float) -> List[Dict[str, Any]]:
    """Look-away spans: away or absent for LOOK_AWAY_MIN_S or more."""
    raw = []
    start = None
    for k, f in enumerate(frames):
        off = f["state"] in (AWAY, ABSENT)
        if off and start is None:
            start = k
        if start is not None and (not off or k == len(frames) - 1):
            end = k if off else k - 1
            raw.append([start, end])
            start = None

    # Merge spans separated by a brief return to the camera.
    merged: List[List[int]] = []
    for a, b in raw:
        if merged and frames[a]["t"] - frames[merged[-1][1]]["t"] <= MERGE_GAP_S + step:
            merged[-1][1] = b
        else:
            merged.append([a, b])

    spans = []
    for a, b in merged:
        t0 = frames[a]["t"]
        t1 = frames[b]["t"] + step
        if t1 - t0 < LOOK_AWAY_MIN_S:
            continue
        chunk = [f for f in frames[a:b + 1] if f["state"] in (AWAY, ABSENT)]
        absent = sum(1 for f in chunk if f["state"] == ABSENT)
        lowered = sum(1 for f in chunk if f.get("lowered"))
        if absent * 2 > len(chunk):
            direction = "out of frame"
        elif lowered * 2 > len(chunk):
            direction = "down"
        else:
            direction = _direction(chunk, baseline)
        spans.append({"start": round(t0, 2), "end": round(t1, 2),
                      "seconds": round(t1 - t0, 2), "direction": direction})
    return spans


def _typical_step(times: List[float]) -> float:
    steps = [b - a for a, b in zip(times, times[1:]) if 0 < b - a <= MAX_STEP_S]
    return _median(steps) if steps else 0.1


def _motion(frames: List[Dict[str, Any]]) -> Optional[float]:
    """Median head rotation speed, degrees per second, over consecutive faces."""
    speeds = []
    for a, b in zip(frames, frames[1:]):
        sa, sb = a.get("signals"), b.get("signals")
        dt = b["t"] - a["t"]
        if not sa or not sb or dt <= 0 or dt > MAX_STEP_S:
            continue
        turn = math.sqrt((sb["yaw"] - sa["yaw"]) ** 2 + (sb["pitch"] - sa["pitch"]) ** 2
                         + (sb["roll"] - sa["roll"]) ** 2)
        speeds.append(turn / dt)
    return _median(speeds) if speeds else None


def _framing(faces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Where the face sat in the frame over the answer: too far, too close, off centre, or good."""
    size = _median([f["face_size"] for f in faces])
    cx = _median([f["centre"][0] for f in faces])
    cy = _median([f["centre"][1] for f in faces])
    if size < FACE_TOO_SMALL:
        verdict = "too far"
    elif size > FACE_TOO_LARGE:
        verdict = "too close"
    elif abs(cx - 0.5) > OFF_CENTRE or abs(cy - 0.5) > OFF_CENTRE:
        verdict = "off centre"
    else:
        verdict = "good"
    return {"verdict": verdict, "face_size": round(size, 3), "centre": [round(cx, 3), round(cy, 3)]}


def _lighting(faces: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """How well the face was lit against the rest of the frame, or None when unmeasured."""
    lit = [f for f in faces if "face_luma" in f and "frame_luma" in f]
    if not lit:
        return None
    face = _median([f["face_luma"] for f in lit])
    frame = _median([f["frame_luma"] for f in lit])
    if face < FACE_DARK:
        verdict = "dark"
    elif frame - face >= BACKLIT_MARGIN:
        verdict = "backlit"
    else:
        verdict = "good"
    return {"verdict": verdict, "face": round(face, 1), "surround": round(frame, 1)}


def lighting_verdict(face_luma: float, frame_luma: float) -> str:
    """The same rule for a single frame, for the live camera check."""
    return _lighting([{"face_luma": face_luma, "frame_luma": frame_luma}])["verdict"]


def framing_verdict(signals: Dict[str, Any]) -> str:
    """The same rule for a single frame, for the live camera check."""
    return _framing([signals])["verdict"]


def _expressiveness(faces: List[Dict[str, Any]]) -> Optional[float]:
    """How much the face moved (smiles and brows), from 0 to 1.

    Shown, never scored.
    """
    smiles = [f["smile"] for f in faces if "smile" in f]
    brows = [f["brow"] for f in faces if "brow" in f]
    if len(smiles) < 5:
        return None

    def spread(values):
        mean = sum(values) / len(values)
        return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))

    return round(_clamp((spread(smiles) + spread(brows) + 0.5 * (sum(smiles) / len(smiles))) / 0.35, 0.0, 1.0), 3)


def _timeline(frames: List[Dict[str, Any]], duration: float) -> str:
    """One letter per half second: c contact, a away, n no face, - nothing seen."""
    if duration <= 0:
        return ""
    bins = int(math.ceil(duration / TIMELINE_STEP_S))
    counts = [{CONTACT: 0, AWAY: 0, ABSENT: 0} for _ in range(bins)]
    for f in frames:
        k = min(bins - 1, int(f["t"] / TIMELINE_STEP_S))
        state = f["state"] if f["state"] in counts[k] else None
        if state:
            counts[k][state] += 1
    letters = []
    for c in counts:
        if not any(c.values()):
            letters.append("-")
            continue
        best = max((CONTACT, AWAY, ABSENT), key=lambda s: c[s])
        letters.append({CONTACT: "c", AWAY: "a", ABSENT: "n"}[best])
    return "".join(letters)


def summarise(samples: List[Dict[str, Any]], baseline: Optional[Dict[str, Any]] = None,
              duration: Optional[float] = None) -> Dict[str, Any]:
    """Eye contact and presence over one answer.

    `samples` are {"t": seconds from the start, "signals": the frame's signals, or None with no face}, in time order.
    """
    samples = sorted((s for s in samples if s is not None), key=lambda s: float(s["t"]))
    base = baseline or DEFAULT_BASELINE
    frames = _states(samples, base)
    times = [f["t"] for f in frames]
    step = _typical_step(times)
    if duration is None:
        duration = (times[-1] + step) if times else 0.0

    faces = [f["signals"] for f in frames if f["signals"]]
    contact = sum(1 for f in frames if f["state"] == CONTACT)
    away = sum(1 for f in frames if f["state"] == AWAY)
    looked = contact + away
    spans = _spans(frames, base, step)
    motion = _motion(frames)

    return {
        "calibrated": bool(base.get("calibrated")),
        "frames": len(frames),
        "frames_with_face": len(faces),
        "eye_contact_0to1": round(contact / looked, 4) if looked else None,
        "look_aways": spans,
        "longest_look_away_s": max((s["seconds"] for s in spans), default=0.0),
        "head_motion_deg_s": round(motion, 2) if motion is not None else None,
        "head_steadiness_0to1": round(_clamp(1.0 - motion / MOTION_FULL_SCALE, 0.0, 1.0), 4) if motion is not None else None,
        "framing": _framing(faces) if faces else None,
        "lighting": _lighting(faces) if faces else None,
        "expressiveness_0to1": _expressiveness(faces) if faces else None,
        "timeline": _timeline(frames, duration),
        "timeline_step_s": TIMELINE_STEP_S,
    }
