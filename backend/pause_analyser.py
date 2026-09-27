# pause_analyser.py
"""Stage 5: pause detection from the word timings and the audio.

Whisper often stretches a word's end time over the silence after it, so gaps between word timestamps understate real pauses.
The timestamps are only used to find where a pause might be.
The pause length is then measured from the audio, growing each gap through the silent frames around it.

Growth stops at the middle of each neighbouring word, so a badly timed word can never create a pause over real speech.
This can make a pause read slightly short, which is the safer mistake.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

import filler_detector
from schema import PauseInstance, Pauses, WordTimestamp

logger = logging.getLogger(__name__)

# Shortest silence that counts as a pause.
# Same value as filler_detector.GAP_MIN_S, so both modules judge the same gaps.
PAUSE_MIN_S = filler_detector.GAP_MIN_S  # 0.30

# Pause length bands.
# Around one second a pause starts to sound like hesitation, and a few seconds feels uncomfortable in an interview.
NOTICEABLE_S = 1.0
DEAD_AIR_S = 2.5

KIND_NATURAL = "natural"
KIND_NOTICEABLE = "noticeable"
KIND_DEAD_AIR = "dead_air"

# Silence threshold, shared with filler_detector so a gap is never counted as both a filler and a pause.
SILENCE_RMS_THRESHOLD = filler_detector.VOICED_RMS_THRESHOLD  # 0.015

# 10 ms hop at the pipeline's 16 kHz.
# Fine enough to place a boundary within a syllable, coarse enough that a whole answer is a few thousand frames.
_FRAME_LENGTH = 512
_HOP_LENGTH = 160

_SR = 16000

_EMPTY = Pauses(
    total_count=0,
    per_minute=0.0,
    longest_seconds=0.0,
    mean_seconds=0.0,
    total_silent_seconds=0.0,
    silent_fraction_0to1=0.0,
    time_to_first_word_seconds=0.0,
    counts_by_kind={},
    instances=[],
)


def _silence_mask(audio: np.ndarray, sr: int) -> Tuple[np.ndarray, float]:
    """A silent or not flag for each audio frame, plus the seconds each frame covers."""
    import librosa

    rms = librosa.feature.rms(
        y=audio, frame_length=_FRAME_LENGTH, hop_length=_HOP_LENGTH
    )[0]
    seconds_per_frame = _HOP_LENGTH / float(sr)
    return rms < SILENCE_RMS_THRESHOLD, seconds_per_frame


def _grow_silence(
    silent: np.ndarray, spf: float, start_s: float, end_s: float,
    lower_bound_s: float, upper_bound_s: float,
) -> Optional[Tuple[float, float]]:
    """Grow a possible pause outwards through the silent frames around it.

    Returns the measured silent span, or None when the gap is not silent (a voiced gap belongs to filler detection).
    Growth stops at the given limits.
    """
    n = len(silent)
    if n == 0:
        return None

    def frame_of(t: float) -> int:
        return int(min(max(t / spf, 0), n - 1))

    i0, i1 = frame_of(start_s), frame_of(end_s)
    # Require the middle of the proposed gap to be silent before believing it.
    mid = (i0 + i1) // 2
    if not silent[mid]:
        return None

    lo_limit, hi_limit = frame_of(lower_bound_s), frame_of(upper_bound_s)
    lo = mid
    while lo > lo_limit and silent[lo - 1]:
        lo -= 1
    hi = mid
    while hi < hi_limit and silent[hi + 1]:
        hi += 1
    return lo * spf, (hi + 1) * spf


def _classify(duration: float) -> str:
    if duration >= DEAD_AIR_S:
        return KIND_DEAD_AIR
    if duration >= NOTICEABLE_S:
        return KIND_NOTICEABLE
    return KIND_NATURAL


def detect(
    word_timestamps: List[WordTimestamp],
    duration_seconds: float,
    audio: Optional[np.ndarray] = None,
    sr: int = _SR,
) -> Pauses:
    """Measure the pauses in one answer.

    Returns an empty result instead of raising when there is nothing to measure.
    """
    if audio is None or len(audio) == 0 or not word_timestamps:
        return dict(_EMPTY, counts_by_kind={}, instances=[])  # type: ignore[return-value]

    silent, spf = _silence_mask(audio, sr)

    instances: List[PauseInstance] = []
    seen_start: set = set()
    for i in range(len(word_timestamps) - 1):
        this_word = word_timestamps[i]
        next_word = word_timestamps[i + 1]
        gap_start = float(this_word["end"])
        gap_end = float(next_word["start"])

        # Bounds: never grow back past the middle of the previous word, nor forward past the middle of the next one.
        lower = (float(this_word["start"]) + gap_start) / 2.0
        upper = (gap_end + float(next_word["end"])) / 2.0

        # A negative or tiny nominal gap is still worth probing, because the alignment may have hidden the silence inside the preceding word.
        grown = _grow_silence(silent, spf, gap_start, gap_end, lower, upper)
        if grown is None:
            continue
        start, end = grown
        length = end - start
        if length < PAUSE_MIN_S:
            continue
        key = round(start, 2)
        if key in seen_start:
            # Two boundaries grew into the same silence.
            continue
        seen_start.add(key)
        instances.append(
            PauseInstance(
                start=round(start, 3), end=round(end, 3),
                duration=round(length, 3), kind=_classify(length),
            )
        )

    counts_by_kind: Dict[str, int] = {}
    for inst in instances:
        counts_by_kind[inst["kind"]] = counts_by_kind.get(inst["kind"], 0) + 1

    total = len(instances)
    silent_seconds = sum(inst["duration"] for inst in instances)
    per_minute = (total / (duration_seconds / 60.0)) if duration_seconds > 0 else 0.0

    return Pauses(
        total_count=total,
        per_minute=round(per_minute, 3),
        longest_seconds=round(max((i["duration"] for i in instances), default=0.0), 3),
        mean_seconds=round(silent_seconds / total, 3) if total else 0.0,
        total_silent_seconds=round(silent_seconds, 3),
        silent_fraction_0to1=(
            round(silent_seconds / duration_seconds, 4) if duration_seconds > 0 else 0.0
        ),
        time_to_first_word_seconds=round(float(word_timestamps[0]["start"]), 3),
        counts_by_kind=counts_by_kind,
        instances=instances,
    )
