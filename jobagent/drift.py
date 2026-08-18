"""Deterministic drift detection.

The LLM verification pass in tailor.py catches invented employers and degrees.
It does not reliably catch the subtle stuff — "led" where the profile says
"contributed to", a metric that grew by an order of magnitude, a tool name that
appeared from nowhere. Those are the claims that fall apart in an interview,
because they are the ones an interviewer digs into.

None of this uses a model. It is string matching, which means it cannot be
talked out of an objection.
"""
from __future__ import annotations

import re

from .models import Profile

# Verbs that claim ownership or scope. If the resume uses one and the profile
# never did, the model has promoted you.
ESCALATION_VERBS = {
    "led", "leading", "owned", "owning", "architected", "designed", "founded",
    "spearheaded", "drove", "directed", "managed", "oversaw", "headed",
    "established", "pioneered", "scaled", "transformed", "revamped",
    "orchestrated", "championed", "mentored", "supervised",
}

SOFTER_EQUIVALENTS = {
    "led": "contributed to / worked on",
    "owned": "worked on / maintained",
    "architected": "helped design / implemented",
    "designed": "implemented / helped design",
    "managed": "coordinated / supported",
    "drove": "worked on",
    "spearheaded": "worked on",
    "scaled": "helped improve",
    "mentored": "helped / supported",
    "supervised": "coordinated",
    "oversaw": "supported",
}

_WORD = re.compile(r"[A-Za-zÄÖÜäöüß][\w\+\#\.\-]*")
_NUM = re.compile(r"\d[\d,\.]*\s*(?:%|k\b|x\b|ms\b|s\b|m\b|bn\b)?", re.I)

# Numbers that are almost never a claim worth checking.
_NOISE_NUMBERS = {"1", "2", "3", "4", "5", "0"}


class Flag:
    __slots__ = ("kind", "text", "detail")

    def __init__(self, kind: str, text: str, detail: str = ""):
        self.kind = kind
        self.text = text
        self.detail = detail

    def __repr__(self) -> str:
        return f"[{self.kind}] {self.text}" + (f" — {self.detail}" if self.detail else "")


def _profile_text(profile: Profile) -> str:
    parts = [profile.summary, " ".join(profile.skills)]
    for e in profile.experience:
        parts += [e.title, e.company, e.location, e.start, e.end,
                  " ".join(e.bullets), " ".join(e.tech)]
    for ed in profile.education:
        parts += [ed.degree, ed.school, ed.year, ed.detail]
    parts += list(profile.standard_answers.values())
    parts += [profile.location, profile.name, profile.email, profile.phone]
    parts += list(profile.links.values())
    return " ".join(p for p in parts if p)


def _norm_number(tok: str) -> str:
    return re.sub(r"[,\s]", "", tok).rstrip(".").lower()


def check_numbers(resume: str, profile: Profile) -> list[Flag]:
    """Every number on the resume should trace back to the profile."""
    source = {_norm_number(m.group()) for m in _NUM.finditer(_profile_text(profile))}
    flags: list[Flag] = []
    seen: set[str] = set()
    for m in _NUM.finditer(resume):
        tok = _norm_number(m.group())
        bare = tok.rstrip("%kxmsbn")
        if not tok or tok in seen or bare in _NOISE_NUMBERS:
            continue
        seen.add(tok)
        if tok in source or bare in {s.rstrip("%kxmsbn") for s in source}:
            continue
        line = _line_around(resume, m.start())
        flags.append(Flag("number", m.group().strip(),
                          f"not found in your profile — line: {line}"))
    return flags


def check_escalation(resume: str, profile: Profile) -> list[Flag]:
    """Ownership verbs the resume uses that the profile never claimed."""
    src_words = {w.group().lower() for w in _WORD.finditer(_profile_text(profile))}
    flags: list[Flag] = []
    seen: set[str] = set()
    for m in _WORD.finditer(resume):
        w = m.group().lower()
        if w not in ESCALATION_VERBS or w in seen or w in src_words:
            continue
        seen.add(w)
        alt = SOFTER_EQUIVALENTS.get(w, "a verb matching what you actually did")
        flags.append(Flag("escalation", m.group(),
                          f"your profile never uses this — consider {alt}"))
    return flags


def check_unknown_terms(resume: str, profile: Profile) -> list[Flag]:
    """Capitalised or technical tokens that appear nowhere in the profile."""
    src_words = {w.group().lower() for w in _WORD.finditer(_profile_text(profile))}
    # Common resume scaffolding that isn't a claim.
    allow = {
        "summary", "skills", "experience", "education", "present", "current",
        "projects", "languages", "tools", "profile", "contact", "references",
        "and", "the", "with", "for", "using", "across", "including", "led",
        "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
        "nov", "dec", "januar", "februar", "märz", "april", "mai", "juni",
        "juli", "august", "september", "oktober", "november", "dezember",
    }
    flags: list[Flag] = []
    seen: set[str] = set()
    for m in _WORD.finditer(resume):
        tok = m.group().rstrip(".,;:")
        low = tok.lower()
        if not tok or low in src_words or low in allow or low in seen or len(tok) < 3:
            continue
        # Only flag things that look like proper nouns, acronyms, or tech names.
        looks_claimy = (tok[0].isupper() and not _starts_sentence(resume, m.start())) \
            or tok.isupper() or any(c in tok for c in "+#.")
        if not looks_claimy:
            continue
        seen.add(low)
        flags.append(Flag("unknown-term", tok,
                          f"not in your profile — line: {_line_around(resume, m.start())}"))
    return flags[:15]


def _starts_sentence(text: str, idx: int) -> bool:
    before = text[max(0, idx - 3):idx].strip()
    return before == "" or before.endswith((".", "!", "?", "#", "-", "•", ":"))


def _line_around(text: str, idx: int, width: int = 90) -> str:
    start = text.rfind("\n", 0, idx) + 1
    end = text.find("\n", idx)
    line = text[start: end if end != -1 else len(text)].strip()
    return line[:width] + ("…" if len(line) > width else "")


def run_all(resume: str, profile: Profile) -> list[Flag]:
    return check_numbers(resume, profile) + check_escalation(resume, profile) \
        + check_unknown_terms(resume, profile)


def format_flags(flags: list[Flag]) -> str:
    if not flags:
        return ""
    by_kind: dict[str, list[Flag]] = {}
    for f in flags:
        by_kind.setdefault(f.kind, []).append(f)
    out = ["DRIFT CHECK — these are not necessarily wrong, but you must be able "
           "to defend each one out loud:"]
    for kind, items in by_kind.items():
        out.append(f"  {kind}:")
        for f in items[:10]:
            out.append(f"    - {f.text}: {f.detail}")
    return "\n".join(out)
