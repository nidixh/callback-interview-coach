# cv_check.py
"""Which of the CV's claims the candidate actually backed up in the interview.

A separate model call with one job, because asking one prompt for many judgements made all of them worse.
Every claim marked as evidenced must quote the candidate, and the quote is checked against the transcripts.
A claim whose quote cannot be found is kept but marked unproven.

The model connection is shared with coach_writer.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Sequence, Tuple

import coach_writer
from schema import CvClaim

logger = logging.getLogger(__name__)

# A CV yields more checkable statements than a session can ever evidence, and a list this long is already more than a candidate will read before an interview.
MAX_CLAIMS = 12

_NUM_PREDICT = 900

_SYSTEM_PROMPT = """\
You are an interview coach comparing a candidate's CV against what they
actually said in a practice interview.

Pull out the concrete, checkable claims the CV makes: things done, built, led,
measured or shipped. Ignore skills lists, tools named without context, and
anything about education or personal detail. Quote each claim in the words the
CV uses.

Then decide, for each, whether the candidate actually demonstrated it in the
interview. Be strict. A claim is evidenced ONLY if they said something that
demonstrates it. It is NOT evidenced because the CV says so, because a question
was about that area, or because they seem like they probably could. If you mark
a claim evidenced you MUST supply "quote": their exact words from the
transcript. No quote means not evidenced.

Claims they never touched are the most useful part of this list. Include them.

Reply with a single JSON object and nothing else:
{"claims": [{"claim": "<quoted from the CV>",
             "evidenced": true or false,
             "where": "<e.g. 'answer 2', or empty if never>",
             "quote": "<their exact words, or empty if not evidenced>",
             "note": "<1 sentence>"}]}\
"""


def _said(answers: Sequence[Dict[str, Any]]) -> str:
    """All the session's transcripts joined into one text to search.

    The whole session is searched because the model's answer numbers are not reliable.
    Non-answers are left out.
    """
    return " ".join(
        ((a.get("speech") or {}).get("transcription") or {}).get("text", "")
        for a in answers
        if isinstance(a, dict) and not (a.get("content") or {}).get("non_answer")
    ).strip()


def check(cv_text: str, answers: Sequence[Dict[str, Any]]
          ) -> Tuple[List[CvClaim], List[str]]:
    """The CV's claims, each marked with whether the session backed it up.

    Returns an empty list with a warning instead of raising, so a missing model only costs this section.
    """
    warnings: List[str] = []
    cv_text = (cv_text or "").strip()
    said = _said(answers)
    if not cv_text or not said:
        return [], warnings

    prompt = (f"CV:\n{cv_text}\n\n"
              f"What the candidate said across the interview:\n{said}")

    try:
        raw = coach_writer._generate(_SYSTEM_PROMPT, prompt, _NUM_PREDICT)
    except Exception as exc:  # model boundary
        warnings.append(
            f"Your CV could not be checked against this session ({exc}).")
        return [], warnings

    try:
        obj = json.loads(raw)
    except Exception:
        # A non-JSON reply is a degraded reply.
        warnings.append(
            "Your CV could not be checked against this session: the model's "
            "reply was not usable.")
        return [], warnings

    items = obj.get("claims") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        warnings.append(
            "Your CV could not be checked against this session: the model's "
            "reply held no claims.")
        return [], warnings

    claims: List[CvClaim] = []
    downgraded = 0
    for item in items[:MAX_CLAIMS]:
        if not isinstance(item, dict):
            continue
        claim = coach_writer._text(item.get("claim"), 300)
        if not claim:
            continue

        quote = coach_writer._text(item.get("quote"), 400)
        evidenced = bool(item.get("evidenced"))
        if evidenced and not coach_writer.is_grounded(quote, said):
            evidenced, quote = False, ""
            downgraded += 1

        claims.append(CvClaim(
            claim=claim,
            evidenced=evidenced,
            where=coach_writer._text(item.get("where"), 60) if evidenced else "",
            quote=quote,
            note=coach_writer._text(item.get("note"), 400),
        ))

    if downgraded:
        warnings.append(
            f"{downgraded} CV claim(s) were reported as demonstrated with words "
            "the candidate did not say, and were marked unproven instead."
        )
    return claims, warnings
