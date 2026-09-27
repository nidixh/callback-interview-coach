# session_history.py
"""The candidate's past sessions, kept for the Progress page.

Stored as one small file beside the recordings, not inside a session folder.
Only the summary numbers from session_metrics are kept.
Every function catches and logs its own errors, so a disk problem never fails a finished interview.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import session_metrics
import session_store

logger = logging.getLogger(__name__)

HISTORY_NAME = "history.json"

# Roughly two years of daily practice.
# The file is read whole on every request, and a candidate reading a trend is not helped by the four-hundredth point.
MAX_ENTRIES = 200

# Lock around read and write, because the web server answers requests on several threads.
_LOCK = threading.Lock()

# Which direction is better for each measure.
# Speaking rate has none, because too fast and too slow are both worse.
_HIGHER_IS_BETTER: Dict[str, Optional[bool]] = {
    "overall": True,
    "fillers_per_minute": False,
    "speaking_rate_syll_per_sec": None,
    "eye_contact": True,
}

_LABELS = {
    "overall": "Overall",
    "fillers_per_minute": "Filler words a minute",
    "speaking_rate_syll_per_sec": "Speaking pace",
    "relevance_1to5": "Relevance",
    "structure_1to5": "Structure",
    "depth_1to5": "Depth",
    "clarity_1to5": "Clarity",
    "eye_contact": "Eye contact",
}

# Measures that older sessions do not have: eye contact arrived with the Face Landmarker, and a session with the camera off has none either.
_MEASURED_SINCE = ("eye_contact",)

# Sessions that measure the whole performance.
# A retake and a drill are single prompts and belong in the record, but not in a line that reads as ability.
FULL_KINDS = ("interview",)


def path() -> Path:
    """The history file, beside the session directories."""
    return session_store.root() / HISTORY_NAME


def _read() -> List[Dict[str, Any]]:
    """The saved history, or an empty list if the file is missing or unreadable."""
    target = path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except Exception as exc:  # unreadable history is not fatal
        logger.warning("Could not read %s: %s", target, exc)
        return []

    try:
        loaded = json.loads(raw)
    except Exception as exc:
        # A half-written file is not fatal.
        logger.warning("Ignoring unreadable history at %s: %s", target, exc)
        return []

    if not isinstance(loaded, list):
        logger.warning("Ignoring history at %s: expected a list", target)
        return []
    return [row for row in loaded if isinstance(row, dict)]


def _write(rows: List[Dict[str, Any]]) -> None:
    """Replace the history file in one step.

    Written to a temporary file first and then moved into place, so a reader never sees half a file.
    """
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    os.replace(str(temp), str(target))


def load() -> List[Dict[str, Any]]:
    """Every stored session, oldest first."""
    with _LOCK:
        return _read()


def forget(session_dir: str) -> Optional[Dict[str, Any]]:
    """Take one session off the trend; return its row so it can be put back."""
    with _LOCK:
        rows = _read()
        kept = [r for r in rows if r.get("session_dir") != str(session_dir)]
        if len(kept) == len(rows):
            return None
        _write(kept)
        return next(r for r in rows if r.get("session_dir") == str(session_dir))


def record(session, kind: str = "interview", session_dir: str = "",
           stamp: str = "") -> Optional[Dict[str, Any]]:
    """Add one finished session to the history and return its row.

    Returns None and writes nothing when the session cannot be scored or saved.
    `stamp` can be passed in to make the result predictable.
    """
    try:
        entry = session_metrics.metrics(session)
        if entry["overall"] is None:
            # Nothing was scored, so there is no point to plot.
            # Recording it would put a gap in the chart that looks like a bad session.
            return None

        if not stamp:
            from datetime import datetime

            stamp = datetime.now().replace(microsecond=0).isoformat()

        entry["stamp"] = stamp
        entry["kind"] = kind or "interview"
        entry["session_dir"] = str(session_dir or "")

        with _LOCK:
            rows = _read()
            # One row per session.
            # The same session can finish twice (two tabs, or a reload), so an existing row for this folder is replaced.
            where = entry["session_dir"]
            if where:
                rows = [r for r in rows if r.get("session_dir") != where]
            rows.append(entry)
            _write(rows[-MAX_ENTRIES:])
        return entry
    except Exception as exc:
        # History must never fail a session.
        logger.warning("Could not record session history: %s", exc)
        return None


def _value(row: Dict[str, Any], metric: str) -> Optional[float]:
    if metric == "overall":
        value = row.get("overall")
    elif metric in ("fillers_per_minute", "speaking_rate_syll_per_sec"):
        value = (row.get("delivery") or {}).get(metric)
    elif metric == "eye_contact":
        value = (row.get("presence") or {}).get("eye_contact")
    else:
        value = (row.get("rubric") or {}).get(metric)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def deltas(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What changed between the first full interview and the latest one.

    Drills, retakes and interviews stopped early are left out.
    Every measure is None until there are two interviews to compare.
    """
    # A session the interviewer cut short is a record of what happened, not a measure of ability, so it is listed but never compared.
    full = [r for r in (rows or [])
            if r.get("kind", "interview") in FULL_KINDS and not r.get("ended_early")]
    change: Dict[str, Any] = {"sessions": len(full)}

    metrics = ["overall", "fillers_per_minute", "speaking_rate_syll_per_sec", "eye_contact"]
    metrics += list(session_metrics.RUBRIC_KEYS)

    if len(full) < 2:
        for metric in metrics:
            change[metric] = None
        return change

    first, latest = full[0], full[-1]
    for metric in metrics:
        before, after = _value(first, metric), _value(latest, metric)
        if metric in _MEASURED_SINCE:
            # Measures that only newer sessions have (or only with the camera on) compare the first and latest sessions that have them.
            have = [r for r in full if _value(r, metric) is not None]
            before, after = ((_value(have[0], metric), _value(have[-1], metric))
                             if len(have) >= 2 else (None, None))
        if before is None or after is None:
            change[metric] = None
            continue
        higher_is_better = _HIGHER_IS_BETTER.get(metric, True)
        better: Optional[bool]
        if higher_is_better is None or after == before:
            better = None
        else:
            better = (after > before) if higher_is_better else (after < before)
        change[metric] = {
            "label": _LABELS.get(metric, metric),
            "from": round(before, 2),
            "to": round(after, 2),
            "change": round(after - before, 2),
            "better": better,
        }
    return change
