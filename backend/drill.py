# drill.py
"""A short practice session aimed at the weakest part of the last one.

The drill targets a rubric dimension (such as structure or relevance) rather than a topic.
Every answer is scored on each dimension, so there are several readings to compare, and the four names never change between sessions.
A dimension is also something the candidate can practise directly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import content_evaluator
import session_metrics

logger = logging.getLogger(__name__)

# Scores above this are not weak enough to drill.
STRONG_ENOUGH = 4.0

DEFAULT_QUESTIONS = 3

# What each dimension means in plain words, and what to tell the question writer.
# `fallback` questions let the drill run without the language model or without an advert.
_AREAS: Dict[str, Dict[str, Any]] = {
    "relevance_1to5": {
        "name": "relevance",
        "headline": "Answering the question that was actually asked",
        "advice": (
            "Your answers were good, but they were often answers to a nearby "
            "question. Before you start, say the question back to yourself in "
            "your own words, and make your first sentence address it directly."
        ),
        "focus": (
            "The candidate's weakness is RELEVANCE: they drift onto a related "
            "topic instead of answering what was asked. Write questions that "
            "ask for one specific, narrow thing, so that drifting is obvious "
            "to them when they read the question again."
        ),
        "fallback": [
            "Tell me about a time you disagreed with a decision. What did you "
            "do about it specifically?",
            "Describe one thing you built that did not work first time. What "
            "was wrong with it?",
            "What is the single hardest problem you have solved, and what made "
            "it hard?",
        ],
    },
    "structure_1to5": {
        "name": "structure",
        "headline": "Telling it in an order somebody can follow",
        "advice": (
            "The material was there but it arrived out of order. Try it as "
            "four beats: the situation, what you were asked to do, what you "
            "actually did, and how it ended. The last one is the one most "
            "often missing."
        ),
        "focus": (
            "The candidate's weakness is STRUCTURE: their answers do not follow "
            "a situation, task, action, result shape, and usually omit the "
            "result. Write questions that explicitly invite a worked example "
            "with an outcome."
        ),
        "fallback": [
            "Walk me through a project from the moment it started to the "
            "moment it shipped. What changed because of it?",
            "Tell me about a time you had to fix something under time "
            "pressure. What was the outcome?",
            "Describe a piece of work you are proud of. What was the situation, "
            "and what did you actually do?",
        ],
    },
    "depth_1to5": {
        "name": "depth",
        "headline": "Going past the summary into what you actually did",
        "advice": (
            "Your answers stayed at the level of a summary. Name the tool, the "
            "number, the decision you made and the one you rejected. Detail is "
            "what separates having done a thing from having been present for it."
        ),
        "focus": (
            "The candidate's weakness is DEPTH: they describe work in general "
            "terms without specifics. Write questions that demand concrete "
            "detail - a number, a named tool, a decision and its alternative."
        ),
        "fallback": [
            "Pick a technical decision you made and tell me what you chose, "
            "what you rejected, and why?",
            "What is something you measured in your work, and what did the "
            "number turn out to be?",
            "Describe the part of a project you personally wrote. What did it "
            "do?",
        ],
    },
    "clarity_1to5": {
        "name": "clarity",
        "headline": "Saying it once, plainly",
        "advice": (
            "The answers were hard to follow on first hearing. Say the point "
            "first and the detail after it, and stop when you have made it - "
            "most of what cost you here was said after the answer was finished."
        ),
        "focus": (
            "The candidate's weakness is CLARITY: their answers are hard to "
            "follow on first hearing and run on past the point. Write questions "
            "that can be answered well in under ninety seconds and reward a "
            "direct opening sentence."
        ),
        "fallback": [
            "In two sentences, what do you do?",
            "Explain something technical you worked on to somebody who does "
            "not share your background. What is it?",
            "What is the most important thing you learned last year?",
        ],
    },
}


def describe(key: str) -> Optional[Dict[str, Any]]:
    """The candidate-facing words for one rubric dimension, or None."""
    area = _AREAS.get(key)
    if not area:
        return None
    return {"key": key, "name": area["name"], "headline": area["headline"],
            "advice": area["advice"]}


def weakest(session) -> Optional[Dict[str, Any]]:
    """The dimension worth drilling, or None when there is nothing to say.

    None when no answer could be scored or nothing scored low enough.
    A tie goes to the dimension listed first in session_metrics.RUBRIC_KEYS.
    """
    means = session_metrics.rubric_means(session)
    if not means:
        return None

    ordered = [k for k in session_metrics.RUBRIC_KEYS if k in means]
    if not ordered:
        return None

    key = min(ordered, key=lambda k: (means[k], ordered.index(k)))
    if means[key] >= STRONG_ENOUGH:
        return None

    others = [means[k] for k in ordered if k != key]
    described = describe(key) or {"key": key, "name": key, "headline": "",
                                  "advice": ""}
    described["mean"] = means[key]
    # How far clear of the next-weakest it is, so a caller can tell a decisive result from a near tie rather than presenting both the same way.
    described["margin"] = round(min(others) - means[key], 2) if others else 0.0
    described["means"] = means
    return described


def _cool() -> None:
    """Free the graphics card before asking for questions.

    Same step as live_session._default_questions, which a custom question source skips.
    Without it the question model can fail to load on a 4 GB card.
    """
    try:
        import followup_planner

        followup_planner.cool()
    except Exception as exc:  # never worth failing a drill
        logger.debug("Could not release the follow-up model: %s", exc)


def _fallback_questions(key: str, n: int) -> List[str]:
    """Generic questions for one area, repeated in a cycle if more are needed.

    Always returns exactly n, because answers are matched to questions by position.
    """
    area = _AREAS.get(key)
    bank = area["fallback"] if area else _AREAS["relevance_1to5"]["fallback"]
    return [bank[i % len(bank)] for i in range(max(n, 0))]


def question_source(key: str):
    """A question source for LiveSession that drills one dimension.

    Same signature as in LiveSession.__init__: (job_description, job_title, n) returns (QuestionSet, warnings).
    """
    area = _AREAS.get(key)
    name = area["name"] if area else "practice"
    focus = area["focus"] if area else ""

    def source(job_description: str, job_title: str, n: int):
        warnings: List[str] = []
        _cool()

        questions: List[Dict[str, Any]] = []
        try:
            generated, warns = content_evaluator.generate_questions(
                job_description, job_title, n, focus=focus)
            warnings.extend(warns or [])
            questions = list(generated.get("questions") or [])
        except Exception as exc:  # model boundary
            logger.warning("Could not generate drill questions: %s", exc)
            warnings.append(
                f"Drill questions could not be generated ({exc}); generic "
                "questions were used instead."
            )

        if questions:
            # The only place the drilled area survives into the stored record, since everything downstream keeps the question text alone.
            for question in questions:
                question["competency"] = name
            return (
                {"model": f"drill:{name}", "job_title": job_title,
                 "questions": questions, "schema_valid": True,
                 "repair_attempts": 0},
                warnings,
            )

        warnings.append(
            f"These are generic {name} questions rather than ones written from "
            "your advert, because the question generator was unavailable."
        )
        return (
            {
                "model": "drill-fallback",
                "job_title": job_title,
                "questions": [
                    {"index": i + 1, "question": text, "competency": name}
                    for i, text in enumerate(_fallback_questions(key, n))
                ],
                "schema_valid": False,
                "repair_attempts": 0,
            },
            warnings,
        )

    return source
