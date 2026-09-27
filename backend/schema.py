# schema.py
"""The data shapes passed between the pipeline stages.

Kept in one module so the stages can share them without circular imports.
TypedDicts are plain dicts at run time, so they save straight to JSON.
"""

from __future__ import annotations

from typing import Any, Dict, List, NotRequired, Optional, TypedDict


class AudioLoadError(Exception):
    """Raised when an audio file is missing, empty or cannot be read.

    Every loading problem becomes this one exception, so callers only catch one thing.
    """


class WordTimestamp(TypedDict):
    word: str
    start: float
    end: float
    probability: float


class TranscriptionSegment(TypedDict):
    id: int
    start: float
    end: float
    text: str


class Transcription(TypedDict):
    text: str
    language: str
    segments: List[TranscriptionSegment]
    word_timestamps: List[WordTimestamp]


class Prosody(TypedDict):
    speaking_rate_syll_per_sec: float
    pitch_mean_hz: float
    pitch_std_hz: float
    voiced_fraction: float
    energy_rms_mean: float
    energy_rms_std: float
    zero_crossing_rate_mean: float
    duration_seconds: float


class SpeakerEmbedding(TypedDict):
    model: str
    dimension: int
    vector: List[float]


class FillerInstance(TypedDict):
    filler: str
    start: float
    end: float
    # "asr" = matched in transcript; "gap" = voiced gap Whisper suppressed.
    source: str


class Fillers(TypedDict):
    total_count: int
    per_minute: float
    counts_by_filler: Dict[str, int]
    instances: List[FillerInstance]


class PauseInstance(TypedDict):
    start: float
    end: float
    duration: float
    kind: str  # "natural" | "noticeable" | "dead_air"


class Pauses(TypedDict):
    """Silent gaps in an answer.

    Kept apart from fillers, which are the sounds people make instead of pausing.
    `time_to_first_word_seconds` is kept apart from the pause list, because a pause before starting reads differently from one in the middle.
    """

    total_count: int
    per_minute: float
    longest_seconds: float
    mean_seconds: float
    total_silent_seconds: float
    silent_fraction_0to1: float
    time_to_first_word_seconds: float
    counts_by_kind: Dict[str, int]
    instances: List[PauseInstance]


class InterviewQuestion(TypedDict):
    index: int
    question: str
    # The role competency the question is intended to probe.
    competency: str
    # From question_planner: which kind of question this is, and the plain sentence saying why it was asked.
    # Absent on generated or bank questions.
    kind: NotRequired[str]
    reason: NotRequired[str]


class QuestionSet(TypedDict):
    model: str
    job_title: str
    questions: List[InterviewQuestion]
    schema_valid: bool
    repair_attempts: int


class RubricScores(TypedDict):
    """Scores from 1 to 5 for each rubric dimension.

    Each dimension is scored separately (Zheng et al., 2023).
    """

    relevance_1to5: int
    depth_1to5: int
    clarity_1to5: int
    # STAR completeness: situation, task, action, result.
    structure_1to5: int


class ContentEvaluation(TypedDict):
    """What the content check returns for one answer: rubric scores, the overall score, and the evidence."""
    model: str  # e.g. "qwen3:4b"
    scores: RubricScores
    overall_content_0to100: float
    strengths: List[str]
    improvements: List[str]
    # Spans quoted back from the transcript.
    evidence_quotes: List[str]
    # Technical statements that did not hold up: the candidate's words, what is wrong, and what is correct.
    # Usually empty.
    disputed_claims: List[Dict[str, str]]
    schema_valid: bool
    repair_attempts: int
    latency_seconds: float
    # Set when the answer was not an attempt (a refusal, abuse, or off topic), decided before any model call.
    # Read with .get(), because older results lack it.
    non_answer: Optional[Dict[str, str]]
    # What the answer proved to a recruiter, 0 to 100 (content_evaluator._proof):
    # {"score_0to100", "basis", "practical", "asked_for", "supplied"}.
    # None for non-answers, failed evaluations and older results.
    proof: Optional[Dict[str, Any]]


class FacialAnalysis(TypedDict):
    """Basic facial observations for one answer.

    These describe what was visible, not how the candidate felt (Barrett et al., 2019).
    When too few frames show a face, `reliable` is False and fusion leaves the channel out.
    """

    model: str  # e.g. "deepface-fer" or "opencv-haar-frontalface"
    frames_sampled: int
    frames_with_face: int
    face_detection_rate_0to1: float
    # "unavailable" when the backend cannot classify.
    dominant_expression: str
    expression_mean_probabilities: Dict[str, float]
    gaze_centre_stability_0to1: float
    sample_rate_fps: float
    reliable: bool
    latency_seconds: float
    # Live camera measures from the Face Landmarker (vision/gaze.summarise): eye_contact_0to1, look_aways, longest_look_away_s, head_motion_deg_s, head_steadiness_0to1, framing, lighting, expressiveness_0to1 (shown, never scored), timeline and calibrated.
    # None when only the presence check ran; read with .get().
    gaze: Optional[Dict[str, Any]]


class Observation(TypedDict):
    """One point about an answer worth telling the candidate.

    Only created when a value is outside the normal range: `notable` for a problem, `strength` for something notably good.
    `headline` and `detail` are the coach's words.
    `measurement` holds the number behind it.
    """

    kind: str
    severity: str  # "strength" | "minor" | "notable"
    headline: str
    detail: str
    measurement: str
    domain: str  # "audio" | "text" | "image"


class DimensionScore(TypedDict):
    """One scored dimension on the common 0 to 100 scale."""

    name: str
    raw_value: float
    normalised_0to100: float
    weight_applied: float
    included: bool
    exclusion_reason: str


class CrossModalFlag(TypedDict):
    """A disagreement between two channels, such as confident voice but weak content.

    Only possible with evidence from both.
    """

    kind: str
    detail: str
    severity: str  # "info" or "warn"
    domains: List[str]  # e.g. ["text", "audio"]


class FusedAssessment(TypedDict):
    """The channels combined, and what the relevance cap did to the result.

    `overall_score_0to100` is the score, capped when the answer did not address the question.
    `uncapped_score_0to100` is the score before the cap, so a retake can still show improvement.
    `ceiling_applied` is the cap used, or None when there was none.
    """

    dimension_scores: List[DimensionScore]
    overall_score_0to100: float
    uncapped_score_0to100: float
    ceiling_applied: Optional[float]
    # Proof first (fusion.PROOF_FLOOR): the total is proof adjusted by presentation.
    # Either part is None when not measured.
    proof_0to100: Optional[float]
    presentation_0to100: Optional[float]
    weights_applied: Dict[str, float]
    weights_renormalised: bool
    cross_modal_flags: List[CrossModalFlag]
    feedback_text: str


class FollowUpDecision(TypedDict):
    """Whether the interviewer should press further, and with what.

    `source` records whether the model or the fixed wording was used.
    """

    ask: bool
    question: str
    trigger: str
    source: str
    template_id: str
    latency_seconds: float
    warnings: List[str]


class LivePrompt(TypedDict):
    """One thing the interviewer is about to say.

    A follow-up carries the number of the main question it belongs to.
    """

    text: str
    is_follow_up: bool
    # Why the probe was asked; empty for a prepared question.
    trigger: str
    # Which tier wrote the wording; see FollowUpDecision.source.
    source: str
    question_number: int
    total_questions: int
    # Carried from the prepared question; "follow_up" for a probe.
    kind: NotRequired[str]
    reason: NotRequired[str]


class LiveTake(TypedDict):
    """One recorded answer, and what it was an answer to."""

    # 1-based, matching the file names on disk.
    take: int
    audio_path: str
    # Always None.
    # Video is watched live and never saved.
    # Kept so older sessions still load.
    video_path: Optional[str]
    # What the camera saw during this answer: how long the candidate was visible and how steady they were.
    faces: Dict[str, float]
    question: str
    is_follow_up: bool
    trigger: str
    # Kept so a session can be audited for how often the model tier fired.
    source: str
    duration_seconds: float
    # The fast transcript used to decide on a probe, not the real one.
    gist: str
    # What the interviewer made of this answer during the interview: "attempt", "non_answer", or "unknown".
    # "" for takes that were not judged.
    attempt: str
    # Carried from the prompt answered; "follow_up" for a probe.
    kind: NotRequired[str]
    reason: NotRequired[str]


class LiveSessionRecord(TypedDict):
    """What a finished live session hands to the analysis.

    The three lists line up by position and go straight to run_session.
    """

    session_dir: str
    audio_paths: List[str]
    video_paths: List[Optional[str]]
    questions: List[str]
    takes: List[LiveTake]
    warnings: List[str]
    # How many questions were written for the session, against len(takes) that were actually answered.
    # None when the record predates this field.
    prepared: Optional[int]
    # Set when the interviewer stopped the session: {"after": takes so far, "prepared": questions written, "reason": one sentence}.
    # None otherwise.
    ended_early: Optional[Dict[str, Any]]
    # True only when every question was reached and answered.
    # An interview the candidate ended early is not graded.
    completed: bool


class CoachPraise(TypedDict):
    quote: str  # the candidate's exact words
    why: str


class CoachRewrite(TypedDict):
    """One thing to change, with a replacement sentence.

    `try_instead` is a suggested sentence the candidate could say, not something they said, and is always shown as a suggestion.
    """

    quote: str  # verbatim from the answer
    problem: str
    # NOT from the answer: a suggested replacement.
    try_instead: str
    why: str


class AnswerDebrief(TypedDict):
    """What a coach says to a candidate about one answer."""

    looking_for: str
    what_you_gave: str
    worked: List[CoachPraise]
    change: List[CoachRewrite]
    # Written from Observations, not by the model.
    delivery: str
    one_thing: str
    source: str  # "model" | "template"
    # Failed the verbatim check and were discarded.
    quotes_dropped: int
    latency_seconds: float


class JobRequirementCoverage(TypedDict):
    """Whether one requirement from the advert was shown in the answers.

    `quote` must be the candidate's own words from the transcript.
    A claim whose quote cannot be found is downgraded.
    """

    requirement: str  # quoted from the job description
    evidenced: bool
    # E.g.
    # "answer 2", empty when not evidenced.
    where: str
    # Verbatim from the answer; empty when not evidenced.
    quote: str
    note: str


class CvClaim(TypedDict):
    """One claim on the CV, and whether the candidate backed it up.

    Same rules as JobRequirementCoverage: the quote must be found in the transcripts, or the claim is downgraded.
    """

    claim: str  # quoted from the CV
    evidenced: bool
    # E.g.
    # "answer 2", empty when never.
    where: str
    # Verbatim from the answers; empty when not evidenced.
    quote: str
    note: str


class SessionDebrief(TypedDict):
    """The end-of-session conversation: verdict, coverage, priorities."""

    opening: str
    verdict: str
    coverage: List[JobRequirementCoverage]
    strongest_moment: str
    # Written from Observations across every answer.
    patterns: List[str]
    priorities: List[str]
    closing: str
    source: str  # "model" | "template"
    latency_seconds: float


class AnswerResult(TypedDict):
    """One interview question and the multimodal analysis of its answer."""

    index: int
    question: str
    audio_file: str
    video_file: str
    speech: "SpeechAnalysisResult"
    content: ContentEvaluation
    facial: FacialAnalysis
    fused: FusedAssessment
    observations: List[Observation]
    debrief: AnswerDebrief
    warnings: List[str]
    latency_seconds: Dict[str, float]
    # The kind of question answered and why it was asked (question_planner).
    # Empty for sessions before the planner, and for bank questions.
    question_kind: NotRequired[str]
    question_reason: NotRequired[str]


class SessionResult(TypedDict):
    """A complete practice interview: the questions asked and every answer."""

    job_title: str
    question_set: QuestionSet
    answers: List[AnswerResult]
    debrief: SessionDebrief
    warnings: List[str]
    latency_seconds: Dict[str, float]


class Processing(TypedDict):
    device: str
    latency_seconds: Dict[str, float]
    warnings: List[str]


class SpeechAnalysisResult(TypedDict):
    audio_file: str
    audio_duration_seconds: float
    transcription: Transcription
    prosody: Prosody
    speaker_embedding: SpeakerEmbedding
    fillers: Fillers
    pauses: Pauses
    processing: Processing
