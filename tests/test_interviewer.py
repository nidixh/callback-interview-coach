# test_interviewer.py
"""The interviewer's choices: which questions to ask, and when to press.

The plan is built by code from what was read in the advert and the CV, so it can be tested without the language model.
Follow-ups are tested with the model switched off, which is the fixed wording the app falls back to.
"""

from datetime import date

import followup_planner
import question_planner

READING = {
    "has_cv": True,
    "requirements": [
        {"text": "SQL", "quote": "strong SQL", "must": True, "shown_quote": ""},
        {"text": "Python", "quote": "Python", "must": True, "shown_quote": "built tools in Python"},
        {"text": "weekends", "quote": "work weekends", "must": True, "shown_quote": "", "practical": True},
        {"text": "Tableau", "quote": "Tableau", "must": False, "shown_quote": ""},
    ],
    "duties": [{"text": "build weekly sales reports", "quote": "build weekly sales reports"},
               {"text": "answer data requests", "quote": "answer data requests"}],
    "claims": [{"text": "led a team of four", "quote": "Led a team of four"}],
}
GAP = [{"after": "Mar 2020", "before": "Jan 2022", "months": 21}]


def test_the_plan_has_exactly_the_number_asked_for():
    for n in (3, 5, 8):
        assert len(question_planner.plan(READING, GAP, n, "Data Analyst")) == n


def test_every_question_says_why_it_is_asked():
    for slot in question_planner.plan(READING, GAP, 8, "Data Analyst"):
        assert slot["reason"].startswith("Asked")


def test_what_the_cv_leaves_out_is_asked_about():
    kinds = [s["kind"] for s in question_planner.plan(READING, GAP, 8, "Data Analyst")]
    # SQL: the advert asks, the CV is silent.
    assert "requirement" in kinds
    assert "cv_claim" in kinds  # the CV's claim gets tested
    # The break in the CV comes up.
    assert "timeline_gap" in kinds


def test_without_a_cv_nothing_is_asked_about_one():
    kinds = {s["kind"] for s in question_planner.plan(dict(READING, has_cv=False), GAP, 8, "Data Analyst")}
    assert not kinds & {"cv_claim", "strength", "timeline_gap"}


def test_a_break_in_the_cv_is_found():
    cv = "Barista, Jan 2019 - Mar 2020\nWaiter, Jan 2022 - Present"
    gaps = question_planner.timeline_gaps(cv, today=date(2026, 9, 1))
    assert gaps == [{"after": "Mar 2020", "before": "Jan 2022", "months": 21}]


def test_back_to_back_jobs_have_no_break():
    cv = "Barista, Jan 2019 - Mar 2020\nWaiter, Apr 2020 - Present"
    assert question_planner.timeline_gaps(cv, today=date(2026, 9, 1)) == []


def test_a_thin_answer_gets_a_follow_up():
    decision = followup_planner.plan("I did it.", 3, question="Tell me about a time.", use_llm=False)
    assert decision["ask"] and decision["trigger"] == followup_planner.TRIGGER_TOO_SHORT


def test_a_refusal_is_not_pressed():
    decision = followup_planner.plan("I can't answer that because I never worked with SQL.", 5,
                                     question="Tell me about your SQL work.", use_llm=False)
    assert not decision["ask"]


def test_follow_ups_stop_at_the_session_limit():
    decision = followup_planner.plan("I did it.", 3, asked_so_far=followup_planner.MAX_FOLLOWUPS_PER_SESSION,
                                     use_llm=False)
    assert not decision["ask"]


def test_star_elements_and_concrete_detail_are_recognised():
    star = followup_planner.star_coverage(
        "When I was at the cafe, my task was to train staff. I set up a rota and as a result sales rose by 10%.")
    assert star["situation"] and star["action"] and star["result"]
    assert followup_planner.has_concrete_detail("we cut it by 40 percent using Python")
    assert not followup_planner.has_concrete_detail("it went well I think")
