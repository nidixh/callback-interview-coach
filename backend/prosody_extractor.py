# prosody_extractor.py
"""Stage 2: voice features with librosa (McFee et al., 2015).

Produces eight numbers describing pace, pitch and energy.
Pitch uses pYIN (Mauch and Dixon, 2014), which follows one pitch line and makes fewer octave errors than plain YIN.
Silent audio gives no pitch at all, so those values become 0.0 with a warning instead of NaN.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

import numpy as np
import librosa

from schema import Prosody

logger = logging.getLogger(__name__)

# pYIN search range: 80 Hz to 400 Hz spans typical adult speaking pitch (low male to high female) while excluding sub-harmonic and noise artefacts.
_FMIN_HZ = 80.0
_FMAX_HZ = 400.0


def extract(audio: np.ndarray, sr: int) -> Tuple[Prosody, List[str]]:
    """Compute the eight prosodic features from a mono waveform."""
    warnings: List[str] = []
    audio = np.asarray(audio, dtype=np.float32)
    duration = float(len(audio) / sr) if sr else 0.0

    # F0 via pYIN (Mauch and Dixon, 2014)
    f0, voiced_flag, _voiced_prob = librosa.pyin(
        audio, fmin=_FMIN_HZ, fmax=_FMAX_HZ, sr=sr
    )
    voiced = f0[~np.isnan(f0)] if f0 is not None else np.array([])
    if voiced.size == 0:
        warnings.append("No voiced frames detected (silent/unvoiced); pitch set to 0.0.")
        pitch_mean = 0.0
        pitch_std = 0.0
    else:
        pitch_mean = float(np.mean(voiced))
        pitch_std = float(np.std(voiced))

    voiced_fraction = (
        float(np.mean(voiced_flag)) if voiced_flag is not None and voiced_flag.size else 0.0
    )

    # Speaking rate from onset detection.
    # Onsets roughly match syllables, so this is a rough syllables per second figure, best used to compare answers.
    if duration > 0:
        onsets = librosa.onset.onset_detect(y=audio, sr=sr, units="time")
        speaking_rate = float(len(onsets) / duration)
    else:
        speaking_rate = 0.0

    # Energy (RMS)
    rms = librosa.feature.rms(y=audio)[0]
    energy_mean = float(np.mean(rms)) if rms.size else 0.0
    energy_std = float(np.std(rms)) if rms.size else 0.0

    # Zero-crossing rate (a rough voicing/fricative indicator)
    zcr = librosa.feature.zero_crossing_rate(y=audio)[0]
    zcr_mean = float(np.mean(zcr)) if zcr.size else 0.0

    prosody: Prosody = Prosody(
        speaking_rate_syll_per_sec=speaking_rate,
        pitch_mean_hz=pitch_mean,
        pitch_std_hz=pitch_std,
        voiced_fraction=voiced_fraction,
        energy_rms_mean=energy_mean,
        energy_rms_std=energy_std,
        zero_crossing_rate_mean=zcr_mean,
        duration_seconds=duration,
    )
    return prosody, warnings
