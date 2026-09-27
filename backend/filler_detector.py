# filler_detector.py
"""Stage 4: filler word detection in two layers.

Layer 1 finds filler words in the Whisper transcript, each with its exact time.

Layer 2 finds fillers Whisper left out.
It checks gaps between words and flags the ones with voice in them, labelled "[uh]" with source "gap".
A deliberate pause can also contain sound, so a gap is only reported when Layer 1 found a filler nearby, because fillers tend to come in clusters.

detect()'s gap_mode chooses Layer 1 only, both layers without that check, or both layers with it (the default).
"""

from __future__ import annotations

import re
import string
from typing import Dict, List, Optional

import numpy as np

from schema import Fillers, FillerInstance, WordTimestamp

# Layer 1: filler words and phrases.
_SINGLE_FILLERS = ["um", "uh", "er", "like", "basically", "literally", "right", "so"]
_MULTI_FILLERS = [("you", "know"), ("sort", "of"), ("kind", "of")]
_SINGLE_RE = re.compile(r"^(?:" + "|".join(_SINGLE_FILLERS) + r")$")

# Layer 2 settings.
# Shortest gap between words worth checking, in seconds.
# Shorter gaps are usually just breaths.
GAP_MIN_S = 0.30

# Loudness below which a slice counts as silent.
# Normal speech sits between 0.03 and 0.15.
VOICED_RMS_THRESHOLD = 0.015

# Zero crossing limit.
# Fillers are voiced sounds with a low rate, so gaps above this are skipped.
VOICED_ZCR_THRESHOLD = 0.15

# Layer 2 check: only report a voiced gap when Layer 1 found a filler within this many seconds of it.
# Fillers tend to cluster, while a deliberate pause usually sits in fluent speech.
GAP_CONTEXT_WINDOW_S = 5.0

# Layer 2 operating modes, exposed so the evaluation harness can ablate them.
GAP_MODE_OFF = "off"  # Layer 1 only
GAP_MODE_UNGATED = "ungated"  # every voiced gap is emitted
# Voiced gaps need Layer 1 corroboration nearby.
GAP_MODE_GATED = "gated"
_GAP_MODES = (GAP_MODE_OFF, GAP_MODE_UNGATED, GAP_MODE_GATED)

# Whisper's default sample rate (shared with the rest of the pipeline).
_SR = 16000


def _normalise(word: str) -> str:
    return word.strip().strip(string.punctuation).lower()


def _is_voiced_slice(audio: np.ndarray, sr: int, start_s: float, end_s: float) -> bool:
    """Return True if the audio slice [start_s, end_s) is voiced (non-silent)."""
    i0 = int(start_s * sr)
    i1 = int(end_s * sr)
    if i1 <= i0 or i1 > len(audio):
        return False
    chunk = audio[i0:i1]
    if chunk.size == 0:
        return False
    rms = float(np.sqrt(np.mean(chunk ** 2)))
    if rms < VOICED_RMS_THRESHOLD:
        return False
    zcr = float(np.mean(np.abs(np.diff(np.sign(chunk)))) / 2)
    return zcr <= VOICED_ZCR_THRESHOLD


def _asr_layer(norm: list) -> List[FillerInstance]:
    """Layer 1: match filler tokens in the normalised word-timestamp stream."""
    instances: List[FillerInstance] = []
    i = 0
    n = len(norm)
    while i < n:
        matched = False
        if i + 1 < n:
            pair = (norm[i][0], norm[i + 1][0])
            if pair in _MULTI_FILLERS:
                instances.append(
                    FillerInstance(
                        filler=" ".join(pair),
                        start=float(norm[i][1]["start"]),
                        end=float(norm[i + 1][1]["end"]),
                        source="asr",
                    )
                )
                i += 2
                matched = True
        if not matched:
            token, w = norm[i]
            if _SINGLE_RE.match(token):
                instances.append(
                    FillerInstance(
                        filler=token,
                        start=float(w["start"]),
                        end=float(w["end"]),
                        source="asr",
                    )
                )
            i += 1
    return instances


def _has_nearby_asr_filler(
    start: float, end: float, asr_instances: List[FillerInstance]
) -> bool:
    """True when a Layer 1 filler lies within the window around a gap.

    Measured edge to edge, so an overlapping filler counts as zero distance.
    """
    for inst in asr_instances:
        if inst["start"] <= end and start <= inst["end"]:
            return True  # overlapping
        distance = inst["start"] - end if inst["start"] > end else start - inst["end"]
        if distance <= GAP_CONTEXT_WINDOW_S:
            return True
    return False


def _gap_layer(
    word_timestamps: List[WordTimestamp],
    audio: np.ndarray,
    sr: int,
    asr_instances: List[FillerInstance],
    gated: bool = True,
) -> List[FillerInstance]:
    """Layer 2: find voiced gaps between words that Whisper did not transcribe.

    With `gated` True, a gap is only kept when Layer 1 found a filler nearby.
    """
    if audio is None or len(word_timestamps) < 2:
        return []

    # Skip gaps Layer 1 already covers: gaps that overlap a found filler, and short gaps that start right where one ends.
    asr_ends = [inst["end"] for inst in asr_instances]
    asr_windows = [(inst["start"], inst["end"]) for inst in asr_instances]

    def _already_covered(start: float, end: float) -> bool:
        # Rule 1: gap overlaps an ASR filler directly.
        for a, b in asr_windows:
            if start < b and end > a:
                return True
        # A short gap straight after a filler is just its tail.
        # A longer one is counted as a separate filler.
        gap_dur = end - start
        if gap_dur <= 2 * GAP_MIN_S:
            for asr_end in asr_ends:
                if 0 <= start - asr_end < GAP_MIN_S:
                    return True
        return False

    gaps: List[FillerInstance] = []
    for i in range(len(word_timestamps) - 1):
        gap_start = float(word_timestamps[i]["end"])
        gap_end = float(word_timestamps[i + 1]["start"])
        gap_dur = gap_end - gap_start
        if gap_dur < GAP_MIN_S:
            continue
        if _already_covered(gap_start, gap_end):
            continue
        if gated and not _has_nearby_asr_filler(gap_start, gap_end, asr_instances):
            continue
        if _is_voiced_slice(audio, sr, gap_start, gap_end):
            gaps.append(
                FillerInstance(
                    filler="[uh]",
                    start=gap_start,
                    end=gap_end,
                    source="gap",
                )
            )
    return gaps


def detect(
    word_timestamps: List[WordTimestamp],
    duration_seconds: float,
    audio: Optional[np.ndarray] = None,
    sr: int = _SR,
    gap_mode: str = GAP_MODE_GATED,
) -> Fillers:
    """Find fillers with both layers and merge them into one result.

    gap_mode: "off" uses Layer 1 only, "ungated" keeps every voiced gap, and "gated" (the default) needs a Layer 1 filler nearby.
    """
    if gap_mode not in _GAP_MODES:
        raise ValueError(
            f"gap_mode must be one of {_GAP_MODES}, got {gap_mode!r}"
        )
    norm = [(_normalise(w["word"]), w) for w in word_timestamps]

    asr_instances = _asr_layer(norm)
    if gap_mode == GAP_MODE_OFF:
        gap_instances: List[FillerInstance] = []
    else:
        gap_instances = _gap_layer(
            word_timestamps,
            audio,
            sr,
            asr_instances,
            gated=(gap_mode == GAP_MODE_GATED),
        )

    instances: List[FillerInstance] = sorted(
        asr_instances + gap_instances, key=lambda x: x["start"]
    )

    counts_by_filler: Dict[str, int] = {}
    for inst in instances:
        counts_by_filler[inst["filler"]] = counts_by_filler.get(inst["filler"], 0) + 1

    total = len(instances)
    per_minute = (total / (duration_seconds / 60.0)) if duration_seconds > 0 else 0.0

    return Fillers(
        total_count=total,
        per_minute=round(per_minute, 3),
        counts_by_filler=counts_by_filler,
        instances=instances,
    )
