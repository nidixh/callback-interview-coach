# followup_planner.py
"""Decides whether the interviewer asks a follow-up, and what it asks.

Like a real interviewer, it presses when an answer stops short, and asks for specifics when a well told answer contains nothing checkable.
It never judges whether someone is exaggerating.

Tier 0 picks the follow-up from the answer's length, word rate, which STAR parts are present and whether it has concrete detail.
It is plain Python and cannot fail.

Tier 1 optionally rewords that follow-up with a small model so it sounds natural.
Its reply is checked before it is spoken, and on any failure the fixed wording is used.

plan() never raises and never waits longer than its time budget.
The model call uses a socket timeout, which really closes the connection, so a slow reply cannot hold up the next one.
The rewording model is llama3.2:3b, which fits on the graphics card and answers in about a second.
"""

from __future__ import annotations

import logging
import re
import string
import time
from typing import Dict, Optional, Sequence, Tuple

from schema import FollowUpDecision

logger = logging.getLogger(__name__)

_HOST = "http://localhost:11434"
_GENERATE_ENDPOINT = f"{_HOST}/api/generate"

# Small enough to sit entirely in VRAM alongside the gist ASR model, which is what makes a live call fast enough to be worth making at all.
LLM_MODEL = "llama3.2:3b"
LLM_NUM_PREDICT = 60

# Time budget for one rewording.
# A warm reply takes about 1.3 s, but loading the model after the question writer can take 8 to 11 s. warm() loads it early; this budget covers the case where that did not happen.
LLM_BUDGET_S = 15.0
LLM_CONNECT_TIMEOUT_S = 2.0

# Enough to load the model and return, without generating anything worth having.
WARM_BUDGET_S = 40.0
COOL_BUDGET_S = 20.0

# How long Ollama keeps the rewording model loaded: long enough for one interview.
KEEP_ALIVE = "10m"

# Limits.
# At most one follow-up per answer (a follow-up is never followed up) and three per session.
MAX_FOLLOWUPS_PER_SESSION = 3

# Tier 0 thresholds.
# Under half a minute rarely fits a full STAR answer, and a very low word rate usually means the candidate trailed off.
MIN_ANSWER_SECONDS = 25.0
MIN_WORD_COUNT = 60
LOW_WORDS_PER_MINUTE = 90.0

TRIGGER_NONE = "none"
TRIGGER_EMPTY = "empty"
TRIGGER_TOO_SHORT = "too_short"
TRIGGER_NO_RESULT = "no_result"
TRIGGER_NO_ACTION = "no_action"
TRIGGER_NO_SITUATION = "no_situation"
TRIGGER_LOW_DENSITY = "low_density"
TRIGGER_NO_SPECIFICS = "no_specifics"

SOURCE_TEMPLATE = "template"
SOURCE_LLM = "llm"
SOURCE_LLM_TIMEOUT = "template_after_llm_timeout"
SOURCE_LLM_INVALID = "template_after_llm_invalid"

# Cue phrases for each STAR part.
# A rough guide for choosing a follow-up, not a real measure of structure.
# A wrong guess only costs one extra question.
_CUES: Dict[str, Sequence[str]] = {
    "situation": (
        "when i", "we were", "at the time", "last year", "last semester",
        "during", "the project", "my team", "i was working", "there was",
    ),
    "task": (
        "i had to", "my job", "needed to", "was responsible", "the goal",
        "asked me to", "we wanted", "the aim",
    ),
    "action": (
        "i built", "i decided", "i set up", "i wrote", "i created", "so i",
        "i started", "i changed", "i implemented", "i suggested", "i took",
        "i ran", "i designed", "i fixed",
    ),
    "result": (
        "as a result", "in the end", "we ended up", "the outcome", "which meant",
        "led to", "reduced", "increased", "improved", "saved", "went from",
        "ended up", "meant that", "since then",
    ),
}

# One follow-up per trigger, in an interviewer's words.
# `anchors` are words that show a reworded version still asks the same thing.
# Several are allowed because the model often uses a natural synonym, such as "impact" for "outcome".
_TEMPLATES: Dict[str, Dict[str, object]] = {
    TRIGGER_EMPTY: {
        "text": "Take your time. Could you tell me a bit more about that?",
        "anchors": ("more", "tell", "about"),
    },
    TRIGGER_TOO_SHORT: {
        "text": "That was quite brief. Could you walk me through a specific example?",
        "anchors": ("example", "specific", "walk", "particular", "instance"),
    },
    TRIGGER_NO_RESULT: {
        "text": "You have told me what you did. What actually changed as a result?",
        "anchors": ("result", "outcome", "impact", "change", "difference",
                    "happen", "effect", "work", "know"),
    },
    TRIGGER_NO_ACTION: {
        "text": "And what did you personally do in that situation?",
        "anchors": ("you", "your", "yourself"),
    },
    TRIGGER_NO_SITUATION: {
        "text": "Could you set the scene for me? Where was this, and what was going on?",
        "anchors": ("scene", "where", "context", "situation", "going on",
                    "setting"),
    },
    TRIGGER_LOW_DENSITY: {
        "text": "Could you give me a concrete example of that?",
        "anchors": ("example", "concrete", "specific", "particular", "instance"),
    },
    TRIGGER_NO_SPECIFICS: {
        "text": "Can you be more specific about what changed, and how you know?",
        "anchors": ("specific", "particular", "example", "exactly", "concrete",
                    "measure", "number"),
    },
}

# Order of checking.
# A missing situation comes before a missing result, following the STAR order.

# What counts as a checkable detail: numbers, spoken quantities, and capitalised names mid sentence (a tool, team or place).
# Vague words like "several" do not count.
_NUMBER_WORDS = frozenset("""
one two three four five six seven eight nine ten eleven twelve
twenty thirty forty fifty sixty seventy eighty ninety
hundred hundreds thousand thousands million millions billion billions
percent half quarter third twice double triple dozen
""".split())

_DIGIT = re.compile(r"\d")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_SYSTEM_PROMPT = """\
You are an interviewer asking one short follow-up question.

You are given the question you asked, what the candidate said, and the follow-up
you intend to ask. Reword the follow-up so it refers to the subject they were
talking about, keeping the same thing being asked for.

Refer to the topic in your own words. Do not repeat their exact phrases back to
them: the transcript is a rough live one and mishears words, so quoting it
risks asking about something they never said.

Keep it to one sentence. Keep it conversational. Do not comment on their answer,
do not give feedback, and do not ask anything they have already told you.

Reply with a single JSON object and nothing else:
{"question": "<the reworded follow-up, ending in a question mark>"}\
"""

_MIN_QUESTION_CHARS = 8
_MAX_QUESTION_CHARS = 220

_PUNCT = str.maketrans("", "", string.punctuation)
_SESSION = None


def _get_session():
    """A requests session for this module, separate from content_evaluator's."""
    global _SESSION
    if _SESSION is None:
        import requests

        _SESSION = requests.Session()
    return _SESSION


def _normalise(text: str) -> str:
    return " ".join((text or "").translate(_PUNCT).lower().split())


def star_coverage(transcript: str) -> Dict[str, bool]:
    """Which STAR elements show a cue phrase in the answer."""
    folded = _normalise(transcript)
    return {
        element: any(cue in folded for cue in cues)
        for element, cues in _CUES.items()
    }


def _names_something(text: str) -> bool:
    """Is any word capitalised other than at the start of a sentence?

    "I" and its contractions are skipped.
    """
    for sentence in _SENTENCE_SPLIT.split(text or ""):
        for position, token in enumerate(sentence.split()):
            if position == 0:
                continue
            bare = token.strip(string.punctuation)
            if not bare or bare == "I" or bare.startswith("I'"):
                continue
            if bare[0].isupper():
                return True
    return False


def has_concrete_detail(transcript: str) -> bool:
    """Does the answer contain anything a listener could check?

    A quantity, or a named tool, team or place.
    It only notices whether such detail is there; it says nothing about honesty.
    It relies on Whisper capitalising names.
    """
    text = transcript or ""
    if _DIGIT.search(text):
        return True
    if any(word in _NUMBER_WORDS for word in _normalise(text).split()):
        return True
    return _names_something(text)


# Ways of saying "I have nothing to say about this".
# Only matched against short answers.
# Written without apostrophes, because the transcript is normalised before matching.
_DECLINE_MARKERS = (
    "i cannot", "i cant", "i could not", "i couldnt",
    "i have never", "ive never", "i havent", "i have not",
    "i didnt work", "i did not work", "i dont have", "i do not have",
    "i dont know", "i do not know", "no experience", "not familiar",
    "never used", "never worked", "no idea", "not really",
    "not have enough experience", "hard for me to answer",
)


def declines_the_question(transcript: str, duration_seconds: float) -> bool:
    """Has the candidate said that they cannot answer this?

    Asking a follow-up after a refusal feels like not listening, so no follow-up is planned.
    Only fires on short answers, so real stories are still followed up.
    """
    words = _normalise(transcript).split()
    if not words:
        return False
    # Only where there is no room for a refusal to be a preamble to something.
    if len(words) >= MIN_WORD_COUNT or duration_seconds >= MIN_ANSWER_SECONDS:
        return False
    text = " ".join(words)
    return any(marker in text for marker in _DECLINE_MARKERS)


def choose_trigger(transcript: str, duration_seconds: float) -> str:
    """Decide what, if anything, is worth probing.
    Pure and instant.
    """
    words = len(_normalise(transcript).split())
    if words == 0:
        return TRIGGER_EMPTY

    # Nothing to draw out.
    # Move on to the next question rather than press for detail the candidate has just said does not exist.
    if declines_the_question(transcript, duration_seconds):
        return TRIGGER_NONE
    if duration_seconds < MIN_ANSWER_SECONDS or words < MIN_WORD_COUNT:
        return TRIGGER_TOO_SHORT

    coverage = star_coverage(transcript)
    if not coverage["situation"]:
        return TRIGGER_NO_SITUATION
    if not coverage["action"]:
        return TRIGGER_NO_ACTION
    if not coverage["result"]:
        return TRIGGER_NO_RESULT

    wpm = (words / (duration_seconds / 60.0)) if duration_seconds > 0 else 0.0
    if wpm and wpm < LOW_WORDS_PER_MINUTE:
        return TRIGGER_LOW_DENSITY

    # The story is whole and told at a normal pace.
    # The only thing left worth asking is for the detail that would let someone believe it.
    if not has_concrete_detail(transcript):
        return TRIGGER_NO_SPECIFICS
    return TRIGGER_NONE


def _validate(question: str, anchors, transcript: str) -> bool:
    """Is the reworded question safe to say out loud?

    It must still contain one of the follow-up's anchor words.
    Quoting the candidate is not accepted as proof, because the quick transcript often mishears words ("Postray SQL" for PostgreSQL).
    """
    text = (question or "").strip()
    if not (_MIN_QUESTION_CHARS <= len(text) <= _MAX_QUESTION_CHARS):
        return False
    if "\n" in text or not text.endswith("?"):
        return False

    folded = _normalise(text)
    if isinstance(anchors, str):  # tolerate a single word
        anchors = (anchors,)
    return any(anchor.lower() in folded for anchor in anchors or ())


def personalise(
    template: str, anchors, transcript: str, question: str,
    budget_s: float = LLM_BUDGET_S,
) -> Tuple[str, str]:
    """Reword the follow-up to fit what the candidate said.
    Returns (question, source).

    Falls back to the fixed wording on any failure, and never waits longer than `budget_s` plus connection time.
    """
    payload = {
        "model": LLM_MODEL,
        "system": _SYSTEM_PROMPT,
        "prompt": (
            f"You asked:\n{question.strip()}\n\n"
            f"They said:\n{transcript.strip()[:1500]}\n\n"
            f"Follow-up to reword:\n{template}"
        ),
        "format": "json",
        "stream": False,
        # A thinking model (qwen3) otherwise spends the budget and replies empty.
        "think": False,
        # Longer than any plausible gap between answers, so the model is not evicted mid-interview and re-paid for on the next probe.
        "keep_alive": KEEP_ALIVE,
        "options": {
            "temperature": 0.0, "seed": 42, "num_predict": LLM_NUM_PREDICT,
        },
    }
    try:
        import json as _json

        resp = _get_session().post(
            _GENERATE_ENDPOINT, json=payload,
            timeout=(LLM_CONNECT_TIMEOUT_S, budget_s),
        )
        resp.raise_for_status()
        obj = _json.loads(resp.json().get("response", ""))
        candidate = str((obj or {}).get("question", "")).strip()
    except Exception as exc:  # optional tier, never fatal
        logger.debug("Follow-up personalisation failed: %s", exc)
        return template, SOURCE_LLM_TIMEOUT

    if not _validate(candidate, anchors, transcript):
        return template, SOURCE_LLM_INVALID
    return candidate, SOURCE_LLM


def cool(budget_s: float = COOL_BUDGET_S) -> bool:
    """Unload the rewording model to free the graphics card.

    The question writer needs most of the 4 GB card, and with this model still loaded it failed to start.
    Failures here only make the next load tighter.
    """
    try:
        response = _get_session().post(
            _GENERATE_ENDPOINT,
            json={"model": LLM_MODEL, "prompt": "", "stream": False,
                  "keep_alive": 0},
            timeout=(LLM_CONNECT_TIMEOUT_S, budget_s),
        )
        response.raise_for_status()
        return True
    except Exception as exc:  # housekeeping is best effort
        logger.debug("Could not unload the follow-up model: %s", exc)
        return False


def warm(budget_s: float = WARM_BUDGET_S) -> bool:
    """Load the rewording model now, so the first follow-up does not wait for it.

    Called while the candidate reads the first question.
    Never raises and never waits past its budget.
    Returns whether the model answered.
    """
    try:
        response = _get_session().post(
            _GENERATE_ENDPOINT,
            json={
                "model": LLM_MODEL, "prompt": "ok", "stream": False,
                "keep_alive": KEEP_ALIVE,
                "options": {"num_predict": 1, "temperature": 0.0},
            },
            timeout=(LLM_CONNECT_TIMEOUT_S, budget_s),
        )
        response.raise_for_status()
        return True
    except Exception as exc:
        # Warming is best effort by design.
        logger.debug("Could not warm the follow-up model: %s", exc)
        return False


# A question asking for a fact (notice period, availability, a break on the CV) is well answered in a few words, so only an empty answer gets a follow-up.
_PRACTICAL_KINDS = ("practical", "timeline_gap")


def _is_practical_question(question: str, question_kind: Optional[str]) -> bool:
    if question_kind:
        return question_kind in _PRACTICAL_KINDS
    import content_evaluator

    return content_evaluator._is_practical(question or "")


def plan(
    transcript: str,
    duration_seconds: float,
    question: str = "",
    asked_so_far: int = 0,
    use_llm: bool = True,
    budget_s: float = LLM_BUDGET_S,
    question_kind: Optional[str] = None,
) -> FollowUpDecision:
    """Decide whether to ask a follow-up, and what it should be.

    Never raises, and never waits longer than `budget_s` plus connection time.
    """
    t0 = time.perf_counter()

    def _no(trigger: str = TRIGGER_NONE, warning: str = "") -> FollowUpDecision:
        return FollowUpDecision(
            ask=False, question="", trigger=trigger, source=SOURCE_TEMPLATE,
            template_id="", latency_seconds=round(time.perf_counter() - t0, 3),
            warnings=[warning] if warning else [],
        )

    try:
        if asked_so_far >= MAX_FOLLOWUPS_PER_SESSION:
            return _no()

        trigger = choose_trigger(transcript or "", float(duration_seconds or 0.0))
        if trigger == TRIGGER_NONE:
            return _no()
        if trigger != TRIGGER_EMPTY and _is_practical_question(question, question_kind):
            return _no(trigger)

        template = _TEMPLATES.get(trigger)
        if not template:
            return _no(trigger)

        text, source = template["text"], SOURCE_TEMPLATE
        if use_llm and trigger != TRIGGER_EMPTY and (transcript or "").strip():
            text, source = personalise(
                template["text"], template["anchors"], transcript, question,
                budget_s=budget_s,
            )

        return FollowUpDecision(
            ask=True, question=text, trigger=trigger, source=source,
            template_id=trigger,
            latency_seconds=round(time.perf_counter() - t0, 3), warnings=[],
        )
    except Exception as exc:
        # A probe is never worth a crash.
        logger.warning("Follow-up planning failed: %s", exc)
        return _no(warning=f"The follow-up question could not be planned ({exc}).")
