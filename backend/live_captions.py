# live_captions.py
"""Live captions while the candidate is speaking.

Usage: Captioner(listen, transcribe, publish), then start() when the answer starts and stop() before it ends.

Every INTERVAL_S seconds the last WINDOW_S seconds of audio go through the same small Whisper model as the quick transcript, and the words are sent to the page.
Windows overlap, so each new pass is joined onto the text already shown.

The captions are a rough draft only.
They are replaced when the answer ends and nothing is scored from them.
A pass is skipped when nothing new was said, a late pass is thrown away, and after MAX_FAILURES failures in a row captions stop for that answer.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# How much audio each pass hears, and how often a pass runs.
WINDOW_S = 20.0
INTERVAL_S = 2.5

# The first pass runs sooner, so loading the model overlaps with the opening words.
FIRST_PASS_S = 1.2

# Less than this and there is not a word to hear yet.
MIN_AUDIO_S = 1.0

# The recorder's own threshold for "nothing was said".
SILENCE_PEAK = 0.01

# Words two windows must share before they are joined there.
# Fewer and a common "and I" could splice the text at the wrong place.
MIN_OVERLAP_WORDS = 3

# Consecutive failed passes before captions give up on this answer.
MAX_FAILURES = 3

# A worker hung on a caption must not hold the interviewer's transcript for the five minutes an answer is allowed.
CAPTION_TIMEOUT_S = 20.0

# Shown between two passes that share no words, rather than guessing a join.
BREAK = " ... "


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


def stitch(settled: str, fresh: str) -> str:
    """Join a new pass onto the text already shown, where the two agree."""
    fresh = (fresh or "").strip()
    settled = (settled or "").strip()
    if not settled:
        return fresh
    if not fresh:
        return settled

    old, new = settled.split(), fresh.split()
    # Only the end of the settled text can overlap the new window.
    start = max(0, len(old) - len(new) - 5)
    a = [_norm(w) for w in old[start:]]
    b = [_norm(w) for w in new]
    match = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b))
    # A settled text shorter than the usual overlap (a first pass that caught one word) needs only to be matched whole.
    if match.size and match.size >= min(MIN_OVERLAP_WORDS, len(a), len(b)):
        return " ".join(old[:start + match.a] + new[match.b:])
    return settled + BREAK + fresh


class Captioner:
    """Runs caption passes on its own thread for the length of one answer."""

    def __init__(
        self,
        listen: Callable[[float], Optional[np.ndarray]],
        transcribe: Callable[[np.ndarray, int], str],
        publish: Callable[[str], None],
        samplerate: int = 16000,
        interval: float = INTERVAL_S,
        window: float = WINDOW_S,
    ):
        self._listen = listen
        self._transcribe = transcribe
        self._publish = publish
        self.samplerate = int(samplerate)
        self.interval = float(interval)
        self.window = float(window)

        self.text = ""
        self.passes = 0
        self.gave_up = False
        self._failures = 0
        self._stopping = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._stopping.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="live-captions")
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Stop, and wait for a pass in flight so it cannot outlive the answer."""
        self._stopping.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("A caption pass was still running after %.0fs", timeout)

    def _loop(self) -> None:
        wait = min(FIRST_PASS_S, self.interval)
        while not self._stopping.wait(wait):
            if self.gave_up:
                return
            self.run_once()
            wait = self.interval

    def run_once(self) -> bool:
        """One pass.
        True when new text was published.
        """
        if self.gave_up or self._stopping.is_set():
            return False
        try:
            audio = self._listen(self.window)
        except Exception as exc:  # no caption, same answer
            logger.debug("Could not read the answer for captions: %s", exc)
            return False
        if audio is None:
            return False
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size < MIN_AUDIO_S * self.samplerate:
            return False
        newest = audio[-max(int(self.interval * self.samplerate), 1):]
        if float(np.abs(newest).max()) < SILENCE_PEAK:
            return False

        try:
            heard = self._transcribe(audio, self.samplerate) or ""
        except Exception as exc:
            # A caption is never worth an answer.
            self._failures += 1
            logger.debug("A caption pass failed (%d): %s", self._failures, exc)
            if self._failures >= MAX_FAILURES:
                self.gave_up = True
                logger.warning("Live captions stopped for this answer: %s", exc)
            return False
        self._failures = 0
        self.passes += 1

        if self._stopping.is_set():
            return False  # answer ended mid pass
        # While the answer is shorter than the window, this pass heard all of it, so it replaces what was shown rather than being joined on.
        whole = audio.size < int(self.window * self.samplerate)
        text = heard.strip() or self.text if whole else stitch(self.text, heard)
        if text == self.text:
            return False
        self.text = text
        self._publish(text)
        return True


def _scratch_path() -> Path:
    return Path(tempfile.gettempdir()) / f"callback_caption_{os.getpid()}.wav"


def transcribe_samples(samples: np.ndarray, samplerate: int) -> str:
    """The quick transcript of some audio, through the interviewer's worker.

    The audio is written to one scratch file that each pass overwrites.
    A failed worker returns "".
    """
    import soundfile as sf

    import gist_transcriber

    path = _scratch_path()
    sf.write(str(path), samples, samplerate)
    return gist_transcriber.transcribe(path, timeout=CAPTION_TIMEOUT_S)
