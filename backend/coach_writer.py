# coach_writer.py
"""Layer 3: write the report in a coach's voice.

The scoring says how good an answer was.
This writes what to say instead, in a separate model call, because one prompt doing both gave vague advice.

The model writes the content advice.
Delivery notes are built from the observations without the model, so it never sees a number it could misreport.

Every quote the model gives is checked against the transcript and dropped if it is not there; the count is kept in quotes_dropped. try_instead is a suggested sentence, so it is not checked this way.

If the model is unreachable or its reply is unusable, a template debrief is written instead and `source` says so.
"""

from __future__ import annotations

import json
import logging
import re
import string
import time
from typing import Dict, List, Optional, Sequence, Tuple

import bands
import content_evaluator
import observations as observations_mod
from schema import (
    AnswerDebrief,
    CoachPraise,
    CoachRewrite,
    ContentEvaluation,
    JobRequirementCoverage,
    Observation,
    SessionDebrief,
)

logger = logging.getLogger(__name__)

_HOST = "http://localhost:11434"
_GENERATE_ENDPOINT = f"{_HOST}/api/generate"
_TAGS_ENDPOINT = f"{_HOST}/api/tags"

# The model that writes the debriefs, the same one used for scoring.
# In a trial it wrote every debrief with no invented quotes, faster than the previous model.
MODEL = content_evaluator._MODEL

# Fixed seed and temperature 0, so the same session gives the same report.
_TEMPERATURE = 0.0
_SEED = 42

# Reply length limits for the written debriefs.
# The prompt asks for a length; these only stop a runaway reply.
_ANSWER_NUM_PREDICT = 900
_SESSION_NUM_PREDICT = 1100

_GENERATE_TIMEOUT_S = 240
_HEALTH_TIMEOUT_S = 3

# A quote must be long enough to be a real span from the answer.
# Below this, a "quote" is a common word that would pass the grounding check by accident.
_MIN_QUOTE_WORDS = 3

# How many words in a row a quote must share with the transcript when it is not an exact match.
_GROUNDING_RUN_WORDS = 6

# One retry only.
# A second failure keeps the template.
_MAX_REPAIRS = 1

_MAX_WORKED = 3
_MAX_CHANGE = 3
_MAX_PRIORITIES = 3
_MAX_COVERAGE = 8

# Transcript sent to the model.
# Long enough for any realistic interview answer; bounded so one runaway recording cannot blow the context window.
_MAX_TRANSCRIPT_CHARS = 6000
_MAX_JD_CHARS = 3000

_SESSION = None


def _get_session():
    """A requests session for this module.

    Separate from content_evaluator's, so a slow debrief never blocks scoring.
    """
    global _SESSION
    if _SESSION is None:
        import requests

        _SESSION = requests.Session()
    return _SESSION


# Grounding

_PUNCT = str.maketrans("", "", string.punctuation)
_CURLY = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _normalise(text: str) -> str:
    """Simplify text for comparing quotes.

    Removes case, punctuation, curly quotes and extra spaces, but keeps the words and their order.
    """
    folded = (text or "").translate(_CURLY).translate(_PUNCT).lower()
    return " ".join(folded.split())


def is_grounded(quote: str, transcript: str) -> bool:
    """True when `quote` really appears in `transcript`.

    An exact match passes.
    Otherwise the quote must share a long run of words in a row with the transcript, because the model tidies quotes slightly (a self correction dropped, a misheard word fixed).
    Short quotes must match in full.
    """
    needle = _normalise(quote)
    words = needle.split()
    if len(words) < _MIN_QUOTE_WORDS:
        return False

    haystack = _normalise(transcript)
    if needle in haystack:
        return True

    run = min(_GROUNDING_RUN_WORDS, len(words))
    return any(" ".join(words[i:i + run]) in haystack
               for i in range(len(words) - run + 1))


_SECOND_PERSON = re.compile(r"\b(you|your|you're|youre|you've|youve|yourself)\b", re.I)

# Phrases that mark advice about what to say, rather than the sentence itself. try_instead must be the sentence.
_ADVICE_MARKERS = (
    "you should", "you could", "you might", "you had to", "you handled",
    "make sure", "be sure to", "also share", "also mention", "also explain",
    "also describe", "also highlight", "remember to", "try to ", "consider ",
    "focus on", "work on ", "in addition to", "share an example",
    "highlight ", "talk about", "mention that", "explain that you",
)


def _is_spoken_sentence(text: str) -> bool:
    """True when `text` reads like something the candidate would say.

    A real suggested sentence is in the first person ("I built...") and never starts with advice such as "also share" or "make sure to".
    """
    folded = f" {_normalise(text)} "
    if any(f" {marker.strip()} " in folded or folded.startswith(f" {marker.strip()} ")
           for marker in _ADVICE_MARKERS):
        return False
    first_person = re.search(r"\b(i|i'm|im|i've|ive|i'll|ill|we|we're|were|"
                             r"we've|weve|my|our)\b", text, re.I)
    return bool(first_person)


# A comma, a capitalised word and end punctuation, as in ", John!".
# Catches the model addressing the candidate by an invented name.
_VOCATIVE_NAME = re.compile(r",\s+[A-Z][a-z]+[.!?]")


# The prompt's example "try_instead". mistral copied it word for word into a hotel housekeeping answer in the model trial.
_EXAMPLE_MARKERS = ("four hours to twenty minutes", "overnight batch")


def _copies_the_example(text: str, transcript: str = "") -> bool:
    """Whether a suggested sentence reuses the prompt's example, not the answer."""
    said, own = _normalise(text), _normalise(transcript)
    return any(m in said and m not in own for m in _EXAMPLE_MARKERS)


def _names_the_candidate(text: str) -> bool:
    return bool(_VOCATIVE_NAME.search(text or ""))


def _speaks_to_the_candidate(text: str) -> bool:
    """True when a passage speaks to the candidate ("you") rather than about them.

    Checks for "you" rather than banning "they", because a coach can say "the interviewer... they want to hear".
    """
    if not text.strip():
        return True  # nothing to judge
    return bool(_SECOND_PERSON.search(text))


# Model plumbing

def health_check() -> Optional[str]:
    """None when the model is reachable, else a warning sentence."""
    try:
        resp = _get_session().get(_TAGS_ENDPOINT, timeout=_HEALTH_TIMEOUT_S)
        resp.raise_for_status()
        names = {m.get("name", "") for m in resp.json().get("models", [])}
    except Exception as exc:  # any failure means "not usable"
        return (
            f"The coaching report could not reach the language model at {_HOST} "
            f"({exc}); a shorter summary was written instead."
        )
    if MODEL not in names:
        return (
            f"The language model {MODEL} is not installed, so a shorter summary "
            "was written instead."
        )
    return None


def _generate(system: str, prompt: str, num_predict: int) -> str:
    """Send one prompt to the language model through Ollama and return the JSON text it replies with."""
    payload = {
        "model": MODEL,
        "system": system,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        # A thinking model (qwen3) otherwise spends the budget and replies empty.
        "think": False,
        "options": {
            "temperature": _TEMPERATURE,
            "seed": _SEED,
            "num_predict": num_predict,
            **content_evaluator.card_options(MODEL),
        },
    }
    return content_evaluator.post_generate(_get_session(), _GENERATE_ENDPOINT, payload, _GENERATE_TIMEOUT_S)


def _text(value, limit: int = 1200) -> str:
    """Coerce a model field to a clean single string."""
    if isinstance(value, list):
        value = " ".join(str(v) for v in value if v)
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit].strip()


# Prompts

_TONE = """\
You are an experienced interview coach giving spoken feedback to a candidate who
has just finished a practice answer. Write the way a good coach talks.

Rules on voice:
- You are talking TO them, not about them. Every sentence uses "you" and "your".
- NEVER use a name for them, under any circumstances. Not a name from the
  transcript, and NOT a placeholder name of your own ("John", "the candidate",
  etc.) if you don't have one. There is no situation where writing a name is
  correct. Address them only as "you".
  Bad:  "Joseph gave an introduction but did not answer the question."
  Bad:  "Thank you for your practice interview, John."
  Good: "You introduced yourself, but you did not get to the question I asked."
  Good: "Thank you for taking the time to practise this interview."
- Never write "the candidate", "they", "he" or "she" about them either.
- Warm and encouraging, but honest. You respect them enough to tell the truth.
- Write in full sentences and connected prose, not clipped notes or bullets.
- Never mention scores, percentages, numbers, measurements or units.
- Never mention speaking speed, filler words, pauses or anything about how they
  sounded. That is covered elsewhere and is not your job here.
- Never invent anything they did not say, and never invent a name, company,
  place or detail that was not in the transcript or the job description.\
"""

_ANSWER_SYSTEM_PROMPT = _TONE + """

You are given one interview question, the job description it was written from,
and a transcript of the answer. Write the debrief for that one answer.

For "worked" and "change", every "quote" MUST be copied word for word from the
transcript. Do not paraphrase, tidy, or correct the grammar of a quote. If no
exact quote fits an item, leave "quote" as an empty string and still write the
item. Never invent a quote to fill the field.

"change" must never be empty unless the answer was genuinely excellent. If the
answer did not address the question at all, that IS the change: leave "quote"
empty, say plainly in "problem" that the question went unanswered, and use
"try_instead" to write the opening sentence they should have led with.

"try_instead" is the opposite: it is a NEW sentence you are suggesting they say
instead. Write out the actual words, as if speaking them. Do not write advice
about what to say. Write the sentence itself.

Bad:  "Give a more specific example with a measurable result."
Good: "We cut the processing time from about four hours to twenty minutes, which
       meant the overnight batch finished before the team arrived."

That example only shows the form. Build every "try_instead" from this answer's
own story and this job; never reuse the example's content.

Wherever the answer does or does not line up with something the job description
asks for, say so explicitly and name the requirement.

If the answer was strong, do not go easy and do not pad. Say precisely what made
it strong, then still offer one refinement, framed as polish rather than
correction. A strong answer earns more specific attention, not less.

Reply with a single JSON object and nothing else:
{"looking_for": "<2-3 sentences: what this question was really probing, and what
   a strong answer to it contains>",
 "what_you_gave": "<2-3 sentences: a fair, warm characterisation of the answer
   they actually gave>",
 "worked": [{"quote": "<exact words from the transcript>",
             "why": "<2-3 sentences on why this works, naming the job
                     requirement it evidences where there is one>"}],
 "change": [{"quote": "<exact words from the transcript>",
             "problem": "<1-2 sentences on what is weak about it>",
             "try_instead": "<the actual replacement sentence, in their voice>",
             "why": "<1-2 sentences on why the replacement is stronger for this
                     role>"}],
 "one_thing": "<2-3 sentences: if they change only one thing about this answer,
   what is it and why>"}

Give one to three items in "worked" and one to three in "change".\
"""

_SESSION_SYSTEM_PROMPT = _TONE + """

You are given a job description and a summary of every answer from one practice
interview. Write the closing conversation a coach has at the end of a session.

Be honest in the verdict. If this would not yet get through a screening for this
role, say so kindly and say what is missing. If it would, say that plainly.

For "coverage", read the job description and pull out the things it actually asks
for. Quote each requirement in the words the advert uses. Requirements they never
touched are the most useful part of this list, so include them.

Be strict about what counts as evidence. A requirement is evidenced ONLY if the
candidate actually said something that demonstrates it. It is NOT evidenced
because the question was about it, because their degree implies it, or because
they seem like they probably could. If you mark a requirement as evidenced you
MUST supply "quote": their exact words showing it, copied from the transcript.
No quote means it is not evidenced. Being unable to evidence something is a
normal, useful finding, not a failure of the report.

"strongest_moment" must quote them too. If nothing in the session stands out,
say so plainly and leave "quote" empty rather than inventing a highlight.

Reply with a single JSON object and nothing else:
{"opening": "<2-3 sentences: warm, names the role, sets up the debrief>",
 "verdict": "<3-4 sentences: would this get through a screening for this role,
   and why>",
 "coverage": [{"requirement": "<quoted from the job description>",
               "evidenced": true or false,
               "where": "<e.g. 'answer 2', or empty if never>",
               "quote": "<their exact words, or empty if not evidenced>",
               "note": "<1 sentence>"}],
 "strongest_moment": {
     "quote": "<their exact words, or empty if nothing stood out>",
     "why": "<2-3 sentences on the best thing they did all session>"},
 "priorities": ["<a concrete action, written as a full sentence>"],
 "closing": "<2-3 sentences: specific encouragement, not generic>"}

Give two or three priorities, ordered with the most important first.\
"""


# Deterministic delivery paragraph

def _delivery_paragraph(observed: Sequence[Observation]) -> str:
    """Write the delivery note from the observations, without the model."""
    if not observed:
        return ""

    concerns = [o for o in observed if o["severity"] != observations_mod.SEVERITY_STRENGTH]
    strengths = [o for o in observed if o["severity"] == observations_mod.SEVERITY_STRENGTH]

    parts: List[str] = []
    if not concerns and strengths:
        parts.append("On how you came across, there is genuinely little to fix.")
        parts.extend(o["detail"] for o in strengths[:3])
        return " ".join(parts)

    for item in concerns[:3]:
        parts.append(f"{item['headline']}. {item['detail']}")
    if strengths:
        best = strengths[0]
        parts.append(f"Worth keeping, though: {best['detail'][0].lower()}{best['detail'][1:]}")
    return " ".join(parts)


# Template fallbacks

def _template_answer_debrief(
    question: str, content: ContentEvaluation, observed: Sequence[Observation],
    overall: float, transcript: str,
) -> AnswerDebrief:
    """A short debrief written without the model, still as prose."""
    label = bands.band_label(overall)
    if label == "strong":
        gave = ("That was a strong answer. It did what the question asked and "
                "gave the interviewer something concrete to hold onto.")
    elif label == "solid":
        gave = ("That was a reasonable answer. The shape of it was right, and "
                "there is room to make it land harder.")
    else:
        gave = ("That answer needs work. The material may well be there, but it "
                "did not reach the interviewer in a form they could use.")

    worked = [
        CoachPraise(quote="", why=item)
        for item in (content.get("strengths") or [])[:_MAX_WORKED]
    ]
    change = [
        CoachRewrite(quote="", problem=item, try_instead="", why="")
        for item in (content.get("improvements") or [])[:_MAX_CHANGE]
    ]
    one_thing = (
        (content.get("improvements") or [""])[0]
        or "Give one specific example, and finish it with what actually changed."
    )
    return AnswerDebrief(
        looking_for=(
            f"This question was asking you to evidence something specific: "
            f"{question.strip() or 'the competency behind the question'}."
        ),
        what_you_gave=gave,
        worked=worked,
        change=change,
        delivery=_delivery_paragraph(observed),
        one_thing=one_thing,
        source="template",
        quotes_dropped=0,
        latency_seconds=0.0,
    )


def _non_answer_debrief(
    question: str, content: ContentEvaluation, observed: Sequence[Observation],
) -> AnswerDebrief:
    """What the coach says when the question was not attempted.

    No "try this instead", because that would treat declining as a kind of answer.
    The delivery note stays.
    """
    reason = (content.get("non_answer") or {}).get("reason", "")
    return AnswerDebrief(
        looking_for=(
            f"This question was asking you to evidence something specific: "
            f"{question.strip() or 'the competency behind the question'}."
        ),
        what_you_gave=(
            f"This was not an attempt at the question, so there is nothing to "
            f"coach yet. {reason}".strip()
        ),
        worked=[],
        change=[],
        delivery=_delivery_paragraph(observed),
        one_thing=(
            "Answer the question that was asked, even if the closest thing you "
            "have is small. An interviewer can work with a small example; they "
            "cannot work with none."
        ),
        source="template",
        quotes_dropped=0,
        latency_seconds=0.0,
    )


def _template_session_debrief(
    job_title: str, mean_score: float, patterns: Sequence[str],
    priorities: Sequence[str], not_attempted: int = 0, total: int = 0,
) -> SessionDebrief:
    """The session debrief built from fixed sentences, used when the model cannot write one."""
    role = job_title.strip() or "this role"

    # Half or more answers were not attempts, so the usual tips and warm closing are skipped.
    if total and not_attempted * 2 >= total:
        return SessionDebrief(
            opening=(
                f"Right, let us be straight about how that went. You were "
                f"practising for {role}, and I listened to every answer."),
            verdict=(
                f"{not_attempted} of {total} answers were not attempts at the "
                "question. There is no screening this would get through, and "
                "there is nothing in it for me to coach: an interviewer cannot "
                "work with an answer that was not given."),
            coverage=[], strongest_moment="", patterns=list(patterns),
            priorities=[],
            closing=(
                "When you want to practise, come back and answer the questions "
                "as they are asked. Small examples are fine. No example is not."),
            source="template", latency_seconds=0.0,
        )

    label = bands.band_label(mean_score)
    if label == "strong":
        verdict = (
            f"On this session you would give a screener for {role} a good deal "
            "to work with. The examples were there and they were specific.")
    elif label == "solid":
        verdict = (
            f"On this session you are close for {role}, but not yet consistent. "
            "The strongest answers show you have the material; the weaker ones "
            "show it is not yet coming out reliably under pressure.")
    else:
        verdict = (
            f"On this session this would not yet get through a screening for "
            f"{role}. That is a fixable problem and mostly one of preparation "
            "rather than experience.")

    return SessionDebrief(
        opening=(
            f"Right, let us go through how that went. You were practising for "
            f"{role}, and I listened to every answer."),
        verdict=verdict,
        coverage=[],
        strongest_moment="",
        patterns=list(patterns),
        priorities=list(priorities) or [
            "Prepare three concrete examples you can adapt to most questions, "
            "and make sure each one ends with what actually changed.",
        ],
        closing=(
            "Practise the same questions again with those changes in place. "
            "This kind of thing improves quickly once you know what to aim at."),
        source="template",
        latency_seconds=0.0,
    )


# Public: one answer

def write_answer_debrief(
    transcript: str,
    question: str,
    content: ContentEvaluation,
    observed: Sequence[Observation],
    overall_score: float,
    job_description: str = "",
) -> Tuple[AnswerDebrief, List[str]]:
    """Write the coaching debrief for one answer.

    Never raises.
    Returns (debrief, warnings); on failure the debrief is the template one and `source` says so.
    """
    warnings: List[str] = []
    t0 = time.perf_counter()

    def _fallback(reason: str = "") -> Tuple[AnswerDebrief, List[str]]:
        if reason:
            warnings.append(reason)
        debrief = _template_answer_debrief(
            question, content, observed, overall_score, transcript
        )
        debrief["latency_seconds"] = round(time.perf_counter() - t0, 3)
        return debrief, warnings

    # A refusal, abuse, nonsense or an off-topic reply is not an answer, so there is nothing for the model to coach and no reason to pay for a call.
    if content.get("non_answer"):
        debrief = _non_answer_debrief(question, content, observed)
        debrief["latency_seconds"] = round(time.perf_counter() - t0, 3)
        return debrief, warnings

    if not transcript or not transcript.strip():
        return _fallback()

    unhealthy = health_check()
    if unhealthy:
        return _fallback(unhealthy)

    label = bands.band_label(overall_score)
    base_prompt = (
        f"Job description:\n{(job_description or '(not supplied)')[:_MAX_JD_CHARS]}\n\n"
        f"Interview question:\n{question.strip() or '(not recorded)'}\n\n"
        f"Transcript of their answer:\n{transcript.strip()[:_MAX_TRANSCRIPT_CHARS]}\n\n"
        f"Independent assessment of this answer: {label}.\n"
        "Write the debrief."
    )

    # Check the reply and retry once with the problem named.
    # Two problems are worth a retry: talking about the candidate instead of to them, and an empty "change" list on an answer that needed changes.
    obj = None
    for attempt in range(_MAX_REPAIRS + 1):
        prompt = base_prompt
        if attempt:
            prompt = f"{base_prompt}\n\nYour previous reply was rejected because {fault}. Write it again, correctly."
        try:
            raw = _generate(_ANSWER_SYSTEM_PROMPT, prompt, _ANSWER_NUM_PREDICT)
            candidate = json.loads(raw)
        except Exception as exc:  # network / decode boundary
            return _fallback(
                f"The coaching narrative for one answer could not be written "
                f"({exc}); a shorter summary was used instead."
            )
        if not isinstance(candidate, dict):
            fault = "the reply was not a JSON object"
            obj = obj or {}
            continue

        obj = candidate
        prose = " ".join(
            _text(candidate.get(k)) for k in ("what_you_gave", "one_thing")
        )
        if not _speaks_to_the_candidate(prose):
            fault = (
                "it described the candidate in the third person, or used their "
                "name, instead of speaking directly to them as \"you\""
            )
            continue
        if _names_the_candidate(prose):
            fault = (
                "it addressed the candidate by a name, real or invented, "
                "instead of always saying \"you\""
            )
            continue
        if any(isinstance(c, dict) and _copies_the_example(_text(c.get("try_instead")), transcript)
               for c in candidate.get("change") or []):
            fault = (
                "a \"try_instead\" copied the example sentence from the instructions "
                "instead of being built from this answer"
            )
            continue
        if not (candidate.get("change") or []) and label != "strong":
            fault = (
                "the \"change\" list was empty even though this answer was not "
                "excellent; there is always something to improve"
            )
            continue
        break

    if not isinstance(obj, dict) or not obj:
        return _fallback(
            "The coaching narrative for one answer came back in an unusable "
            "form; a shorter summary was used instead."
        )

    dropped = 0
    advice_dropped = 0
    worked: List[CoachPraise] = []
    for item in (obj.get("worked") or [])[:_MAX_WORKED]:
        if not isinstance(item, dict):
            continue
        quote, why = _text(item.get("quote"), 400), _text(item.get("why"))
        if not why:
            continue
        if quote and not is_grounded(quote, transcript):
            dropped += 1
            # Keep the point, drop the unverifiable attribution.
            quote = ""
        worked.append(CoachPraise(quote=quote, why=why))

    change: List[CoachRewrite] = []
    for item in (obj.get("change") or [])[:_MAX_CHANGE]:
        if not isinstance(item, dict):
            continue
        quote = _text(item.get("quote"), 400)
        problem = _text(item.get("problem"))
        # try_instead is a suggestion, so instead of the quote check it must read as a sentence the candidate could say.
        try_instead = _text(item.get("try_instead"), 600)
        why = _text(item.get("why"))
        if not (problem or try_instead):
            continue
        if quote and not is_grounded(quote, transcript):
            dropped += 1
            quote = ""
        if try_instead and (not _is_spoken_sentence(try_instead)
                            or _copies_the_example(try_instead, transcript)):
            advice_dropped += 1
            try_instead = ""
        change.append(CoachRewrite(
            quote=quote, problem=problem, try_instead=try_instead, why=why))

    debrief = AnswerDebrief(
        looking_for=_text(obj.get("looking_for")),
        what_you_gave=_text(obj.get("what_you_gave")),
        worked=worked,
        change=change,
        delivery=_delivery_paragraph(observed),
        one_thing=_text(obj.get("one_thing")),
        source="model",
        quotes_dropped=dropped,
        latency_seconds=round(time.perf_counter() - t0, 3),
    )

    # A reply that produced no usable prose is a failure however well-formed it was; fall back rather than render an empty debrief.
    if not (debrief["what_you_gave"] or debrief["worked"] or debrief["change"]):
        return _fallback(
            "The coaching narrative for one answer came back empty; a shorter "
            "summary was used instead."
        )
    if dropped:
        warnings.append(
            f"{dropped} quotation(s) in one answer's feedback could not be found "
            "in the transcript and were removed."
        )
    if advice_dropped:
        warnings.append(
            f"{advice_dropped} suggested rewrite(s) read as coaching notes rather "
            "than something sayable, and were removed."
        )
    return debrief, warnings


# Public: the whole session

def write_session_debrief(
    answers: Sequence[Dict],
    job_title: str = "",
    job_description: str = "",
) -> Tuple[SessionDebrief, List[str]]:
    """Write the end of session debrief across every answer.

    Never raises.
    Patterns across answers come from the observations and are attached even if the model call fails.
    """
    warnings: List[str] = []
    t0 = time.perf_counter()

    scored = [a for a in answers if (a.get("fused") or {}).get("dimension_scores")]
    mean = (
        sum(a["fused"]["overall_score_0to100"] for a in scored) / len(scored)
        if scored else 0.0
    )
    patterns = observations_mod.across_answers(
        [list(a.get("observations") or []) for a in answers]
    )
    not_attempted = sum(
        1 for a in answers if (a.get("content") or {}).get("non_answer"))

    def _fallback(reason: str = "") -> Tuple[SessionDebrief, List[str]]:
        if reason:
            warnings.append(reason)
        priorities: List[str] = []
        for answer in answers:
            for item in (answer.get("content") or {}).get("improvements") or []:
                if item not in priorities:
                    priorities.append(item)
        debrief = _template_session_debrief(
            job_title, mean, patterns, priorities[:_MAX_PRIORITIES],
            not_attempted=not_attempted, total=len(answers),
        )
        debrief["latency_seconds"] = round(time.perf_counter() - t0, 3)
        return debrief, warnings

    if not answers:
        return _fallback()

    # Half or more were not attempts, so the verdict is written without the model or its health check.
    if not_attempted * 2 >= len(answers):
        return _fallback()

    unhealthy = health_check()
    if unhealthy:
        return _fallback(unhealthy)

    lines: List[str] = []
    for answer in answers:
        transcript = (
            (answer.get("speech") or {}).get("transcription", {}).get("text", "")
        )
        fused = answer.get("fused") or {}
        # Non-answers are labelled separately, so the model does not coach them as weak attempts.
        if (answer.get("content") or {}).get("non_answer"):
            assessment = "not an attempt at the question, scored 0"
        else:
            assessment = bands.band_label(fused.get("overall_score_0to100", 0.0))
        lines.append(
            f"--- Answer {answer.get('index', '?')} ---\n"
            f"Question: {answer.get('question', '')}\n"
            f"Assessment: {assessment}\n"
            f"What they said: {transcript.strip()[:1500] or '(nothing was recorded)'}"
        )

    # A minority of non-answers still drags the session down.
    # Telling the model the count stops it averaging them away into a kinder verdict.
    not_attempted_note = (
        f"{not_attempted} of {len(answers)} answers were not attempts at the "
        "question and scored 0. Do not soften the verdict to compensate.\n\n"
        if not_attempted else ""
    )
    prompt = (
        f"Job title: {job_title.strip() or '(not supplied)'}\n\n"
        f"Job description:\n{(job_description or '(not supplied)')[:_MAX_JD_CHARS]}\n\n"
        f"The session, answer by answer:\n\n" + "\n\n".join(lines) + "\n\n"
        + not_attempted_note
        + "Write the closing debrief."
    )

    # Same check and retry as the per answer debrief.
    obj = None
    fault = ""
    for attempt in range(_MAX_REPAIRS + 1):
        attempt_prompt = prompt
        if attempt:
            attempt_prompt = f"{prompt}\n\nYour previous reply was rejected because {fault}. Write it again, correctly."
        try:
            raw = _generate(
                _SESSION_SYSTEM_PROMPT, attempt_prompt, _SESSION_NUM_PREDICT)
            candidate = json.loads(raw)
        except Exception as exc:  # network / decode boundary
            return _fallback(
                f"The end-of-session summary could not be written ({exc}); a "
                "shorter one was used instead."
            )
        if not isinstance(candidate, dict):
            fault = "the reply was not a JSON object"
            continue

        obj = candidate
        moment = candidate.get("strongest_moment")
        prose = " ".join([
            _text(candidate.get("opening")),
            _text(candidate.get("verdict")),
            _text(candidate.get("closing")),
            _text(moment.get("why")) if isinstance(moment, dict) else _text(moment),
        ])
        if not _speaks_to_the_candidate(prose):
            fault = (
                "it described the candidate in the third person, or used their "
                "name, instead of speaking directly to them as \"you\""
            )
            continue
        if _names_the_candidate(prose):
            fault = (
                "it addressed the candidate by a name, real or invented, "
                "instead of always saying \"you\""
            )
            continue
        break

    if not isinstance(obj, dict) or not obj:
        return _fallback(
            "The end-of-session summary came back in an unusable form; a shorter "
            "one was used instead."
        )

    # All transcripts joined into one text, because the model's answer numbers are not reliable enough to check one answer.
    all_said = " ".join(
        (a.get("speech") or {}).get("transcription", {}).get("text", "")
        for a in answers
    )

    coverage: List[JobRequirementCoverage] = []
    downgraded = 0
    for item in (obj.get("coverage") or [])[:_MAX_COVERAGE]:
        if not isinstance(item, dict):
            continue
        requirement = _text(item.get("requirement"), 300)
        if not requirement:
            continue

        quote = _text(item.get("quote"), 400)
        claimed = bool(item.get("evidenced"))
        # A claim whose quote cannot be found is marked "still unproven" rather than removed.
        if claimed and not is_grounded(quote, all_said):
            claimed, quote = False, ""
            downgraded += 1

        coverage.append(JobRequirementCoverage(
            requirement=requirement,
            evidenced=claimed,
            where=_text(item.get("where"), 60) if claimed else "",
            quote=quote,
            note=_text(item.get("note"), 400),
        ))
    if downgraded:
        warnings.append(
            f"{downgraded} job requirement(s) were reported as evidenced without "
            "anything in the transcript to support it, and were marked unproven "
            "instead."
        )

    moment = obj.get("strongest_moment")
    if isinstance(moment, dict):
        moment_quote = _text(moment.get("quote"), 400)
        moment_why = _text(moment.get("why"))
        if moment_quote and not is_grounded(moment_quote, all_said):
            moment_quote = ""
        strongest = (
            f"You said: “{moment_quote}” {moment_why}".strip()
            if moment_quote else moment_why
        )
    else:
        # Older shape, or a model that ignored the structure: keep the prose but it carries no checkable quote.
        strongest = _text(moment)

    priorities = [
        _text(p, 500) for p in (obj.get("priorities") or [])[:_MAX_PRIORITIES]
    ]

    debrief = SessionDebrief(
        opening=_text(obj.get("opening")),
        verdict=_text(obj.get("verdict")),
        coverage=coverage,
        strongest_moment=strongest,
        patterns=patterns,
        priorities=[p for p in priorities if p],
        closing=_text(obj.get("closing")),
        source="model",
        latency_seconds=round(time.perf_counter() - t0, 3),
    )
    if not (debrief["verdict"] or debrief["priorities"]):
        return _fallback(
            "The end-of-session summary came back empty; a shorter one was used "
            "instead."
        )
    return debrief, warnings
