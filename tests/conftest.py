# conftest.py
"""Shared setup for the tests.

The app's modules live in backend/ and import each other by plain name, the way they do when the server runs from that folder, so both backend/ and its vision/ folder go on the import path here.

Anything a test saves (CV, calendar, sessions, history) goes to a throwaway folder, through the same COACH_RECORDINGS_DIR setting the app reads, so a test run never touches the candidate's real data.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "vision"))

ARTIFACTS = ROOT / "artifacts"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A fresh, empty place for the app to keep its data during one test."""
    monkeypatch.setenv("COACH_RECORDINGS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def strong_session():
    """A real scored session: the strong example debrief the app ships with."""
    return json.loads((ARTIFACTS / "showcase" / "strong.json").read_text(encoding="utf-8"))


def word(text, start, end):
    """One word as the transcriber reports it, with its timing in seconds."""
    return {"word": text, "start": start, "end": end, "probability": 0.9}
