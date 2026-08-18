"""Core data structures for the job-search agent."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Experience:
    title: str = ""
    company: str = ""
    start: str = ""
    end: str = ""
    location: str = ""
    bullets: list[str] = field(default_factory=list)
    tech: list[str] = field(default_factory=list)


@dataclass
class Education:
    degree: str = ""
    school: str = ""
    year: str = ""
    detail: str = ""


@dataclass
class Preferences:
    titles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    remote_ok: bool = True
    onsite_ok: bool = True
    min_salary: int | None = None
    currency: str = "USD"
    seniority: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_companies: list[str] = field(default_factory=list)
    must_have_keywords: list[str] = field(default_factory=list)
    work_authorization: str = ""
    notice_period: str = ""


@dataclass
class Profile:
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    skills: list[str] = field(default_factory=list)
    experience: list[Experience] = field(default_factory=list)
    education: list[Education] = field(default_factory=list)
    preferences: Preferences = field(default_factory=Preferences)

    # Free-form answers the agent reuses for ATS screening questions.
    standard_answers: dict[str, str] = field(default_factory=dict)

    def skills_lower(self) -> set[str]:
        return {s.strip().lower() for s in self.skills if s.strip()}

    def to_prompt_block(self) -> str:
        """Compact, token-efficient rendering of the profile for the LLM."""
        lines = [f"NAME: {self.name}", f"LOCATION: {self.location}"]
        if self.summary:
            lines.append(f"SUMMARY: {self.summary}")
        if self.skills:
            lines.append("SKILLS: " + ", ".join(self.skills))
        for e in self.experience:
            lines.append(
                f"\nROLE: {e.title} @ {e.company} ({e.start}–{e.end or 'present'}) {e.location}"
            )
            for b in e.bullets:
                lines.append(f"  - {b}")
            if e.tech:
                lines.append("  TECH: " + ", ".join(e.tech))
        for ed in self.education:
            lines.append(f"\nEDUCATION: {ed.degree}, {ed.school} {ed.year} {ed.detail}".rstrip())
        p = self.preferences
        lines.append(
            f"\nTARGET TITLES: {', '.join(p.titles) or 'any'}"
            f"\nTARGET LOCATIONS: {', '.join(p.locations) or 'any'}"
            f"\nREMOTE OK: {p.remote_ok}"
        )
        return "\n".join(lines)


@dataclass
class JobPosting:
    source: str
    company: str
    title: str
    url: str
    description: str = ""
    location: str = ""
    remote: bool | None = None
    salary_text: str = ""
    posted_at: str = ""
    department: str = ""
    external_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        key = f"{self.source}|{self.company}|{self.title}|{self.url}".lower()
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def to_prompt_block(self, max_desc: int = 6000) -> str:
        desc = (self.description or "")[:max_desc]
        return (
            f"COMPANY: {self.company}\nTITLE: {self.title}\n"
            f"LOCATION: {self.location} (remote={self.remote})\n"
            f"SALARY: {self.salary_text or 'not stated'}\n"
            f"URL: {self.url}\n\nDESCRIPTION:\n{desc}"
        )


@dataclass
class Score:
    job_id: str
    heuristic: float = 0.0
    fit: int = 0                      # 0-100, LLM judgment
    verdict: str = ""                 # strong | good | stretch | weak
    reasons: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    scored_at: str = field(default_factory=_now)

    @property
    def combined(self) -> float:
        """Blend cheap signal with the model's judgment."""
        return round(0.25 * (self.heuristic * 100) + 0.75 * self.fit, 2)


@dataclass
class ApplicationPackage:
    job_id: str
    company: str
    title: str
    url: str
    resume_md: str = ""
    cover_letter_md: str = ""
    screening_answers: dict[str, str] = field(default_factory=dict)
    autofill: dict[str, str] = field(default_factory=dict)
    status: str = "drafted"           # drafted|approved|rejected|submitted|failed
    notes: str = ""
    created_at: str = field(default_factory=_now)
    submitted_at: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)   # kind -> path


def to_json(obj: Any) -> str:
    return json.dumps(asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj,
                      indent=2, ensure_ascii=False)
