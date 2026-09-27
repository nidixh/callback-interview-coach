# test_non_answers.py
"""Non-answers: spotting them, and how many the interviewer will sit through.

A refusal or a hostile reply is not scored as a weak answer; it is a different thing.
After one non-answer the interviewer offers a second chance, and after two in a row it stops the session.
"""

import non_answer
import patience

QUESTION = "Tell me about a time you led a team."


def test_a_refusal_is_caught():
    found = non_answer.classify("I don't know.", QUESTION, duration_seconds=2)
    assert found and found["kind"] == "refusal"


def test_a_short_hostile_reply_is_caught():
    found = non_answer.classify("this is a shit question", QUESTION, duration_seconds=3)
    assert found and found["kind"] == "hostile"


def test_a_real_answer_is_left_alone():
    answer = ("At my last job I led a team of four to rebuild our reporting pipeline, "
              "and we cut the run time from two hours to twenty minutes by moving the "
              "joins into the database.")
    assert non_answer.classify(answer, QUESTION, duration_seconds=40) is None


def test_second_chance_then_stop():
    p = patience.Patience()
    assert p.note(patience.VERDICT_NON_ANSWER, False) == patience.ACTION_SECOND_CHANCE
    assert p.note(patience.VERDICT_NON_ANSWER, True) == patience.ACTION_STOP


def test_an_attempt_on_the_second_chance_carries_on():
    p = patience.Patience()
    assert p.note(patience.VERDICT_ATTEMPT, False) == patience.ACTION_CONTINUE
    assert p.note(patience.VERDICT_NON_ANSWER, False) == patience.ACTION_SECOND_CHANCE
    assert p.note(patience.VERDICT_ATTEMPT, True) == patience.ACTION_CONTINUE
