# non_answer.py
"""Was this an attempt at the question at all?

A quick rule based check that runs before any model call, so answers like "I don't know" or abuse are not scored as weak answers.

It leans towards calling something an attempt, because telling a nervous candidate they did not try is the worst mistake.
Abuse counts at any length.
Every other rule needs a very short answer, and naming the topic counts as trying.
The word and time limits are shared with the follow-up planner.
"""
from __future__ import annotations

from typing import Iterable, Literal, Optional, TypedDict

from followup_planner import (
    MIN_ANSWER_SECONDS, MIN_WORD_COUNT, _DECLINE_MARKERS, _normalise,
)

__all__ = ["NonAnswer", "NotAnAttempt", "classify", "MIN_ANSWER_SECONDS",
           "MIN_WORD_COUNT"]

Kind = Literal["empty", "hostile", "refusal", "nonsense"]


class NonAnswer(TypedDict):
    kind: Kind
    # One plain sentence, shown on the report card.
    reason: str


class NotAnAttempt(Exception):
    """A session that was not a real attempt, so it is not scored.

    Raised by the session runner when half or more of the answers are non-answers.
    """

    def __init__(self, not_attempted: int, total: int):
        self.not_attempted = int(not_attempted)
        self.total = int(total)
        verb = "was not an attempt" if self.not_attempted == 1 else "were not attempts"
        self.reason = (f"{self.not_attempted} of {self.total} answers {verb} "
                       "at the question.")
        super().__init__(self.reason)


# Abuse aimed at the interviewer.
# Counts at any length.
_ABUSE_MARKERS = (
    "fuck you", "fuck off", "screw you", "piss off", "asshole", "bitch",
    "wanker", "dumb question", "stupid question", "wasting my time",
    "bullshit question",
)

# Swearing on its own.
# Only counts in an answer that is already very short, because a genuine answer can contain a slip.
_SWEAR_MARKERS = ("fuck", "fucking", "shit", "bullshit")

# Asking the interviewer to answer instead, or to move on.
# Always a refusal, whatever else the answer says.
_EVASION_MARKERS = (
    "help me answer", "answer it for me", "answer that for me", "you tell me",
    "you answer", "next question", "skip this", "skip that", "pass on this",
    "i just use claude", "i just use chatgpt", "i use claude for everything",
    "i use chatgpt for everything", "ask me something else",
)

# Asking for the question again.
# Not a refusal, so the live loop repeats or rewords the question.
# Checked before refusals, because "I'm not sure I understand" sounds like one.
_CLARIFY_MARKERS = (
    "repeat the question", "repeat that", "rephrase", "what do you mean",
    "say that again", "didnt catch", "did not catch", "understand the question",
)

# Words that carry no topic.
# Kept short on purpose: the overlap test only needs to stop "the" and "and" counting as shared subject matter.
_STOP_WORDS = frozenset("""
a an the and or but if then so of to in on at for from by with about as into
like through after over between out against during without before under
around among is are was were be been being am do does did doing have has had
having i me my mine we us our you your he him his she her it its they them
their what which who whom this that these those there here when where why how
all any both each few more most other some such no nor not only own same than
too very can will just dont cant wont im ive youre thats yeah yes okay ok um uh
please sir madam know think really also well one thing things
""".split())


def _content_words(text: str) -> set:
    # Words of three letters or more count as topic words.
    # This drops some real short terms, but avoids listing every short common word.
    return {w for w in _normalise(text).split()
            if len(w) >= 3 and w not in _STOP_WORDS}


def _has(text: str, markers: Iterable[str]) -> bool:
    """Whole phrase match.

    Spaces are added around the text and each phrase, so "shit" does not match inside "shitake".
    """
    folded = f" {text} "
    return any(f" {marker} " in folded for marker in markers)


def _shares_topic(said: set, asked: set) -> bool:
    """True when a word in the answer matches a word in the question or advert.

    Simple plurals count ("pipelines" matches "pipeline").
    Anything wider matched too much: "work" inside "working" hid a real non-answer.
    """
    def is_plural_of(root: str, word: str) -> bool:
        return word == root + "s" or word == root + "es"

    for word in said:
        for topic in asked:
            if word == topic:
                return True
            shorter, longer = (word, topic) if len(word) <= len(topic) else (topic, word)
            if len(shorter) >= 4 and is_plural_of(shorter, longer):
                return True
            # Long words may also match as a prefix: "postgres" and "postgresql" name the same tool.
            if len(shorter) >= 5 and longer.startswith(shorter):
                return True
    return False


def classify(transcript: str, question: str, job_description: str = "",
             duration_seconds: float = 0.0) -> Optional[NonAnswer]:
    """The kind of non-answer, or None when it was an attempt.

    Rules are checked in order and the first match wins.
    Only abuse can fire on a longer answer.
    """
    text = _normalise(transcript or "")
    words = text.split()
    if not words:
        return NonAnswer(kind="empty",
                         reason="Nothing was said in the time given.")

    if _has(text, _ABUSE_MARKERS):
        return NonAnswer(
            kind="hostile",
            reason="The answer was directed at the interviewer rather than the question.")

    # A request to hear the question again is for the interviewer to answer, not for the scorer to judge, so it is neither refused nor scored.
    if _has(text, _CLARIFY_MARKERS):
        return None

    short = len(words) < MIN_WORD_COUNT and duration_seconds < MIN_ANSWER_SECONDS
    if not short:
        return None

    said = _content_words(text)
    advert = _content_words(job_description)
    asked = _content_words(question) | advert
    shared = _shares_topic(said, asked)

    # A swear in a short answer that names the topic is a slip, so it only counts when the topic is missing.
    if not shared and _has(text, _SWEAR_MARKERS):
        return NonAnswer(
            kind="hostile",
            reason="The answer was too short to be an attempt and contained swearing.")

    # Asking the interviewer to answer or move on is always a refusal.
    # A plain "I don't know" only counts when the topic is missing too.
    if _has(text, _EVASION_MARKERS) or (_has(text, _DECLINE_MARKERS) and not shared):
        return NonAnswer(
            kind="refusal",
            reason="The question was declined rather than attempted.")

    if len(advert) < 10:
        # Without an advert the question alone has too few words to judge an answer off topic, so this rule needs one.
        return None

    if not shared:
        return NonAnswer(
            kind="nonsense",
            reason="Nothing in the answer touched the question or the role.")
    return None
