# whisper_transcriber.py
"""Stage 1: speech to text with a time for every word (OpenAI Whisper).

Every later stage depends on this transcript.
The model is loaded once and reused.

WHISPER_MODEL_SIZE sets the model.
"medium" fits a 4 GB card.
The card is used when it has enough free memory, with a fallback to the CPU, including when it runs out of memory while loading.

On Windows, word timing uses a slower CPU method because Triton is not available.
The timings are the same.
"""

from __future__ import annotations

import gc
import logging
from typing import List, Optional

import numpy as np
import torch
import whisper

from schema import Transcription, TranscriptionSegment, WordTimestamp

logger = logging.getLogger(__name__)

# Single configurable model size.
# Change here only.
WHISPER_MODEL_SIZE = "medium"

# Whisper operates natively at 16 kHz mono.
WHISPER_SAMPLE_RATE = 16000

# Whisper tends to leave out "um" and "uh".
# A prompt full of fillers and turning off conditioning on earlier text help keep them in.
# Filler counts are still a lower bound.
_FILLER_PROMPT = "Um, uh, er, hmm, like, you know, so, well, I mean, okay, um, uh."
_DECODE_OPTS = dict(
    language="en",
    word_timestamps=True,
    verbose=False,
    condition_on_previous_text=False,
    initial_prompt=_FILLER_PROMPT,
)

# Skipped speech check.
# On unclear audio the filler prompt can make Whisper drop the middle of an answer, which looks like a short answer rather than an error.
# So any stretch of audio loud enough to be speech, lying outside every returned segment, counts as skipped.
# The loudness threshold matches filler and pause detection.
SKIP_GAP_MIN_S = 2.0
SKIP_SPEECH_RMS = 0.015  # shared with filler_detector.VOICED_RMS_THRESHOLD
# Skipped speech above this share means a collapse.
SKIP_FRACTION_MAX = 0.15
# Bounded: each retry is a full decode of the answer.
SKIP_MAX_RETRIES = 2
# 0.1 s frames at 16 kHz; fine enough to locate a gap.
_SKIP_HOP = 1600

# Free graphics memory (bytes) needed before loading medium on the card; otherwise the CPU is used.
_MIN_FREE_VRAM_BYTES = int(2.8 * 1024 ** 3)

# Module-level cache keyed by device so a CPU fallback does not evict the GPU model.
_MODELS: dict = {}


def _select_device() -> str:
    """Pick the device for transcription.

    Once a model is on the card it stays there.
    The free memory check only applies to the first load, because with the model loaded less memory will always show as free.
    """
    if "cuda" in _MODELS:
        return "cuda"
    if not torch.cuda.is_available():
        return "cpu"
    try:
        free, _total = torch.cuda.mem_get_info()
    except Exception:  # driver quirk, treat as CPU
        return "cpu"
    return "cuda" if free >= _MIN_FREE_VRAM_BYTES else "cpu"


def _is_out_of_memory(exc: BaseException) -> bool:
    """True when `exc` means the graphics card ran out of memory.

    This shows up either as torch.cuda.OutOfMemoryError or as a RuntimeError saying "CUDA error: out of memory", so both are checked.
    Other errors are not caught.
    """
    if isinstance(exc, torch.cuda.OutOfMemoryError):
        return True
    return isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower()


def _to_half(model):
    """Store the weights at 16 bit, except LayerNorm, which Whisper runs at 32 bit.

    Converting once instead of on every call keeps the model inside the 4 GB card and more than halves transcription time.
    """
    model.half()
    for module in model.modules():
        if isinstance(module, torch.nn.LayerNorm):
            module.float()
    return model


def _get_model(device: str):
    if device not in _MODELS:
        logger.info("Loading Whisper '%s' on %s", WHISPER_MODEL_SIZE, device)
        model = whisper.load_model(WHISPER_MODEL_SIZE, device=device)
        # Only on the GPU: the CPU path decodes in 32-bit (fp16=False).
        _MODELS[device] = _to_half(model) if device == "cuda" else model
    return _MODELS[device]


def release() -> int:
    """Free the Whisper model and return the graphics memory reclaimed.

    Called after all answers are transcribed, so the scoring model gets the card.
    Reloading costs about 20 s, so call it between phases, not between answers.
    """
    if not _MODELS:
        return 0
    had_cuda = "cuda" in _MODELS
    _MODELS.clear()
    gc.collect()
    reclaimed = 0
    if had_cuda and torch.cuda.is_available():
        try:
            before, _total = torch.cuda.mem_get_info()
            torch.cuda.empty_cache()
            after, _total = torch.cuda.mem_get_info()
            reclaimed = max(0, after - before)
        except Exception:  # driver quirk
            logger.warning("Could not query VRAM while releasing the model.")
    logger.info("Released Whisper weights; reclaimed %.2f GB", reclaimed / 1024 ** 3)
    return reclaimed


def _flatten_words(segments: list) -> List[WordTimestamp]:
    """Flatten Whisper's per-segment word lists into one ordered list."""
    words: List[WordTimestamp] = []
    for seg in segments:
        for w in seg.get("words", []):
            words.append(
                WordTimestamp(
                    word=w["word"].strip(),
                    start=float(w["start"]),
                    end=float(w["end"]),
                    probability=float(w.get("probability", 0.0)),
                )
            )
    return words


def skipped_speech_seconds(audio: np.ndarray, sr: int, segments) -> float:
    """Seconds of speech level audio outside every transcribed segment.

    Only gaps longer than SKIP_GAP_MIN_S count.
    The start and end of the audio are included.
    """
    if audio is None or len(audio) == 0:
        return 0.0

    duration = len(audio) / float(sr)
    spans = sorted(
        (float(s["start"]), float(s["end"])) for s in segments
    ) if segments else []

    gaps = []
    cursor = 0.0
    for start, end in spans:
        if start - cursor >= SKIP_GAP_MIN_S:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor >= SKIP_GAP_MIN_S:
        gaps.append((cursor, duration))
    if not gaps:
        return 0.0

    # One RMS pass over the whole waveform, then measure only inside the gaps.
    frames = len(audio) // _SKIP_HOP
    if frames <= 0:
        return 0.0
    trimmed = audio[: frames * _SKIP_HOP].reshape(frames, _SKIP_HOP)
    rms = np.sqrt(np.mean(trimmed.astype(np.float64) ** 2, axis=1))
    seconds_per_frame = _SKIP_HOP / float(sr)

    skipped = 0.0
    for start, end in gaps:
        lo = int(start / seconds_per_frame)
        hi = min(int(end / seconds_per_frame), frames)
        if hi > lo:
            skipped += float(np.count_nonzero(rms[lo:hi] >= SKIP_SPEECH_RMS))
    return skipped * seconds_per_frame


def transcribe(audio: np.ndarray, sr: int = WHISPER_SAMPLE_RATE,
               decode_options: Optional[dict] = None) -> dict:
    """Transcribe 16 kHz mono float32 audio.

    Returns the text, language, segments, word timings, the device used and any warnings.
    `decode_options` replaces the default settings for one call, for testing.
    """
    warnings: List[str] = []
    device = _select_device()
    if device == "cpu" and torch.cuda.is_available():
        warnings.append("CUDA present but free VRAM below threshold; using CPU.")

    audio = np.asarray(audio, dtype=np.float32)
    opts = _DECODE_OPTS if decode_options is None else decode_options

    try:
        model = _get_model(device)
        result = model.transcribe(audio, fp16=(device == "cuda"), **opts)
    except Exception as exc:  # graceful hard fallback to CPU
        if not _is_out_of_memory(exc):
            raise
        # Drop the cached card model before retrying, because after an out of memory error the card cannot be trusted.
        logger.warning("CUDA out of memory during transcription; falling back to CPU.")
        warnings.append("CUDA out of memory; transcription fell back to CPU.")
        _MODELS.pop("cuda", None)
        try:
            torch.cuda.empty_cache()
        except Exception:  # context may already be broken
            pass
        device = "cpu"
        model = _get_model(device)
        result = model.transcribe(audio, fp16=False, **opts)

    # Recover from skipped speech.
    # The problem comes and goes, so decoding again usually works.
    # Retries drop the filler prompt, and the attempt that covers the most audio is kept.
    duration = len(audio) / float(sr) if len(audio) else 0.0
    if duration:
        skipped = skipped_speech_seconds(audio, sr, result.get("segments", []))
        if skipped / duration > SKIP_FRACTION_MAX:
            retry_opts = {k: v for k, v in opts.items() if k != "initial_prompt"}
            best, best_skipped, attempts = result, skipped, 0
            while (best_skipped / duration > SKIP_FRACTION_MAX
                   and attempts < SKIP_MAX_RETRIES):
                attempts += 1
                logger.warning(
                    "Decoder skipped %.1fs of %.1fs; retry %d of %d.",
                    best_skipped, duration, attempts, SKIP_MAX_RETRIES,
                )
                candidate = model.transcribe(
                    audio, fp16=(device == "cuda"), **retry_opts)
                candidate_skipped = skipped_speech_seconds(
                    audio, sr, candidate.get("segments", []))
                if candidate_skipped < best_skipped:
                    best, best_skipped = candidate, candidate_skipped

            if best is not result:
                result = best
                warnings.append(
                    "The transcriber dropped part of this answer and was rerun; "
                    "filler-word counts for it are likely to be lower than they "
                    "should be."
                )
            elif best_skipped / duration > SKIP_FRACTION_MAX:
                # Every attempt lost a large share of the audio.
                # Say so, rather than let a truncated transcript be scored as a short answer.
                warnings.append(
                    f"About {best_skipped / duration:.0%} of this answer could "
                    "not be transcribed, so its scores are unreliable."
                )

    segments: List[TranscriptionSegment] = [
        TranscriptionSegment(
            id=int(s["id"]),
            start=float(s["start"]),
            end=float(s["end"]),
            text=s["text"].strip(),
        )
        for s in result.get("segments", [])
    ]

    transcription: Transcription = Transcription(
        text=result.get("text", "").strip(),
        language=result.get("language", "en"),
        segments=segments,
        word_timestamps=_flatten_words(result.get("segments", [])),
    )

    return {
        "transcription": transcription,
        "device": device,
        "warnings": warnings,
    }
