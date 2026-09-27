# interview_calendar.py
"""Upcoming real interviews, stored on this computer.

The calendar page adds and removes them, and the setup page counts down to the next one.
Stored beside the recordings, like the CV.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import session_store

logger = logging.getLogger(__name__)

CALENDAR_NAME = "calendar.json"
_FIELDS = ("company", "role", "notes")
_LIMITS = {"company": 80, "role": 80, "notes": 300}


def path() -> Path:
    return session_store.root() / CALENDAR_NAME


def _when(entry: Dict[str, Any]) -> datetime:
    return datetime.strptime(f"{entry['date']} {entry['time']}", "%Y-%m-%d %H:%M")


def load() -> List[Dict[str, Any]]:
    """Every interview, soonest first.
    A missing or damaged file is an empty calendar.
    """
    try:
        entries = json.loads(path().read_text(encoding="utf-8"))
        good = []
        for e in entries:
            try:
                _when(e)
                good.append(e)
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(good, key=_when)
    except FileNotFoundError:
        return []
    except Exception as exc:
        # Losing it costs a retype, not a session.
        logger.warning("Could not read the interview calendar: %s", exc)
        return []


def _save(entries: List[Dict[str, Any]]) -> None:
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(entries, indent=1), encoding="utf-8")


def add(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Store one interview and return it; ValueError says what is missing or wrong."""
    entry = {"id": uuid.uuid4().hex[:12],
             "date": str(fields.get("date") or "").strip(),
             "time": str(fields.get("time") or "").strip() or "09:00"}
    for name in _FIELDS:
        entry[name] = str(fields.get(name) or "").strip()[:_LIMITS[name]]
    if not entry["company"]:
        raise ValueError("Add the company name.")
    try:
        _when(entry)
    except ValueError:
        raise ValueError("Pick a real date and time.") from None
    _save(load() + [entry])
    return entry


def remove(entry_id: str) -> bool:
    entries = load()
    kept = [e for e in entries if e.get("id") != entry_id]
    if len(kept) == len(entries):
        return False
    _save(kept)
    return True


def next_upcoming(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The nearest interview that has not started yet, or None."""
    now = now or datetime.now()
    for entry in load():
        try:
            if _when(entry) > now:
                return entry
        except (KeyError, ValueError):
            continue
    return None
