# live_session.py
"""The live interview loop: ask, listen, decide whether to press, move on.

Usage: prompt = session.prepare(); while prompt: session.begin_answer(), then prompt = session.end_answer(); finally record = session.finish().

end_answer() returns None when the questions run out, or when the interviewer stops after two non-answers in a row.

The recorder, transcriber and speaker are passed in, so the whole loop can be tested without hardware or models.

No failure ends the interview early.
A dead microphone loses one answer, a failed quick transcript means no follow-up, and a silent voice costs nothing because the question is on screen.
Each adds a warning to the report.

The quick transcript used here only decides follow-ups and non-answers.
Every number in the report comes from the full transcription run_session does afterwards.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import patience
import session_store
from schema import LivePrompt, LiveSessionRecord, LiveTake, QuestionSet

logger = logging.getLogger(__name__)

DEFAULT_QUESTION_COUNT = 6

# What the interviewer says when offering the same question again after an answer that was not an attempt at it, and when stopping after the second.
SECOND_CHANCE_PREFIX = "Would you like to try that again? "
CLOSING_LINE = "I don't think there's value in continuing today. Let's stop here."
STOP_REASON = "Two answers in a row were not attempts at the question."
TRIGGER_SECOND_CHANCE = "second_chance"


class LiveSessionError(RuntimeError):
    """The loop was used out of order, such as answering before preparing.

    A bug in the caller, so it raises instead of warning.
    """


def _default_speak(text: str) -> None:
    """Start reading the question aloud without waiting for it to finish.

    Speaking a long question takes over ten seconds, so the question appears on screen straight away.
    """
    import feedback_speaker

    feedback_speaker.speak_async(text)


def _default_hush() -> None:
    import feedback_speaker

    feedback_speaker.stop_speaking()


def _default_transcribe(audio_path) -> str:
    import gist_transcriber

    return gist_transcriber.transcribe(audio_path)


def _default_release_transcriber() -> None:
    import gist_transcriber

    gist_transcriber.release()


def _default_release_card() -> None:
    import followup_planner

    followup_planner.cool()


def _default_questions(job_description: str, job_title: str, n: int,
                       cv_text: Optional[str] = None):
    """Plan the questions for a session from the advert and the CV."""
    import followup_planner
    import session_runner

    # Free the graphics card before asking for questions, because the rewording model and the question writer do not fit together.
    followup_planner.cool()
    # The session's own CV (an example from the setup screen) plans the questions; None reads the one saved on this machine.
    return session_runner.prepare_questions(job_description, job_title, n, cv_text=cv_text)


class LiveSession:
    """One practice interview, driven a step at a time by the interface."""

    def __init__(
        self,
        job_description: str = "",
        job_title: str = "",
        question_count: int = DEFAULT_QUESTION_COUNT,
        use_video: bool = True,
        recorder=None,
        transcribe: Optional[Callable[[Path], str]] = None,
        release_transcriber: Optional[Callable[[], None]] = None,
        speak: Optional[Callable[[str], None]] = None,
        question_source: Optional[Callable[[str, str, int], Tuple[QuestionSet, List[str]]]] = None,
        use_llm_rewording: bool = True,
        session_dir=None,
        allow_follow_ups: bool = True,
        stop_early: bool = True,
        cv_text: Optional[str] = None,
        release_card: Optional[Callable[[], None]] = None,
    ):
        self.job_description = job_description
        self.job_title = job_title
        self.question_count = max(int(question_count or 1), 1)
        self.use_video = bool(use_video)
        self.use_llm_rewording = bool(use_llm_rewording)
        # A retake gets no follow-up, so it can be compared cleanly with the first attempt.
        self.allow_follow_ups = bool(allow_follow_ups)
        # Whether the interviewer may end the session on non-answers.
        # Off for retakes and drills.
        self.stop_early = bool(stop_early)
        self._patience = patience.Patience() if self.stop_early else None
        self.ended_early: Optional[Dict[str, Any]] = None
        # Set when the last question has been answered, so an interview ended early can be told apart.
        self.completed = False

        self.recorder = recorder if recorder is not None else _lazy_recorder()
        self._transcribe = transcribe or _default_transcribe
        self._release_transcriber = (
            release_transcriber or _default_release_transcriber)
        self._release_card = release_card or _default_release_card
        self._speak = speak or _default_speak
        # Only meaningful for the real speaker; an injected one is synchronous and has nothing in flight to stop.
        self._hush = _default_hush if speak is None else (lambda: None)
        # The CV this session came with plans its questions (question_planner).
        self._question_source = question_source or functools.partial(_default_questions, cv_text=cv_text)

        self.session_dir: Optional[Path] = Path(session_dir) if session_dir else None
        self.questions: List[str] = []
        # Each prepared question's kind and reason (question_planner), in step with self.questions; empty dicts for generated or bank questions.
        self.question_items: List[Dict[str, Any]] = []
        self.takes: List[LiveTake] = []
        self.warnings: List[str] = []

        # Which prepared question we are on, 0-based.
        self._index = 0
        # The probe queued after a take.
        self._pending: Optional[LivePrompt] = None
        self._current: Optional[LivePrompt] = None
        self._recording = False
        self._recorder_started = False
        self._pending_paths: Tuple[Optional[Path], Optional[Path]] = (None, None)
        self._prepared = False
        self._speaker_reported = False
        self.finished = False

    # Setting up

    def prepare(self) -> Optional[LivePrompt]:
        """Generate the questions, make somewhere to record, ask the first one."""
        if self._prepared:
            raise LiveSessionError("This session has already been prepared.")

        complaint = session_store.check_space()
        if complaint:
            raise LiveSessionError(complaint)

        # Open the camera first, so the candidate can fix their framing and lighting while the questions are written.
        try:
            self._open_camera()
        except Exception as exc:  # an interview without a camera
            logger.warning("The camera could not be opened: %s", exc)
            self.warnings.append(
                f"The camera could not be started, so this session has no "
                f"video ({exc})."
            )
            self.use_video = False

        try:
            question_set, warns = self._question_source(
                self.job_description, self.job_title, self.question_count
            )
            self.questions = [q["question"] for q in question_set["questions"]]
            self.question_items = [dict(q) for q in question_set["questions"]]
            self.warnings.extend(warns)
        except Exception as exc:  # falls back rather than blocks
            logger.warning("Question generation failed: %s", exc)
            self.questions = []
            self.warnings.append(
                f"The interview questions could not be generated ({exc})."
            )

        if not self.questions:
            # Release the camera, because on Windows only one program can hold it.
            self._close_camera()
            raise LiveSessionError(
                "No interview questions could be prepared, so the session "
                "cannot start."
            )

        if self.session_dir is None:
            self.session_dir = session_store.new_session_dir(self.job_title)

        self._prepared = True
        self._current = self._question_prompt(0)

        # Load the rewording model while the first question is read, so the first follow-up is not slow.
        # Best effort.
        if self.use_llm_rewording:
            try:
                import followup_planner

                followup_planner.warm()
            except Exception as exc:  # never worth a failed start
                logger.debug("Could not warm the follow-up model: %s", exc)

        self._say(self._current["text"])
        return self._current

    # One answer

    def begin_answer(self) -> None:
        """Start recording.
        The candidate is now talking.
        """
        if not self._prepared:
            raise LiveSessionError("prepare() must run before an answer starts.")
        if self._recording:
            raise LiveSessionError("This session is already recording an answer.")
        if self.finished or self._current is None:
            raise LiveSessionError("This session has no question left to answer.")

        # The question may still be playing.
        # Stop it before opening the microphone, so the interviewer's voice is not recorded.
        self._hush()

        audio_path, video_path = session_store.take_paths(
            self.session_dir, len(self.takes) + 1
        )
        self._pending_paths = (audio_path, video_path if self.use_video else None)

        # `_recording` is the loop's step; `_recorder_started` is whether a device is really recording.
        # They differ when the microphone failed.
        self._recording = True
        try:
            self.recorder.start(*self._pending_paths)
            self._recorder_started = True
        except Exception as exc:
            # One lost answer, not a lost session.
            logger.warning("Recording failed to start: %s", exc)
            self.warnings.append(
                f"Answer {len(self.takes) + 1} could not be recorded ({exc}); "
                "the interview continued without it."
            )
            self._recorder_started = False
            self._pending_paths = (None, None)

    def end_answer(self) -> Optional[LivePrompt]:
        """Stop recording, decide whether to press, and return what to ask next.

        Returns None when the interview is over.
        """
        if not self._recording:
            raise LiveSessionError("No answer is being recorded.")
        self._recording = False

        audio_path, _ = self._pending_paths
        duration = self._stop_recorder()
        answered = self._current

        if audio_path is not None:
            # Only transcribe when the result will be used.
            # A follow-up's own answer is never judged or followed up.
            wants_gist = self._wants_gist(answered)
            gist = self._gist(audio_path) if wants_gist else ""
            # Free the quick transcript model before planning a follow-up, so the rewording model has the graphics card.
            # This halved the wait between questions.
            self._free_transcriber()
            # Only judge the answer when the interviewer is allowed to act on it.
            verdict = (self._judge(gist, answered, duration)
                       if wants_gist and self.stop_early else "")
            self.takes.append(LiveTake(
                take=len(self.takes) + 1,
                audio_path=str(audio_path),
                # No video is written, so there is nothing to point at.
                video_path=None,
                faces=dict(getattr(self.recorder, "face_summary", None) or {}),
                question=self._asked(answered),
                is_follow_up=answered["is_follow_up"],
                trigger=answered["trigger"],
                source=answered["source"],
                duration_seconds=round(float(duration or 0.0), 3),
                gist=gist,
                attempt=verdict,
                kind=answered.get("kind", ""),
                reason=answered.get("reason", ""),
            ))
            # Saved before the probe is planned, so an answer survives even if the planning stage takes the process down with it.
            self._save_manifest()
        else:
            gist = ""
            verdict = ""

        # Patience first: a non-answer gets the same question again, and two in a row end the interview.
        if verdict and self._patience is not None:
            action = self._patience.note(
                verdict, answered["trigger"] == TRIGGER_SECOND_CHANCE)
            if action == patience.ACTION_STOP:
                self._stop()
                return None
            if action == patience.ACTION_SECOND_CHANCE:
                self._pending = self._second_chance(answered)
                return self._advance()

        # A follow-up is never followed up, and the session limit is checked before planning.
        if (audio_path is not None and not answered["is_follow_up"]
                and self._could_probe(answered)):
            self._pending = self._plan_probe(gist, duration, answered)

        return self._advance()

    # The answer so far, read while it is being given

    def levels(self) -> List[float]:
        """How loud the candidate is right now, for the meter. [] when idle.

        Called from another thread, so it only touches the recorder.
        """
        return self._read_recorder("levels") or []

    def recent_audio(self, seconds: float):
        """The last `seconds` of the answer being given, or None when idle."""
        return self._read_recorder("recent_audio", seconds)

    def calibrate_camera(self, seconds: float = 3.0) -> dict:
        """Three seconds of looking at the camera, between answers only.

        Called from another thread.
        Refused during an answer.
        """
        if self._recording:
            return {"ok": False, "error": "Not while you are answering."}
        calibrate = getattr(self.recorder, "calibrate_camera", None)
        if calibrate is None:
            return {"ok": False, "error": "There is no camera in this session."}
        return calibrate(seconds)

    @property
    def samplerate(self) -> int:
        return int(getattr(self.recorder, "samplerate", 16000) or 16000)

    def _read_recorder(self, name: str, *args):
        if not (self._recording and self._recorder_started):
            return None
        reader = getattr(self.recorder, name, None)
        if reader is None:
            return None
        try:
            return reader(*args)
        except Exception as exc:
            # A meter is never worth an answer.
            logger.debug("Reading the recorder mid-take failed: %s", exc)
            return None

    def abort(self) -> None:
        """Give up on the session, leaving whatever was recorded on disk."""
        if self._recording:
            self._recording = False
            self._stop_recorder()
        self._close_camera()
        self.finished = True
        self._current = None

    # Finishing

    def finish(self) -> LiveSessionRecord:
        """Write the manifest and hand the parallel lists to the caller."""
        if self._recording:
            self.abort()

        # Nothing needs the camera once the last answer is in.
        self._close_camera()
        # Unload the follow-up model too, because transcription is about twice as fast without it.
        if self.use_llm_rewording:
            self._hand_back_card()
        self._save_manifest()

        record = LiveSessionRecord(
            session_dir=str(self.session_dir) if self.session_dir else "",
            audio_paths=[t["audio_path"] for t in self.takes],
            video_paths=[t["video_path"] for t in self.takes],
            questions=[t["question"] for t in self.takes],
            takes=list(self.takes),
            warnings=list(self.warnings),
            prepared=len(self.questions),
            ended_early=self.ended_early,
            completed=self.completed,
        )

        self.finished = True
        return record

    def _save_manifest(self) -> None:
        """Write the manifest that ties each recording to its question.

        Written after every answer, so a crash loses at most one answer rather than the whole session.
        """
        if self.session_dir is None:
            return
        try:
            session_store.write_manifest(self.session_dir, {
                "job_title": self.job_title,
                "job_description": self.job_description,
                "prepared_questions": self.questions,
                "used_video": self.use_video,
                "takes": list(self.takes),
                "warnings": self.warnings,
                # The count comes from "prepared_questions" above.
                "ended_early": self.ended_early,
            })
        except Exception as exc:  # the recordings still exist
            logger.warning("Could not write the session manifest: %s", exc)
            # Once per session.
            # A disk that refuses the write refuses it every time, and the same sentence repeated per answer is noise.
            message = (
                f"The session index could not be written ({exc}), though the "
                "recordings were kept."
            )
            if message not in self.warnings:
                self.warnings.append(message)

    # Internals

    def _question_prompt(self, index: int) -> LivePrompt:
        item = self.question_items[index] if index < len(self.question_items) else {}
        return LivePrompt(
            text=self.questions[index],
            is_follow_up=False,
            trigger="",
            source="",
            question_number=index + 1,
            total_questions=len(self.questions),
            kind=item.get("kind", "") or "",
            reason=item.get("reason", "") or "",
        )

    def _advance(self) -> Optional[LivePrompt]:
        """Ask the queued probe if there is one, else move to the next question."""
        if self._pending is not None:
            self._current, self._pending = self._pending, None
        else:
            self._index += 1
            if self._index >= len(self.questions):
                self._current = None
                self.finished = True
                self.completed = True
                return None
            self._current = self._question_prompt(self._index)

        self._say(self._current["text"])
        return self._current

    def _follow_ups_asked(self) -> int:
        """Probes so far.
        A second chance is not a probe and does not count.
        """
        return sum(1 for t in self.takes
                   if t["is_follow_up"] and t["trigger"] != TRIGGER_SECOND_CHANCE)

    def _wants_gist(self, answered: LivePrompt) -> bool:
        """Is the quick transcript needed for this answer?

        Yes for main questions and second chances, to judge the answer and plan a follow-up.
        No for a follow-up's own answer.
        """
        if answered["trigger"] == TRIGGER_SECOND_CHANCE:
            return self.stop_early
        if answered["is_follow_up"]:
            return False
        return self.stop_early or self._could_probe(answered)

    def _could_probe(self, answered: LivePrompt) -> bool:
        """Can a follow-up still be asked after this answer?

        Answerable before the transcript exists.
        """
        import followup_planner

        if not self.allow_follow_ups:
            return False
        if answered["is_follow_up"]:
            return False
        return self._follow_ups_asked() < followup_planner.MAX_FOLLOWUPS_PER_SESSION

    def _judge(self, gist: str, answered: LivePrompt, duration: float) -> str:
        """What the interviewer makes of the answer, from the quick transcript.

        An empty transcript, or one with no words, is "unknown" and counts for nothing.
        Only a refusal, abuse or nonsense counts as a non-answer.
        """
        import non_answer

        if not gist.strip():
            return patience.VERDICT_UNKNOWN
        hit = non_answer.classify(gist, self._asked(answered), self.job_description,
                                  duration)
        if hit is None:
            return patience.VERDICT_ATTEMPT
        if hit["kind"] == "empty":
            return patience.VERDICT_UNKNOWN
        return patience.VERDICT_NON_ANSWER

    @staticmethod
    def _asked(prompt: LivePrompt) -> str:
        """The question behind a prompt, without the interviewer's framing.

        A second chance starts with "Would you like to try that again?", which must not count as topic words.
        """
        text = prompt["text"]
        if prompt["trigger"] == TRIGGER_SECOND_CHANCE and text.startswith(SECOND_CHANCE_PREFIX):
            return text[len(SECOND_CHANCE_PREFIX):]
        return text

    def _second_chance(self, answered: LivePrompt) -> LivePrompt:
        return LivePrompt(
            text=SECOND_CHANCE_PREFIX + answered["text"],
            is_follow_up=True,
            trigger=TRIGGER_SECOND_CHANCE,
            source="template",
            question_number=answered["question_number"],
            total_questions=len(self.questions),
            # The same question again, so the same kind and reason.
            kind=answered.get("kind", ""),
            reason=answered.get("reason", ""),
        )

    def _stop(self) -> None:
        """End the interview here.
        Spoken, recorded, and final.
        """
        self.ended_early = {
            # Questions reached, not takes: a probe or a second chance is not another question, and "after 4 of 3 questions" was shown once.
            "after": self._index + 1,
            "prepared": len(self.questions),
            "reason": STOP_REASON,
        }
        self._pending = None
        self._current = None
        self.finished = True
        self._say(CLOSING_LINE)
        self._save_manifest()

    def _plan_probe(self, gist: str, duration: float,
                    answered: LivePrompt) -> Optional[LivePrompt]:
        """Ask the follow-up planner whether this answer earns a probe; the probe, or None."""
        if not gist:
            return None

        import followup_planner

        decision = followup_planner.plan(
            gist,
            duration,
            question=answered["text"],
            asked_so_far=self._follow_ups_asked(),
            use_llm=self.use_llm_rewording,
            question_kind=answered.get("kind") or None,
        )
        self.warnings.extend(decision.get("warnings") or [])
        if not decision["ask"]:
            return None

        return LivePrompt(
            text=decision["question"],
            is_follow_up=True,
            trigger=decision["trigger"],
            source=decision["source"],
            question_number=answered["question_number"],
            total_questions=len(self.questions),
            kind="follow_up",
            reason="",
        )

    def _stop_recorder(self) -> float:
        if not self._recorder_started:
            return 0.0
        self._recorder_started = False
        try:
            return float(self.recorder.stop() or 0.0)
        except Exception as exc:
            # The audio may still be on disk.
            logger.warning("Recording failed to stop cleanly: %s", exc)
            self.warnings.append(
                f"Answer {len(self.takes) + 1} may have been cut short ({exc})."
            )
            return 0.0

    def _gist(self, audio_path) -> str:
        try:
            return self._transcribe(audio_path) or ""
        except Exception as exc:
            # No gist means unknown, and no probe.
            logger.warning("Gist transcription failed: %s", exc)
            self.warnings.append(
                f"Answer {len(self.takes) + 1} could not be transcribed during "
                f"the interview ({exc}), so it was neither judged nor "
                "considered for a follow-up. The answer itself was still "
                "recorded and will be scored."
            )
            return ""

    def _open_camera(self) -> None:
        """Open the camera for the whole interview rather than each answer.

        Optional, because the test recorders have no camera.
        """
        if not self.use_video:
            return
        opener = getattr(self.recorder, "open_camera", None)
        if opener is None:
            return
        try:
            opener()
        except Exception as exc:  # audio-only is a valid session
            logger.warning("The camera could not be opened: %s", exc)

    def _close_camera(self) -> None:
        """Release the camera once there are no more answers to record."""
        closer = getattr(self.recorder, "close_camera", None)
        if closer is None:
            return
        try:
            closer()
        except Exception as exc:  # releasing is best effort
            logger.debug("Releasing the camera failed: %s", exc)

    def _hand_back_card(self) -> None:
        try:
            self._release_card()
        except Exception as exc:  # housekeeping is best effort
            logger.debug("Could not unload the follow-up model: %s", exc)

    def _free_transcriber(self) -> None:
        try:
            self._release_transcriber()
        except Exception as exc:  # housekeeping is best effort
            logger.debug("Could not free the gist model: %s", exc)

    def _say(self, text: str) -> None:
        """Read a question aloud.
        It is on screen too, so a failure here is only logged.
        """
        try:
            self._speak(text)
        except Exception as exc:
            # The question is on screen anyway.
            logger.warning("Speaking the question failed: %s", exc)
            # Warn once per session, not for every question.
            if not self._speaker_reported:
                self._speaker_reported = True
                self.warnings.append(
                    f"The questions could not be read aloud ({exc}); they were "
                    "shown on screen instead."
                )


def _lazy_recorder():
    """Import the real recorder only when none was given, so tests never load a sound device."""
    import recorder

    return recorder.Recorder()
