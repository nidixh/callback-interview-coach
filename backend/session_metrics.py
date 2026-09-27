# session_metrics.py
"""The numbers worth keeping from a finished session.

Plain functions over a SessionResult, with no disk or network, so the history and the drill read the same figures.

Some answers are left out on purpose: an answer the model could not score (neutral 3s are not a real judgement), a channel fusion left out (turning the camera off is not a zero), and an answer with no fused score.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Ordered by how much an interviewer notices each one.
# This order also breaks ties when a drill picks the weakest.
RUBRIC_KEYS = (
    "relevance_1to5",
    "structure_1to5",
    "depth_1to5",
    "clarity_1to5",
)


def _answers(session) -> List[Dict[str, Any]]:
    if not isinstance(session, dict):
        return []
    answers = session.get("answers")
    return [a for a in answers if isinstance(a, dict)] if isinstance(answers, list) else []


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def scored_answers(session) -> List[Dict[str, Any]]:
    """Answers the language model actually judged.

    Leaves out the neutral fallback and non-answers, whose bands were never judged.
    """
    out = []
    for answer in _answers(session):
        content = answer.get("content")
        # Non-answers carry all-ones bands that nobody judged.
        if isinstance(content, dict) and content.get("non_answer"):
            continue
        if isinstance(content, dict) and content.get("schema_valid"):
            scores = content.get("scores")
            if isinstance(scores, dict):
                out.append(answer)
    return out


def rubric_means(session) -> Dict[str, float]:
    """Mean 1 to 5 band per rubric dimension, over the judged answers.

    Empty when nothing was judged, meaning no signal.
    """
    answers = scored_answers(session)
    means: Dict[str, float] = {}
    for key in RUBRIC_KEYS:
        values = []
        for answer in answers:
            value = answer["content"]["scores"].get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
        mean = _mean(values)
        if mean is not None:
            means[key] = mean
    return means


def _fused(answer) -> Dict[str, Any]:
    fused = answer.get("fused")
    return fused if isinstance(fused, dict) else {}


def fused_answers(session) -> List[Dict[str, Any]]:
    """Answers fusion produced a breakdown for.

    Checks dimension_scores rather than the score, because a real 0.0 would count as false.
    """
    return [a for a in _answers(session) if _fused(a).get("dimension_scores")]


def answer_scores(session) -> List[float]:
    """The 0-to-100 score of every answer fusion scored, in order."""
    out = []
    for answer in fused_answers(session):
        value = _fused(answer).get("overall_score_0to100")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(float(value))
    return out


def overall_score(session) -> Optional[float]:
    """The session's mean score, or None when nothing was scored."""
    return _mean(answer_scores(session))


def uncapped_score(session) -> Optional[float]:
    """The mean score before the relevance cap was applied.

    Shows progress even when capped scores stay flat.
    None for older sessions that did not record it.
    Non-answers are left out.
    """
    values = []
    for answer in fused_answers(session):
        content = answer.get("content")
        if isinstance(content, dict) and content.get("non_answer"):
            continue
        value = _fused(answer).get("uncapped_score_0to100")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return _mean(values)


def dimension_means(session) -> Dict[str, float]:
    """Mean of each fusion dimension, over the answers it was included in."""
    collected: Dict[str, List[float]] = {}
    for answer in _answers(session):
        for dimension in _fused(answer).get("dimension_scores") or []:
            if not isinstance(dimension, dict) or not dimension.get("included"):
                continue
            name = dimension.get("name")
            value = dimension.get("normalised_0to100")
            if name and isinstance(value, (int, float)) and not isinstance(value, bool):
                collected.setdefault(str(name), []).append(float(value))
    return {name: _mean(values) for name, values in collected.items() if values}


def delivery_means(session) -> Dict[str, float]:
    """Filler rate and speaking pace averaged over the answers that have them.

    Pace stays in syllables per second, because it is an estimate best used for comparison.
    """
    fillers: List[float] = []
    rates: List[float] = []
    for answer in _answers(session):
        speech = answer.get("speech")
        if not isinstance(speech, dict):
            continue
        per_minute = (speech.get("fillers") or {}).get("per_minute")
        if isinstance(per_minute, (int, float)) and not isinstance(per_minute, bool):
            fillers.append(float(per_minute))
        rate = (speech.get("prosody") or {}).get("speaking_rate_syll_per_sec")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool):
            rates.append(float(rate))

    out: Dict[str, float] = {}
    filler_mean = _mean(fillers)
    if filler_mean is not None:
        out["fillers_per_minute"] = filler_mean
    rate_mean = _mean(rates)
    if rate_mean is not None:
        out["speaking_rate_syll_per_sec"] = rate_mean
    return out


def presence_means(session) -> Optional[Dict[str, Any]]:
    """Eye contact and head steadiness over the answers the camera measured.

    Only reliable attempts count.
    None when no answer was measured.
    """
    eyes, steady = [], []
    for answer in _answers(session):
        if (answer.get("content") or {}).get("non_answer"):
            continue
        facial = answer.get("facial") or {}
        gaze = facial.get("gaze") or {}
        if not facial.get("reliable") or gaze.get("eye_contact_0to1") is None:
            continue
        eyes.append(float(gaze["eye_contact_0to1"]))
        if gaze.get("head_steadiness_0to1") is not None:
            steady.append(float(gaze["head_steadiness_0to1"]))
    if not eyes:
        return None
    return {"eye_contact": _mean(eyes), "head_steadiness": _mean(steady), "answers": len(eyes)}


def metrics(session) -> Dict[str, Any]:
    """Everything one history row keeps about one session.

    Kept small, because the history file is read on every Progress page visit.
    """
    answers = _answers(session)
    return {
        "job_title": (session or {}).get("job_title", "") if isinstance(session, dict) else "",
        "answers": len(answers),
        "scored_answers": len(scored_answers(session)),
        "overall": overall_score(session),
        "uncapped": uncapped_score(session),
        "rubric": rubric_means(session),
        "dimensions": dimension_means(session),
        "delivery": delivery_means(session),
        # Eye contact, once the camera measured it.
        # None before that.
        "presence": presence_means(session),
        # How many questions were planned, and whether the interviewer stopped early.
        # A stopped session is kept but not plotted as a full one.
        "prepared": (session or {}).get("prepared") if isinstance(session, dict) else None,
        "ended_early": bool((session or {}).get("ended_early")) if isinstance(session, dict) else False,
    }
