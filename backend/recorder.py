# recorder.py
"""Records the candidate's voice, and optionally their face, one answer at a time.

Usage: r = Recorder(); r.start(Path("take_01.wav"), Path("take_01.mp4")); seconds = r.stop(); then check r.heard_nothing.

Audio is saved at 16 kHz mono, the format Whisper wants.
Stereo is mixed down instead of dropping a channel.

A muted microphone does not raise; it records silence. heard_nothing checks the peak level, so the page can say "we could not hear you" straight away.

The camera starts first so both begin together; the video may run a fraction of a second longer, which does not matter.

The audio callback only appends each block, because slow work there causes clicks.
Everything else happens in stop(). levels() and recent_audio() read the blocks collected so far, for the meter and live captions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# What the rest of the pipeline expects, so nothing downstream has to resample.
SAMPLE_RATE = 16000
CHANNELS = 1

# Below this peak amplitude, nothing was said.
# Speech into a laptop microphone peaks around 0.2 to 0.5; an idle or muted device sits near 0.0004.
SILENCE_PEAK = 0.01

# The range the level meter shows, in decibels.
# Silence sits near -68 dB and speech around -26 to -14 dB.
METER_FLOOR_DB = -55.0
METER_CEILING_DB = -12.0


class RecorderError(RuntimeError):
    """The device could not be opened, or the recorder was driven out of order."""


def _default_stream_factory(samplerate: int, channels: int, callback):
    import sounddevice as sd

    return sd.InputStream(
        samplerate=samplerate, channels=channels, dtype="float32",
        callback=callback,
    )


def _loudness(samples: np.ndarray) -> float:
    """Root-mean-square level of some samples, placed on the meter's 0 to 1."""
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    if rms <= 0.0:
        return 0.0
    db = 20.0 * np.log10(rms)
    span = METER_CEILING_DB - METER_FLOOR_DB
    return round(min(max((db - METER_FLOOR_DB) / span, 0.0), 1.0), 3)


def _default_video():
    import webcam_recorder

    return webcam_recorder.WebcamRecorder()


class Recorder:
    """One microphone, optionally one webcam, recording one take at a time."""

    def __init__(
        self,
        samplerate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        stream_factory: Optional[Callable] = None,
        video=None,
    ):
        self.samplerate = int(samplerate)
        self.channels = max(int(channels), 1)
        self._stream_factory = stream_factory or _default_stream_factory
        self._video = video
        self._video_requested = video is not None

        self._stream = None
        self._blocks: List[np.ndarray] = []
        self._audio_path: Optional[Path] = None
        self._recording = False
        self._video_running = False

        self.peak_level = 0.0
        # What the camera measured during the last take.
        # Empty until one has been recorded, and empty for an interview with no camera.
        self.face_summary: dict = {}
        self.warnings: List[str] = []

    # The camera, across the whole session

    def open_camera(self) -> None:
        """Open the camera before the first question and keep it open.

        Opening takes about 1.8 s, so the candidate sees themselves while reading question one.
        A failure is only a warning, because the report can be built without the camera.
        """
        try:
            self._video_for_take().open()
        except Exception as exc:  # audio-only is a valid session
            logger.warning("The camera could not be opened: %s", exc)
            self.warnings.append(
                f"The camera could not be started ({exc}), so this session is "
                "being recorded as audio only."
            )

    def calibrate_camera(self, seconds: float = 3.0) -> dict:
        """Ask the camera to learn where this person looks when looking at the camera.

        Called from a web request's thread.
        Never raises.
        """
        calibrate = getattr(self._video, "calibrate", None) if self._video is not None else None
        if calibrate is None:
            return {"ok": False, "error": "There is no camera in this session."}
        try:
            return calibrate(seconds)
        except Exception as exc:
            # A calibration is never worth the interview.
            logger.warning("Calibrating the camera failed: %s", exc)
            return {"ok": False, "error": "The camera could not be calibrated just now."}

    def close_camera(self) -> None:
        """Release the camera.
        Nothing else needs it after the last answer.
        """
        if self._video is None:
            return
        try:
            self._video.close()
        except Exception as exc:  # releasing is best effort
            logger.debug("Releasing the camera failed: %s", exc)

    # The take

    def start(self, audio_path, video_path=None) -> None:
        """Open the devices and start recording.

        Raises if the microphone cannot be opened.
        """
        if self._recording:
            raise RecorderError("This recorder is already recording a take.")

        self._audio_path = Path(audio_path)
        self._audio_path.parent.mkdir(parents=True, exist_ok=True)
        self._blocks = []
        self.peak_level = 0.0
        self.warnings = []

        # Camera first, so the audio does not start before the video.
        # Camera failures are never raised, because losing the camera only costs the facial channel.
        if video_path is not None:
            try:
                self._video_for_take().start(Path(video_path))
                self._video_running = True
            except Exception as exc:  # audio-only is a valid session
                logger.warning("Webcam failed to start: %s", exc)
                self.warnings.append(
                    f"The camera could not be started ({exc}), so this answer "
                    "was recorded as audio only."
                )
                self._video_running = False

        try:
            self._stream = self._stream_factory(
                self.samplerate, self.channels, self._on_audio
            )
            self._stream.start()
        except Exception as exc:  # re-raised as our own type
            self._stream = None
            # Without this the camera stays open and the next answer cannot record any video at all.
            self._stop_video()
            raise RecorderError(
                f"The microphone could not be opened ({exc})."
            ) from exc

        self._recording = True

    def stop(self) -> float:
        """Close the devices, write the take, and return its length in seconds."""
        if not self._recording:
            raise RecorderError("This recorder is not recording.")
        self._recording = False

        self._close_stream()
        self._stop_video()

        audio = self._collected()
        self.peak_level = float(np.abs(audio).max()) if audio.size else 0.0

        try:
            import soundfile as sf

            sf.write(str(self._audio_path), audio, self.samplerate)
        except Exception as exc:  # reported, not raised
            logger.warning("Could not write %s: %s", self._audio_path, exc)
            self.warnings.append(
                f"The recording could not be saved ({exc})."
            )
            return 0.0

        return round(len(audio) / float(self.samplerate), 3)

    @property
    def heard_nothing(self) -> bool:
        """True when the last take contained no sound worth calling speech."""
        return self.peak_level < SILENCE_PEAK

    # The take so far, read while it is still being recorded

    def recent_audio(self, seconds: float) -> Optional[np.ndarray]:
        """The last `seconds` of the current take, mono, or None when idle."""
        if not self._recording:
            return None
        wanted = max(int(seconds * self.samplerate), 1)
        blocks = self._blocks
        tail: List[np.ndarray] = []
        have = 0
        # Newest first, and only as far back as needed: an answer runs to thousands of blocks and the meter asks ten times a second.
        for i in range(len(blocks) - 1, -1, -1):
            tail.append(blocks[i])
            have += len(blocks[i])
            if have >= wanted:
                break
        if not tail:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(tail[::-1], axis=0).astype(np.float32)
        if audio.ndim > 1 and audio.shape[1] > 1:
            audio = audio.mean(axis=1)
        return audio.reshape(-1)[-wanted:]

    def levels(self, count: int = 14, seconds: float = 1.2) -> List[float]:
        """Loudness of the last `seconds`, in `count` slices, oldest first.

        Each value runs from 0 (silence) to 1 (loud speech).
        Empty when idle.
        """
        audio = self.recent_audio(seconds)
        if audio is None:
            return []
        count = max(int(count), 1)
        if audio.size == 0:
            return [0.0] * count
        return [_loudness(part) for part in np.array_split(audio, count)]

    # Internals

    def _on_audio(self, indata, frames, time_info, status) -> None:
        """Runs on the audio thread.
        Append and return; do nothing slow here.
        """
        if status:
            logger.debug("Audio stream status: %s", status)
        self._blocks.append(np.asarray(indata).copy())

    def _collected(self) -> np.ndarray:
        if not self._blocks:
            return np.zeros(0, dtype=np.float32)

        audio = np.concatenate(self._blocks, axis=0).astype(np.float32)
        if audio.ndim > 1 and audio.shape[1] > 1:
            # Mean, not channel zero: on an array microphone both channels carry the speaker, and dropping one throws away half the signal.
            audio = audio.mean(axis=1)
        return audio.reshape(-1)

    def _video_for_take(self):
        if self._video is None:
            self._video = _default_video()
        return self._video

    def _close_stream(self) -> None:
        if self._stream is None:
            return
        for step in ("stop", "close"):
            try:
                getattr(self._stream, step)()
            except Exception as exc:  # best effort teardown
                logger.debug("Stream %s() failed: %s", step, exc)
        self._stream = None

    def _stop_video(self) -> None:
        """Stop the camera for this take and keep what it measured.
        Does nothing when it is off.
        """
        if not self._video_running:
            return
        self._video_running = False
        try:
            self._video.stop()
            # What the camera measured during the answer.
            # This is the whole of the camera's contribution now: there is no file to analyse later.
            self.face_summary = dict(
                (getattr(self._video, "summary", None) or {}).get("faces") or {})
        except Exception as exc:  # the audio take is unaffected
            logger.warning("Webcam failed to stop cleanly: %s", exc)
            self.warnings.append(
                f"The camera did not shut down cleanly ({exc}); the video for "
                "this answer may be incomplete."
            )
