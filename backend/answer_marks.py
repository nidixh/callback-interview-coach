# answer_marks.py
"""The marks shown on each answer's transcript, and the report view built around them.

Every mark comes from results the pipeline already produced, and no model is called here.
The kinds of mark are: what worked (highlight), what to change (underline with a suggested sentence), a disputed claim (wavy underline with the correction), proof of a requirement or CV claim, and filler words.

A quote that cannot be found in the transcript is dropped, because a mark on the wrong words is worse than none.
When two marks overlap, the more useful one is kept.
Look-aways from the camera sit in a separate layer above the words.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

__all__ = ["for_answer", "gaze_marks", "presence", "unplaced", "view"]

# Which mark survives an overlap.
# A thing to change outranks a thing that worked on the same words: the candidate can do something about it.
_PRIORITY = {"change": 0, "claim": 1, "worked": 2, "proves": 3, "filler": 4, "gap": 5}
_CIRCLE_MAX_WORDS = 5
_WORD = re.compile(r"[\w']+")


def _find(transcript: str, quote: str, taken_from: int = 0):
    """(start, end) of `quote` in `transcript`, ignoring case, spacing and punctuation between words; None when it is not there."""
    words = _WORD.findall(quote or "")
    if not words:
        return None
    pattern = r"(?<![\w'])" + r"[^\w']+".join(re.escape(w) for w in words) + r"(?![\w'])"
    m = re.compile(pattern, re.IGNORECASE).search(transcript, taken_from)
    return (m.start(), m.end()) if m else None


def _answers_named(where: str) -> List[int]:
    """The answer numbers a coverage line points at: "answer 2", "answers 1 and 3"."""
    return [int(n) for n in re.findall(r"\d+", where or "")]


def _word_spans(transcript: str, words: List[Dict[str, Any]]):
    """Each timed word located in the transcript, in order: (start, end, t0, t1)."""
    spans, cursor = [], 0
    for w in words or []:
        text = str(w.get("word", "")).strip()
        core = _WORD.findall(text)
        if not core:
            continue
        hit = _find(transcript, " ".join(core), cursor)
        if not hit:
            continue
        # Keep the punctuation the transcript attached to the word ("Um,").
        end = hit[1]
        while end < len(transcript) and transcript[end] in ",.;:!?":
            end += 1
        spans.append((hit[0], end, float(w.get("start", 0.0)), float(w.get("end", 0.0))))
        cursor = end
    return spans


def _filler_marks(answer: Dict[str, Any], transcript: str) -> List[Dict[str, Any]]:
    """A filler mark for each filler word the detector timed, placed on the matching words."""
    speech = answer.get("speech") or {}
    instances = ((speech.get("fillers") or {}).get("instances") or [])
    if not instances:
        return []
    spans = _word_spans(transcript, (speech.get("transcription") or {}).get("word_timestamps") or [])
    marks = []
    for f in instances:
        t0, t1 = float(f.get("start", 0.0)), float(f.get("end", 0.0))
        if f.get("source") == "gap":
            before = [s for s in spans if s[3] <= t0 + 1e-6]
            at = before[-1][1] if before else 0
            marks.append({"start": at, "end": at, "kind": "gap", "style": "point",
                          "label": str(f.get("filler", "")).strip("[]"), "note": ""})
            continue
        hit = [s for s in spans if s[2] < t1 and s[3] > t0]
        if hit:
            marks.append({"start": hit[0][0], "end": hit[-1][1], "kind": "filler",
                          "style": "dash", "label": str(f.get("filler", "")), "note": ""})
    return marks


def for_answer(answer: Dict[str, Any], session_debrief: Optional[Dict[str, Any]] = None
               ) -> List[Dict[str, Any]]:
    """The marks on one answer, in reading order, each with its note."""
    if not answer or (answer.get("content") or {}).get("non_answer"):
        return []
    transcript = ((answer.get("speech") or {}).get("transcription") or {}).get("text") or ""
    if not transcript.strip():
        return []
    debrief = answer.get("debrief") or {}
    index = int(answer.get("index") or 0)
    found: List[Dict[str, Any]] = []

    def place(quote, **mark):
        hit = _find(transcript, quote)
        if hit:
            found.append({"start": hit[0], "end": hit[1], **mark})

    for c in debrief.get("change") or []:
        note = " ".join(x for x in (str(c.get("problem") or "").strip(), str(c.get("why") or "").strip()) if x)
        place(c.get("quote"), kind="change", style="underline", note=note,
              try_instead=str(c.get("try_instead") or "").strip())
    for c in (answer.get("content") or {}).get("disputed_claims") or []:
        note = " ".join(x for x in (str(c.get("problem") or "").strip(),
                                    ("Actually: " + str(c.get("correction")).strip()) if c.get("correction") else "") if x)
        place(c.get("quote"), kind="claim", style="wavy", note=note)
    for w in debrief.get("worked") or []:
        quote = str(w.get("quote") or "")
        style = "circle" if len(_WORD.findall(quote)) <= _CIRCLE_MAX_WORDS else "highlight"
        place(quote, kind="worked", style=style, note=str(w.get("why") or "").strip())
    for key, field, lead in (("coverage", "requirement", "Proves what the advert asks for: "),
                             ("cv_claims", "claim", "Backs up your CV: ")):
        for item in (session_debrief or {}).get(key) or []:
            if item.get("evidenced") and index in _answers_named(item.get("where", "")):
                label = str(item.get(field) or "").strip()
                place(item.get("quote"), kind="proves", style="tag", label=label, note=lead + label)
    fillers = _filler_marks(answer, transcript)
    counted = [m for m in fillers if m["kind"] in ("filler", "gap")]
    if len(counted) >= 2:
        names = sorted({m["label"].lower() for m in counted if m["label"]})
        counted[0]["note"] = (f"{len(counted)} filler words in this answer"
                              + (f" ({', '.join(names)})" if names else "")
                              + ". A silent pause reads better than a filler.")
    found.extend(fillers)

    kept: List[Dict[str, Any]] = []
    # Tags and points sit beside the words rather than on them, so they never compete with a highlight or an underline for the same span.
    beside = ("tag", "point")
    for m in sorted(found, key=lambda m: (_PRIORITY[m["kind"]], m["start"])):
        if m["style"] not in beside and any(k["start"] < m["end"] and m["start"] < k["end"]
                                            for k in kept if k["style"] not in beside):
            continue
        kept.append(m)
    kept.sort(key=lambda m: (m["start"], m["end"]))
    # Where each mark falls in the recording, so the tape can show it too.
    spans = _word_spans(transcript, ((answer.get("speech") or {}).get("transcription") or {})
                        .get("word_timestamps") or [])
    for n, m in enumerate(kept, 1):
        m["id"] = f"m{index}-{n}"
        inside = [s for s in spans if s[0] < max(m["end"], m["start"] + 1) and s[1] > m["start"] - (1 if m["start"] == m["end"] else 0)]
        m["t0"] = inside[0][2] if inside else None
        m["t1"] = inside[-1][3] if inside else None
    return kept


_LOOKED = {"down": "down", "up": "up", "to the side": "to the side",
           "out of frame": "out of the frame"}
_LOOK_ADVICE = {
    "down": "If that was your notes, glance, then come back to the lens to say it.",
    "up": "Thinking is fine; say the point once your eyes are back on the lens.",
    "to the side": "On a call that reads as talking to someone else in the room.",
    "out of frame": "Stay in the frame, even when you shift in your seat.",
}
_QUOTE_MAX_WORDS = 12


def gaze_marks(answer: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each look-away placed on the words said during it.

    A look-away during silence has no words to sit on and only appears on the tape.
    """
    if not answer or (answer.get("content") or {}).get("non_answer"):
        return []
    gaze = (answer.get("facial") or {}).get("gaze") or {}
    looks = gaze.get("look_aways") or []
    transcription = (answer.get("speech") or {}).get("transcription") or {}
    transcript = transcription.get("text") or ""
    if not looks or not transcript.strip():
        return []
    spans = _word_spans(transcript, transcription.get("word_timestamps") or [])
    index = int(answer.get("index") or 0)
    marks = []
    for look in looks:
        t0, t1 = float(look.get("start", 0.0)), float(look.get("end", 0.0))
        hit = [s for s in spans if s[2] < t1 and s[3] > t0]
        if not hit:
            continue
        start, end = hit[0][0], hit[-1][1]
        said = transcript[start:end].strip().rstrip(",.;:!?")
        words = said.split()
        if len(words) > _QUOTE_MAX_WORDS:
            said = " ".join(words[:_QUOTE_MAX_WORDS - 2]) + " …"
        direction = str(look.get("direction") or "away")
        seconds = float(look.get("seconds", t1 - t0))
        note = (f"You looked {_LOOKED.get(direction, 'away')} for about "
                f"{max(1, round(seconds))} seconds while saying “{said}”. "
                + _LOOK_ADVICE.get(direction, "")).strip()
        marks.append({"id": f"g{index}-{len(marks) + 1}", "start": start, "end": end,
                      "kind": "gaze", "style": "overline", "direction": direction,
                      "seconds": round(seconds, 1), "t0": t0, "t1": t1, "note": note})
    return marks


def unplaced(answer: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Advice that has no words to sit on, returned so it is still shown.

    Covers advice about something the answer left out and quotes the transcript does not contain.
    Returned in the coach's order.
    Items with no text are skipped.
    """
    if not answer or (answer.get("content") or {}).get("non_answer"):
        return []
    transcript = ((answer.get("speech") or {}).get("transcription") or {}).get("text") or ""
    debrief = answer.get("debrief") or {}
    out = []
    for kind in ("change", "worked"):
        for item in debrief.get(kind) or []:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or "").strip()
            if quote and _find(transcript, quote):
                continue
            if not any(str(item.get(k) or "").strip() for k in ("problem", "try_instead", "why")):
                continue
            out.append(dict(item, kind=kind))
    return out


def presence(answer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What the camera measured, for the Presence card and the tape.

    None when only the presence check ran or there was no camera.
    """
    facial = (answer or {}).get("facial") or {}
    gaze = facial.get("gaze") or {}
    if gaze.get("eye_contact_0to1") is None and not gaze.get("look_aways"):
        return None
    return {
        "eye_contact": gaze.get("eye_contact_0to1"),
        "look_aways": [{"t0": s.get("start"), "t1": s.get("end"), "seconds": s.get("seconds"),
                        "direction": s.get("direction")} for s in gaze.get("look_aways") or []],
        "longest": gaze.get("longest_look_away_s"),
        "steadiness": gaze.get("head_steadiness_0to1"),
        "framing": (gaze.get("framing") or {}).get("verdict"),
        "lighting": (gaze.get("lighting") or {}).get("verdict"),
        # Reported, never scored, and never turned into advice.
        "expressiveness": gaze.get("expressiveness_0to1"),
        "timeline": gaze.get("timeline") or "",
        "timeline_step_s": gaze.get("timeline_step_s") or 0.5,
        "calibrated": bool(gaze.get("calibrated")),
        "in_frame": facial.get("face_detection_rate_0to1"),
        "reliable": bool(facial.get("reliable")),
    }


def _dims(content: Dict[str, Any]) -> Dict[str, Any]:
    scores = content.get("scores") or {}
    return {k: scores.get(f"{k}_1to5") for k in ("relevance", "depth", "clarity", "structure")}


def view(session: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Everything the in-app report shows beyond the summary: each answer with its transcript, marks and coaching, and the session's written debrief."""
    if not session:
        return None
    sd = session.get("debrief") or {}
    answers = []
    for a in session.get("answers") or []:
        speech = a.get("speech") or {}
        content = a.get("content") or {}
        fused = a.get("fused") or {}
        d = a.get("debrief") or {}
        answers.append({
            "index": a.get("index"),
            "question": a.get("question", ""),
            # Why the question was asked (question_planner); "" before it.
            "why": a.get("question_reason", "") or "",
            "transcript": (speech.get("transcription") or {}).get("text", "") or "",
            "duration": speech.get("audio_duration_seconds"),
            "score": fused.get("overall_score_0to100"),
            "uncapped": fused.get("uncapped_score_0to100"),
            "ceiling": None if content.get("non_answer") else fused.get("ceiling_applied"),
            "non_answer": content.get("non_answer"),
            "dims": _dims(content),
            "marks": for_answer(a, sd),
            "gaze_marks": gaze_marks(a),
            "unplaced": unplaced(a),
            # Proof first: what the answer proved and how it came across, the two parts of its score.
            # None for answers scored before.
            "proof": ({"score": fused.get("proof_0to100"), "presentation": fused.get("presentation_0to100"),
                       **{k: (content.get("proof") or {}).get(k) for k in ("basis", "asked_for", "supplied", "practical",
                                                                  "built_0to100", "second_opinion_0to100")}}
                      if fused.get("proof_0to100") is not None else None),
            "presence": presence(a),
            "observations": [{"headline": o.get("headline", ""), "detail": o.get("detail", ""),
                              "severity": o.get("severity", "")}
                             for o in (a.get("observations") or [])],
            "debrief": {k: d.get(k, [] if k in ("worked", "change") else "")
                        for k in ("looking_for", "what_you_gave", "worked", "change", "delivery", "one_thing")},
            "warnings": list(a.get("warnings") or []),
        })
    return {
        "answers": answers,
        "debrief": {k: sd.get(k, [] if k in ("coverage", "patterns", "priorities", "cv_claims") else "")
                    for k in ("opening", "verdict", "coverage", "strongest_moment", "patterns",
                              "priorities", "closing", "cv_claims")},
    }
