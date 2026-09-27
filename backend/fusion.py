# fusion.py
"""Fusion: combine the channels into one assessment for each answer.

Each channel measures something different, and the useful findings are often where they disagree, such as strong content in a nervous voice.

Delivery carries more weight than the camera, following Naim et al. (2015), who found speech and language predict interview ratings better than the face.
The camera has the smallest weight and is dropped when unreliable, so it can inform advice but never decide the outcome.

Raw measures are mapped onto 0 to 100 with simple straight line bands.
Published sources are cited; other bands are marked as reasoned choices.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import bands
from schema import (
    ContentEvaluation,
    CrossModalFlag,
    DimensionScore,
    FacialAnalysis,
    FusedAssessment,
    SpeechAnalysisResult,
)

logger = logging.getLogger(__name__)

# Proof first.
# An answer's total is what it proved to the recruiter, adjusted by how it came across: total = proof x (PROOF_FLOOR + (1 minus PROOF_FLOOR) x presentation / 100).
# Presentation is delivery and camera in a 3 to 1 ratio.
# Delivery can move an answer by a quarter either way but cannot create value the answer does not have (Campion et al., 1997).
# Nothing is capped.
PROOF_FLOOR = 0.75
PRESENTATION_WEIGHTS = {"delivery": 0.75, "facial": 0.25}
# Proof at or under this is said plainly, with what was asked and what came.
LITTLE_PROOF = 30.0

# Normalisation bands.
# Speaking rate: relaxed conversation is about 4 to 5 syllables per second.
# The flat top is a little wider than that, with a straight line falloff either side.
RATE_IDEAL_LOW, RATE_IDEAL_HIGH = 3.5, 5.5
RATE_FLOOR_LOW, RATE_FLOOR_HIGH = 1.5, 8.0

# Fillers per minute.
# Occasional fillers are normal in fluent speech; the upper bound is a reasoned choice rather than a published threshold.
FILLERS_IDEAL_MAX = 2.0
FILLERS_FLOOR = 12.0

# Pitch variation.
# Very flat delivery reads as monotone.
# Expressed as the standard deviation of F0 in hertz; a reasoned band, not a published one.
PITCH_STD_FLOOR, PITCH_STD_IDEAL = 5.0, 25.0

# Cross channel flag thresholds on the 0 to 100 scale, taken from bands under shorter names.
STRONG, WEAK = bands.FLAG_STRONG, bands.FLAG_WEAK


def _plateau_score(value: float, floor_low: float, ideal_low: float,
                   ideal_high: float, floor_high: float) -> float:
    """Score 100 inside the ideal band, falling linearly to 0 at the floors."""
    if ideal_low <= value <= ideal_high:
        return 100.0
    if value < ideal_low:
        if value <= floor_low:
            return 0.0
        return 100.0 * (value - floor_low) / max(ideal_low - floor_low, 1e-6)
    if value >= floor_high:
        return 0.0
    return 100.0 * (floor_high - value) / max(floor_high - ideal_high, 1e-6)


def _descending_score(value: float, ideal_max: float, floor: float) -> float:
    """Score 100 at or below `ideal_max`, falling linearly to 0 at `floor`."""
    if value <= ideal_max:
        return 100.0
    if value >= floor:
        return 0.0
    return 100.0 * (floor - value) / max(floor - ideal_max, 1e-6)


def _ascending_score(value: float, floor: float, ideal: float) -> float:
    """Score 0 at or below `floor`, rising linearly to 100 at `ideal`."""
    if value >= ideal:
        return 100.0
    if value <= floor:
        return 0.0
    return 100.0 * (value - floor) / max(ideal - floor, 1e-6)


def _delivery_score(
    speech: SpeechAnalysisResult,
) -> Tuple[Optional[float], List[DimensionScore]]:
    """Combine the speech measures into one delivery score.

    Returns (None, []) when there is no speech analysis, so a failed answer is never scored as if it had been measured.
    """
    prosody = speech.get("prosody") or {}
    fillers = speech.get("fillers") or {}
    if not prosody:
        return None, []

    rate = float(prosody.get("speaking_rate_syll_per_sec", 0.0))
    per_minute = float(fillers.get("per_minute", 0.0))
    pitch_std = float(prosody.get("pitch_std_hz", 0.0))

    parts = [
        ("speaking_rate", rate,
         _plateau_score(rate, RATE_FLOOR_LOW, RATE_IDEAL_LOW,
                        RATE_IDEAL_HIGH, RATE_FLOOR_HIGH), 0.40),
        ("filler_rate", per_minute,
         _descending_score(per_minute, FILLERS_IDEAL_MAX, FILLERS_FLOOR), 0.40),
        ("pitch_variation", pitch_std,
         _ascending_score(pitch_std, PITCH_STD_FLOOR, PITCH_STD_IDEAL), 0.20),
    ]
    breakdown = [
        DimensionScore(
            name=name, raw_value=round(raw, 3), normalised_0to100=round(score, 1),
            weight_applied=weight, included=True, exclusion_reason="",
        )
        for name, raw, score, weight in parts
    ]
    total = sum(score * weight for _, _, score, weight in parts)
    return total, breakdown


# The camera score with gaze measured, in the order a video call interviewer notices things: eye contact, a steady head, being in frame, and being framed well.
FACIAL_PARTS = {"eye_contact": 0.50, "steadiness": 0.20, "presence": 0.20, "framing": 0.10}
# Framing that is too close, too far or off centre still shows the face; it costs something, not everything.
FRAMING_OFF_SCORE = 60.0
# Look-aways of a second or more adding up to this share of an answer read, on a call, as not looking at the interviewer.
LOOKED_AWAY_SHARE = 0.25
# Eye contact at or below this with strong content: the answer was heard and not seen.
LITTLE_EYE_CONTACT = 0.50


def _facial_score(facial: Optional[FacialAnalysis]) -> Optional[float]:
    """Score the camera channel, or None when it cannot be trusted.

    With gaze: eye contact, head steadiness, presence and framing (FACIAL_PARTS).
    Without it: presence and steadiness only.

    Expression is shown but never scored, because it does not reliably show how someone feels (Barrett et al., 2019).
    """
    if not facial or not facial.get("reliable"):
        return None
    presence = 100.0 * float(facial.get("face_detection_rate_0to1", 0.0))
    stability = 100.0 * float(facial.get("gaze_centre_stability_0to1", 0.0))
    gaze = facial.get("gaze") or {}
    eye = gaze.get("eye_contact_0to1")
    if eye is None:
        return 0.5 * presence + 0.5 * stability
    steady = gaze.get("head_steadiness_0to1")
    steady = 100.0 * float(steady) if steady is not None else stability
    verdict = (gaze.get("framing") or {}).get("verdict", "good")
    framing = 100.0 if verdict == "good" else FRAMING_OFF_SCORE
    return (FACIAL_PARTS["eye_contact"] * 100.0 * float(eye)
            + FACIAL_PARTS["steadiness"] * steady
            + FACIAL_PARTS["presence"] * presence
            + FACIAL_PARTS["framing"] * framing)


def _looked_away_share(gaze: Dict[str, Any]) -> float:
    """Share of the answer spent in look-aways of a second or more."""
    duration = len(gaze.get("timeline") or "") * float(gaze.get("timeline_step_s") or 0.5)
    if duration <= 0:
        return 0.0
    away = sum(float(s.get("seconds", 0.0)) for s in gaze.get("look_aways") or [])
    return min(1.0, away / duration)


def _cross_modal_flags(delivery: Optional[float], content_score: float,
                       facial: Optional[FacialAnalysis],
                       speech: SpeechAnalysisResult,
                       content: ContentEvaluation) -> List[CrossModalFlag]:
    """Find disagreements that need evidence from more than one domain."""
    flags: List[CrossModalFlag] = []

    # No speech analysis means neither flag below can be judged.
    if delivery is not None:
        if content_score >= STRONG and delivery <= WEAK:
            flags.append(CrossModalFlag(
                kind="content_strong_delivery_weak",
                detail=("The substance of the answer is strong but the delivery works "
                        "against it. The ideas are there; the way they are voiced is "
                        "what needs practice."),
                severity="warn", domains=["text", "audio"],
            ))
        if delivery >= STRONG and content_score <= WEAK:
            flags.append(CrossModalFlag(
                kind="delivery_strong_content_weak",
                detail=("The answer sounds confident and fluent but says little that is "
                        "concrete. Add a specific example with a measurable outcome."),
                severity="warn", domains=["audio", "text"],
            ))

    clarity = (content.get("scores") or {}).get("clarity_1to5", 0)
    per_minute = float((speech.get("fillers") or {}).get("per_minute", 0.0))
    if clarity >= 4 and per_minute >= 8.0:
        # Worded without the number, which is shown in the appendix instead.
        flags.append(CrossModalFlag(
            kind="clear_ideas_obscured_by_disfluency",
            detail=("Your ideas are clearly organised, but the filler words are "
                    "obscuring them. The thinking is better than it sounds, "
                    "which is a frustrating way to be judged."),
            severity="warn", domains=["text", "audio"],
        ))

    if facial and facial.get("reliable"):
        voiced = float((speech.get("prosody") or {}).get("voiced_fraction", 0.0))
        rate = float(facial.get("face_detection_rate_0to1", 1.0))
        gaze = facial.get("gaze") or {}
        eye = gaze.get("eye_contact_0to1")
        if eye is not None:
            # Measured where the eyes were, not only whether a face was there.
            if _looked_away_share(gaze) >= LOOKED_AWAY_SHARE and voiced >= 0.5:
                flags.append(CrossModalFlag(
                    kind="looked_away_while_speaking",
                    detail=("You kept talking, but for a good part of the answer "
                            "your eyes were off the camera. On a call that reads as "
                            "talking past the interviewer rather than to them."),
                    severity="info", domains=["image", "audio"],
                ))
            if float(eye) <= LITTLE_EYE_CONTACT and content_score >= STRONG:
                flags.append(CrossModalFlag(
                    kind="strong_content_little_eye_contact",
                    detail=("The substance of this answer is strong, but most of it "
                            "was given away from the camera. The interviewer hears a "
                            "good answer and does not see you owning it."),
                    severity="warn", domains=["image", "text"],
                ))
        elif rate <= 0.75 and voiced >= 0.5:
            flags.append(CrossModalFlag(
                kind="looked_away_while_speaking",
                detail=("You were speaking for much of the answer but out of frame "
                        "for part of it. In a real interview that reads as lost eye "
                        "contact."),
                severity="info", domains=["image", "audio"],
            ))
        negative = sum(
            v for k, v in (facial.get("expression_mean_probabilities") or {}).items()
            if k in ("fear", "sad", "angry", "disgust")
        )
        if negative >= 0.40 and content_score >= STRONG:
            flags.append(CrossModalFlag(
                kind="anxious_expression_strong_content",
                detail=("Your answer is strong, but your expression reads as tense. "
                        "This is a presentation cue, not a judgement of the answer."),
                severity="info", domains=["image", "text"],
            ))

    return flags


def _feedback_text(overall: float, breakdown: List[DimensionScore],
                   content: ContentEvaluation,
                   flags: List[CrossModalFlag]) -> str:
    """Write the short spoken summary.

    Used when no language model is available, and read aloud by the "read aloud" button.
    The full coaching notes come from coach_writer.
    """
    label = bands.band_label(overall)
    if label == "strong":
        opening = "That was a strong answer."
    elif label == "solid":
        opening = "That was a reasonable answer with clear room to improve."
    else:
        opening = "There is a good deal to work on in that answer."

    lines = [f"{opening} Overall score {overall:.0f} out of 100."]

    # Only the delivery parts.
    # `breakdown` also holds the channel totals, which must not be named as the weakest part of delivery.
    weakest = min(
        (d for d in breakdown
         if d["included"] and d["name"] in bands.DELIVERY_SUB_DIMENSIONS),
        key=lambda d: d["normalised_0to100"], default=None,
    )
    if weakest is not None and weakest["normalised_0to100"] < bands.FLAG_STRONG:
        name = weakest["name"].replace("_", " ")
        lines.append(f"The weakest part of your delivery was {name}.")

    if content.get("improvements"):
        lines.append(f"On content: {content['improvements'][0]}")
    if flags:
        lines.append(flags[0]["detail"])
    return " ".join(lines)


def fuse(speech: SpeechAnalysisResult, content: ContentEvaluation,
         facial: Optional[FacialAnalysis] = None) -> FusedAssessment:
    """Combine the channels into a single assessment.

    Missing or unreliable channels are left out and the other weights are rescaled, so a session without a camera is scored on the same 0 to 100 scale.
    """
    delivery, breakdown = _delivery_score(speech)

    content_valid = bool(content.get("schema_valid"))
    content_score = float(content.get("overall_content_0to100", 0.0))
    facial_score = _facial_score(facial)

    channels: Dict[str, Optional[float]] = {
        "delivery": delivery,
        "content": content_score if content_valid else None,
        "facial": facial_score,
    }
    reasons = {
        "delivery": "" if delivery is not None else "no speech analysis was available",
        "content": "" if content_valid else "the content evaluation did not return a valid score",
        "facial": "" if facial_score is not None else (
            "no video was analysed" if not facial
            else "too few frames contained a detectable face"
        ),
    }

    # How it came across: voice and camera, 3 to 1, renormalised when one is missing (no camera, or too few frames with a face).
    shown = {k: channels[k] for k in PRESENTATION_WEIGHTS if channels[k] is not None}
    total_weight = sum(PRESENTATION_WEIGHTS[k] for k in shown) or 1.0
    weights = {k: round(PRESENTATION_WEIGHTS[k] / total_weight, 4) for k in shown}
    renormalised = len(shown) != len(PRESENTATION_WEIGHTS)
    presentation = sum(shown[k] * weights[k] for k in shown) if shown else None

    # What the answer proved.
    # Older results without a proof score use their rubric content score.
    proof_detail = (content.get("proof") or {}) if content_valid else {}
    proof = proof_detail.get("score_0to100") if proof_detail else (content_score if content_valid else None)
    proof = float(proof) if proof is not None else None

    if proof is None:
        # The content model failed, so there is nothing to build on: the answer is scored on what was measured, and says so in the breakdown.
        overall = presentation if presentation is not None else 0.0
    elif presentation is None:
        overall = proof * (PROOF_FLOOR + (1.0 - PROOF_FLOOR) * 0.5)
    else:
        overall = proof * (PROOF_FLOOR + (1.0 - PROOF_FLOOR) * presentation / 100.0)

    # Not an attempt, so no score.
    # Delivery and camera stay in the breakdown for information, but add nothing.
    not_attempted = content.get("non_answer") if content_valid else None
    if not_attempted:
        overall = 0.0

    for name, value in channels.items():
        breakdown.append(DimensionScore(
            name=name,
            raw_value=round(float(value), 2) if value is not None else 0.0,
            normalised_0to100=round(float(value), 1) if value is not None else 0.0,
            weight_applied=weights.get(name, 0.0),
            included=value is not None,
            exclusion_reason=reasons[name],
        ))

    flags = _cross_modal_flags(delivery, content_score if content_valid else 0.0,
                               facial, speech, content)

    # Say plainly why the total is low when delivery scored well.
    # A non-answer gets its own flag.
    if not_attempted:
        # With no real answer, only the non-answer flag is kept.
        flags = []
        flags.insert(0, CrossModalFlag(
            kind="non_answer",
            detail=(f"This was not an attempt at the question, so it scores "
                    f"nothing. {not_attempted.get('reason', '')} The notes on "
                    "how you came across still stand, and will count once there "
                    "is an answer for them to sit alongside."),
            severity="warn", domains=["text"],
        ))
    elif proof is not None and proof <= LITTLE_PROOF:
        asked, gave = proof_detail.get("asked_for"), proof_detail.get("supplied")
        said = (f"The question was after {asked}; the answer gave {gave}. " if asked and gave else "")
        flags.insert(0, CrossModalFlag(
            kind="little_proof",
            detail=(said + "That gave the interviewer little they can rely on for this "
                    "job, so it scores low however well it was said. What you did, "
                    "how, and what came of it is what earns the marks."),
            severity="warn", domains=["text"],
        ))

    return FusedAssessment(
        dimension_scores=breakdown,
        overall_score_0to100=round(float(overall), 1),
        # Kept for sessions and readers from before: with nothing capped, the score before a cap is the score.
        uncapped_score_0to100=round(float(overall), 1),
        ceiling_applied=None,
        proof_0to100=round(proof, 1) if proof is not None else None,
        presentation_0to100=round(presentation, 1) if presentation is not None else None,
        weights_applied=weights,
        weights_renormalised=renormalised,
        cross_modal_flags=flags,
        feedback_text=_feedback_text(overall, breakdown, content, flags),
    )
