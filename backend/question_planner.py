# question_planner.py
"""Chooses the interview questions for this role and this candidate.

Like a real interviewer holding the advert and the CV, it asks where the risk is: what the job needs that the CV does not show, whether the CV's claims hold up, and how the candidate would handle the job's problems.
The model reads the documents and writes the questions.
Plain code decides which requirements are unproven, how the 3, 5 or 8 questions are shared out, their order, and the reason shown for each.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from typing import Any, Callable, Dict, List, Optional

import content_evaluator
from schema import InterviewQuestion, QuestionSet

logger = logging.getLogger(__name__)

MIN_GAP_MONTHS = 6

# Timeline gaps, found in code.
# Date ranges as CVs write them: "Mar 2021 to Jun 2022", "2019 to 2020", "May 2020 to present".
# A bare year runs January to December, so consecutive years never make a gap.

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_POINT = rf"(?:{_MONTH}\s+)?(?:19|20)\d{{2}}"
_NOW = r"present|current|now|today"
_RANGE = re.compile(rf"({_POINT})\s*(?:-|\u2013|\u2014|to|until)\s*({_POINT}|{_NOW})", re.IGNORECASE)
_PARTS = re.compile(rf"(?:({_MONTH})\s+)?((?:19|20)\d{{2}})", re.IGNORECASE)


def _month_number(point: str, end: bool, today: date) -> int:
    if re.fullmatch(_NOW, point.strip(), re.IGNORECASE):
        return today.year * 12 + today.month - 1
    m = _PARTS.fullmatch(point.strip())
    month = _MONTHS[m.group(1)[:3].lower()] if m.group(1) else (12 if end else 1)
    return int(m.group(2)) * 12 + month - 1


def timeline_gaps(cv_text: str, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Breaks of MIN_GAP_MONTHS or more between dated CV entries.

    Each is {"after": end of the earlier entry, "before": start of the later entry, "months": whole months between}.
    Overlapping entries are merged first.
    """
    today = today or date.today()
    spans = []
    for m in _RANGE.finditer(cv_text or ""):
        start_text, end_text = m.group(1).strip(), m.group(2).strip()
        start = _month_number(start_text, False, today)
        end = _month_number(end_text, True, today)
        if end >= start:
            spans.append([start, end, start_text, end_text])
    spans.sort(key=lambda s: s[0])
    merged: List[List[Any]] = []
    for span in spans:
        if merged and span[0] <= merged[-1][1] + 1:
            if span[1] > merged[-1][1]:
                merged[-1][1], merged[-1][3] = span[1], span[3]
        else:
            merged.append(list(span))
    gaps = []
    for earlier, later in zip(merged, merged[1:]):
        months = later[0] - earlier[1] - 1
        if months >= MIN_GAP_MONTHS:
            gaps.append({"after": earlier[3], "before": later[2], "months": months})
    return gaps


# How many questions of each kind, for each question count the setup screen offers.
# "gap" is shared by requirement and practical questions.
BUDGET: Dict[int, Dict[str, int]] = {
    3: {"gap": 1, "cv_claim": 1, "scenario": 1, "opener": 0, "strength": 0, "timeline_gap": 0},
    5: {"gap": 2, "cv_claim": 1, "scenario": 1, "opener": 1, "strength": 0, "timeline_gap": 0},
    8: {"gap": 2, "cv_claim": 1, "scenario": 2, "opener": 1, "strength": 1, "timeline_gap": 1},
}
# What goes first when a count between the offered ones trims a plan.
TRIM_ORDER = ("timeline_gap", "strength", "opener", "scenario", "cv_claim", "gap")
# Where a slot with nothing to ask goes instead.
# Never the opener, never a gap.
SPILL_ORDER = ("gap", "cv_claim", "strength", "scenario")
MAX_SCENARIOS = 3
# Four "walk me through your CV claim" questions in one interview read as an audit, not an interview (a CV that showed every requirement produced that).
MAX_CV_CLAIMS = 2


def _allocation(n: int) -> Dict[str, int]:
    n = max(int(n or 1), 1)
    offered = next((k for k in sorted(BUDGET) if k >= n), max(BUDGET))
    alloc = dict(BUDGET[offered])
    over = sum(alloc.values()) - n
    for line in TRIM_ORDER:
        while over > 0 and alloc[line] > 0:
            alloc[line] -= 1
            over -= 1
    if over < 0:
        # More than the largest plan: the extra spill from the top.
        alloc["gap"] += -over
    return alloc


def _reason(kind: str, target: Dict[str, Any], has_cv: bool, style: str = "") -> str:
    """The sentence shown beside the question.
    Written by code, never the model.
    """
    # Requirements are named in the advert's own quoted words, because the model's summaries do not always read well in a sentence.
    text = target.get("text", "")
    quote = target.get("quote") or text
    if kind == "requirement":
        asks = "asks for" if target.get("must", True) else "mentions"
        return (f"Asked because the advert {asks} \"{quote}\" and your CV does not show it." if has_cv
                else f"Asked because the advert {asks} \"{quote}\".")
    if kind == "practical":
        return (f"Asked because the advert says \"{quote}\" and your CV does not say whether that suits you."
                if has_cv else f"Asked because the advert says \"{quote}\".")
    if kind == "cv_claim":
        return f"Asked because your CV says \"{quote}\", and an interviewer will want you to back it up."
    if kind == "strength":
        return f"Asked because the advert asks for \"{quote}\" and your CV shows it: a chance to prove it well."
    if kind == "scenario":
        if style == "logical":
            return "Asked to see how you think when two things need you at once."
        return f"Asked to see how you would handle part of the job: {text}."
    if kind == "opener":
        return "Asked because interviews usually start by finding out why you want the role."
    return (f"Asked because your CV shows a break between {target.get('after')} and "
            f"{target.get('before')}. A short, honest answer is all it needs.")


def plan(reading: Dict[str, Any], gaps: List[Dict[str, Any]], n: int,
         job_title: str = "") -> List[Dict[str, Any]]:
    """The slots for n questions: kind, target, reason and (scenarios) style, in asking order."""
    has_cv = bool(reading.get("has_cv"))
    reqs = list(reading.get("requirements") or [])
    unshown = [r for r in reqs if not r.get("shown_quote")]
    unshown.sort(key=lambda r: 0 if r.get("must", True) else 1)  # stable: advert order within
    pools = {
        "gap": unshown,
        "cv_claim": list(reading.get("claims") or [])[:MAX_CV_CLAIMS] if has_cv else [],
        # A practical requirement the CV shows is settled; "give an example of being happy to work weekends" is not a question.
        "strength": [r for r in reqs if r.get("shown_quote") and not r.get("practical")] if has_cv else [],
        "timeline_gap": list(gaps or []) if has_cv else [],
    }
    alloc = _allocation(n)
    chosen: Dict[str, List[Dict[str, Any]]] = {}
    spare = 0
    for line in ("gap", "cv_claim", "strength", "timeline_gap"):
        chosen[line] = pools[line][:alloc[line]]
        pools[line] = pools[line][alloc[line]:]
        spare += alloc[line] - len(chosen[line])
    # With no tasks read from the advert, a scenario is about the everyday work, never a requirement ("In this role you would is honest and reliable").
    duties = list(reading.get("duties") or []) or [{"text": "do the everyday work of this role", "quote": ""}]
    # Each practical scenario takes its own task and one logical scenario can share one, so one task gives two scenarios at most, never the same twice.
    max_scenarios = min(MAX_SCENARIOS, len(duties) + 1)
    scenarios = min(alloc["scenario"], max_scenarios)
    spare += alloc["scenario"] - scenarios
    while spare > 0:
        for line in SPILL_ORDER:
            if line == "scenario":
                if scenarios < max_scenarios:
                    scenarios += 1
                    break
            elif pools[line]:
                chosen[line].append(pools[line].pop(0))
                break
        else:
            break  # no questions left
        spare -= 1

    slots: List[Dict[str, Any]] = []

    def add(kind, target, style=""):
        slots.append({"kind": kind, "target": target, "style": style,
                      "reason": _reason(kind, target, has_cv, style)})

    # The order questions are asked in, which is not the order they were chosen in: warm up first, the pointed questions in the middle and at the end.
    for _ in range(alloc["opener"]):
        add("opener", {"text": job_title or "this role"})
    for claim in chosen["cv_claim"]:
        add("cv_claim", claim)
    for r in chosen["strength"]:
        add("strength", r)
    practical_used = 0
    for i in range(scenarios):
        if i == 1:
            add("scenario", duties[1 % len(duties)], "logical")
        else:
            add("scenario", duties[practical_used], "practical")
            practical_used += 1
    for r in chosen["gap"]:
        add("practical" if r.get("practical") else "requirement", r)
    for g in chosen["timeline_gap"]:
        add("timeline_gap", g)
    return slots


# Reading (model)
_NUM_PREDICT = 900
MAX_REQUIREMENTS = 8
MAX_DUTIES = 4
MAX_CLAIMS = 5

_ADVERT_SYSTEM_PROMPT = """\
You read a job advert the way an interviewer does before an interview.

List what the advert asks the candidate to have or be able to do (at most 8),
and the everyday tasks the job involves (at most 4). Use only what the advert
says; never add a requirement of your own.

For each requirement give:
- "text": a short plain phrase, such as "weekend work" or "cleaning to a high standard"
- "quote": the advert's own words that state it, copied exactly
- "must": true if the advert makes it essential, false if it is only preferred
- "practical": true if it is an arrangement or a fact rather than a skill
  (hours, shifts, weekends, a licence, a certificate, travel, a start date)

For each task give "text", a short phrase starting with a verb such as "clean
and restock guest rooms", and "quote", the advert's own words.

Reply with a single JSON object and nothing else:
{"requirements": [{"text": "", "quote": "", "must": true, "practical": false}],
 "duties": [{"text": "", "quote": ""}]}\
"""

_CV_SYSTEM_PROMPT = """\
You read a candidate's CV against the requirements of a job.

For each requirement the CV clearly shows the candidate has, give the
requirement copied exactly as it is listed, and the CV's exact words that
show it. Leave out any requirement the CV does not clearly show; do not
stretch a loose connection.

Then list up to 5 claims from the CV an interviewer for this job would want
backed up: things the candidate says they did or achieved, best with a number
or an outcome. For each give "text", a short phrase such as "training two new
starters", and "quote", the CV's exact words.

Reply with a single JSON object and nothing else:
{"shown": [{"requirement": "", "quote": ""}],
 "claims": [{"text": "", "quote": ""}]}\
"""


# Exact reply shapes, so longer replies stay on these keys and types.
_STR = {"type": "string"}
_ADVERT_SCHEMA = {"type": "object", "required": ["requirements", "duties"], "properties": {
    "requirements": {"type": "array", "maxItems": MAX_REQUIREMENTS, "items": {
        "type": "object", "required": ["text", "quote", "must", "practical"],
        "properties": {"text": _STR, "quote": _STR, "must": {"type": "boolean"}, "practical": {"type": "boolean"}}}},
    "duties": {"type": "array", "maxItems": MAX_DUTIES, "items": {
        "type": "object", "required": ["text", "quote"], "properties": {"text": _STR, "quote": _STR}}}}}
_CV_SCHEMA = {"type": "object", "required": ["shown", "claims"], "properties": {
    "shown": {"type": "array", "maxItems": MAX_REQUIREMENTS, "items": {
        "type": "object", "required": ["requirement", "quote"],
        "properties": {"requirement": _STR, "quote": _STR}}},
    "claims": {"type": "array", "maxItems": MAX_CLAIMS, "items": {
        "type": "object", "required": ["text", "quote"], "properties": {"text": _STR, "quote": _STR}}}}}


def _phrase(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split()).strip().rstrip(".")
    return text[:limit]


def _flag(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no"):
        return value.strip().lower() in ("true", "yes")
    return default


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _span(quote: str, source: str) -> Optional[str]:
    """The source's own text for a quote when every word appears in order, else None.

    Only spacing and punctuation may differ, and the source's text is returned, so stray marks from the model never reach the candidate.
    """
    words = re.findall(r"\w+", quote or "")
    if not words:
        return None
    m = re.search(r"\b" + r"\W+".join(re.escape(w) for w in words) + r"\b", source or "", re.IGNORECASE)
    if not m:
        return None
    end = m.end()
    # The match stops at the last word, so a bracket it opened ("(AWS, Azure or GCP") is closed from the source when the source closes it.
    if source[m.start():end].count("(") > source[m.start():end].count(")") and source[end:end + 1] == ")":
        end += 1
    # One line: a CV wrapped mid-sentence must not put a line break into a question read aloud or a reason shown under it.
    return " ".join(source[m.start():end].split())


def _checked(text: str, quote: str, source: str) -> str:
    """The model's short summary if every word is in the source, else the quote."""
    # A word counts as the source's if its first five letters are there, so "training" is allowed from "Trained" and "weekend" from "weekends".
    folded = (source or "").lower()
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return _lower_first(text if text and all(w[:5] in folded for w in words) else quote)


def read_advert(job_description: str, job_title: str = ""):
    """(requirements, duties) from the advert, each backed by the advert's own words.

    Raises ValueError when the reply is unusable; prepare() then falls back.
    """
    raw = content_evaluator._generate(
        _ADVERT_SYSTEM_PROMPT,
        f"Job title: {job_title.strip() or '(not given)'}\n\nJob advert:\n{job_description.strip()}",
        num_predict=_NUM_PREDICT, schema=_ADVERT_SCHEMA)
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"the advert reading was not valid JSON ({exc})") from exc
    if not isinstance(obj, dict):
        raise ValueError("the advert reading was not a JSON object")
    requirements, seen = [], set()
    for item in obj.get("requirements") or []:
        if not isinstance(item, dict):
            continue
        text, quote = _phrase(item.get("text")), _span(_phrase(item.get("quote"), 300), job_description)
        if not text or not quote or text.lower() in seen:
            continue  # not quoted in the advert
        seen.add(text.lower())
        requirements.append({"text": _checked(text, quote, job_description), "quote": quote, "must": _flag(item.get("must"), True),
                             "practical": _flag(item.get("practical"), False), "shown_quote": ""})
        if len(requirements) >= MAX_REQUIREMENTS:
            break
    duties = []
    for item in obj.get("duties") or []:
        if not isinstance(item, dict):
            continue
        text, quote = _phrase(item.get("text")), _span(_phrase(item.get("quote"), 300), job_description)
        if text and quote:
            duties.append({"text": _checked(text, quote, job_description), "quote": quote})
        if len(duties) >= MAX_DUTIES:
            break
    return requirements, duties


def read_cv(cv_text: str, requirements: List[Dict[str, Any]]):
    """(requirements, with shown_quote filled where the CV shows them, and the CV's claims).

    A requirement only counts as shown with the CV's own words, so when unsure it gets asked about.
    Raises ValueError when the reply is unusable.
    """
    listing = "\n".join(f"- {r['text']}" for r in requirements) or "(none)"
    raw = content_evaluator._generate(
        _CV_SYSTEM_PROMPT, f"Job requirements:\n{listing}\n\nCV:\n{cv_text.strip()}",
        num_predict=_NUM_PREDICT, schema=_CV_SCHEMA)
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"the CV reading was not valid JSON ({exc})") from exc
    if not isinstance(obj, dict):
        raise ValueError("the CV reading was not a JSON object")
    out = [dict(r) for r in requirements]
    # Matched by wording rather than number, and the CV quote must share a word with the requirement.
    # Both mistakes lean towards "not shown", so the requirement gets asked.
    for item in obj.get("shown") or []:
        if not isinstance(item, dict):
            continue
        quote = _span(_phrase(item.get("quote"), 300), cv_text)
        if not quote:
            continue
        named = _stems(str(item.get("requirement") or ""))
        scored = [(len(named & _stems(r["text"] + " " + r.get("quote", ""))), i) for i, r in enumerate(out)
                  if not r["shown_quote"]]
        best = max(scored, default=(0, -1))
        if best[0] == 0:
            continue
        target = out[best[1]]
        if (_stems(quote) & _stems(target["text"] + " " + target.get("quote", ""))) - _WEAK_STEMS:
            target["shown_quote"] = quote
    claims = []
    for item in obj.get("claims") or []:
        if not isinstance(item, dict):
            continue
        quote = _span(_phrase(item.get("quote"), 300), cv_text)
        text = _phrase(item.get("text")) or quote
        if quote and quote.lower() not in {c["quote"].lower() for c in claims}:
            claims.append({"text": _checked(text, quote, cv_text), "quote": quote})
        if len(claims) >= MAX_CLAIMS:
            break
    return out, claims


# Writing (model, checked, with fixed wordings)
MAX_WORDS = 30

_WRITE_SYSTEM_PROMPT = """\
You are an interviewer writing the questions for one interview. Each numbered
item says what one question must be about. Write exactly one question per
item, in the same order.

Rules for every question:
- One sentence to be spoken aloud, ending with "?", at most 30 words.
- About exactly what its item says; add no facts that are not in the item.
- Ask for ONE thing only. Never list several things to cover ("including
  X and Y", "such as"), and never join two separate asks with "and".
- Never ask about salary expectations, visa status, age, health, family,
  religion or any other personal characteristic.
- Items marked scenario ask what the candidate would do in that situation
  ("How would you...", "What would you do if...").

Reply with a single JSON object and nothing else:
{"questions": ["", ""]}\
"""

# Words too general to tie CV words to a requirement on their own.
_WEAK_STEMS = {"work", "worke", "worki", "able", "happy", "someo", "their", "every"}

_WRITE_SCHEMA = {"type": "object", "required": ["questions"],
                 "properties": {"questions": {"type": "array", "items": {"type": "string"}}}}

# A scenario sets out a problem; asking them to recall one they faced makes it a past-experience question with a "would" tacked on.
_RECALLED = re.compile(r"\b(describe|tell me about)\b|\bshare (a|an|one)\b|\byou (have )?(faced|encountered)\b",
                       re.IGNORECASE)

_LISTED = re.compile(r"\b(including|such as|as well as)\b", re.IGNORECASE)

_BANNED = re.compile(r"\b(salary|visa|age|how old|health|illness|disab\w*|pregnan\w*|married|"
                     r"children|religio\w*|nationality|ethnic\w*)\b", re.IGNORECASE)
_STOP = {"about", "their", "there", "these", "those", "which", "would", "could", "should", "your",
         "with", "from", "that", "this", "have", "what", "when", "where", "while", "will", "they",
         "role", "time", "tell", "give", "into", "been", "were"}


def _stems(text: str) -> set:
    return {w[:5] for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) >= 4 and w not in _STOP}


def _anchor(slot: Dict[str, Any]) -> str:
    t = slot["target"] or {}
    return f"{t.get('text', '')} {t.get('quote', '')}" if slot["kind"] == "cv_claim" else t.get("text", "")


def _valid(question: str, slot: Dict[str, Any]) -> bool:
    """True when the model's question fits its slot: one short question, of the right kind."""
    q = (question or "").strip()
    if (not q.endswith("?") or q.count("?") > 1 or ":" in q or len(q.split()) > MAX_WORDS
            or _BANNED.search(q)):
        return False
    if slot["kind"] == "scenario" and ("would" not in q.lower() or _RECALLED.search(q)):
        return False
    # A question listing several things to cover is marked against all of them, and an answer that covers one scores as a miss.
    if _LISTED.search(q):
        return False
    if slot["kind"] == "opener":
        return True
    return bool(_stems(_anchor(slot)) & _stems(q))


def _brief(slot: Dict[str, Any], job_title: str) -> str:
    """The one-line instruction the model gets for this slot: what the question must ask about."""
    t, kind = slot["target"] or {}, slot["kind"]
    if kind == "opener":
        return f"opener: ask why they want this {job_title or 'role'}"
    if kind == "cv_claim":
        return (f"cv_claim: their CV says \"{t.get('quote', t.get('text', ''))}\"; ask them to walk you "
                "through it: what they did and how it went")
    if kind == "strength":
        return f"strength: ask for a real example of a time they showed {t.get('text', '')}"
    if kind == "scenario" and slot.get("style") == "logical":
        return (f"scenario: two specific urgent tasks arrive at once while they {t.get('text', '')}; "
                "name both in the question, and ask which they would do first and why")
    if kind == "scenario":
        return (f"scenario: the question itself must state one specific problem that really happens while "
                f"they {t.get('text', '')} and fits that task, then ask what they would do; "
                "do not ask them to recall a problem they faced")
    if kind == "practical":
        return f"practical: the job involves {t.get('text', '')}; ask directly how that works for them"
    return f"requirement: the job needs {t.get('text', '')}; ask for a real example that shows it"


def _fixed(slot: Dict[str, Any], job_title: str) -> str:
    """The wording used when the model's fails its check, and always for a timeline gap."""
    t, kind = slot["target"] or {}, slot["kind"]
    if kind == "opener":
        return f"What attracted you to this {job_title} role?" if job_title else "What attracted you to this role?"
    quote = t.get("quote") or t.get("text")
    if kind == "cv_claim":
        return f"Your CV says \"{quote}\". Can you walk me through that?"
    if kind == "strength":
        return f"The advert asks for this: \"{quote}\". Can you tell me about a time you showed it?"
    if kind == "scenario" and slot.get("style") == "logical":
        return (f"Imagine two urgent jobs come up at once while you {t.get('text')}. "
                "How would you decide what to do first?")
    if kind == "scenario":
        return f"In this role you would {t.get('text')}. How would you approach that on a busy day?"
    if kind == "practical":
        return f"The advert says: \"{quote}\". How does that fit with your situation?"
    if kind == "timeline_gap":
        return (f"Your CV shows a break between {t.get('after')} and {t.get('before')}. "
                "What were you doing during that time?")
    return f"The advert asks for this: \"{quote}\". Can you tell me about a time you showed that?"


_COMPETENCY = {"opener": "motivation", "cv_claim": "CV claim", "scenario": "problem solving",
               "timeline_gap": "career history"}


def write(slots: List[Dict[str, Any]], job_title: str = "", job_description: str = ""):
    """(questions, how many used the fixed wording).
    One model call for every slot.
    """
    items = [(i, s) for i, s in enumerate(slots) if s["kind"] != "timeline_gap"]
    got: Any = None
    if items:
        listing = "\n".join(f"{n}. {_brief(s, job_title)}" for n, (_, s) in enumerate(items, 1))
        try:
            obj = json.loads(content_evaluator._generate(
                _WRITE_SYSTEM_PROMPT,
                f"Job title: {job_title or '(not given)'}\n\nJob advert:\n{(job_description or '').strip()[:3000]}"
                f"\n\nQuestions to write:\n{listing}", num_predict=_NUM_PREDICT, schema=_WRITE_SCHEMA))
            got = obj.get("questions") if isinstance(obj, dict) else None
        except Exception as exc:  # the fixed wordings stand in
            logger.warning("The questions could not be written (%s); fixed wordings used.", exc)
    written: Dict[int, str] = {}
    for n, (i, s) in enumerate(items):
        text = got[n] if isinstance(got, list) and n < len(got) else ""
        if isinstance(text, dict):
            text = text.get("question", "")
        # The model sometimes sets a clause off with long dashes; a spoken question reads the same with commas.
        text = re.sub(r"\s*[\u2013\u2014]\s*", ", ", str(text or ""))
        text = " ".join(text.split())
        if _valid(text, s):
            written[i] = text
    questions, fixed = [], 0
    for i, s in enumerate(slots):
        text = written.get(i)
        if not text:
            text, fixed = _fixed(s, job_title), fixed + 1
        questions.append(InterviewQuestion(
            index=i + 1, question=text,
            competency=_COMPETENCY.get(s["kind"], (s["target"] or {}).get("text", "")),
            kind=s["kind"], reason=s["reason"]))
    return questions, fixed


# The reading of each advert and CV is cached while the app runs, so a second interview for the same job starts faster.
_CACHE: Dict[str, Dict[str, Any]] = {}


# prepare() reports each slow step as it starts ("advert", "cv", "writing"), so the page can show real progress.
_listeners: List[Callable[[str], None]] = []


def listen(fn: Callable[[str], None]) -> None:
    _listeners.append(fn)


def unlisten(fn: Callable[[str], None]) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def _stage(name: str) -> None:
    for fn in list(_listeners):
        try:
            fn(name)
        except Exception as exc:
            # Progress is never worth a failed plan.
            logger.debug("A planning listener failed: %s", exc)


def read(job_description: str, job_title: str, cv_text: str) -> Dict[str, Any]:
    """The advert (and CV) read once; raises ValueError if the advert cannot be."""
    key = hashlib.sha256("\x00".join((job_title or "", job_description or "", cv_text or ""))
                         .encode("utf-8")).hexdigest()
    if key in _CACHE:
        return _CACHE[key]
    _stage("advert")
    requirements, duties = read_advert(job_description, job_title)
    has_cv = bool((cv_text or "").strip())
    claims: List[Dict[str, Any]] = []
    cv_read = True
    if has_cv:
        try:
            _stage("cv")
            requirements, claims = read_cv(cv_text, requirements)
        except Exception as exc:  # plan from the advert alone
            logger.warning("The CV could not be read (%s); planning from the advert.", exc)
            cv_read = False
    reading = {"requirements": requirements, "duties": duties, "claims": claims, "has_cv": has_cv}
    if cv_read:
        _CACHE[key] = reading
    return reading


def prepare(job_description: str, job_title: str, cv_text: str, n: int):
    """Exactly n planned questions (fewer only if nothing is left to ask), and warnings.

    Never raises.
    An empty set means the caller should use the plain question writer.
    """
    empty = QuestionSet(model="planner", job_title=job_title, questions=[], schema_valid=False,
                        repair_attempts=0)
    if not (job_description or "").strip():
        return empty, ["No job description was supplied, so the questions could not be planned."]
    unhealthy = content_evaluator._health_check()
    if unhealthy:
        return empty, [unhealthy.replace("content evaluation", "question planning")]
    try:
        reading = read(job_description, job_title, cv_text or "")
    except Exception as exc:  # the caller falls back
        logger.warning("The job advert could not be read for planning: %s", exc)
        return empty, [f"The job advert could not be read to plan the questions ({exc})."]
    if not reading["requirements"] and not reading["duties"]:
        return empty, ["Nothing the job asks for could be read from the advert."]
    gaps = timeline_gaps(cv_text) if reading["has_cv"] else []
    slots = plan(reading, gaps, n, job_title)
    _stage("writing")
    questions, fixed = write(slots, job_title, job_description)
    if fixed:
        logger.info("%d of %d planned questions used a fixed wording.", fixed, len(questions))
    return QuestionSet(model=content_evaluator._MODEL, job_title=job_title, questions=questions,
                       schema_valid=True, repair_attempts=0), []
