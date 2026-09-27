# content_evaluator.py
"""Scores each answer and writes interview questions, using a local model through Ollama.

The model is qwen3:4b.
In a trial of six local models it scored closest to hand grades and it fits a 4 GB graphics card.
CALLBACK_MODEL=mistral:latest switches back to the earlier model.

Ollama runs as its own program and is reached over local HTTP, so no model library is installed here and the pinned speech libraries stay intact.

Three things make the output reliable: JSON mode, so every reply parses; a rubric with 1 to 5 band descriptions that must quote the transcript, scored one dimension at a time (Zheng et al., 2023); and validation with one repair attempt, after which neutral scores are returned with schema_valid=False.
Temperature 0 and a fixed seed make scores repeatable.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import non_answer
from schema import (
    ContentEvaluation,
    InterviewQuestion,
    QuestionSet,
    RubricScores,
)

logger = logging.getLogger(__name__)

# Ollama's default loopback endpoint.
# This is inter-process communication with a local process, not a network service; nothing leaves the machine.
_HOST = "http://localhost:11434"
_GENERATE_ENDPOINT = f"{_HOST}/api/generate"
_TAGS_ENDPOINT = f"{_HOST}/api/tags"
_PS_ENDPOINT = f"{_HOST}/api/ps"
_UNLOAD_TIMEOUT_S = 30

_MODEL = os.environ.get("CALLBACK_MODEL", "").strip() or "qwen3:4b"

# Models that fit the 4 GB card completely, so every layer is placed on it.
# This made scoring about 20% faster with the same scores.
# A 7B model does not fit and is left to Ollama.
_ALL_ON_CARD = {"qwen3:4b"}

# A second model whose view of each answer is averaged with the first (see second_opinions).
# Over 41 hand graded answers it cut the average error from 1.26 to 1.00 levels.
# CALLBACK_SECOND_OPINION=0 turns it off.
_SECOND_OPINION_MODEL = os.environ.get("CALLBACK_SECOND_OPINION", "qwen2.5:7b")
if _SECOND_OPINION_MODEL.strip().lower() in ("", "0", "off", "no"):
    _SECOND_OPINION_MODEL = ""

# Fixed seed and temperature 0, so the same answer always gets the same score.
_TEMPERATURE = 0.0
_SEED = 42

# Caps worst-case latency.
# The rubric object is ~120 tokens; 400 leaves room for the quoted evidence spans without allowing an unbounded generation.
_NUM_PREDICT = 400

# A cold model load can take about 86 s, so the first call gets a long timeout.
# The health check uses a short one.
_GENERATE_TIMEOUT_S = 180
_HEALTH_TIMEOUT_S = 3

# One repair attempt only.
# A model that fails the schema twice is not going to succeed on a third try, and each attempt costs a full generation.
_MAX_REPAIRS = 1

_RUBRIC_DIMENSIONS = ("relevance", "depth", "clarity", "structure")

# Highest relevance allowed when the focused check says the question was not answered.
# 2 rather than 1, because it is a single model judgement.
_UNANSWERED_RELEVANCE_MAX = 2

# Neutral fallback: the middle band on every dimension, used when the model gives no valid rubric, so a failure reads as no signal rather than a poor answer.
_NEUTRAL_SCORES = RubricScores(
    relevance_1to5=3, depth_1to5=3, clarity_1to5=3, structure_1to5=3
)

# Hedging words.
# A correction that will not commit is dropped, because wrongly telling a candidate they made a mistake does more harm than missing one.
_HEDGES = ("might", "may ", "maybe", "possibly", "perhaps", "could be",
           "unclear", "not sure", "unsure", "appears to", "seems to",
           "verify", "unverified", "potentially", "if accurate")

# Words of a quote that must appear as a contiguous run in the transcript for the claim to count as something the candidate actually said.
_CLAIM_ANCHOR_RUN = 5

_SESSION = None  # lazy singleton; see _get_session


_RUBRIC_SYSTEM_PROMPT = """\
You are an experienced technical interviewer scoring one spoken answer.

Score each dimension on a 1 to 5 band:

relevance  Judge only whether the answer does what the question asked for.
           Being about the same subject area is NOT relevance. If the question
           asks for a specific example, an answer that gives background,
           introduces the speaker, or discusses the topic in general has NOT
           answered it and scores 1 or 2 however polished it is.
           1 = does not do what was asked; 3 = partially does it, or does it
           only in general terms; 5 = directly and fully does what was asked.
depth      1 = assertion with no support; 3 = some detail but generic;
           5 = specific, concrete examples with reasoning and outcomes.
clarity    1 = incoherent or rambling; 3 = understandable but unfocused;
           5 = well organised and easy to follow.
structure  1 = no discernible structure; 3 = partial structure, some elements
           missing; 5 = complete situation, task, action and result.

Judge only what the answer says. Do not reward length or confident delivery.
A short precise answer must score higher than a long vague one.

Before scoring relevance, state to yourself what the question actually asked
for and check the answer supplies it. Candidates often drift into background
or self-introduction; that is a low relevance score, not a high one.

The transcript comes from automatic speech recognition, so ignore punctuation
and disfluencies such as "um" and "uh"; they are assessed elsewhere and are not
a content weakness.

Reply with a single JSON object and nothing else:
{"relevance": <1-5>, "depth": <1-5>, "clarity": <1-5>, "structure": <1-5>,
 "strengths": ["<short phrase>", ...],
 "improvements": ["<short actionable phrase>", ...],
 "evidence_quotes": ["<exact phrase copied from the answer>", ...]}

Give one to three items per list. Every evidence quote must be copied verbatim
from the answer.\
"""

_RELEVANCE_SYSTEM_PROMPT = """\
You check one thing: did the candidate actually do what the interview question
asked for?

Being about the same subject area does not count. If the question asks for a
specific example or experience and the answer instead gives background,
introduces the speaker, states an opinion, or talks about the topic in general,
then it did NOT do what was asked.

If the question asks for more than one thing, judge the main thing it asks
for. An answer that properly does that counts as answered, even if it leaves
out a smaller part; how much detail it gave is judged elsewhere.

Reply with a single JSON object and nothing else:
{"asked_for": "<what the question required, in a few words>",
 "supplied": "<what the answer actually gave, in a few words>",
 "answered": true or false}\
"""

# Practical questions ask for a fact, such as availability, not a story, so a short clear reply is a full answer.
# Spotted by their wording.
_PRACTICAL = re.compile(r"\b(availab\w*|notice period|start date|when can you start|salary|pay|"
                        r"wage|rate of pay|weekends?|shifts?|hours|relocat\w*|right to work|visa|"
                        r"commute|full[- ]time|part[- ]time|days a week)\b", re.IGNORECASE)
_STORY = re.compile(r"\b(tell me about|describe|a time|an example|a situation|how did you|"
                    r"walk me through|what did you do)\b", re.IGNORECASE)

_CLEAR_SYSTEM_PROMPT = """\
The interview question asks for a practical fact or arrangement: availability,
a notice period, pay, a start date, hours or similar. You check one thing: did
the answer give a clear answer to exactly that? It may add conditions or
preferences; it still counts if what was asked for is stated plainly.

Reply with a single JSON object and nothing else:
{"asked_for": "<the fact or arrangement asked for, in a few words>",
 "gave": "<what the answer said about it, in a few words>",
 "clear": true or false}\
"""

# For a planned "How would you...?" question.
# Checks the reasoning instead of asking for a past example.
# Same reply shape as the relevance check.
_REASONING_SYSTEM_PROMPT = """\
The interview question describes a situation from the job and asks what the
candidate would do. It does not need a past story. You check one thing: did
the answer give a sensible way to handle that situation, with specific steps
in a workable order?

Say what the question needed and what the answer gave, then decide. An answer
that only says general things ("I would stay calm and do my best"), or talks
about something else, has not handled it.

Reply with a single JSON object and nothing else:
{"asked_for": "<what handling the situation needs, in a few words>",
 "supplied": "<what the answer offered, in a few words>",
 "answered": true or false}\
"""

# Added to the clarity check for a question about a break on the CV.
# The candidate owes no reason, so "personal reasons" is a clear answer.
_GAP_NOTE = ("This question asks about a break in the candidate's CV. Any brief account of that time, "
             "including simply \"personal reasons\", is a clear answer.")
_PRACTICAL_KINDS = ("practical", "timeline_gap")

_GRADE_SYSTEM_PROMPT = """\
You are the recruiter for this job, listening to one answer in an interview.
Judge one thing: what did this answer prove to you? After hearing it, how much
more sure are you that this candidate can do what this question was testing,
for this job?

Claims are not proof. "I have worked as a waiter", "I am a hard worker", "I
always keep things clean" tell you nothing you can rely on. Proof is what they
did, how and why they did it, and what came of it, in a real situation that
matters for this job. For a practical question (availability, notice period,
salary expectations) proof is a clear, usable answer to exactly what was asked.

Levels:
0  nothing usable: off the point, about something else, or no answer
2  claims only: says they have the experience or quality, nothing to back it
4  a relevant real situation is named, but little about what they themselves did
6  a real example with what they did, but thin on how or why, or no result
8  specific actions and reasons, in a situation that matters for this job, with a result
10 all of that, with a result you could check (a number, a change, a reaction)
   and clearly meeting what this job needs
Use the odd numbers when an answer falls between two levels.

How it was said (grammar, hesitation, filler words, accent) is judged
elsewhere; ignore it. The answer is an automatic transcript of speech.

Reply with a single JSON object and nothing else:
{"testing": "<what this question tests for this job, in a few words>",
 "proved": "<what the answer actually proved, in a few words, or nothing>",
 "missing": "<what you still do not know after hearing it, in a few words>",
 "level": <0-10>}\
"""

_CLAIMS_EXTRACT_PROMPT = """You list the technical statements a candidate made. You do not judge them.

A technical statement names a specific technology, tool, language or mechanism
and says something about it. Copy each one as a short self-contained sentence,
using the candidate's own words where you can.

If the answer contains no technical statements, reply with an empty list. Many
answers contain none; that is normal.

Reply with a single JSON object and nothing else:
{"statements": [{"quote": "<exact words copied from the answer>",
                 "statement": "<the claim as one self-contained sentence>"}]}"""

_CLAIMS_VERIFY_PROMPT = """You judge whether one technical statement is correct.

Answer true if the statement is technically accurate, or if it simply describes
something someone did without making a factual error. Answer false only when it
contains a clear factual error about a technology - for example naming a tool as
belonging to a language or ecosystem it does not belong to.

Describing ordinary use of a technology is not an error. Being vague is not an
error. Only being wrong is.

Reply with a single JSON object and nothing else:
{"reason": "<one short sentence>", "correct": true or false,
 "correction": "<what is actually the case, only if correct is false>"}"""

# Reply length for up to eight questions.
# 400 tokens cut the JSON off at eight questions.
_QUESTIONS_NUM_PREDICT = 900
_QUESTIONS_SCHEMA = {"type": "object", "required": ["questions"], "properties": {
    "questions": {"type": "array", "items": {
        "type": "object", "required": ["question", "competency"],
        "properties": {"question": {"type": "string"}, "competency": {"type": "string"}}}}}}

_QUESTION_SYSTEM_PROMPT = """\
You are an experienced interviewer preparing a screening interview.

Read the job description and write interview questions that probe the
competencies it actually asks for. Prefer behavioural and situational questions
that require a concrete worked example over questions answerable with a
definition. Do not ask about salary, visa status, age, health or any other
protected characteristic.

Reply with a single JSON object and nothing else:
{"questions": [{"question": "<the question>", "competency": "<competency probed>"}, ...]}\
"""


def _get_session():
    """A shared requests session, created on first use."""
    global _SESSION
    if _SESSION is None:
        import requests  # loaded only when needed

        _SESSION = requests.Session()
    return _SESSION


def unload_all() -> List[str]:
    """Unload every model Ollama holds and return their names.

    Frees the graphics card for transcription, which was about twice as fast without a language model loaded.
    Failures only cost speed.
    CALLBACK_FREE_CARD=0 turns it off (the tests do).
    """
    if os.environ.get("CALLBACK_FREE_CARD", "").strip() == "0":
        return []
    try:
        resp = _get_session().get(_PS_ENDPOINT, timeout=_HEALTH_TIMEOUT_S)
        resp.raise_for_status()
        names = [m.get("name", "") for m in resp.json().get("models", []) if m.get("name")]
    except Exception as exc:  # housekeeping is best effort
        logger.debug("Could not list the loaded models: %s", exc)
        return []
    unloaded: List[str] = []
    for name in names:
        try:
            _get_session().post(_GENERATE_ENDPOINT, json={"model": name, "keep_alive": 0},
                                timeout=_UNLOAD_TIMEOUT_S).raise_for_status()
            unloaded.append(name)
        except Exception as exc:
            logger.debug("Could not unload %s: %s", name, exc)
    return unloaded


def _health_check() -> Optional[str]:
    """Return None if the Ollama server is reachable, else a warning sentence."""
    try:
        resp = _get_session().get(_TAGS_ENDPOINT, timeout=_HEALTH_TIMEOUT_S)
        resp.raise_for_status()
        names = {m.get("name", "") for m in resp.json().get("models", [])}
    except Exception as exc:  # any failure means "not usable"
        return (
            f"Ollama server is not reachable at {_HOST} ({exc}); "
            "content evaluation was skipped."
        )
    if _MODEL not in names:
        return (
            f"Ollama is running but the model {_MODEL} is not installed; "
            "content evaluation was skipped."
        )
    return None


def _generate(system: str, prompt: str, num_predict: Optional[int] = None,
              schema: Optional[Dict[str, Any]] = None) -> str:
    """One JSON generation.
    Returns the raw reply text.

    `num_predict` raises the reply length limit.
    `schema` limits the reply to exactly those keys and types, which stops long replies drifting into extra keys.
    """
    payload = {
        "model": _MODEL,
        "system": system,
        "prompt": prompt,
        "format": schema or "json",  # grammar-constrained decoding
        "stream": False,
        # A thinking model (qwen3) otherwise spends the budget and replies empty.
        "think": False,
        "options": {
            "temperature": _TEMPERATURE,
            "seed": _SEED,
            "num_predict": num_predict or _NUM_PREDICT,
            **card_options(_MODEL),
        },
    }
    return post_generate(_get_session(), _GENERATE_ENDPOINT, payload, _GENERATE_TIMEOUT_S)


def card_options(model: str) -> Dict[str, Any]:
    """Ask for every layer on the graphics card, for a model known to fit."""
    return {"num_gpu": 99} if model in _ALL_ON_CARD else {}


def post_generate(session, endpoint: str, payload: Dict[str, Any], timeout) -> str:
    """Send one generation request and return its text.

    If loading every layer on the card fails, ask again and let Ollama place what fits.
    Slower, but still scored by the model.
    """
    resp = session.post(endpoint, json=payload, timeout=timeout)
    options = payload.get("options") or {}
    if getattr(resp, "status_code", 200) >= 500 and "num_gpu" in options:
        logger.warning("Loading %s wholly on the card failed; letting Ollama place it.", payload.get("model"))
        retry = {**payload, "options": {k: v for k, v in options.items() if k != "num_gpu"}}
        resp = session.post(endpoint, json=retry, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("response", "")


def _clamp_band(value) -> Optional[int]:
    """Coerce a rubric value to an int in 1..5, or None if it cannot be."""
    try:
        band = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return band if 1 <= band <= 5 else None


def _as_phrase_list(value, limit: int = 3) -> List[str]:
    """Normalise a model-supplied list field to a short list of clean strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        if len(out) >= limit:
            break
    return out


def _check_answered(transcript: str, question: str) -> Optional[bool]:
    detail = _answered_detail(transcript, question)
    return detail["answered"] if detail else None


def _answered_detail(transcript: str, question: str,
                     system: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Ask on its own whether the answer did what the question asked.

    Inside the full rubric the model counted "same subject" as relevant.
    Asked alone, as one yes or no decision, it judges correctly, and the result caps the relevance score.

    Returns {"answered", "asked_for", "supplied"}, or None when the check could not run.
    `system` swaps in another check with the same reply shape.
    """
    prompt = (
        f"Interview question:\n{question.strip()}\n\n"
        f"Candidate's answer:\n{transcript.strip()}"
    )
    try:
        raw = _generate(system or _RELEVANCE_SYSTEM_PROMPT, prompt)
        obj = json.loads(raw)
    except Exception:  # advisory check, never fatal
        return None
    if not isinstance(obj, dict) or "answered" not in obj:
        return None
    value = obj.get("answered")
    if isinstance(value, str):
        value = value.strip().lower() in ("true", "yes")
    if not isinstance(value, bool):
        return None
    return {"answered": value, "asked_for": str(obj.get("asked_for") or "").strip(),
            "supplied": str(obj.get("supplied") or "").strip()}


def _is_practical(question: str) -> bool:
    """A question after a fact or an arrangement rather than a story."""
    return bool(_PRACTICAL.search(question or "")) and not _STORY.search(question or "")


def _check_clear(transcript: str, question: str, note: str = "") -> Optional[bool]:
    """For a practical question: was the answer stated plainly?

    `note` adds guidance for special cases, such as a break on the CV.
    """
    prompt = (f"Interview question:\n{question.strip()}\n\n"
              f"Candidate's answer:\n{transcript.strip()}")
    if note:
        prompt += f"\n\n{note}"
    try:
        obj = json.loads(_generate(_CLEAR_SYSTEM_PROMPT, prompt))
    except Exception:  # advisory, never fatal
        return None
    value = obj.get("clear") if isinstance(obj, dict) else None
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes")
    return value if isinstance(value, bool) else None


def _proof(detail: Optional[Dict[str, Any]], bands: Dict[str, int], practical: bool,
           clear: Optional[bool]) -> Dict[str, Any]:
    """What the answer proved to a recruiter, 0 to 100.

    An answer that did what was asked scores 40 to 100 by how specific it was.
    One that did something else scores 5 to 25, so it always stays below the weakest real answer.
    A practical question is scored on whether the answer was clear.
    This matched 21 hand graded answers better than asking the model for a grade directly.
    """
    asked_for = (detail or {}).get("asked_for", "")
    supplied = (detail or {}).get("supplied", "")
    if practical and clear is not None:
        if clear:
            score, basis = 70 + 5 * (bands["clarity"] - 1), "A clear answer to a practical question."
        else:
            score, basis = 10 + 5 * (bands["clarity"] - 1), "A practical question, without a clear answer to it."
    elif detail is None:
        score = 100.0 * (sum(bands.values()) - 4) / 16.0
        basis = "Judged on the rubric alone: the focused check did not answer."
    elif detail["answered"]:
        score = 40 + 15 * (bands["depth"] - 1)
        basis = {1: "Did what was asked, but nothing to back it up.",
                 2: "Did what was asked, with a little to back it up.",
                 3: "Did what was asked, with some detail, but general.",
                 4: "Did what was asked, with specific detail.",
                 5: "Did what was asked, specifically, with reasons and a result."}[bands["depth"]]
    else:
        score = 5 * bands["depth"]
        basis = "Did not give what the question asked for, so it proves little for this job."
    return {"score_0to100": round(float(score), 1), "basis": basis, "practical": practical,
            "clear": clear, "asked_for": asked_for, "supplied": supplied}


def _generate_with(model: str, system: str, prompt: str) -> str:
    """One constrained generation from a named model (the second opinion's)."""
    payload = {"model": model, "system": system, "prompt": prompt, "format": "json", "stream": False,
               "think": False, "options": {"temperature": _TEMPERATURE, "seed": _SEED, "num_predict": _NUM_PREDICT,
                                           **card_options(model)}}
    return post_generate(_get_session(), _GENERATE_ENDPOINT, payload, _GENERATE_TIMEOUT_S)


def _installed(model: str) -> bool:
    """Whether Ollama has this model, so a missing one costs a list, not a failure."""
    try:
        resp = _get_session().get(_TAGS_ENDPOINT, timeout=_HEALTH_TIMEOUT_S)
        names = {m.get("name", "") for m in resp.json().get("models", [])}
    except Exception:  # treated as not installed
        return False
    return model in names or f"{model}:latest" in names


def second_opinions(items, job_description: str = "") -> List[Optional[int]]:
    """The second model's proof level (0 to 10) for each answer, asked in one batch.

    `items` holds (transcript, question) per answer, or None to skip one.
    Asked once after all answers are scored, because swapping models on the card costs 12 to 14 s.
    None wherever no opinion was given.
    """
    out: List[Optional[int]] = [None] * len(items)
    if not _SECOND_OPINION_MODEL or not any(items) or not _installed(_SECOND_OPINION_MODEL):
        return out
    for i, item in enumerate(items):
        if not item:
            continue
        transcript, question = item
        prompt = (f"The job:\n{job_description.strip() or '(not supplied)'}\n\n"
                  f"Interview question:\n{question.strip()}\n\n"
                  f"Candidate's answer (automatic transcript):\n{transcript.strip()}")
        try:
            level = json.loads(_generate_with(_SECOND_OPINION_MODEL, _GRADE_SYSTEM_PROMPT, prompt)).get("level")
        except Exception:
            # One opinion missing is not a failure.
            continue
        if isinstance(level, str) and level.strip().isdigit():
            level = int(level.strip())
        if isinstance(level, (int, float)) and not isinstance(level, bool) and 0 <= level <= 10:
            out[i] = int(round(level))
    return out


def combine_proof(proof: Dict[str, Any], level: Optional[int]) -> Dict[str, Any]:
    """The proof score averaged with the second opinion, when there is one.

    A practical question keeps its clarity verdict, because the second model undervalues short plain answers.
    """
    # "practical" alone is enough: a proof without the clarity verdict (stored before it was kept) still came from the practical rule if it says so.
    judged_practical = proof.get("practical") and (proof.get("clear") is not None
                                                  or "practical question" in str(proof.get("basis", "")))
    if level is None or judged_practical:
        return proof
    built = float(proof["score_0to100"])
    return dict(proof, built_0to100=built, second_opinion_0to100=float(level * 10),
                score_0to100=round((built + level * 10) / 2.0, 1))


def _words(text: str) -> List[str]:
    return "".join(
        c if c.isalnum() or c.isspace() else " " for c in (text or "").lower()
    ).split()


def _was_actually_said(quote: str, transcript: str) -> bool:
    """Did the candidate really say this, or did the model invent it?

    The quote must appear in the transcript.
    This is what stops an invented quote being used to accuse the candidate of an error.
    """
    said = _words(transcript)
    claimed = _words(quote)
    if not claimed or not said:
        return False
    haystack = " ".join(said)
    if " ".join(claimed) in haystack:
        return True
    # ASR transcripts drift, so a long quote may not match end to end.
    # A contiguous run is still evidence; a bag of shared words is not.
    run = _CLAIM_ANCHOR_RUN
    if len(claimed) < run:
        return False
    return any(" ".join(claimed[i:i + run]) in haystack
               for i in range(len(claimed) - run + 1))


def _check_claims(transcript: str, question: str = "") -> List[Dict[str, str]]:
    """Technical statements in the answer that look wrong.

    Shown to the candidate as things to check, never used in the score.

    Asking "what is wrong here" made the model always find something, even in correct answers.
    So the check is split in two: first pull out the technical statements, then ask a yes or no question about each one.
    The model is still sometimes wrong, which is why it never moves a score.

    Returns [] on any failure, for any quote not in the transcript, and for any correction that hedges.
    """
    if not (transcript or "").strip():
        return []
    try:
        obj = json.loads(_generate(
            _CLAIMS_EXTRACT_PROMPT,
            "Candidate's answer:\n" + transcript.strip()))
    except Exception:  # advisory check, never fatal
        return []
    if not isinstance(obj, dict):
        return []

    found: List[Dict[str, str]] = []
    for item in (obj.get("statements") or [])[:4]:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        statement = str(item.get("statement") or "").strip()
        if not quote or not statement or not _was_actually_said(quote, transcript):
            continue
        try:
            verdict = json.loads(
                _generate(_CLAIMS_VERIFY_PROMPT, f"Statement: {statement}"))
        except Exception:
            continue
        if not isinstance(verdict, dict) or verdict.get("correct") is not False:
            continue
        correction = str(verdict.get("correction") or "").strip()
        problem = str(verdict.get("reason") or "").strip()
        if not correction or not problem:
            continue
        if any(h in f"{problem} {correction}".lower() for h in _HEDGES):
            continue
        found.append({"quote": quote, "problem": problem,
                      "correction": correction})
        if len(found) >= 2:
            # Two is plenty to raise with one answer.
            break
    return found


def _validate_rubric(obj) -> Tuple[Optional[Dict[str, int]], str]:
    """Check a decoded rubric object.
    Returns (bands, "") or (None, reason).
    """
    if not isinstance(obj, dict):
        return None, "the response was not a JSON object"
    bands: Dict[str, int] = {}
    missing: List[str] = []
    out_of_range: List[str] = []
    for dim in _RUBRIC_DIMENSIONS:
        if dim not in obj:
            missing.append(dim)
            continue
        band = _clamp_band(obj[dim])
        if band is None:
            out_of_range.append(dim)
        else:
            bands[dim] = band
    if missing:
        return None, f"missing required key(s): {', '.join(missing)}"
    if out_of_range:
        return None, (
            f"key(s) {', '.join(out_of_range)} were not an integer in the range 1 to 5"
        )
    return bands, ""


def claims_for(transcript: str, question: str = "") -> List[Dict[str, str]]:
    """Run the claims check on its own, for a caller that postponed it.

    evaluate(check_claims=False) skips it so the report appears sooner, and the caller then runs this to fill in disputed_claims.
    It can take about 45 s on a long answer and never changes a score.

    Whether the question was answered is checked again here, because an answer that declined has nothing to verify.
    """
    if _check_answered(transcript, question) is False:
        return []
    return _check_claims(transcript, question)


def evaluate(
    transcript: str, question: str, job_description: str = "",
    check_claims: bool = True, duration_seconds: float = 0.0,
    question_kind: Optional[str] = None,
) -> Tuple[ContentEvaluation, List[str]]:
    """Score one transcribed answer against its question.

    Never raises for expected problems (server down, model missing, bad output).
    Those give neutral scores with a warning.

    `check_claims=False` skips the technical claims check; the caller must then run claims_for.
    `duration_seconds` is how long the candidate spoke.
    `question_kind` is the planned question's kind, which picks the focused check.
    """
    warnings: List[str] = []
    t0 = time.perf_counter()

    def _degraded(reason: str, repairs: int = 0) -> Tuple[ContentEvaluation, List[str]]:
        warnings.append(reason)
        return (
            ContentEvaluation(
                model=_MODEL,
                # Copy: callers must not share state.
                scores=dict(_NEUTRAL_SCORES),
                overall_content_0to100=0.0,
                strengths=[],
                improvements=[],
                evidence_quotes=[],
                disputed_claims=[],
                schema_valid=False,
                repair_attempts=repairs,
                latency_seconds=round(time.perf_counter() - t0, 3),
                non_answer=None,
                proof=None,
            ),
            warnings,
        )

    # First, was this an attempt at all?
    # A refusal or abuse scores zero without calling the model.
    hit = non_answer.classify(transcript or "", question, job_description,
                              duration_seconds)
    if hit is not None:
        warnings.append(
            # The kind is a label for the code; the reason is already the sentence a candidate should read.
            f"This answer was not scored: {hit['reason']}")
        return (
            ContentEvaluation(
                model="non-answer-gate",
                scores=RubricScores(relevance_1to5=1, depth_1to5=1,
                                    clarity_1to5=1, structure_1to5=1),
                overall_content_0to100=0.0,
                strengths=[], improvements=[], evidence_quotes=[],
                disputed_claims=[],
                schema_valid=True,
                repair_attempts=0,
                latency_seconds=round(time.perf_counter() - t0, 3),
                non_answer=dict(hit),
                proof=None,
            ),
            warnings,
        )

    unhealthy = _health_check()
    if unhealthy:
        return _degraded(unhealthy)

    user_prompt = (
        f"Job description:\n{job_description.strip() or '(not supplied)'}\n\n"
        f"Interview question:\n{question.strip()}\n\n"
        f"Candidate's answer (automatic transcript):\n{transcript.strip()}"
    )

    repairs = 0
    last_error = ""
    raw = ""
    while repairs <= _MAX_REPAIRS:
        prompt = user_prompt
        if repairs:
            # Carry the specific validation error so the retry is informed rather than a blind resample.
            prompt = (
                f"{user_prompt}\n\nYour previous reply was rejected because "
                f"{last_error}. Reply again with the exact JSON object required."
            )
        try:
            raw = _generate(_RUBRIC_SYSTEM_PROMPT, prompt)
        except Exception as exc:  # network/server boundary
            return _degraded(
                f"Content evaluation could not reach the language model ({exc}); "
                "neutral scores were substituted.",
                repairs,
            )
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            obj = None
            last_error = "the reply was not valid JSON"
        else:
            bands, last_error = _validate_rubric(obj)
            if bands is not None:
                # Cap relevance when the focused check says the question was not answered.
                # This only ever lowers the score.
                kind = (question_kind or "").strip()
                planned = bool(kind) and kind != "follow_up"
                detail = _answered_detail(
                    transcript, question,
                    _REASONING_SYSTEM_PROMPT if kind == "scenario" else _RELEVANCE_SYSTEM_PROMPT)
                answered = detail["answered"] if detail else None
                practical = kind in _PRACTICAL_KINDS if planned else _is_practical(question)
                clear = (_check_clear(transcript, question, _GAP_NOTE if kind == "timeline_gap" else "")
                         if practical else None)
                # The relevance band is lowered for an answer that missed, so the bar says so; a clear practical answer did not miss.
                if (answered is False and not (practical and clear)
                        and bands["relevance"] > _UNANSWERED_RELEVANCE_MAX):
                    warnings.append(
                        "The answer did not do what the question asked for, so its "
                        f"relevance score was reduced from {bands['relevance']} to "
                        f"{_UNANSWERED_RELEVANCE_MAX}."
                    )
                    bands["relevance"] = _UNANSWERED_RELEVANCE_MAX

                # Technical statements that look wrong, reported but never scored.
                # Skipped when the question was not answered.
                proof = _proof(detail, bands, practical, clear)

                claims = ([] if (answered is False or not check_claims)
                          else _check_claims(transcript, question))

                overall = 100.0 * (sum(bands.values()) - 4) / 16.0  # 4..20 -> 0..100
                return (
                    ContentEvaluation(
                        model=_MODEL,
                        scores=RubricScores(
                            relevance_1to5=bands["relevance"],
                            depth_1to5=bands["depth"],
                            clarity_1to5=bands["clarity"],
                            structure_1to5=bands["structure"],
                        ),
                        overall_content_0to100=round(float(overall), 1),
                        proof=proof,
                        strengths=_as_phrase_list(obj.get("strengths")),
                        improvements=_as_phrase_list(obj.get("improvements")),
                        evidence_quotes=_as_phrase_list(obj.get("evidence_quotes")),
                        disputed_claims=claims,
                        schema_valid=True,
                        repair_attempts=repairs,
                        latency_seconds=round(time.perf_counter() - t0, 3),
                        non_answer=None,
                    ),
                    warnings,
                )
        repairs += 1

    return _degraded(
        "Content evaluation returned an invalid rubric object after one repair "
        f"attempt ({last_error}); neutral scores were substituted.",
        repairs - 1,
    )


def generate_questions(
    job_description: str, job_title: str = "", n: int = 6, focus: str = ""
) -> Tuple[QuestionSet, List[str]]:
    """Write interview questions for a role from its job advert.

    Returns an empty set with a warning instead of raising, so the caller can use the fallback questions.
    `focus` aims the questions at one weakness for a drill, while still asking about the role.
    """
    warnings: List[str] = []

    def _degraded(reason: str, repairs: int = 0) -> Tuple[QuestionSet, List[str]]:
        warnings.append(reason)
        return (
            QuestionSet(
                model=_MODEL,
                job_title=job_title,
                questions=[],
                schema_valid=False,
                repair_attempts=repairs,
            ),
            warnings,
        )

    if not job_description or not job_description.strip():
        return _degraded(
            "No job description was supplied, so no questions were generated."
        )

    unhealthy = _health_check()
    if unhealthy:
        return _degraded(unhealthy.replace("content evaluation", "question generation"))

    prompt = (
        f"Job title: {job_title.strip() or '(not supplied)'}\n\n"
        f"Job description:\n{job_description.strip()}\n\n"
        f"Write exactly {n} interview questions."
    )
    if focus.strip():
        prompt += f"\n\n{focus.strip()}"

    try:
        raw = _generate(_QUESTION_SYSTEM_PROMPT, prompt, num_predict=_QUESTIONS_NUM_PREDICT,
                        schema=_QUESTIONS_SCHEMA)
    except Exception as exc:  # network/server boundary
        return _degraded(
            f"Question generation could not reach the language model ({exc}); "
            "no questions were generated."
        )

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return _degraded(
            "Question generation returned output that was not valid JSON; "
            "no questions were generated."
        )

    items = obj.get("questions") if isinstance(obj, dict) else None
    if not isinstance(items, list) or not items:
        return _degraded(
            "Question generation returned no usable questions; "
            "no questions were generated."
        )

    questions: List[InterviewQuestion] = []
    for raw_item in items[:n]:
        if isinstance(raw_item, str):
            text, competency = raw_item.strip(), ""
        elif isinstance(raw_item, dict):
            text = str(raw_item.get("question", "")).strip()
            competency = str(raw_item.get("competency", "")).strip()
        else:
            continue
        if text:
            questions.append(
                InterviewQuestion(
                    index=len(questions) + 1, question=text, competency=competency
                )
            )

    if not questions:
        return _degraded(
            "Question generation returned no usable questions; "
            "no questions were generated."
        )
    if len(questions) < n:
        warnings.append(
            f"Question generation returned {len(questions)} questions rather than "
            f"the {n} requested."
        )

    return (
        QuestionSet(
            model=_MODEL,
            job_title=job_title,
            questions=questions,
            schema_valid=True,
            repair_attempts=0,
        ),
        warnings,
    )
