# speaker_embedder.py
"""Stage 3: a voice fingerprint with SpeechBrain ECAPA-TDNN (Ravanelli et al., 2021).

Produces a 192 number embedding of the speaker's voice, independent of the words.
It runs on the CPU so the graphics card stays free for Whisper.
The model is loaded once and reused.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple

import numpy as np
import torch

from schema import SpeakerEmbedding

logger = logging.getLogger(__name__)

_MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
# Downloaded on first use into pretrained_models/ at the top of the project.
_SAVE_DIR = Path(__file__).parent.parent / "pretrained_models" / "spkrec-ecapa-voxceleb"
_EMBEDDING_DIM = 192

_CLASSIFIER = None


def _get_classifier():
    global _CLASSIFIER
    if _CLASSIFIER is None:
        # Imported lazily so importing this module does not pull in SpeechBrain until an embedding is actually requested.
        from speechbrain.inference import EncoderClassifier

        logger.info("Loading ECAPA-TDNN (%s) on CPU", _MODEL_SOURCE)
        _CLASSIFIER = EncoderClassifier.from_hparams(
            source=_MODEL_SOURCE,
            savedir=str(_SAVE_DIR),
            run_opts={"device": "cpu"},
        )
    return _CLASSIFIER


def embed(audio: np.ndarray, sr: int) -> Tuple[SpeakerEmbedding, List[str]]:
    """Return a 192-d speaker embedding for a mono 16 kHz waveform."""
    warnings: List[str] = []
    audio = np.asarray(audio, dtype=np.float32)

    classifier = _get_classifier()
    signal = torch.from_numpy(audio).unsqueeze(0)  # (1, time)
    with torch.no_grad():
        emb = classifier.encode_batch(signal)  # (1, 1, 192)
    vector = emb.squeeze().cpu().numpy().astype(float)

    if vector.shape[0] != _EMBEDDING_DIM:
        warnings.append(
            f"Unexpected embedding dimension {vector.shape[0]} (expected {_EMBEDDING_DIM})."
        )

    embedding: SpeakerEmbedding = SpeakerEmbedding(
        model=_MODEL_SOURCE,
        dimension=int(vector.shape[0]),
        vector=vector.tolist(),
    )
    return embedding, warnings
