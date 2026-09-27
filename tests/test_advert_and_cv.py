# test_advert_and_cv.py
"""The advert and the CV: how demanding an advert is, and how well a CV fits it.

Difficulty is measured, not labelled, so every example advert the app ships should measure into the level it is filed under.
The example deck behind "Fill with an example" should work through every job at a level before it repeats one.
"""

import pytest

import difficulty
import examples

LEVELS = ("easy", "medium", "hard")


@pytest.mark.parametrize("level", LEVELS)
def test_every_example_advert_measures_into_its_own_level(level):
    jobs = examples.Deck()._level(level)
    assert jobs
    for job in jobs:
        score = difficulty.measure(job["advert"])["score"]
        assert difficulty.level_for(score) == level, f"{job['id']} measured {score}"


def test_a_technical_advert_measures_harder_than_a_plain_one():
    plain = difficulty.measure("Wash pots. Keep the kitchen clean. Be friendly.")
    technical = difficulty.measure(
        "We need a senior engineer with Kubernetes, Terraform, distributed systems, gRPC, Kafka, "
        "PostgreSQL replication and observability with Prometheus.\n- Design microservices\n"
        "- Own on-call\n- Tune SQL\n- Mentor engineers")
    assert technical["score"] > plain["score"]
    assert technical["requirements"] == 4


def test_cv_fit_rises_with_shared_content():
    advert = "We need Python and SQL for reporting dashboards"
    assert difficulty.fit("Python SQL dashboards reporting", advert) == 1.0
    assert difficulty.fit("baking bread", advert) == 0.0


def test_the_deck_deals_every_job_before_repeating():
    deck = examples.Deck()
    n = len(deck._level("easy"))
    dealt = [deck.deal("easy")["id"] for _ in range(n)]
    assert len(set(dealt)) == n


def test_an_example_comes_with_a_cv_and_its_question_count():
    for level, count in zip(LEVELS, (3, 5, 8)):
        job = examples.Deck().deal(level)
        assert job["job_description"] and job["cv"]
        assert job["question_count"] == count


def test_an_unknown_level_is_refused():
    with pytest.raises(ValueError):
        examples.Deck().deal("impossible")
