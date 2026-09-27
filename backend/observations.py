# observations.py
"""Layer 2: turn measurements into points worth telling the candidate.

A value inside the normal range produces nothing.
A value outside it produces an observation already worded for the report.
A notably good value produces a `strength`, so the report has something specific to praise.

The ranges come from fusion, so a value is never scored one way and described another.
No model is used here.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import fusion
from schema import (
    ContentEvaluation,
    FacialAnalysis,
    Observation,
    SpeechAnalysisResult,
)

logger = logging.getLogger(__name__)

SEVERITY_STRENGTH = "strength"
SEVERITY_MINOR = "minor"
SEVERITY_NOTABLE = "notable"

# Ordering used when the narrative writer can only be given so much: the things that change an interviewer's impression most come first.
_SEVERITY_RANK = {SEVERITY_NOTABLE: 0, SEVERITY_MINOR: 1, SEVERITY_STRENGTH: 2}

# Speaking rate limits for the wording.
# Each side is split at its midpoint, so slightly fast speech is described as "a little quick".
_RATE_VERY_FAST = (fusion.RATE_IDEAL_HIGH + fusion.RATE_FLOOR_HIGH) / 2.0  # 6.75
_RATE_VERY_SLOW = (fusion.RATE_IDEAL_LOW + fusion.RATE_FLOOR_LOW) / 2.0  # 2.50

# Filler rate. fusion scores this continuously; these are the points at which it becomes worth *mentioning*, which is a different question from scoring.
FILLERS_NOTABLE_PER_MIN = 8.0
FILLERS_MINOR_PER_MIN = 4.0

# Hesitation before the first word.
# Under this a candidate has started talking before thinking, which is the most common nervous habit in a mock interview.
NO_THINKING_PAUSE_S = 0.25
CONSIDERED_START_MIN_S = 0.4
CONSIDERED_START_MAX_S = 3.0
SLOW_START_S = 5.0

# Answer length.
# Under half a minute rarely fits a situation, action and result; over three minutes loses the listener.
ANSWER_SHORT_S = 30.0
ANSWER_IDEAL_LOW_S = 45.0
ANSWER_IDEAL_HIGH_S = 150.0
ANSWER_LONG_S = 210.0

# Facial channel.
# Below the first value the candidate is meaningfully off camera; above the second they held the frame well.
FACE_PRESENT_POOR = 0.75
FACE_PRESENT_GOOD = 0.92
STEADINESS_POOR = 0.55
STEADINESS_GOOD = 0.85

# Eye contact.
# Looking at the camera for three quarters of an answer counts as holding it; half or less does not.
EYE_CONTACT_GOOD = 0.75
EYE_CONTACT_POOR = 0.50
# One look-away this long is worth a word even when the rest was fine.
LONG_LOOK_AWAY_S = 4.0

_WHERE_THE_EYES_WENT = {
    "down": ("mostly downwards, as if reading. Glance at your notes, then bring "
             "your eyes back to the lens before you deliver the point"),
    "up": ("mostly upwards, the way people look when searching for words. "
           "Pause and think if you need to, then come back to the lens"),
    "to the side": ("mostly to the side, which on a call reads as talking to "
                    "someone else in the room. Put anything you need to read "
                    "just under the camera"),
    "out of frame": ("partly out of the frame altogether. Check your framing "
                     "before you start"),
}
_FRAMING_FIX = {
    "too far": "You were small in the frame. Sit closer, so your head and shoulders fill most of it.",
    "too close": "You were very close to the camera. Sit back a little, so your shoulders are in view.",
    "off centre": "You sat to one side of the frame. Move so your face is in the middle.",
}
_LIGHT_FIX = {
    "dark": "Your face was in shadow. Put a lamp or a window in front of you.",
    "backlit": ("The light was behind you, so your face was darker than the room. "
                "Turn so a window or lamp is in front of you, not behind."),
}


def _obs(kind: str, severity: str, headline: str, detail: str,
         measurement: str, domain: str) -> Observation:
    return Observation(
        kind=kind, severity=severity, headline=headline, detail=detail,
        measurement=measurement, domain=domain,
    )


def _pace(prosody: Dict) -> Optional[Observation]:
    """An observation about speaking speed, or None when there is no rate to judge."""
    rate = float(prosody.get("speaking_rate_syll_per_sec", 0.0))
    if rate <= 0:
        return None
    measurement = f"{rate:.1f} syllables per second"

    if rate >= _RATE_VERY_FAST:
        return _obs(
            "pace_fast", SEVERITY_NOTABLE,
            "You were speaking very fast",
            "You were racing. At that speed an interviewer is still processing "
            "your last point while you are two ahead, and detail gets lost even "
            "though you said it.",
            measurement, "audio")
    if rate > fusion.RATE_IDEAL_HIGH:
        return _obs(
            "pace_fast", SEVERITY_MINOR,
            "You were speaking a little quickly",
            "Slightly faster than a comfortable conversational pace. Not a "
            "problem in itself, but it is the first thing that slips further "
            "when nerves rise.",
            measurement, "audio")
    if rate <= _RATE_VERY_SLOW:
        return _obs(
            "pace_slow", SEVERITY_NOTABLE,
            "You were speaking very slowly",
            "Slow enough that the answer loses momentum and starts to read as "
            "uncertainty rather than care.",
            measurement, "audio")
    if rate < fusion.RATE_IDEAL_LOW:
        return _obs(
            "pace_slow", SEVERITY_MINOR,
            "You were speaking a little slowly",
            "A shade under conversational pace. Fine when you are being "
            "deliberate; worth watching if it is hesitation.",
            measurement, "audio")
    return _obs(
        "pace_good", SEVERITY_STRENGTH,
        "Your pace was comfortable throughout",
        "You held a steady conversational speed, which is harder than it sounds "
        "under interview pressure and makes you easy to listen to.",
        measurement, "audio")


def _fillers(fillers: Dict) -> Optional[Observation]:
    """An observation about filler words, from how many were used per minute."""
    per_minute = float(fillers.get("per_minute", 0.0))
    total = int(fillers.get("total_count", 0))
    counts = fillers.get("counts_by_filler") or {}
    measurement = f"{total} filler words, {per_minute:.1f} per minute"

    if per_minute >= FILLERS_NOTABLE_PER_MIN:
        commonest = max(counts, key=counts.get) if counts else "um"
        return _obs(
            "fillers_high", SEVERITY_NOTABLE,
            "Filler words were getting in the way",
            f"They came often enough to be distracting, and \"{commonest}\" was "
            "the one you reached for most. The fix is not to speak more "
            "carefully, it is to let yourself be silent for a beat instead.",
            measurement, "audio")
    if per_minute >= FILLERS_MINOR_PER_MIN:
        return _obs(
            "fillers_moderate", SEVERITY_MINOR,
            "A few filler words crept in",
            "Not enough to distract, but they cluster where you are thinking. "
            "A short silence in the same place would read as composure.",
            measurement, "audio")
    if per_minute <= fusion.FILLERS_IDEAL_MAX and total >= 0:
        return _obs(
            "fillers_low", SEVERITY_STRENGTH,
            "Your speech was clean of filler",
            "Almost no \"um\"s or \"uh\"s. That is unusual under pressure and it "
            "makes you sound prepared.",
            measurement, "audio")
    return None


def _pauses(pauses: Dict) -> List[Observation]:
    """Pause observations, raised only when there is a real pattern."""
    out: List[Observation] = []
    if not pauses:
        return out

    counts = pauses.get("counts_by_kind") or {}
    longest = float(pauses.get("longest_seconds", 0.0))
    dead_air = int(counts.get("dead_air", 0))
    noticeable = int(counts.get("noticeable", 0))

    if dead_air:
        out.append(_obs(
            "dead_air", SEVERITY_NOTABLE,
            "You lost the thread once or twice",
            f"There was a silence of about {longest:.0f} seconds mid-answer. "
            "That length reads as being stuck rather than thinking. If it "
            "happens, say what you are doing out loud: \"let me think about "
            "that for a second\" buys you the same time and sounds deliberate.",
            f"{dead_air} silence(s) over "
            f"{2.5:.1f}s, longest {longest:.2f}s", "audio"))
    elif noticeable >= 3:
        out.append(_obs(
            "pauses_frequent", SEVERITY_MINOR,
            "The answer stopped and started",
            "Several noticeable gaps broke the flow. Usually this means the "
            "structure was being assembled while speaking rather than before.",
            f"{noticeable} pauses over 1s, longest {longest:.2f}s", "audio"))

    first_word = float(pauses.get("time_to_first_word_seconds", 0.0))
    if first_word >= SLOW_START_S:
        out.append(_obs(
            "slow_start", SEVERITY_MINOR,
            "You took a long time to start",
            "A few seconds of thinking is fine and expected. This was long "
            "enough that an interviewer would start to wonder.",
            f"{first_word:.1f}s before the first word", "audio"))
    elif first_word < NO_THINKING_PAUSE_S:
        out.append(_obs(
            "no_thinking_pause", SEVERITY_MINOR,
            "You started answering instantly",
            "You began before the question had finished landing. A two-second "
            "pause before you start does not read as being unprepared; it reads "
            "as considering the question, and it buys you a better first "
            "sentence.",
            f"{first_word:.2f}s before the first word", "audio"))
    elif CONSIDERED_START_MIN_S <= first_word <= CONSIDERED_START_MAX_S:
        out.append(_obs(
            "considered_start", SEVERITY_STRENGTH,
            "You took a beat before answering",
            "You paused to think before speaking rather than filling the space. "
            "That reads as composure.",
            f"{first_word:.1f}s before the first word", "audio"))
    return out


def _pitch(prosody: Dict) -> Optional[Observation]:
    """An observation about how much the voice's pitch varied, or None when unmeasured."""
    pitch_std = float(prosody.get("pitch_std_hz", 0.0))
    if pitch_std <= 0:
        return None
    measurement = f"pitch variation {pitch_std:.1f} Hz"

    if pitch_std <= fusion.PITCH_STD_FLOOR:
        return _obs(
            "monotone", SEVERITY_NOTABLE,
            "Your delivery was quite flat",
            "Very little variation in pitch. The content may be strong, but a "
            "level tone makes it sound like you are reciting rather than "
            "telling someone something you care about.",
            measurement, "audio")
    if pitch_std >= fusion.PITCH_STD_IDEAL:
        return _obs(
            "expressive", SEVERITY_STRENGTH,
            "You sounded engaged",
            "Good variation in your voice, which carries interest and keeps an "
            "interviewer with you.",
            measurement, "audio")
    return None


def _answer_length(duration: float) -> Optional[Observation]:
    """An observation about how long the answer ran, or None when it has no length."""
    if duration <= 0:
        return None
    measurement = f"{duration:.0f} seconds"

    if duration < ANSWER_SHORT_S:
        return _obs(
            "answer_short", SEVERITY_NOTABLE,
            "The answer was too short",
            "There was not room here for a situation, what you actually did, "
            "and how it turned out. Interviewers read very short answers as "
            "not having an example ready.",
            measurement, "audio")
    if duration > ANSWER_LONG_S:
        return _obs(
            "answer_long", SEVERITY_NOTABLE,
            "The answer ran long",
            "Past a couple of minutes an interviewer is no longer listening for "
            "content, they are waiting for the end. The strongest material gets "
            "buried by whatever came after it.",
            measurement, "audio")
    if duration > ANSWER_IDEAL_HIGH_S:
        return _obs(
            "answer_long", SEVERITY_MINOR,
            "The answer was on the long side",
            "Still within reason, but it could land harder if it were tighter.",
            measurement, "audio")
    if ANSWER_IDEAL_LOW_S <= duration <= ANSWER_IDEAL_HIGH_S:
        return _obs(
            "answer_well_judged", SEVERITY_STRENGTH,
            "The answer was a good length",
            "Long enough to give a real example, short enough to hold "
            "attention. That judgement is worth keeping.",
            measurement, "audio")
    return None


def _eye_contact(gaze: Dict) -> List[Observation]:
    """Where the eyes were, when the landmarker measured it."""
    out: List[Observation] = []
    eye = float(gaze["eye_contact_0to1"])
    spans = gaze.get("look_aways") or []
    longest = max(spans, key=lambda s: float(s.get("seconds", 0.0)), default=None)
    measurement = f"eye contact {eye:.0%} of the time your face was in view"
    if eye <= EYE_CONTACT_POOR:
        seconds: Dict[str, float] = {}
        for s in spans:
            seconds[s.get("direction", "")] = seconds.get(s.get("direction", ""), 0.0) + float(s.get("seconds", 0.0))
        where = max(seconds, key=seconds.get) if seconds else ""
        detail = "More than half of this answer was spoken away from the camera"
        detail += f", {_WHERE_THE_EYES_WENT[where]}." if where in _WHERE_THE_EYES_WENT else "."
        out.append(_obs("little_eye_contact", SEVERITY_NOTABLE,
                        "Most of this answer was given away from the camera",
                        detail, measurement, "image"))
    elif eye >= EYE_CONTACT_GOOD:
        out.append(_obs("held_eye_contact", SEVERITY_STRENGTH,
                        "You held eye contact well",
                        "You looked at the camera for most of the answer, which on "
                        "a call is looking at the interviewer.",
                        measurement, "image"))
    if (eye > EYE_CONTACT_POOR and longest is not None
            and float(longest.get("seconds", 0.0)) >= LONG_LOOK_AWAY_S):
        direction = longest.get("direction", "away")
        where = "out of the frame" if direction == "out of frame" else direction
        out.append(_obs("long_look_away", SEVERITY_MINOR,
                        "One long look away",
                        f"Mostly you kept your eyes on the camera, but once you looked "
                        f"{where} for long enough to lose the thread with the "
                        "interviewer. The transcript shows where.",
                        f"{float(longest['seconds']):.0f} s from "
                        f"{float(longest.get('start', 0.0)):.0f} s in", "image"))
    return out


def _facial(facial: Optional[FacialAnalysis]) -> List[Observation]:
    """Camera observations: presence, steadiness, and with gaze, eye contact, framing and light.

    Facial expression is never turned into advice, because it does not reliably show how someone feels (Barrett et al., 2019).
    """
    out: List[Observation] = []
    if not facial or not facial.get("reliable"):
        return out

    presence = float(facial.get("face_detection_rate_0to1", 0.0))
    steadiness = float(facial.get("gaze_centre_stability_0to1", 0.0))
    gaze = facial.get("gaze") or {}
    if gaze.get("head_steadiness_0to1") is not None:
        # The head's own angles, ten times a second, rather than how far the face's box wandered: turning the head in place now counts.
        steadiness = float(gaze["head_steadiness_0to1"])

    if gaze.get("eye_contact_0to1") is not None:
        out.extend(_eye_contact(gaze))

    if presence <= FACE_PRESENT_POOR:
        out.append(_obs(
            "off_camera", SEVERITY_NOTABLE,
            "You were out of frame for part of the answer",
            "On a video call that reads as broken eye contact, which costs you "
            "more than it should. Check your framing before you start and try "
            "to look at the camera, not at your own face on the screen.",
            f"in frame {presence:.0%} of sampled frames", "image"))
    elif presence >= FACE_PRESENT_GOOD and gaze.get("eye_contact_0to1") is None:
        out.append(_obs(
            "in_frame", SEVERITY_STRENGTH,
            "You held the frame well",
            "You stayed centred and present on camera throughout, which is "
            "exactly what a remote interviewer wants to see.",
            f"in frame {presence:.0%} of sampled frames", "image"))

    if steadiness <= STEADINESS_POOR:
        out.append(_obs(
            "restless", SEVERITY_MINOR,
            "You moved around a fair amount",
            "Noticeable shifting while answering. A little movement is natural; "
            "a lot of it reads as discomfort even when you are fine.",
            f"steadiness {steadiness:.2f} of 1.00", "image"))
    elif steadiness >= STEADINESS_GOOD:
        out.append(_obs(
            "steady", SEVERITY_STRENGTH,
            "You held yourself still and settled",
            "Very little restless movement, which reads as being at ease.",
            f"steadiness {steadiness:.2f} of 1.00", "image"))

    framing = (gaze.get("framing") or {}).get("verdict")
    if framing in _FRAMING_FIX:
        out.append(_obs("framing", SEVERITY_MINOR, "Your framing could be better",
                        _FRAMING_FIX[framing], f"framing: {framing}", "image"))
    light = (gaze.get("lighting") or {}).get("verdict")
    if light in _LIGHT_FIX:
        out.append(_obs("lighting", SEVERITY_MINOR, "The lighting worked against you",
                        _LIGHT_FIX[light], f"lighting: {light}", "image"))
    return out

    presence = float(facial.get("face_detection_rate_0to1", 0.0))
    steadiness = float(facial.get("gaze_centre_stability_0to1", 0.0))

    if presence <= FACE_PRESENT_POOR:
        out.append(_obs(
            "off_camera", SEVERITY_NOTABLE,
            "You were out of frame for part of the answer",
            "On a video call that reads as broken eye contact, which costs you "
            "more than it should. Check your framing before you start and try "
            "to look at the camera, not at your own face on the screen.",
            f"in frame {presence:.0%} of sampled frames", "image"))
    elif presence >= FACE_PRESENT_GOOD:
        out.append(_obs(
            "in_frame", SEVERITY_STRENGTH,
            "You held the frame well",
            "You stayed centred and present on camera throughout, which is "
            "exactly what a remote interviewer wants to see.",
            f"in frame {presence:.0%} of sampled frames", "image"))

    if steadiness <= STEADINESS_POOR:
        out.append(_obs(
            "restless", SEVERITY_MINOR,
            "You moved around a fair amount",
            "Noticeable shifting while answering. A little movement is natural; "
            "a lot of it reads as discomfort even when you are fine.",
            f"steadiness {steadiness:.2f} of 1.00", "image"))
    elif steadiness >= STEADINESS_GOOD:
        out.append(_obs(
            "steady", SEVERITY_STRENGTH,
            "You held yourself still and settled",
            "Very little restless movement, which reads as being at ease.",
            f"steadiness {steadiness:.2f} of 1.00", "image"))
    return out


def for_answer(
    speech: Optional[SpeechAnalysisResult],
    facial: Optional[FacialAnalysis] = None,
    content: Optional[ContentEvaluation] = None,
) -> List[Observation]:
    """Everything worth saying about how one answer was delivered.

    Often empty, because a well delivered answer needs no comment.
    Most important first.
    """
    if not speech:
        return []

    prosody = speech.get("prosody") or {}
    fillers = speech.get("fillers") or {}
    pauses = speech.get("pauses") or {}
    duration = float(prosody.get("duration_seconds", 0.0))

    found: List[Optional[Observation]] = [
        _answer_length(duration),
        _pace(prosody),
        _fillers(fillers),
        _pitch(prosody),
    ]
    out = [o for o in found if o is not None]
    out.extend(_pauses(pauses))
    out.extend(_facial(facial))

    out.sort(key=lambda o: _SEVERITY_RANK.get(o["severity"], 9))
    return out


def strengths(observed: List[Observation]) -> List[Observation]:
    return [o for o in observed if o["severity"] == SEVERITY_STRENGTH]


def concerns(observed: List[Observation]) -> List[Observation]:
    return [o for o in observed if o["severity"] != SEVERITY_STRENGTH]


def across_answers(per_answer: List[List[Observation]]) -> List[str]:
    """Patterns that only show when all the answers are read together."""
    if len(per_answer) < 2:
        return []

    total = len(per_answer)
    kinds: Dict[str, int] = {}
    for observed in per_answer:
        for kind in {o["kind"] for o in observed}:
            kinds[kind] = kinds.get(kind, 0) + 1

    patterns: List[str] = []
    majority = max(2, (total + 1) // 2)

    recurring = {
        "fillers_high": "filler words got in the way in {n} of your {total} answers",
        "fillers_moderate": "filler words crept into {n} of your {total} answers",
        "pace_fast": "you were speaking quickly in {n} of your {total} answers",
        "pace_slow": "you were speaking slowly in {n} of your {total} answers",
        "answer_short": "{n} of your {total} answers were too short to carry a full example",
        "answer_long": "{n} of your {total} answers ran longer than they needed to",
        "monotone": "your delivery was flat in {n} of your {total} answers",
        "dead_air": "you lost the thread in {n} of your {total} answers",
        "no_thinking_pause": "you started answering instantly in {n} of your {total} answers",
        "off_camera": "you drifted out of frame in {n} of your {total} answers",
    }
    for kind, template in recurring.items():
        n = kinds.get(kind, 0)
        if n >= majority:
            patterns.append(template.format(n=n, total=total))

    consistent = {
        "pace_good": "your pace stayed comfortable across the whole session",
        "fillers_low": "your speech stayed clean of filler throughout",
        "in_frame": "you held the camera frame well throughout",
        "considered_start": "you consistently took a beat before answering",
        "answer_well_judged": "you judged the length of your answers well throughout",
    }
    for kind, phrase in consistent.items():
        if kinds.get(kind, 0) >= majority:
            patterns.append(phrase)

    return patterns
