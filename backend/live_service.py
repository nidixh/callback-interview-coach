# live_service.py
"""Runs a live interview for the web page, and tells every open page what is happening.

Usage: service = LiveService(); service.start(job_description=..., job_title=..., question_count=5); then read events from service.subscribe().

LiveSession is not safe to use from two threads, so one worker thread owns it.
The web server and every open tab send commands and read events back.

Each watcher gets its own queue with a snapshot of the current state, so a page that connects late is up to date.
A watcher that stops reading is dropped, so one abandoned tab can never stall the interview.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from typing import Any, Dict, List, Optional

import drill
import session_history
import session_library
import session_metrics
from non_answer import NotAnAttempt

logger = logging.getLogger(__name__)

# States a watcher can be shown.
# Kept as plain strings because they cross a JSON boundary to the browser and are read by humans in the process.
IDLE = "idle"
PREPARING = "preparing"
# A question is on screen, nobody is speaking yet.
WAITING = "waiting"
RECORDING = "recording"
# Transcribing, and deciding whether to press.
THINKING = "thinking"
# Every answer recorded, nothing analysed yet.
FINISHED = "finished"
# The long part, several minutes for a full session.
ANALYSING = "analysing"
REPORTED = "reported"
# Not a sincere attempt: no scoring, no report.
DISMISSED = "dismissed"
FAILED = "failed"

NOT_SCORED = "This session was not scored."


def _ended_by_you(record) -> str:
    """The sentence shown when the candidate ends an interview before the last question."""
    answered = len((record or {}).get("takes") or [])
    prepared = (record or {}).get("prepared") or 0
    if not answered:
        return "You ended it before answering any questions."
    return f"You answered {answered} of {prepared} questions before ending it."


class LiveService:
    """Owns one live interview and publishes what happens to it."""

    def __init__(self, session_factory=None, on_prepared=None, analyser=None,
                 captioner_factory=None):
        self._session_factory = session_factory or _default_factory
        # Builds the live captions for one answer, or declines with None.
        self._captioner_factory = captioner_factory or _default_captioner
        self._captioner = None
        # A CV that came with this session (from an example).
        # Used instead of the saved CV and never saved over it.
        # None means the saved CV.
        self._session_cv: Optional[str] = None
        self._on_prepared = on_prepared
        self._analyser = analyser or _default_analyse

        # The full analysis, kept here.
        # Only a summary is published, because the full result is too large to send on every change.
        self.report = None
        self.report_file = ""
        # Remembered from the last start, so a retake is scored against the same advert.
        self._job_description = ""
        self._job_title = ""
        # Which kind of session this is (interview, retake or drill), so the progress chart does not mix them.
        self._session_kind = "interview"
        self._commands: "queue.Queue[tuple]" = queue.Queue()
        self._watchers: List["queue.Queue[dict]"] = []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

        self._session = None
        self._state: Dict[str, Any] = {
            "state": IDLE,
            "prompt": None,
            # Published so a page that reconnects mid-interview, having never seen the setup screen, knows whether to show a self-view.
            "use_video": False,
            "answered": 0,
            "total": 0,
            "warnings": [],
            "record": None,
            "summary": None,
            "progress": None,
            "error": "",
            # Set when the interviewer stopped the session; carried from FINISHED through REPORTED so the page can say it on both.
            "ended_early": None,
            # What was said so far, question by question, from the quick transcript.
            "so_far": [],
            # Live captions of the current answer.
            # Cleared when the answer ends, when so_far takes over.
            "live_words": "",
            # True while the session is using an example CV rather than the candidate's saved one, so a page that reconnects can say so.
            "example_cv": False,
            # Set when the session was not an attempt: the interviewer stopped it, or half or more answers were non-answers.
            # {"reason", "not_attempted", "total"}; the counts are None for a stop.
            # Nothing is scored or kept.
            "dismissed": None,
            # True once the camera has been calibrated this session.
            "camera_calibrated": False,
        }

    # Watching

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def subscribe(self) -> "queue.Queue[dict]":
        """A queue receiving every event from now on, starting with the state."""
        watcher: "queue.Queue[dict]" = queue.Queue(maxsize=64)
        with self._lock:
            watcher.put(dict(self._state))
            self._watchers.append(watcher)
        return watcher

    def unsubscribe(self, watcher) -> None:
        with self._lock:
            if watcher in self._watchers:
                self._watchers.remove(watcher)

    def _publish(self, **changes) -> None:
        """Apply `changes` to the shared state and send the new state to every watcher."""
        with self._lock:
            self._state.update(changes)
            payload = dict(self._state)
            watchers = list(self._watchers)

        for watcher in watchers:
            try:
                watcher.put_nowait(payload)
            except queue.Full:
                # Nobody is draining this one.
                # An abandoned browser tab must not be able to stall an interview, so it loses its place.
                self.unsubscribe(watcher)

    # Driving

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="live-session")
            self._thread.start()

    def _send(self, *command) -> None:
        """Queue a command, starting the worker thread if needed.

        Every command checks the thread, so a command can never wait for a thread that was never started.
        """
        self._ensure_thread()
        self._commands.put(command)

    def start(self, job_description: str, job_title: str,
              question_count: int = 6, use_video: bool = True,
              cv: Optional[str] = None) -> None:
        """Begin an interview.
        `cv`, when given, is used for this session only.

        An empty `cv` means the saved one.
        """
        example = (cv or "").strip() or None
        # Published straight away, so a page reloaded during the long wait for questions still shows the camera.
        self._publish(state=PREPARING, error="", use_video=bool(use_video),
                      example_cv=example is not None, camera_calibrated=False)
        self._send("start", job_description, job_title, question_count,
                   use_video, example)

    def begin_answer(self) -> None:
        self._send("begin")

    def end_answer(self) -> None:
        self._send("end")

    def finish(self) -> None:
        self._send("finish")

    def analyse(self, job_description: str = "", job_title: str = "") -> None:
        """Score the recorded answers and write the report.

        Separate from finish(), because this takes minutes and the page shows its progress.
        """
        self._send("analyse", job_description, job_title)

    def drill(self, dimension: str, use_video: bool = True) -> None:
        """A short session on the weakest area of the last one.

        Runs as a new session, so the previous answers are never overwritten.
        """
        self._publish(state=PREPARING, error="", use_video=bool(use_video))
        self._send("drill", dimension, use_video)

    def retake(self, question: str, use_video: bool = True) -> None:
        """Answer one question again after reading its feedback.

        Runs as a new one question session, so the first attempt's recordings are never overwritten.
        No follow-ups, so the two answers compare cleanly.
        """
        self._publish(state=PREPARING, error="", use_video=bool(use_video))
        self._send("retake", question, use_video)

    def reset(self) -> None:
        """Throw the session away and go back to the start.

        Without this, reloading the page would rejoin the finished session.
        """
        self._send("reset")

    def level(self) -> Dict[str, Any]:
        """The microphone level right now, for the meter.

        Not part of the published state, because the page asks about ten times a second.
        Reads the recorder only and changes nothing.
        """
        with self._lock:
            recording = self._state.get("state") == RECORDING
        session = self._session
        levels: List[float] = []
        if recording and session is not None:
            reader = getattr(session, "levels", None)
            try:
                levels = [float(v) for v in (reader() if reader else []) or []]
            except Exception as exc:  # no meter, same answer
                logger.debug("Reading the level failed: %s", exc)
                levels = []
        return {"recording": recording, "levels": levels}

    def calibrate(self, seconds: float = 3.0) -> Dict[str, Any]:
        """Three seconds of looking at the camera, asked for by the page.

        Runs on the caller's thread, so it works while the questions are being written.
        Allowed while preparing and between answers.
        Always returns a dictionary.
        """
        with self._lock:
            state = self._state.get("state")
            use_video = bool(self._state.get("use_video"))
        if state not in (PREPARING, WAITING):
            if state in (RECORDING, THINKING):
                return {"ok": False, "error": "Not while you are answering."}
            return {"ok": False, "error": "Calibrating needs an interview running."}
        if not use_video:
            return {"ok": False, "error": "This interview has the camera switched off."}
        session = self._session
        calibrate = getattr(session, "calibrate_camera", None) if session is not None else None
        if calibrate is None:
            return {"ok": False, "error": "The camera is still starting. Try again in a moment."}
        try:
            reply = calibrate(max(1.0, min(float(seconds), 10.0)))
        except Exception as exc:
            # Never at the cost of the interview.
            logger.warning("Calibrating failed: %s", exc)
            return {"ok": False, "error": "The camera could not be calibrated just now."}
        if not isinstance(reply, dict):
            return {"ok": False, "error": "The camera could not be calibrated just now."}
        if reply.get("ok"):
            self._publish(camera_calibrated=True)
        return reply

    def stop(self) -> None:
        """Ask the owning thread to exit.
        Used when shutting the server down.
        """
        # Not _send, so a closed service stays closed.
        self._commands.put(("stop",))

    # The one thread that touches the session

    def _run(self) -> None:
        while True:
            command, *args = self._commands.get()
            try:
                if command == "stop":
                    return
                self._handle(command, args)
            except Exception as exc:  # one interview, not the server
                logger.warning("Live command %r failed: %s", command, exc)
                self._stop_captions()
                self._publish(state=FAILED, error=str(exc))

    # Live captions

    def _start_captions(self) -> None:
        """Start live captions for the answer that just began, if the recorder can be read mid answer.

        The captioner runs on its own thread.
        Its words are only shown while it is still the current captioner.
        """
        self._stop_captions()
        mine: Dict[str, Any] = {}

        def publish(text: str) -> None:
            if mine.get("captioner") is not None and self._captioner is mine["captioner"]:
                self._publish(live_words=str(text))

        try:
            captioner = self._captioner_factory(self._session, publish)
        except Exception as exc:
            # Captions are never worth an answer.
            logger.warning("Live captions could not be set up: %s", exc)
            return
        if captioner is None:
            return
        mine["captioner"] = captioner
        self._captioner = captioner
        try:
            captioner.start()
        except Exception as exc:
            logger.warning("Live captions could not start: %s", exc)
            self._captioner = None

    def _stop_captions(self) -> None:
        captioner, self._captioner = self._captioner, None
        if captioner is None:
            return
        try:
            captioner.stop()
        except Exception as exc:  # stopping is best effort
            logger.debug("Stopping live captions failed: %s", exc)

    def _finish_deferred_claims(self, record) -> None:
        """Run the postponed claims check after the report is shown.

        Stops as soon as another command is waiting, so the candidate never waits behind it.
        The saved result is only rewritten if a claim was found.
        """
        if not self.report:
            return
        answers = self.report.get("answers") or []
        changed = False
        for index, answer in enumerate(answers):
            if not self._commands.empty():
                logger.info(
                    "Deferred claims stopped after %d of %d answers: "
                    "another command is waiting", index, len(answers))
                break
            changed = _claims_for_answer(answer) or changed

        changed = self._check_the_cv() or changed

        if not changed:
            return
        saved = _write_result(self.report, record.get("session_dir") or "")
        if saved:
            self.report_file = saved
        self._publish(state=REPORTED, summary=summarise(self.report),
                      warnings=list(self.report.get("warnings") or []))

    def _check_the_cv(self) -> bool:
        """Compare the stored CV with what was said.
        Optional, like the claims check.

        Runs after the report is shown and is skipped when another command is waiting.
        Catches its own failures, so a finished report never turns into a failed session.
        """
        if not self.report or not self._commands.empty():
            return False
        try:
            import candidate_cv
            import cv_check

            # The example CV this session came with, or the saved one.
            stored = self._session_cv or candidate_cv.load()
            if not stored:
                return False

            claims, warnings = cv_check.check(
                stored, self.report.get("answers") or [])
            if not claims:
                return False

            debrief = self.report.setdefault("debrief", {})
            debrief["cv_claims"] = claims
            if warnings:
                self.report.setdefault("warnings", []).extend(warnings)
            return True
        except Exception as exc:  # advisory, never fatal
            logger.warning("Could not check the CV against this session: %s", exc)
            return False

    def _short_session(self, kind: str, count: int, use_video: bool,
                       question_source, **published) -> None:
        """Start a one off session after a finished one: a retake or a drill.

        The previous report is kept, because the new attempt is compared with it.
        The old record is cleared, so an abandoned drill can never re-score the previous session.

        Follow-ups, their rewording model and the interviewer's patience are all off for these short sessions.
        """
        self._session_kind = kind
        self._session = self._session_factory(
            job_description=self._job_description,
            job_title=self._job_title,
            question_count=count, use_video=use_video,
            question_source=question_source,
            allow_follow_ups=False,
            use_llm_rewording=False,
            stop_early=False,
        )
        prompt = self._session.prepare()
        # The number of questions actually prepared, which can be fewer than asked for.
        prepared = len(getattr(self._session, "questions", None) or []) or count
        self._publish(state=WAITING, prompt=prompt, use_video=bool(use_video),
                      answered=0, total=prepared, record=None, ended_early=None,
                      dismissed=None, so_far=[], live_words="",
                      warnings=list(getattr(self._session, "warnings", []) or []),
                      **published)

    def _dismiss(self, record, reason: str, not_attempted=None, total=None,
                 ended_early=None, by_user: bool = False) -> None:
        """Mark the session as not an attempt and keep nothing of it.

        No summary, report or history row.
        The recordings stay on disk, marked so the Sessions list leaves them out.
        """
        session_library.dismiss(str((record or {}).get("session_dir") or ""), reason)
        self.report = None
        self.report_file = ""
        self._publish(state=DISMISSED, prompt=None, record=record,
                      summary=None, progress=None, error="",
                      ended_early=ended_early,
                      dismissed={"reason": reason, "not_attempted": not_attempted,
                                 "total": total, "by_user": bool(by_user)},
                      warnings=list((record or {}).get("warnings") or []))

    def _handle(self, command: str, args) -> None:
        """Carry out one queued command, on the thread that owns the session."""
        if command == "retake":
            question, use_video = args
            self._short_session("retake", 1, use_video,
                                _one_question(question),
                                retake_of=question, drill_of=None)
            return

        if command == "drill":
            dimension, use_video = args
            area = drill.describe(dimension)
            self._short_session(
                "drill", drill.DEFAULT_QUESTIONS, use_video,
                drill.question_source(dimension),
                retake_of=None,
                drill_of=(area or {}).get("name") or "practice")
            return

        if command == "start":
            job_description, job_title, count, use_video, *example = args
            self._session_cv = example[0] if example else None
            self._job_description = job_description
            self._job_title = job_title
            self._session_kind = "interview"
            self._session = self._session_factory(
                job_description=job_description, job_title=job_title,
                question_count=count, use_video=use_video,
                # An example CV plans this session's questions; None means the saved one (question_planner reads it).
                cv_text=self._session_cv,
                # No follow-ups: the candidate chose the question count, and each follow-up adds minutes to the report.
                # The second chance after a non-answer still applies.
                allow_follow_ups=False, use_llm_rewording=False)
            import question_planner

            # Each planning step as it starts, for the loading percentage.
            def planning(stage: str) -> None:
                self._publish(progress={"stage": "q_" + stage, "done": 0, "total": 1})

            self._publish(progress=None)
            question_planner.listen(planning)
            try:
                prompt = self._session.prepare()
            finally:
                question_planner.unlisten(planning)
            if self._on_prepared is not None:
                # Somewhere to warm anything expensive while the candidate is still reading the first question rather than waiting on it.
                try:
                    self._on_prepared()
                except Exception as exc:  # warming is optional
                    logger.debug("Warming after prepare failed: %s", exc)
            self._publish(state=WAITING, prompt=prompt, answered=0,
                          total=len(self._session.questions), progress=None,
                          use_video=bool(use_video),
                          warnings=list(self._session.warnings),
                          ended_early=None, dismissed=None, retake_of=None, drill_of=None, so_far=[], live_words="")

        elif command == "begin":
            self._session.begin_answer()
            self._publish(state=RECORDING, live_words="")
            self._start_captions()

        elif command == "end":
            # Stop captions before ending the answer, so the interviewer's transcript never waits behind a caption.
            self._stop_captions()
            self._publish(state=THINKING)
            prompt = self._session.end_answer()
            answered = len(self._session.takes)
            self._publish(so_far=_so_far(self._session), live_words="")
            if prompt is None:
                # Out of questions, so the interview is over and the page is told so.
                self._publish(state=THINKING, prompt=None, answered=answered)
                self._handle("finish", [])
            else:
                self._publish(state=WAITING, prompt=prompt, answered=answered,
                              warnings=list(self._session.warnings))

        elif command == "finish":
            self._stop_captions()
            record = self._session.finish() if self._session else None
            ended_early = (record or {}).get("ended_early")
            if ended_early:
                # The interviewer stopped it.
                # The candidate was told so aloud; the page says it again and offers nothing else.
                self._dismiss(record, reason=str(ended_early.get("reason") or ""),
                              ended_early=ended_early)
                return
            if record is not None and record.get("completed") is False:
                # The candidate ended it before the last question.
                # Nothing is graded, so the models stay free and a new interview can start straight away.
                self._dismiss(record, reason=_ended_by_you(record), by_user=True)
                return
            self._publish(state=FINISHED, prompt=None, record=record,
                          ended_early=None,
                          warnings=list(record["warnings"]) if record else [])

        elif command == "reset":
            self._stop_captions()
            self._session_cv = None
            # A live session still holds the camera and the microphone, so it is closed rather than dropped.
            if self._session is not None and not getattr(self._session, "finished", False):
                try:
                    self._session.abort()
                except Exception as exc:  # going away regardless
                    logger.debug("Aborting the session while clearing: %s", exc)
            self._session = None
            self.report = None
            self.report_file = ""
            self._session_kind = "interview"
            # Clear retake_of and drill_of too, so a new interview is not labelled as a retake.
            self._publish(state=IDLE, prompt=None, answered=0, total=0,
                          warnings=[], record=None, summary=None,
                          progress=None, error="", use_video=False,
                          ended_early=None, dismissed=None, retake_of=None, drill_of=None, so_far=[], live_words="",
                          example_cv=False, camera_calibrated=False)

        elif command == "analyse":
            job_description, job_title = args
            snapshot = self.snapshot()
            if snapshot.get("dismissed"):
                # Refused here, not only on the page: a reconnecting page or a stale one must not be able to score what was dismissed.
                self._publish(error=NOT_SCORED)
                return
            if snapshot.get("state") == REPORTED and self.report is not None:
                # Every open page asks for the analysis when an interview ends, so only the first request runs it.
                logger.info("This interview is already analysed; the request was ignored.")
                return
            record = snapshot.get("record")
            if not record or not record.get("audio_paths"):
                self._publish(state=REPORTED, summary=None,
                              error="There was nothing recorded to analyse.")
                return

            total = len(record["audio_paths"])
            self._publish(state=ANALYSING,
                          progress={"stage": "starting", "done": 0,
                                    "total": total})

            def report_progress(stage: str, done: int, total_answers: int) -> None:
                self._publish(state=ANALYSING,
                              progress={"stage": stage, "done": done,
                                        "total": total_answers})

            def report_partial(session, done: int, total: int) -> None:
                # Scores are ready before the coach's notes.
                # The report opens only when both are done; this just moves the loading screen on.
                self._publish(state=ANALYSING,
                              progress={"stage": "writing", "done": done,
                                        "total": total})

            try:
                session, result_file = self._analyser(
                    record, job_description, job_title, report_progress,
                    on_partial=report_partial)
            except NotAnAttempt as exc:
                # Every transcript was read and half or more were not answers.
                # Nothing was scored; nothing is kept.
                self._dismiss(record, reason=exc.reason,
                              not_attempted=exc.not_attempted, total=exc.total)
                return
            # Copy these onto the result, whichever analyser produced it.
            if isinstance(session, dict):
                session["prepared"] = record.get("prepared")
                session["ended_early"] = record.get("ended_early")
            self.report = session
            self.report_file = result_file or ""
            self._publish(state=REPORTED, summary=summarise(session),
                          warnings=list((session or {}).get("warnings") or []))

            # Record the history now, before the optional checks below, which stop early when another command is waiting. session_history catches its own failures.
            session_history.record(session, kind=self._session_kind,
                                   session_dir=record.get("session_dir") or "")

            # The report is shown, so now run the optional claims check.
            self._finish_deferred_claims(record)


def _so_far(session) -> List[Dict[str, str]]:
    """Each answer given so far: the question and the quick transcript of it."""
    return [{"question": str(t.get("question") or ""), "heard": str(t.get("gist") or "")}
            for t in (getattr(session, "takes", None) or []) if isinstance(t, dict)]


def _one_question(text: str):
    """A question source that asks exactly the same question again.

    A retake must use the same words as the first attempt so the answers compare.
    """
    def source(job_description: str, job_title: str, n: int):
        return (
            {
                "model": "retake",
                "job_title": job_title,
                "questions": [
                    {"index": 1, "question": text, "competency": ""}],
                "schema_valid": True,
                "repair_attempts": 0,
            },
            [],
        )
    return source


def _default_captioner(session, publish):
    """Live captions for one answer, or None when they cannot or should not run.

    None for a recorder that cannot be read mid answer, and when CALLBACK_LIVE_CAPTIONS is "0".
    Imported late so the tests never load the audio code.
    """
    if os.environ.get("CALLBACK_LIVE_CAPTIONS", "1").strip() == "0":
        return None
    listen = getattr(session, "recent_audio", None)
    if listen is None:
        return None
    import live_captions

    return live_captions.Captioner(
        listen=listen,
        transcribe=live_captions.transcribe_samples,
        publish=publish,
        samplerate=int(getattr(session, "samplerate", 16000) or 16000),
    )


def _default_factory(**kwargs):
    """The real session.
    Imported late so tests never load the audio stack.
    """
    import live_session

    return live_session.LiveSession(**kwargs)


def summarise(session) -> Optional[Dict[str, Any]]:
    """The part of a session result worth sending to the page.

    The page asks for full details only when an answer is opened, so events stay small.
    """
    if not session:
        return None
    answers = session.get("answers") or []
    scored = [
        {
            "index": a.get("index"),
            "question": a.get("question", ""),
            "score": round(float((a.get("fused") or {})
                                 .get("overall_score_0to100", 0.0)), 1),
            # The score before the relevance cap and the cap applied, so the page can show that the score was limited.
            "uncapped": (a.get("fused") or {}).get("uncapped_score_0to100"),
            # A non-answer shows no cap, because it was never judged.
            "ceiling": (None if (a.get("content") or {}).get("non_answer")
                        else (a.get("fused") or {}).get("ceiling_applied")),
            "non_answer": (a.get("content") or {}).get("non_answer"),
        }
        for a in answers
    ]
    # Averaged over the answers fusion scored, so one failed transcription does not look like a bad answer.
    overall = session_metrics.overall_score(session)
    debrief = session.get("debrief") or {}
    return {
        "job_title": session.get("job_title", ""),
        "overall": round(overall, 1) if overall is not None else 0.0,
        # Arrives after the report is first published, when the CV check has run, so the page must tolerate it being absent and then appearing.
        "cv_claims": list(debrief.get("cv_claims") or []),
        # What to practise next, or None when nothing scored low enough.
        "focus": drill.weakest(session),
        "answers": scored,
        # How many answers were not attempts, so the page can say so once rather than per card.
        "non_answers": sum(1 for a in answers
                           if (a.get("content") or {}).get("non_answer")),
        "verdict": debrief.get("verdict", ""),
        "priorities": list(debrief.get("priorities") or []),
        # Both None for sessions recorded before the interviewer could stop.
        "prepared": session.get("prepared"),
        "ended_early": session.get("ended_early"),
        "coverage": [
            {"requirement": c.get("requirement", ""),
             "evidenced": bool(c.get("evidenced")),
             "asked": bool(c.get("evidenced")) or _was_asked(c.get("requirement", ""), answers)}
            for c in (debrief.get("coverage") or [])
        ],
    }


def _was_asked(requirement: str, answers) -> bool:
    """Did any question (or its shown reason) ask about this requirement?

    A requirement nobody asked about is shown as "not asked", not as unproven.
    """
    from question_planner import _stems

    wanted = _stems(requirement)
    if not wanted:
        return True
    for a in answers or []:
        heard = _stems(f"{a.get('question', '')} {a.get('question_reason', '')}")
        if len(wanted & heard) * 2 >= len(wanted):
            return True
    return False


def _write_result(session, directory: str) -> str:
    """Save the scored session beside its recordings.
    Returns the path, or "".

    The page can redraw the full report from this file at any time.
    """
    import json
    from pathlib import Path

    if not directory:
        return ""
    target = Path(directory) / "result.json"
    try:
        target.write_text(json.dumps(session, indent=1, default=str), encoding="utf-8")
        return str(target)
    except Exception as exc:  # the scores survive without it
        logger.warning("The result could not be saved: %s", exc)
        return ""


def report_view(session):
    """The whole report as the app shows it: the summary, plus every answer with its transcript, the coach's marks and notes, and the written debrief.
    None when there is no session.
    """
    import answer_marks

    if not session:
        return None
    return {"summary": summarise(session), **(answer_marks.view(session) or {})}


def _claims_for_answer(answer) -> bool:
    """Run the postponed claims check for one answer.
    True if any claim was found.

    Updates the answer in place.
    Failures are caught and look the same as finding nothing.
    """
    import content_evaluator

    content = answer.get("content") or {}
    if content.get("disputed_claims"):
        return False
    # A refusal makes no claims about the candidate's work, so there is nothing to check and no reason to spend a model call on it.
    if content.get("non_answer"):
        return False
    # The transcript text sits at answer["speech"]["transcription"]["text"].
    transcript = ((answer.get("speech") or {})
                  .get("transcription") or {}).get("text") or ""
    if not transcript.strip():
        return False
    try:
        claims = content_evaluator.claims_for(
            transcript, answer.get("question") or "")
    except Exception as exc:  # advisory, never fatal
        logger.warning("The deferred claims check failed: %s", exc)
        return False
    if not claims:
        return False
    content["disputed_claims"] = claims
    answer["content"] = content
    return True


def _default_analyse(record, job_description: str, job_title: str, on_progress,
                     on_partial=None):
    """Score the answers and save the result.
    Returns (session, result path).

    `on_partial` receives the result as each part lands (see session_runner.run_session).
    Imported late so the tests never load the analysis code.
    The claims check runs later, after the report is shown.
    """
    import session_runner

    videos = list(record.get("video_paths") or [])
    faces = [dict(t.get("faces") or {}) for t in (record.get("takes") or [])]

    def partial(session, done, total):
        if on_partial is None:
            return
        if isinstance(session, dict):
            session["prepared"] = record.get("prepared")
            session["ended_early"] = record.get("ended_early")
        on_partial(session, done, total)

    session = session_runner.run_session(
        record["audio_paths"],
        job_description=job_description,
        job_title=job_title,
        questions=list(record.get("questions") or []),
        video_paths=videos if any(videos) else None,
        face_summaries=faces if any(faces) else None,
        on_progress=on_progress,
        defer_claims=True,
        on_partial=partial,
        # Each take's question kind and reason (question_planner), in step with the questions above: the kind picks the scoring check.
        question_kinds=[t.get("kind", "") for t in (record.get("takes") or [])],
        question_reasons=[t.get("reason", "") for t in (record.get("takes") or [])],
    )
    # Copy whether the interviewer stopped, and after how many questions, before the result is saved.
    # Older records lack both, hence .get.
    if isinstance(session, dict):
        session["prepared"] = record.get("prepared")
        session["ended_early"] = record.get("ended_early")
    return session, _write_result(session, record.get("session_dir") or "")
