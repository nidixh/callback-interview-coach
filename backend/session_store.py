# session_store.py
"""Where a session's recordings are saved.

Recordings are stored outside the project folder, in Documents by default, so they can never end up on GitHub.
Free space is checked before a session starts, because running out halfway would lose answers.
Each take is one wav and one mp4 in recording order, and the manifest records which question each take answered.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Overridable so a test, or a user with a larger drive elsewhere, can redirect the whole tree without editing code.
_ENV_VAR = "COACH_RECORDINGS_DIR"

DEFAULT_ROOT = Path.home() / "Documents" / "InterviewCoach" / "sessions"

# Minimum free space to start a session.
# A full session uses about 60 MB, so this leaves plenty of margin.
MIN_FREE_BYTES = 2 * 1024 ** 3

_SAFE = re.compile(r"[^a-z0-9]+")


def root() -> Path:
    """The directory holding every session."""
    override = os.environ.get(_ENV_VAR)
    return Path(override) if override else DEFAULT_ROOT


def _slug(text: str, limit: int = 40) -> str:
    return _SAFE.sub("-", (text or "").lower()).strip("-")[:limit]


def free_space_bytes(path: Path) -> int:
    """Free bytes on the volume holding `path`, walking up to one that exists."""
    probe = Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(str(probe)).free
    except Exception:
        # Unreadable volume is treated as unknown.
        return 0


def check_space() -> Optional[str]:
    """None when there is room to record, else a sentence explaining why not."""
    free = free_space_bytes(root())
    if free == 0:
        return None  # unknown, so do not block
    if free < MIN_FREE_BYTES:
        return (
            f"There is only {free / 1024 ** 3:.1f} GB free on this drive. A "
            "practice session records audio and video, so free up some space "
            "before starting."
        )
    return None


def new_session_dir(job_title: str = "", stamp: str = "") -> Path:
    """Create and return a new folder for one session.

    `stamp` can be passed in to make the name predictable; otherwise the current time is used.
    """
    if not stamp:
        from datetime import datetime

        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    name = f"{stamp}_{_slug(job_title) or 'practice'}"
    path = root() / name
    # A second session inside the same minute must not overwrite the first.
    suffix = 2
    while path.exists():
        path = root() / f"{name}-{suffix}"
        suffix += 1
    path.mkdir(parents=True, exist_ok=True)
    return path


def take_paths(session_dir, take: int) -> Tuple[Path, Path]:
    """The (audio, video) paths for one take, numbered from 1."""
    session_dir = Path(session_dir)
    return (session_dir / f"take_{take:02d}.wav",
            session_dir / f"take_{take:02d}.mp4")


def write_manifest(session_dir, manifest: dict) -> Path:
    """Record what each take was, so a folder is readable without the app."""
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def list_sessions() -> List[Path]:
    """Every session directory, newest first."""
    base = root()
    if not base.exists():
        return []
    dirs = [p for p in base.iterdir() if p.is_dir()]
    return sorted(dirs, key=lambda p: p.name, reverse=True)
