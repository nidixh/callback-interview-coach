# test_speech.py
"""Speech measures: filler words, pauses, and a failing stage kept contained.

Fillers are counted from the transcriber's words.
Pauses are gaps between words that the audio confirms were silent, so the pause test builds a short signal: tone where the words are, silence between them.
"""

import numpy as np

import filler_detector
import pause_analyser
import stage_guard
from conftest import word

SR = 16000


def test_fillers_are_counted_including_two_word_ones():
    words = [word("So", 0.0, 0.2), word("um", 0.3, 0.5), word("I", 0.6, 0.7), word("basically", 0.8, 1.2),
             word("built", 1.3, 1.6), word("it", 1.7, 1.8), word("you", 1.9, 2.0), word("know", 2.0, 2.2)]
    found = filler_detector.detect(words, 6.0, gap_mode=filler_detector.GAP_MODE_OFF)
    assert found["counts_by_filler"] == {"so": 1, "um": 1, "basically": 1, "you know": 1}
    assert found["per_minute"] == 40.0


def test_an_answer_without_fillers_has_none():
    words = [word("I", 0.0, 0.2), word("built", 0.3, 0.6), word("the", 0.7, 0.8), word("pipeline", 0.9, 1.4)]
    assert filler_detector.detect(words, 2.0, gap_mode=filler_detector.GAP_MODE_OFF)["total_count"] == 0


def test_a_long_silence_is_measured_as_dead_air():
    words = [word("I", 0.0, 0.2), word("built", 0.3, 0.6), word("it", 0.7, 0.9),
             word("then", 4.0, 4.3), word("finished", 4.4, 4.9)]
    audio = np.zeros(int(5.0 * SR), dtype=np.float32)
    for w in words:
        a, b = int(w["start"] * SR), int(w["end"] * SR)
        audio[a:b] = 0.2 * np.sin(np.arange(b - a) * 0.3)
    pauses = pause_analyser.detect(words, 5.0, audio=audio)
    assert pauses["total_count"] == 1
    assert pauses["counts_by_kind"] == {pause_analyser.KIND_DEAD_AIR: 1}
    assert 2.9 < pauses["longest_seconds"] < 3.2


def test_nothing_to_measure_is_an_empty_result_not_an_error():
    assert pause_analyser.detect([], 0.0)["total_count"] == 0


def test_a_failing_stage_falls_back_and_says_so():
    warnings, latency = [], {}
    out = stage_guard.run_stage("broken", lambda: 1 / 0, warnings, latency, default="fallback")
    assert out == "fallback"
    assert warnings and "broken" in warnings[0]
    assert "broken" in latency  # its time is still recorded


def test_a_working_stage_returns_its_value():
    assert stage_guard.run_stage("fine", lambda: 42, [], {}, default=None) == 42
