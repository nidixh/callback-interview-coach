# fetch_models.py
"""Downloads the speech models during setup so the first interview does not wait.

Usage: python backend/fetch_models.py

Models already downloaded are skipped.
Without this step each model downloads the first time it is needed.
"""

from __future__ import annotations

import os

import whisper

import gist_transcriber
import speaker_embedder
import whisper_transcriber


def whisper_folder() -> str:
    """Where Whisper keeps its models; the same place whisper.load_model looks."""
    cache = os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache"))
    return os.path.join(cache, "whisper")


def main() -> None:
    for name in (whisper_transcriber.WHISPER_MODEL_SIZE, gist_transcriber.GIST_MODEL_SIZE):
        print(f"Whisper {name}")
        # Download only: loading the model into memory is not needed here.
        whisper._download(whisper._MODELS[name], whisper_folder(), False)
    print("Speaker model")
    speaker_embedder._get_classifier()
    print("Speech models ready.")


if __name__ == "__main__":
    main()
