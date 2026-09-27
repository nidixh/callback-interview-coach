# candidate_cv.py
"""The candidate's CV, stored on this computer.

The questions are planned from the job advert and the CV, so the interviewer can ask about claims the CV makes.

The CV holds personal details, so it is stored beside the recordings and never inside the project folder.
Clearing it deletes the file.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import session_store

logger = logging.getLogger(__name__)

CV_NAME = "cv.txt"

# Upper limit on CV length, so a huge paste cannot overflow the model's context.
MAX_CHARS = 20_000


def path() -> Path:
    """The stored CV, beside the session directories."""
    return session_store.root() / CV_NAME


def load() -> str:
    """The stored CV, or an empty string when there is none.

    A missing or unreadable file both count as no CV.
    """
    try:
        return path().read_text(encoding="utf-8")[:MAX_CHARS]
    except FileNotFoundError:
        return ""
    except Exception as exc:
        # An unreadable CV is not fatal.
        logger.warning("Could not read the stored CV: %s", exc)
        return ""


def save(text: str) -> bool:
    """Store the CV, or delete it when the text is empty.

    Returns whether it worked.
    Written to a temporary file first and then moved into place, so a reader never sees half a file.
    """
    trimmed = (text or "").strip()[:MAX_CHARS]
    target = path()

    if not trimmed:
        return clear()

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(trimmed, encoding="utf-8")
        os.replace(str(temp), str(target))
        return True
    except Exception as exc:  # never worth failing a session
        logger.warning("Could not store the CV: %s", exc)
        return False


def clear() -> bool:
    """Remove the stored CV.
    Returns True when nothing is left on disk.
    """
    try:
        path().unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:  # report rather than raise
        logger.warning("Could not remove the stored CV: %s", exc)
        return False
    return True
