# examples.py
"""The example jobs behind "Fill with an example" on the setup screen.

Usage: Deck().deal("medium") returns one job with its advert, a CV and a question count.

There are three levels with twelve jobs each.
Every job has two CVs, one that fits the advert well and one that only partly fits.
They are stored as plain text in artifacts/examples, one block per job, with the advert and the two CVs separated by "---" lines.

Jobs are dealt from a shuffled pile per level, so nothing repeats until the whole level has been seen.
The CV is picked at random.
Every advert was measured with difficulty.measure to check it sits inside its level.
"""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Dict, List, Optional

import difficulty

FOLDER = Path(__file__).parent.parent / "artifacts" / "examples"
LEVELS = ("easy", "medium", "hard")

# Questions the interview asks at each level, matching the 3 / 5 / 8 choice on the setup screen.
# A café job does not need eight; a technical one does.
QUESTIONS = {"easy": 3, "medium": 5, "hard": 8}

_JOB = re.compile(r"^===\s*(\S+)\s*$", re.M)
_SECTION = re.compile(r"^---\s*(advert|cv\s+(?:strong|partial))\s*$", re.M)


def load_level(path) -> List[Dict]:
    """The jobs in one level file, in file order."""
    text = Path(path).read_text(encoding="utf-8")
    text = "\n".join(l for l in text.splitlines() if not l.startswith("#"))
    heads = list(_JOB.finditer(text))
    jobs: List[Dict] = []
    for i, head in enumerate(heads):
        body = text[head.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        title = re.search(r"^title:\s*(.+)$", body, re.M)
        job = {"id": head.group(1), "title": title.group(1).strip() if title else head.group(1),
               "advert": "", "cvs": {}}
        parts = list(_SECTION.finditer(body))
        for k, part in enumerate(parts):
            content = body[part.end(): parts[k + 1].start() if k + 1 < len(parts) else len(body)].strip()
            name = part.group(1)
            if name == "advert":
                job["advert"] = content
            else:
                job["cvs"][name.split()[1]] = content
        if not job["advert"]:
            raise ValueError(f"The example {job['id']!r} in {Path(path).name} has no advert.")
        jobs.append(job)
    return jobs


class Deck:
    """Deals examples level by level, without repeats until a level runs out."""

    def __init__(self, folder=None, rng: Optional[random.Random] = None):
        self.folder = Path(folder) if folder else FOLDER
        self.rng = rng or random.Random()
        self._jobs: Dict[str, List[Dict]] = {}
        self._piles: Dict[str, List[Dict]] = {}
        self._last: Dict[str, str] = {}

    def _level(self, level: str) -> List[Dict]:
        if level not in self._jobs:
            path = self.folder / f"{level}.txt"
            self._jobs[level] = load_level(path) if path.exists() else []
        return self._jobs[level]

    def deal(self, level: str) -> Optional[Dict]:
        """The next example at this level, or None if the level has none."""
        if level not in LEVELS:
            raise ValueError(f"Unknown level {level!r}; expected one of {', '.join(LEVELS)}.")
        jobs = self._level(level)
        if not jobs:
            return None
        pile = self._piles.get(level) or []
        if not pile:
            pile = list(jobs)
            self.rng.shuffle(pile)
            # A new round never opens with the job that closed the last one.
            if len(pile) > 1 and pile[-1]["id"] == self._last.get(level):
                pile[0], pile[-1] = pile[-1], pile[0]
        job = pile.pop()
        self._piles[level] = pile
        self._last[level] = job["id"]

        fit = self.rng.choice(sorted(job["cvs"])) if job["cvs"] else ""
        return {
            "level": level,
            "id": job["id"],
            "job_title": job["title"],
            "job_description": job["advert"],
            "cv": job["cvs"].get(fit, ""),
            "cv_fit": fit,
            "question_count": QUESTIONS[level],
            "difficulty": difficulty.measure(job["advert"])["score"],
        }
