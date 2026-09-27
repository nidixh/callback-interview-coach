# test_gaze.py
"""Eye contact and presence, from the camera's per-frame readings.

The measurement itself is plain arithmetic over face landmarks (backend/ vision/gaze.py), so the rules can be checked without a camera.
"""

import gaze


def test_no_face_is_absent():
    assert gaze.classify(None) == "absent"


def test_lighting_verdicts():
    assert gaze.lighting_verdict(40, 120) == "dark"
    assert gaze.lighting_verdict(120, 110) == "good"


def test_an_answer_with_no_face_reports_no_eye_contact_rather_than_zero():
    samples = [{"t": i * 0.5, "signals": None} for i in range(10)]
    summary = gaze.summarise(samples)
    assert summary["eye_contact_0to1"] is None
    assert summary["frames_with_face"] == 0
    assert summary["timeline"] == "n" * 10
    assert summary["look_aways"][0]["direction"] == "out of frame"


def test_calibration_needs_enough_frames():
    assert gaze.calibrate([]) is None
