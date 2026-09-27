# test_local_data.py
"""What the app keeps on this computer: the CV, the calendar, sessions and history.

Everything is written to a throwaway folder (the data_dir fixture), never to the candidate's real one.
"""

from datetime import datetime, timedelta

import pytest

import candidate_cv
import interview_calendar
import session_history
import session_library
import session_store


def test_the_cv_is_saved_read_back_and_removed(data_dir):
    assert candidate_cv.save("Aisha Rahman, BSc Computer Science")
    assert candidate_cv.load() == "Aisha Rahman, BSc Computer Science"
    assert candidate_cv.path().parent == data_dir
    assert candidate_cv.clear()
    assert candidate_cv.load() == ""


def test_the_calendar_adds_finds_and_removes_an_interview(data_dir):
    soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    entry = interview_calendar.add({"date": soon, "time": "10:00", "company": "Acme", "role": "Analyst"})
    assert interview_calendar.next_upcoming()["id"] == entry["id"]
    assert interview_calendar.remove(entry["id"])
    assert interview_calendar.load() == []


def test_a_past_interview_is_not_the_next_one(data_dir):
    past = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    interview_calendar.add({"date": past, "time": "10:00", "company": "Acme"})
    assert interview_calendar.next_upcoming() is None


def test_an_interview_needs_a_company(data_dir):
    with pytest.raises(ValueError):
        interview_calendar.add({"date": "2030-01-01", "time": "10:00", "company": ""})


def test_a_session_gets_a_readable_folder_and_numbered_takes(data_dir):
    folder = session_store.new_session_dir("Kitchen Porter!", stamp="20260927_0150")
    assert folder.name == "20260927_0150_kitchen-porter"
    audio, video = session_store.take_paths(folder, 1)
    assert (audio.name, video.name) == ("take_01.wav", "take_01.mp4")
    assert session_store.list_sessions() == [folder]


def test_deleting_a_session_keeps_its_recordings(data_dir):
    folder = session_store.new_session_dir("Kitchen Porter", stamp="20260927_0150")
    session_store.write_manifest(folder, {"job_title": "Kitchen Porter", "takes": [{"take": 1}]})
    recording = session_store.take_paths(folder, 1)[0]
    recording.write_bytes(b"RIFF")
    assert [e["job_title"] for e in session_library.entries()] == ["Kitchen Porter"]
    assert session_library.delete(folder.name)
    assert session_library.entries() == []
    assert recording.exists()


def test_history_records_sessions_and_compares_first_with_latest(data_dir, strong_session):
    row = session_history.record(strong_session, stamp="2026-09-20T10:00")
    assert row["overall"] == pytest.approx(94.9)
    session_history.record(strong_session, stamp="2026-09-22T10:00")
    changes = session_history.deltas(session_history.load())
    assert changes["sessions"] == 2
    assert changes["overall"]["change"] == 0.0
