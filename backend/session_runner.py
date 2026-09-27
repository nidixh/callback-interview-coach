# session_runner.py
"""Runs the analysis for a whole practice interview.

speech_analyser.analyse() handles one answer.
This module handles the session: the questions, every answer, and sharing the 4 GB graphics card.

Whisper medium uses about 2.6 GB of the card, so a language model loaded beside it would run on the CPU.
So every answer is transcribed first, Whisper is unloaded, and then the scoring runs with the card free.

Usage: python session_runner.py --job-description jd.txt answer1.wav answer2.wav
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import coach_writer
import content_evaluator
import facial_analyser
import fusion
import non_answer
import observations as observations_mod
import speech_analyser
import stage_guard
import whisper_transcriber
from schema import (
    AnswerDebrief,
    AnswerResult,
    AudioLoadError,
    ContentEvaluation,
    FacialAnalysis,
    FusedAssessment,
    QuestionSet,
    RubricScores,
    SessionDebrief,
    SessionResult,
    SpeechAnalysisResult,
)

logger = logging.getLogger(__name__)

DEFAULT_QUESTION_COUNT = 6

# Generic questions used when the language model cannot write any, so a session still runs offline.
_FALLBACK_QUESTIONS = [
    "Tell me about a challenging project you worked on and your role in it.",
    "Describe a time you had to learn something unfamiliar quickly.",
    "Tell me about a disagreement with a colleague and how you resolved it.",
    "Describe a mistake you made and what you changed afterwards.",
    "Why are you interested in this role?",
]

_EMPTY_FUSION = FusedAssessment(
    dimension_scores=[],
    overall_score_0to100=0.0,
    weights_applied={},
    weights_renormalised=False,
    cross_modal_flags=[],
    feedback_text="No assessment could be produced for this answer.",
)

_EMPTY_ANSWER_DEBRIEF = AnswerDebrief(
    looking_for="", what_you_gave="", worked=[], change=[], delivery="",
    one_thing="", source="template", quotes_dropped=0, latency_seconds=0.0,
)

_EMPTY_SESSION_DEBRIEF = SessionDebrief(
    opening="", verdict="", coverage=[], strongest_moment="", patterns=[],
    priorities=[], closing="", source="template", latency_seconds=0.0,
)

_NEUTRAL_CONTENT = ContentEvaluation(
    model="",
    scores=RubricScores(
        relevance_1to5=3, depth_1to5=3, clarity_1to5=3, structure_1to5=3
    ),
    overall_content_0to100=0.0,
    strengths=[],
    improvements=[],
    evidence_quotes=[],
    disputed_claims=[],
    schema_valid=False,
    repair_attempts=0,
    latency_seconds=0.0,
    non_answer=None,
)


# The warning stage_guard.run_stage records when the Whisper stage raises, as speech_analyser names it.
# Matched by prefix because the exception text follows.
_TRANSCRIPTION_FAILED_PREFIX = "Stage 'transcription' failed"


def _refuse_if_not_an_attempt(speech_results, question_texts, job_description) -> None:
    """Raise NotAnAttempt when half or more of the transcribed answers are non-answers.
    Answers with no speech result, or whose transcription stage failed, are left out of both counts.
    """
    counted = hits = 0
    for idx, speech in enumerate(speech_results):
        if speech is None:
            continue
        transcript = speech["transcription"]["text"]
        if not transcript.strip() and any(
                w.startswith(_TRANSCRIPTION_FAILED_PREFIX)
                for w in speech["processing"]["warnings"]):
            continue
        counted += 1
        question = question_texts[idx] if idx < len(question_texts) else ""
        if non_answer.classify(
                transcript, question, job_description,
                duration_seconds=float(speech.get("audio_duration_seconds") or 0.0)):
            hits += 1
    if counted and hits * 2 >= counted:
        raise non_answer.NotAnAttempt(hits, counted)


def _fresh_neutral_content() -> ContentEvaluation:
    """A neutral ContentEvaluation with its own copy of `scores`, so no two answers share one dict."""
    fresh = dict(_NEUTRAL_CONTENT)
    fresh["scores"] = dict(_NEUTRAL_CONTENT["scores"])
    return fresh  # type: ignore[return-value]


def _fallback_question_set(job_title: str, n: int) -> QuestionSet:
    """n generic questions, repeating the bank if more are needed.

    Always returns exactly n, because answers are matched to questions by position.
    """
    from schema import InterviewQuestion

    bank = _FALLBACK_QUESTIONS
    return QuestionSet(
        model="fallback",
        job_title=job_title,
        questions=[
            InterviewQuestion(
                index=i + 1, question=bank[i % len(bank)], competency="general"
            )
            for i in range(max(n, 0))
        ],
        schema_valid=False,
        repair_attempts=0,
    )


def _supplied_question_set(job_title: str, questions: Sequence[str],
                           kinds: Optional[Sequence[str]] = None,
                           reasons: Optional[Sequence[str]] = None) -> QuestionSet:
    """Wrap question text from the caller as a QuestionSet.

    Not limited to the size of the built in bank, because the live interview adds follow-up questions to the list.
    """
    from schema import InterviewQuestion

    kinds, reasons = list(kinds or []), list(reasons or [])
    return QuestionSet(
        model="supplied",
        job_title=job_title,
        questions=[
            InterviewQuestion(index=i + 1, question=str(text), competency="",
                              kind=str(kinds[i]) if i < len(kinds) else "",
                              reason=str(reasons[i]) if i < len(reasons) else "")
            for i, text in enumerate(questions)
        ],
        schema_valid=True,
        repair_attempts=0,
    )


def _fill_from_generator(planned: QuestionSet, job_description: str, job_title: str, n: int,
                         warnings: List[str]) -> QuestionSet:
    """Top up a short plan to n with questions written for this job, before using the generic bank."""
    from schema import InterviewQuestion

    have = list(planned["questions"])
    short = n - len(have)
    if short <= 0:
        return planned
    extra, warns = content_evaluator.generate_questions(job_description, job_title, n)
    warnings.extend(warns)
    asked = {q["question"].strip().lower() for q in have}
    fill = [q for q in extra["questions"] if q["question"].strip().lower() not in asked][:short]
    merged = have + [InterviewQuestion(index=0, question=q["question"], competency=q.get("competency", ""))
                     for q in fill]
    for i, q in enumerate(merged, 1):
        q["index"] = i
    return QuestionSet(**{**planned, "questions": merged})


def _planner_on() -> bool:
    """CALLBACK_QUESTION_PLANNER=0 restores the unplanned generator, no code change."""
    value = os.environ.get("CALLBACK_QUESTION_PLANNER", "1").strip().lower()
    return value not in ("0", "off", "no", "false")


def prepare_questions(
    job_description: str, job_title: str = "", n: int = DEFAULT_QUESTION_COUNT,
    cv_text: Optional[str] = None,
) -> tuple[QuestionSet, List[str]]:
    """The interview questions: planned for this role and CV, else written by the model, else the generic bank.

    A session that cannot reach the language model still runs.
    `cv_text` None means the saved CV.
    """
    warnings: List[str] = []
    question_set: Optional[QuestionSet] = None
    if _planner_on():
        import candidate_cv
        import question_planner

        cv = candidate_cv.load() if cv_text is None else cv_text
        planned, warns = question_planner.prepare(job_description, job_title, cv, n)
        if planned["questions"]:
            question_set = _fill_from_generator(planned, job_description, job_title, n, warnings)
        warnings.extend(warns)
    if question_set is None:
        question_set, warns = content_evaluator.generate_questions(
            job_description, job_title, n
        )
        warnings.extend(warns)
    if not question_set["questions"]:
        warnings.append(
            "Falling back to the built-in question bank because no questions "
            "could be generated."
        )
        question_set = _fallback_question_set(job_title, n)
    elif len(question_set["questions"]) < n:
        # If the model wrote fewer questions than asked, top up from the bank, skipping repeats.
        from schema import InterviewQuestion

        have = question_set["questions"]
        asked = {q["question"].strip().lower() for q in have}
        spare = [q for q in _FALLBACK_QUESTIONS if q.strip().lower() not in asked]
        added = [InterviewQuestion(index=len(have) + k + 1, question=spare[k % len(spare)], competency="general")
                 for k in range(n - len(have))] if spare else []
        question_set = QuestionSet(**{**question_set, "questions": list(have) + added})
        if added:
            warnings.append(f"Added {len(added)} question(s) from the built-in bank to make up {n}.")
    return question_set, warnings


def run_session(
    audio_paths: Sequence,
    job_description: str = "",
    job_title: str = "",
    questions: Optional[Sequence[str]] = None,
    video_paths: Optional[Sequence] = None,
    face_summaries: Optional[Sequence] = None,
    release_gpu_between_phases: bool = True,
    concurrent: bool = True,
    write_narrative: bool = True,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
    defer_claims: bool = False,
    on_partial: Optional[Callable[[SessionResult, int, int], None]] = None,
    question_kinds: Optional[Sequence[str]] = None,
    question_reasons: Optional[Sequence[str]] = None,
) -> SessionResult:
    """Analyse a full session, one recorded answer per question.

    Transcribes every answer first, then frees the graphics card for scoring, then writes the coaching notes last.
    `write_narrative=False` skips the notes (used for timing tests).

    `on_partial(session, done, total)` receives a copy of the result as each part lands: when all answers are scored, when the session debrief is written, then after each answer debrief.
    """
    session_warnings: List[str] = []
    session_latency: Dict[str, float] = {}
    t_session = time.perf_counter()

    def progress(stage: str, done: int, total: int) -> None:
        """Report progress to whatever is watching.

        A listener that raises is ignored, so progress can never break the run.
        """
        if on_progress is None:
            return
        try:
            on_progress(stage, done, total)
        except Exception as exc:  # watching must not break running
            logger.debug("A progress listener failed: %s", exc)

    def hand_over(done: int, total: int) -> None:
        """Give a listener the result so far.
        Same discipline as progress().
        """
        if on_partial is None:
            return
        snapshot = _result(
            job_title, question_set,
            [AnswerResult(**dict(a, warnings=list(a["warnings"])))  # type: ignore[misc]
             for a in answers],
            dict(session_debrief), list(session_warnings), dict(session_latency))
        try:
            on_partial(snapshot, done, total)
        except Exception as exc:
            # A reader must not break the run.
            logger.debug("A partial-result listener failed: %s", exc)

    # Questions
    if questions is not None:
        question_set = _supplied_question_set(job_title, questions, question_kinds, question_reasons)
    else:
        t0 = time.perf_counter()
        question_set, warns = prepare_questions(
            job_description, job_title, max(len(audio_paths), 1)
        )
        session_latency["question_generation"] = round(time.perf_counter() - t0, 3)
        session_warnings.extend(warns)

    question_texts = [q["question"] for q in question_set["questions"]]
    # What kind of question each answer answered, and why it was asked (question_planner).
    # Picks the scoring check and is kept on the answer.
    kinds_by_index = [q.get("kind", "") or "" for q in question_set["questions"]]
    reasons_by_index = [q.get("reason", "") or "" for q in question_set["questions"]]

    # Phase 1: transcription and speech analysis on the graphics card.
    # Unload the language models first, because transcribing beside them is about twice as slow.
    if release_gpu_between_phases:
        t0 = time.perf_counter()
        freed = content_evaluator.unload_all()
        session_latency["ollama_handoff"] = round(time.perf_counter() - t0, 3)
        if freed:
            logger.info("Unloaded %s before transcribing", ", ".join(freed))
    t_phase1 = time.perf_counter()
    speech_results: List[Optional[SpeechAnalysisResult]] = []
    for idx, path in enumerate(audio_paths):
        progress("transcribing", idx, len(audio_paths))
        try:
            speech = speech_analyser.analyse(path)
        except AudioLoadError as exc:
            # The single fatal condition inside an answer.
            # The session continues so one unreadable recording does not discard the whole interview.
            session_warnings.append(
                f"Answer {idx + 1} could not be analysed because its audio could "
                f"not be read ({exc}); it was skipped."
            )
            speech = None
        speech_results.append(speech)
    session_latency["phase1_speech"] = round(time.perf_counter() - t_phase1, 3)

    # Device handoff
    if release_gpu_between_phases:
        t0 = time.perf_counter()
        reclaimed = whisper_transcriber.release()
        session_latency["gpu_handoff"] = round(time.perf_counter() - t0, 3)
        logger.info("Handed the GPU over, reclaiming %.2f GB", reclaimed / 1024 ** 3)

    # Was this an attempt at all?
    # Decided before any model call, using the same threshold as coach_writer.
    # A failed transcription is not counted either way.
    _refuse_if_not_an_attempt(speech_results, question_texts, job_description)

    # Phase 2: content scoring, language-model bound
    t_phase2 = time.perf_counter()
    answers: List[AnswerResult] = []
    for idx, (path, speech) in enumerate(zip(audio_paths, speech_results)):
        progress("scoring", idx, len(audio_paths))
        question = question_texts[idx] if idx < len(question_texts) else ""
        kind = kinds_by_index[idx] if idx < len(kinds_by_index) else ""
        reason = reasons_by_index[idx] if idx < len(reasons_by_index) else ""
        answer_warnings: List[str] = []
        answer_latency: Dict[str, float] = {}

        video = (
            video_paths[idx] if video_paths and idx < len(video_paths) else None
        )
        # What the camera measured during this answer.
        # Absent for older sessions.
        measured = (
            face_summaries[idx]
            if face_summaries and idx < len(face_summaries) else None
        )

        if speech is None:
            content: ContentEvaluation = _fresh_neutral_content()
            facial: FacialAnalysis = facial_analyser._empty()
            answer_warnings.append(
                "The answer was not analysed, so it was not scored for content."
            )
        else:
            answer_warnings.extend(speech["processing"]["warnings"])
            transcript = speech["transcription"]["text"]

            # Each stage times itself inside its worker thread, because timing the wait on the main thread would make the second stage look almost instant.
            def _score_content(t=transcript, q=question, s=speech, k=kind):
                # An empty transcript after a failed transcription is the system's fault, so the neutral fallback is used instead of scoring it as a non-answer.
                if not t.strip() and any(
                        w.startswith(_TRANSCRIPTION_FAILED_PREFIX)
                        for w in s["processing"]["warnings"]):
                    return (_fresh_neutral_content(), [
                        "Transcription failed, so this answer was not "
                        "scored for content."]), 0.0
                t0 = time.perf_counter()
                result = content_evaluator.evaluate(
                    t, q, job_description, check_claims=not defer_claims,
                    duration_seconds=float(s.get("audio_duration_seconds") or 0.0),
                    question_kind=k or None)
                return result, time.perf_counter() - t0

            def _score_face(v=video, m=measured):
                t0 = time.perf_counter()
                if m:
                    # Already known: the camera worker detected the face while the answer was happening, so there is nothing to re-run.
                    result = (facial_analyser.from_live(m), [])
                elif v is None:
                    result = (facial_analyser._empty(), [])
                else:
                    result = facial_analyser.analyse_video(v)
                return result, time.perf_counter() - t0

            # These two stages mostly wait on other processes, so running them side by side really saves time.
            if concurrent and video is not None and not measured:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    f_content = pool.submit(_score_content)
                    f_face = pool.submit(_score_face)
                    content_result, content_elapsed = stage_guard.run_stage(
                        "content_evaluation", f_content.result, answer_warnings,
                        answer_latency, ((_fresh_neutral_content(), []), 0.0),
                    )
                    facial_result, facial_elapsed = stage_guard.run_stage(
                        "facial_analysis", f_face.result, answer_warnings,
                        answer_latency, ((facial_analyser._empty(), []), 0.0),
                    )
            else:
                content_result, content_elapsed = stage_guard.run_stage(
                    "content_evaluation", _score_content, answer_warnings,
                    answer_latency, ((_fresh_neutral_content(), []), 0.0),
                )
                facial_result, facial_elapsed = stage_guard.run_stage(
                    "facial_analysis", _score_face, answer_warnings,
                    answer_latency, ((facial_analyser._empty(), []), 0.0),
                )

            # Replace the wait times recorded by stage_guard with each stage's real duration.
            answer_latency["content_evaluation"] = round(content_elapsed, 3)
            answer_latency["facial_analysis"] = round(facial_elapsed, 3)

            content, content_warnings = content_result
            facial, facial_warnings = facial_result
            answer_warnings.extend(content_warnings)
            answer_warnings.extend(facial_warnings)

        fused: FusedAssessment = stage_guard.run_stage(
            "fusion",
            lambda s=speech, c=content, f=facial: fusion.fuse(
                s if s is not None else {}, c, f
            ),
            answer_warnings,
            answer_latency,
            _EMPTY_FUSION,
        )

        # Materiality gating.
        # Pure and local, so it is not worth a stage guard of its own; it cannot reach a model, a file or the network.
        observed = observations_mod.for_answer(speech, facial, content)

        answers.append(
            AnswerResult(
                index=idx + 1,
                question=question,
                audio_file=str(path),
                video_file=str(video) if video else "",
                speech=speech if speech is not None else {},  # type: ignore[arg-type]
                content=content,
                facial=facial,
                fused=fused,
                observations=observed,
                debrief=dict(_EMPTY_ANSWER_DEBRIEF),  # type: ignore[arg-type]
                warnings=answer_warnings,
                latency_seconds=answer_latency,
                question_kind=kind,
                question_reason=reason,
            )
        )
    session_latency["phase2_content"] = round(time.perf_counter() - t_phase2, 3)

    # Phase 2b: a second model's view of what each answer proved.
    # Asked in one batch so the model loads once.
    # Runs before the coaching notes so they use the final scores.
    progress("checking", len(audio_paths), len(audio_paths))
    stage_guard.run_stage(
        "second_opinion",
        lambda: _apply_second_opinions(answers, job_description),
        session_warnings, session_latency, None,
    )

    # Phase 3: the coaching notes, written last because they need every other result.
    # Optional.
    session_debrief: SessionDebrief = dict(_EMPTY_SESSION_DEBRIEF)  # type: ignore[assignment]
    progress("writing", len(audio_paths), len(audio_paths))
    pieces = 1 + len(answers)
    hand_over(0, pieces)
    if write_narrative:
        t_phase3 = time.perf_counter()
        # The session debrief first, since it is what a reader looks for before the notes on each answer.
        session_debrief, session_narrative_warnings = stage_guard.run_stage(
            "session_debrief",
            lambda: coach_writer.write_session_debrief(
                answers, job_title, job_description
            ),
            session_warnings,
            session_latency,
            (dict(_EMPTY_SESSION_DEBRIEF), []),
        )
        session_warnings.extend(session_narrative_warnings)
        hand_over(1, pieces)

        for n, answer in enumerate(answers, start=2):
            transcript = (
                (answer["speech"] or {}).get("transcription", {}).get("text", "")
            )
            debrief, narrative_warnings = stage_guard.run_stage(
                "coach_narrative",
                lambda t=transcript, a=answer: coach_writer.write_answer_debrief(
                    t, a["question"], a["content"], a["observations"],
                    (a["fused"] or {}).get("overall_score_0to100", 0.0),
                    job_description,
                ),
                answer["warnings"],
                answer["latency_seconds"],
                (dict(_EMPTY_ANSWER_DEBRIEF), []),
            )
            answer["debrief"] = debrief
            answer["warnings"].extend(narrative_warnings)
            hand_over(n, pieces)
        session_latency["phase3_narrative"] = round(time.perf_counter() - t_phase3, 3)

    session_latency["total"] = round(time.perf_counter() - t_session, 3)

    return _result(job_title, question_set, answers, session_debrief,
                   session_warnings, session_latency)


def _apply_second_opinions(answers, job_description: str, opinions=None) -> None:
    """Average each answer's proof with the second model's, and re-fuse it."""
    opinions = opinions or content_evaluator.second_opinions
    items = []
    for answer in answers:
        content = answer.get("content") or {}
        transcript = ((answer.get("speech") or {}).get("transcription") or {}).get("text", "")
        items.append((transcript, answer.get("question", ""))
                     if content.get("proof") and not content.get("non_answer") and transcript.strip() else None)
    if not any(items):
        return
    levels = opinions(items, job_description)
    for answer, item, level in zip(answers, items, levels):
        if item is None or level is None:
            continue
        answer["content"]["proof"] = content_evaluator.combine_proof(answer["content"]["proof"], level)
        answer["fused"] = fusion.fuse(answer.get("speech") or {}, answer["content"], answer.get("facial"))


def _result(job_title, question_set, answers, debrief, warnings, latency) -> SessionResult:
    return SessionResult(
        job_title=job_title,
        question_set=question_set,
        answers=answers,
        debrief=debrief,
        warnings=warnings,
        latency_seconds=latency,
    )


def _print_summary(session: SessionResult) -> None:
    """Print a readable summary of a scored session (command-line use)."""
    print("=" * 70)
    print(f"Session: {session['job_title'] or '(no job title)'}")
    print(f"Questions: {len(session['question_set']['questions'])} "
          f"(source: {session['question_set']['model']})")
    print("-" * 70)
    for ans in session["answers"]:
        speech = ans["speech"]
        transcript = speech.get("transcription", {}).get("text", "") if speech else ""
        fillers = speech.get("fillers", {}).get("total_count", 0) if speech else 0
        scores = ans["content"]["scores"]
        fused = ans.get("fused") or {}
        facial = ans.get("facial") or {}
        print(f"\nQ{ans['index']}: {ans['question'][:66]}")
        print(f"  transcript : {transcript[:66]}")
        print(f"  fillers    : {fillers}")
        print(f"  content    : {ans['content']['overall_content_0to100']}/100  {scores}")
        if facial.get("frames_sampled"):
            print(f"  facial     : {facial['dominant_expression']} "
                  f"(face in {facial['face_detection_rate_0to1']:.0%} of frames, "
                  f"reliable={facial['reliable']})")
        print(f"  OVERALL    : {fused.get('overall_score_0to100', 0)}/100 "
              f"weights={fused.get('weights_applied', {})}")
        for flag in fused.get("cross_modal_flags", []):
            print(f"  [{'/'.join(flag['domains'])}] {flag['detail'][:80]}")
        if ans["warnings"]:
            print(f"  warnings   : {len(ans['warnings'])}")
    print("-" * 70)
    print("Session latency (s):")
    for k, v in session["latency_seconds"].items():
        print(f"    {k:<20}: {v}")
    if session["warnings"]:
        print("Session warnings:")
        for w in session["warnings"]:
            print(f"    - {w}")
    print("=" * 70)


def main(argv: List[str]) -> int:
    """Score recorded answers from the command line, without the app, and save the result as JSON."""
    import warnings as _warnings

    _warnings.filterwarnings("ignore")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.disable(logging.WARNING)

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("audio", nargs="+", help="recorded answer audio files, in order")
    parser.add_argument("--job-description", default="", help="path to a job description text file")
    parser.add_argument("--job-title", default="", help="job title")
    parser.add_argument("--video", nargs="*", default=None,
                        help="recorded answer video files, in the same order as the audio")
    parser.add_argument("--no-gpu-handoff", action="store_true",
                        help="keep Whisper resident between phases (for measuring the handoff)")
    parser.add_argument("--out", default="session_result_full.json")
    args = parser.parse_args(argv[1:])

    jd = ""
    if args.job_description:
        jd_path = Path(args.job_description)
        if jd_path.exists():
            jd = jd_path.read_text(encoding="utf-8")
        else:
            jd = args.job_description  # allow the text inline

    session = run_session(
        args.audio,
        job_description=jd,
        job_title=args.job_title,
        video_paths=args.video,
        release_gpu_between_phases=not args.no_gpu_handoff,
    )
    _print_summary(session)
    out_path = Path(args.out)
    out_path.write_text(json.dumps(session, indent=2), encoding="utf-8")
    print(f"Full session written to {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
