# patience.py
"""How many non-answers the interviewer accepts before it stops.

Two non-answers in a row end the interview.
After the first one the candidate gets a second chance at the same question, because the live check uses a quick, rough transcript.
A real attempt resets the count.
An "unknown" verdict (no quick transcript) changes nothing.
"""
from __future__ import annotations

STRIKES = 2

VERDICT_ATTEMPT = "attempt"
VERDICT_NON_ANSWER = "non_answer"
VERDICT_UNKNOWN = "unknown"
VERDICTS = (VERDICT_ATTEMPT, VERDICT_NON_ANSWER, VERDICT_UNKNOWN)

ACTION_CONTINUE = "continue"
ACTION_SECOND_CHANCE = "second_chance"
ACTION_STOP = "stop"


class Patience:
    def __init__(self, strikes: int = STRIKES):
        # A limit of zero or less reads as no patience at all: the first non-answer stops, rather than a limit the count can never reach.
        self._limit = max(int(strikes), 1)
        self._strikes = 0

    def note(self, verdict: str, second_chance_given: bool) -> str:
        """Record one judged answer and return what the interviewer does next.

        `second_chance_given` is True when this answer was already the second chance, so a non-answer here cannot earn another one.
        """
        if verdict not in VERDICTS:
            raise ValueError(f"unknown verdict {verdict!r}")
        if verdict == VERDICT_ATTEMPT:
            self._strikes = 0
            return ACTION_CONTINUE
        if verdict == VERDICT_UNKNOWN:
            return ACTION_CONTINUE

        self._strikes += 1
        if self._strikes >= self._limit:
            return ACTION_STOP
        if not second_chance_given:
            return ACTION_SECOND_CHANCE
        return ACTION_CONTINUE
