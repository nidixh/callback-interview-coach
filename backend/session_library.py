# session_library.py
"""The sessions saved on disk, ready for the Sessions page.

A session folder is created before anything is recorded, so folders with no takes are not listed.
The takes on disk decide whether a session exists, and the manifest adds its details.
Sessions are ordered by the time in their folder name.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import session_history
import session_store

logger = logging.getLogger(__name__)

# "2026-09-04_2059_graduate-data-engineer" and its "-2" collision variant.
_NAME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})_(.*?)(?:-(\d+))?$")


def _parsed(name: str):
    """(iso minute, collision suffix) for a session directory name."""
    match = _NAME.match(name)
    if not match:
        return "", 0
    y, mo, d, h, mi, _slug, suffix = match.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}", int(suffix or 1)


def _manifest(path: Path) -> Dict[str, Any]:
    try:
        loaded = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        # A bad manifest is not a missing session.
        logger.debug("Ignoring unreadable manifest in %s: %s", path, exc)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _scores() -> Dict[str, Dict[str, Any]]:
    """Every score in the history file, keyed by session folder.

    Older sessions may have no row, which means no score rather than a bad one.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for row in session_history.load():
        where = str(row.get("session_dir") or "")
        if not where:
            continue
        try:
            out[Path(where).name] = row
        except Exception:
            # A malformed path is simply skipped.
            continue
    return out


def entries() -> List[Dict[str, Any]]:
    """Every session that recorded something, newest first."""
    root = session_store.root()
    if not root.exists():
        return []

    scored = _scores()
    found: List[Dict[str, Any]] = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        takes = sorted(path.glob("take_*.wav"))
        if not takes:
            # Prepared and abandoned: a directory, but not a session.
            continue
        manifest = _manifest(path)
        if manifest.get("dismissed"):
            # Not an attempt, so never scored: kept on disk, not offered back.
            continue
        when, suffix = _parsed(path.name)
        row = scored.get(path.name) or {}
        found.append({
            "name": path.name,
            "when": when,
            "job_title": str(manifest.get("job_title") or ""),
            "takes": len(takes),
            # result.json is the report now; report.pdf is what sessions scored before the in-app report wrote.
            # Either one is a report.
            "has_report": (path / "result.json").is_file() or (path / "report.pdf").is_file(),
            "has_result": (path / "result.json").is_file(),
            "overall": row.get("overall"),
            "kind": str(row.get("kind") or ""),
            "_order": (when, suffix),
        })

    found.sort(key=lambda e: e.pop("_order"), reverse=True)
    return found


def dismiss(session_dir: str, reason: str) -> None:
    """Mark a session as not an attempt, so entries() leaves it out.

    Saved in the manifest.
    A failed write is logged rather than raised.
    """
    if not session_dir:
        return
    path = Path(session_dir)
    if not path.is_dir():
        logger.warning("Cannot mark %s as dismissed: no such directory", path)
        return
    manifest = _manifest(path)
    manifest["dismissed"] = {"reason": str(reason)}
    try:
        (path / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not mark %s as dismissed: %s", path, exc)


def delete(name: str) -> bool:
    """Remove a session from the list and the progress chart, keeping its files.

    The session is marked in its manifest and its history row is moved there, so the change can be undone by hand.
    No file is deleted.
    """
    path = find(name)
    if path is None:
        return False
    row = session_history.forget(str(path))
    manifest = _manifest(path)
    manifest["dismissed"] = {"reason": "Deleted by you", "deleted": True, "history_row": row}
    try:
        (path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not mark %s as deleted: %s", path, exc)
        return False
    return True


def find(name: str) -> Optional[Path]:
    """The folder for a session, or None.

    Looked up in the listed sessions rather than built from the given name, so a request can never reach another folder.
    """
    if not name:
        return None
    for entry in entries():
        if entry["name"] == name:
            return session_store.root() / name
    return None


def result(name: str) -> Optional[Dict[str, Any]]:
    """The saved scored session, or None when the session was never scored."""
    path = find(name)
    if path is None:
        return None
    target = path / "result.json"
    if not target.is_file():
        return None
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        # A bad file is an absent report.
        logger.warning("Could not read %s: %s", target, exc)
        return None
    return loaded if isinstance(loaded, dict) else None


def report_pdf(name: str) -> Optional[Path]:
    """The written report for a session, when one was ever produced."""
    path = find(name)
    if path is None:
        return None
    target = path / "report.pdf"
    return target if target.is_file() else None
