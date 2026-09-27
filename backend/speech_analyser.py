# speech_analyser.py
"""Runs the speech analysis for one recording.

analyse(audio_path) loads the audio once, then runs transcription, voice features, the voice fingerprint, fillers and pauses in that order.
A failing stage falls back to a safe default with a warning.
Only audio that cannot be loaded at all raises AudioLoadError.

From the command line: python speech_analyser.py clip.wav
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import List

import numpy as np
import soundfile as sf
import librosa

import whisper_transcriber
import prosody_extractor
import speaker_embedder
import filler_detector
import pause_analyser
import stage_guard
from schema import (
    AudioLoadError,
    Fillers,
    Pauses,
    Processing,
    Prosody,
    SpeakerEmbedding,
    SpeechAnalysisResult,
    Transcription,
)

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000

_EMPTY_TRANSCRIPTION = Transcription(
    text="", language="en", segments=[], word_timestamps=[]
)


def _load_audio(audio_path: Path) -> tuple[np.ndarray, int]:
    """Load an audio file as 16 kHz mono float32.

    Every loading problem (missing, empty, unreadable) raises AudioLoadError, so callers handle one exception.
    Uses soundfile and librosa, so ffmpeg is not needed.
    """
    if not audio_path.exists():
        raise AudioLoadError(f"Audio file not found: {audio_path}")
    try:
        data, sr = sf.read(str(audio_path), dtype="float32")
    except Exception as exc:  # corrupt / unsupported container
        raise AudioLoadError(f"Could not decode audio file {audio_path}: {exc}") from exc

    if data.size == 0:
        raise AudioLoadError(f"Audio file is empty: {audio_path}")

    if data.ndim > 1:  # stereo -> mono
        data = data.mean(axis=1)
    if sr != TARGET_SAMPLE_RATE:
        data = librosa.resample(data, orig_sr=sr, target_sr=TARGET_SAMPLE_RATE)
        sr = TARGET_SAMPLE_RATE
    return data.astype(np.float32), sr


# The shared stage guard, under the short name used below.
_run_stage = stage_guard.run_stage


def analyse(audio_path) -> SpeechAnalysisResult:
    """Run the full pipeline on one audio file and return a typed result dict."""
    audio_path = Path(audio_path)
    warnings: List[str] = []
    latency: dict = {}

    t_total = time.perf_counter()
    audio, sr = _load_audio(audio_path)  # fatal on failure (AudioLoadError)
    duration = float(len(audio) / sr)
    latency["audio_load"] = round(time.perf_counter() - t_total, 3)

    # Stage 1: transcription (sequential; downstream depends on it)
    default_tx = {"transcription": _EMPTY_TRANSCRIPTION, "device": "cpu", "warnings": []}
    tx_out = _run_stage(
        "transcription",
        lambda: whisper_transcriber.transcribe(audio, sr),
        warnings,
        latency,
        default_tx,
    )
    transcription: Transcription = tx_out["transcription"]
    device = tx_out["device"]
    warnings.extend(tx_out.get("warnings", []))

    # Stage 2: prosody
    default_prosody = (
        Prosody(
            speaking_rate_syll_per_sec=0.0,
            pitch_mean_hz=0.0,
            pitch_std_hz=0.0,
            voiced_fraction=0.0,
            energy_rms_mean=0.0,
            energy_rms_std=0.0,
            zero_crossing_rate_mean=0.0,
            duration_seconds=duration,
        ),
        [],
    )
    prosody, prosody_warn = _run_stage(
        "prosody",
        lambda: prosody_extractor.extract(audio, sr),
        warnings,
        latency,
        default_prosody,
    )
    warnings.extend(prosody_warn)

    # Stage 3: speaker embedding (CPU)
    default_emb = (
        SpeakerEmbedding(model="speechbrain/spkrec-ecapa-voxceleb", dimension=0, vector=[]),
        [],
    )
    embedding, emb_warn = _run_stage(
        "speaker_embedding",
        lambda: speaker_embedder.embed(audio, sr),
        warnings,
        latency,
        default_emb,
    )
    warnings.extend(emb_warn)

    # Stage 4: filler detection (over Stage 1 word timestamps)
    default_fillers = Fillers(
        total_count=0, per_minute=0.0, counts_by_filler={}, instances=[]
    )
    fillers = _run_stage(
        "filler_detection",
        lambda: filler_detector.detect(
            transcription["word_timestamps"], duration, audio, sr
        ),
        warnings,
        latency,
        default_fillers,
    )

    # Stage 5: pauses.
    # Uses the same gaps between words as filler detection but measures the silent ones.
    default_pauses = Pauses(
        total_count=0, per_minute=0.0, longest_seconds=0.0, mean_seconds=0.0,
        total_silent_seconds=0.0, silent_fraction_0to1=0.0,
        time_to_first_word_seconds=0.0, counts_by_kind={}, instances=[],
    )
    pauses = _run_stage(
        "pause_detection",
        lambda: pause_analyser.detect(
            transcription["word_timestamps"], duration, audio, sr
        ),
        warnings,
        latency,
        default_pauses,
    )

    latency["total"] = round(time.perf_counter() - t_total, 3)

    processing: Processing = Processing(
        device=device, latency_seconds=latency, warnings=warnings
    )

    result: SpeechAnalysisResult = SpeechAnalysisResult(
        audio_file=str(audio_path),
        audio_duration_seconds=round(duration, 3),
        transcription=transcription,
        prosody=prosody,
        speaker_embedding=embedding,
        fillers=fillers,
        pauses=pauses,
        processing=processing,
    )
    return result


def _print_summary(result: SpeechAnalysisResult) -> None:
    """Print a readable summary of one analysed recording (command-line use)."""
    p = result["processing"]
    print("=" * 70)
    print(f"Audio file       : {result['audio_file']}")
    print(f"Duration (s)     : {result['audio_duration_seconds']}")
    print(f"Device           : {p['device']}")
    print("-" * 70)
    print(f"Transcript       : {result['transcription']['text'][:300]}")
    print(f"Words (timed)    : {len(result['transcription']['word_timestamps'])}")
    pr = result["prosody"]
    print(
        "Prosody          : rate={:.2f} syll/s  pitch={:.1f}+/-{:.1f} Hz  "
        "voiced={:.2f}".format(
            pr["speaking_rate_syll_per_sec"],
            pr["pitch_mean_hz"],
            pr["pitch_std_hz"],
            pr["voiced_fraction"],
        )
    )
    emb = result["speaker_embedding"]
    print(f"Speaker embedding: dim={emb['dimension']} ({emb['model']})")
    f = result["fillers"]
    print(
        f"Fillers          : total={f['total_count']}  "
        f"per_min={f['per_minute']}  by={f['counts_by_filler']}"
    )
    pz = result["pauses"]
    print(
        f"Pauses           : total={pz['total_count']}  "
        f"longest={pz['longest_seconds']}s  by={pz['counts_by_kind']}  "
        f"to_first_word={pz['time_to_first_word_seconds']}s"
    )
    print("-" * 70)
    print("Per-stage latency (s):")
    for k, v in p["latency_seconds"].items():
        print(f"    {k:<18}: {v}")
    if p["warnings"]:
        print("Warnings:")
        for w in p["warnings"]:
            print(f"    - {w}")
    print("=" * 70)


def main(argv: List[str]) -> int:
    """Analyse one audio file from the command line and save the result as JSON."""
    import warnings as _warnings

    # Quiet third-party FutureWarnings for the demo.
    _warnings.filterwarnings("ignore")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Suppress verbose dependency chatter (speechbrain/numba DEBUG and INFO) so the CLI summary is easy to read.
    # ERROR and above still surface real failures.
    logging.disable(logging.WARNING)
    if len(argv) < 2:
        print("Usage: python speech_analyser.py <audio_file>")
        return 2
    result = analyse(argv[1])
    _print_summary(result)
    out_path = Path("session_result.json")
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Full result written to {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
