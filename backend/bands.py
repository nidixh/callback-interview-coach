# bands.py
"""Score bands: where a 0 to 100 score becomes a word such as "strong".

BAND_* thresholds label a score for the candidate.
FLAG_* thresholds decide when two channels disagree enough to point out, so they sit further apart than the labels.
"""

from __future__ import annotations

# Descriptive bands, on the common 0 to 100 scale
BAND_STRONG = 75.0
BAND_SOLID = 55.0

# Cross channel flag thresholds.
# Wider apart than the bands, so a flag only fires on a clear gap.
FLAG_STRONG = 70.0
FLAG_WEAK = 45.0

# The parts of the delivery score. fusion.fuse() adds the delivery, content and facial totals to the same list, so code looking for the weakest part of delivery filters on this set.
DELIVERY_SUB_DIMENSIONS = frozenset(
    {"speaking_rate", "filler_rate", "pitch_variation"}
)


def band_label(score: float) -> str:
    """Describe a 0-100 score in the words used throughout the report."""
    if score >= BAND_STRONG:
        return "strong"
    if score >= BAND_SOLID:
        return "solid"
    return "needs work"

