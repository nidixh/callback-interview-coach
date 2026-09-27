# test_scoring.py
"""Proof-first scoring: bands, the two judges, and marks placed on the answer.

The score an answer gets is built from what it proved, averaged with a second model's opinion, and then shown as marks sitting on the words the candidate actually said.
These tests hold each of those steps to its rule.
"""

import answer_marks
import bands
import content_evaluator


def test_bands_turn_scores_into_the_words_the_report_uses():
    assert bands.band_label(bands.BAND_STRONG) == "strong"
    assert bands.band_label(bands.BAND_STRONG - 0.1) == "solid"
    assert bands.band_label(bands.BAND_SOLID) == "solid"
    assert bands.band_label(bands.BAND_SOLID - 0.1) == "needs work"


def test_two_judges_are_averaged_and_both_are_kept():
    combined = content_evaluator.combine_proof({"score_0to100": 60.0}, 8)
    assert combined["score_0to100"] == 70.0
    assert combined["built_0to100"] == 60.0
    assert combined["second_opinion_0to100"] == 80.0


def test_no_second_opinion_leaves_the_score_alone():
    proof = {"score_0to100": 60.0}
    assert content_evaluator.combine_proof(proof, None) == proof


def test_a_practical_question_keeps_its_clarity_verdict():
    # A plain practical answer ("two weeks") is undervalued by the direct grade, so the second opinion is not allowed to drag it down.
    proof = {"score_0to100": 90.0, "practical": True, "clear": True}
    assert content_evaluator.combine_proof(proof, 3) == proof


def test_a_quote_counts_only_if_the_candidate_said_it():
    said = "we cut the run time by half by moving the joins into the database"
    assert content_evaluator._was_actually_said("cut the run time by half", said)
    assert not content_evaluator._was_actually_said("doubled the revenue", said)


def test_marks_sit_on_the_words_they_describe(strong_session):
    view = answer_marks.view(strong_session)
    assert view and view["answers"]
    for answer in view["answers"]:
        text = answer["transcript"]
        marks = answer["marks"]
        assert marks, "a scored answer should carry marks"
        for mark in marks:
            assert 0 <= mark["start"] <= mark["end"] <= len(text)
            if mark["kind"] in ("worked", "change"):
                assert text[mark["start"]:mark["end"]].strip()
            if mark["kind"] == "filler":
                assert text[mark["start"]:mark["end"]].lower() == mark["label"].lower()


def test_marks_come_in_reading_order(strong_session):
    for answer in answer_marks.view(strong_session)["answers"]:
        starts = [m["start"] for m in answer["marks"]]
        assert starts == sorted(starts)
