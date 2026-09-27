# difficulty.py
"""How demanding a job advert (or a CV) is, measured from its text.

Usage: measure(advert) returns the parts and a score; level_for(score) gives "easy", "medium" or "hard"; fit(cv, advert) gives 0 to 1 for how much of the advert the CV mentions.

Four parts are measured: reading grade (Flesch Kincaid), the share of technical words, how many requirements are listed, and length.
Each is scaled to 0 to 1 and weighted into one 0 to 100 score, with vocabulary and reading grade weighted most.

The score reads the writing, not the job, so a simple job written in heavy prose scores higher.
Every example advert is tested to sit inside its level.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List

# Where each level starts on the 0 to 100 score.
# Set in the gaps between the example adverts (easy 3.2 to 16.8, medium 21.6 to 42.5, hard 61.0 to 81.4), so the levels never overlap.
BANDS = {"easy": (0.0, 20.0), "medium": (20.0, 52.0), "hard": (52.0, 100.1)}

# Scaling ceilings: beyond these an advert does not read as harder.
GRADE_FLOOR, GRADE_CEILING = 5.0, 16.0
TECH_CEILING = 0.14
REQUIREMENTS_CEILING = 14
WORDS_FLOOR, WORDS_CEILING = 60, 420

WEIGHTS = {"grade": 0.30, "technical": 0.40, "requirements": 0.15, "length": 0.15}

# Technical words plain adverts do not use.
# Acronyms, dotted names (Node.js), symbols (C#) and inner capitals (PostgreSQL) are caught by their shape, so only ordinary looking words are listed.
LEXICON = {
    # software and data
    "python", "java", "javascript", "typescript", "golang", "rust", "scala", "kotlin",
    "swift", "ruby", "php", "sql", "nosql", "html", "css", "react", "angular", "vue",
    "django", "flask", "fastapi", "spring", "kubernetes", "docker", "terraform",
    "ansible", "jenkins", "airflow", "spark", "hadoop", "kafka", "snowflake",
    "databricks", "tableau", "looker", "excel", "vba", "pandas", "numpy", "pytorch",
    "tensorflow", "linux", "unix", "bash", "git", "microservices", "api", "apis",
    "rest", "restful", "graphql", "postgres", "postgresql", "mysql", "mongodb",
    "redis", "elasticsearch", "azure", "gcp", "cloud", "serverless", "lambda",
    "devops", "ci", "cd", "pipeline", "pipelines", "etl", "elt", "schema", "schemas",
    "latency", "throughput", "scalability", "scalable", "observability", "telemetry",
    "encryption", "authentication", "oauth", "firewall", "firewalls", "siem",
    "penetration", "vulnerability", "vulnerabilities", "malware", "network",
    "networking", "tcp", "dns", "vpn", "backend", "frontend", "full-stack",
    "algorithm", "algorithms", "debugging", "refactoring", "deployment", "deployments",
    "containerised", "containerized", "orchestration", "infrastructure",
    "regression", "classification", "clustering", "forecasting", "statistical",
    "statistics", "hypothesis", "bayesian", "a/b", "dashboards", "dashboard",
    "warehouse", "warehousing", "modelling", "modeling", "machine", "learning",
    "ml", "llm", "llms", "nlp", "transformer", "transformers", "inference",
    "embeddings", "vector", "metrics", "kpis", "sla", "slas", "uptime", "incident",
    "incidents", "on-call", "sre", "compliance", "gdpr", "iso", "audit", "sharepoint",
    "crm", "salesforce", "erp", "sap", "jira", "confluence", "agile", "scrum",
    # finance and engineering
    "derivatives", "hedging", "valuation", "liquidity", "reconciliation",
    "reconciliations", "ledger", "ifrs", "gaap", "var", "quantitative", "stochastic",
    "monte", "carlo", "cad", "autocad", "solidworks", "matlab", "simulink", "plc",
    "scada", "tolerances", "finite", "fea", "cfd", "thermodynamics", "hvac",
    "firmware", "embedded", "rtos", "microcontroller", "microcontrollers", "fpga",
    "verilog", "vhdl", "pcb", "oscilloscope", "signal", "circuits",
    # healthcare and science
    "pharmacology", "pharmacokinetics", "clinical", "protocols", "gcp-compliant",
    "assay", "assays", "pcr", "sequencing", "genomics", "bioinformatics",
    "chromatography", "hplc", "spectrometry", "titration", "sterile", "aseptic",
    "diagnostics", "triage", "epidemiology", "biostatistics",
}

# Capitals that are not technical.
_PLAIN_CAPS = {"I", "A", "UK", "US", "USA", "EU", "CV", "OK", "HR", "TV", "AM", "PM",
               "ID", "NHS", "MR", "MRS", "MS", "DR", "BSC", "BA", "MSC", "GCSE",
               "GCSES", "UCAS", "PHD", "MA", "LTD", "PLC", "CEO", "ASAP", "FAQ", "N/A",
               "and/or", "AND/OR"}

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.#+/'&-]*")
_BULLET = re.compile(r"^\s*(?:[-*•●]|\d+[.)])\s+\S", re.M)

STOPWORDS = set("""
a about above after again all also am an and any are as at be been being below
between both but by can could did do does doing down during each few for from
further had has have having he her here hers him his how i if in into is it its
itself just me more most my no nor not now of off on once only or other our ours
out over own same she should so some such than that the their them then there
these they this those through to too under until up very was we were what when
where which while who whom why will with would you your yours yourself
role job team work working works experience experienced ability able skills skill
strong good great excellent including include includes within across using use
used new join looking help helping ensure ensuring support supporting based well
year years day days week weeks per must may might need needs needed want like
people company business role's opportunity candidate candidates successful apply
please required requirements desirable essential responsibilities responsible
""".split())


def syllables(word: str) -> int:
    """A close count of spoken syllables: vowel groups, less a silent final e."""
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups)
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and n > 1:
        n -= 1
    return max(n, 1)


def _words(text: str) -> List[str]:
    return [m.group(0) for m in _WORD.finditer(text or "")]


def _sentences(text: str) -> int:
    # A bullet or a line of its own is a sentence to the reader, punctuated or not.
    parts = [p for p in re.split(r"[.!?]+(?:\s|$)|\n+", text or "") if re.search(r"[A-Za-z]", p)]
    return max(len(parts), 1)


def reading_grade(text: str) -> float:
    """Flesch-Kincaid grade level: school years of reading needed."""
    words = _words(text)
    if not words:
        return 0.0
    syl = sum(syllables(w) for w in words)
    return 0.39 * (len(words) / _sentences(text)) + 11.8 * (syl / len(words)) - 15.59


def is_technical(token: str) -> bool:
    """True when a word reads as a technical term: in the lexicon, or an acronym such as SQL."""
    t = (token or "").strip(".,;:()[]{}\"'!?")
    if not t or t in _PLAIN_CAPS:
        return False
    if t.lower() in LEXICON:
        return True
    letters = re.sub(r"[^A-Za-z]", "", t)
    if len(letters) >= 2 and t.isupper() and t.upper() not in _PLAIN_CAPS:
        return True  # SQL, AWS, KPI, GDPR
    if re.search(r"[A-Za-z][.#+/][A-Za-z]|[A-Za-z](#|\+\+)$", t):
        return True  # Node.js, CI/CD, C#, C++
    if re.search(r"[a-z][A-Z]", t):
        return True  # PostgreSQL, GitHub
    return False


def technical_share(text: str) -> float:
    words = _words(text)
    if not words:
        return 0.0
    return sum(1 for w in words if is_technical(w)) / len(words)


def requirements(text: str) -> int:
    """How many items the text lists, by bullet or number."""
    return len(_BULLET.findall(text or ""))


def _scale(value: float, low: float, high: float) -> float:
    return min(max((value - low) / (high - low), 0.0), 1.0)


def measure(text: str) -> Dict[str, float]:
    """The four signals and the 0..100 score they add up to."""
    words = _words(text)
    if not words:
        return {"words": 0, "grade": 0.0, "technical_share": 0.0,
                "requirements": 0, "score": 0.0}
    grade = reading_grade(text)
    tech = technical_share(text)
    reqs = requirements(text)
    parts = {
        "grade": _scale(grade, GRADE_FLOOR, GRADE_CEILING),
        "technical": _scale(tech, 0.0, TECH_CEILING),
        "requirements": _scale(reqs, 0, REQUIREMENTS_CEILING),
        "length": _scale(len(words), WORDS_FLOOR, WORDS_CEILING),
    }
    score = 100.0 * sum(WEIGHTS[k] * v for k, v in parts.items())
    return {"words": len(words), "grade": round(grade, 1),
            "technical_share": round(tech, 3), "requirements": reqs,
            "score": round(score, 1)}


def level_for(score: float) -> str:
    for name, (low, high) in BANDS.items():
        if low <= score < high:
            return name
    return "hard" if score >= BANDS["hard"][0] else "easy"


# How much of an advert a CV names

def _stem(word: str) -> str:
    w = word.lower().strip(".,;:()[]{}\"'!?")
    for suffix in ("ing", "ed", "es", "s"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def _terms(text: str) -> Dict[str, float]:
    """The advert's content words, technical ones counting double."""
    out: Dict[str, float] = {}
    for w in _words(text):
        s = _stem(w)
        if len(s) < 3 or s in STOPWORDS or w.lower() in STOPWORDS:
            continue
        out[s] = max(out.get(s, 0.0), 2.0 if is_technical(w) else 1.0)
    return out


def fit(cv: str, advert: str) -> float:
    """The weighted share of the advert's content words that the CV also uses."""
    wanted = _terms(advert)
    if not wanted or not (cv or "").strip():
        return 0.0
    have = {_stem(w) for w in _words(cv)}
    total = sum(wanted.values())
    return round(sum(v for k, v in wanted.items() if k in have) / total, 3)


def describe(scores: Iterable[float]) -> Dict[str, float]:
    """Min, median and max of some scores, for the report's table."""
    xs = sorted(scores)
    if not xs:
        return {"min": 0.0, "median": 0.0, "max": 0.0}
    mid = xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2
    return {"min": xs[0], "median": round(mid, 1), "max": xs[-1]}
