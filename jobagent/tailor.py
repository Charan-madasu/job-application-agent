"""Generate a tailored resume + cover letter per posting.

Design constraint that matters more than anything else here: the model may
reframe, reorder, and re-emphasise what is in the profile. It may not invent
employers, titles, dates, degrees, certifications, or metrics. A separate
verification pass checks the draft against the source profile and flags
anything unsupported, because a fabricated resume is worse than no resume.
"""
from __future__ import annotations

import json

from . import drift
from .language import enforce_level, language_instruction, language_note
from .llm import LLM, LLMError
from .models import ApplicationPackage, JobPosting, Profile, Score

NO_FABRICATION = """
ABSOLUTE RULE — NON-NEGOTIABLE:
Every employer, job title, date, degree, certification, tool, and number you
write must already appear in the candidate profile provided. You may:
  - reorder and reprioritise content
  - rewrite bullets to lead with the outcome and mirror the posting's vocabulary
  - drop content that is irrelevant to this role
  - use a posting's exact term when it is a true synonym of a profile term
You may NOT:
  - add an employer, role, date, school, credential, or clearance not in the profile
  - invent, inflate, or "estimate" a metric that is not in the profile
  - claim years of experience with a technology the profile does not show
  - state a work authorization, visa status, or salary that is not in the profile
If the posting demands something the candidate lacks, leave it out. Do not
paper over the gap. The candidate has to defend every line of this in an
interview.
"""

RESUME_SYSTEM = """You are an expert resume writer who gets candidates through \
both automated ATS keyword screens and a human recruiter's 20-second scan.
""" + NO_FABRICATION + """
Output a complete resume in clean Markdown:
  # Full Name
  contact line (email · phone · location · links)
  ## Summary            — 2-3 lines, targeted at THIS role
  ## Skills             — grouped, front-loading the posting's real keywords
  ## Experience         — reverse chronological; per role, 3-5 bullets
  ## Education
Bullets start with a strong verb, carry the outcome, and stay on one line each.
Keep it to one page of content for under 10 years of experience, two at most.
Output only the Markdown resume — no commentary, no fences."""

COVER_SYSTEM = """You write short, specific cover letters that sound like a \
competent human wrote them in ten focused minutes.
""" + NO_FABRICATION + """
Rules: 200-300 words. No "I am writing to apply for". No "passionate about". No
flattery of the company's mission unless you tie it to something concrete the
candidate has actually done. Structure: one line on the specific hook, two short
paragraphs of directly relevant evidence drawn from the profile, one line on why
this company/team in particular, one line close. Address a hiring manager
generically if no name is available. Output only the letter body."""

VERIFY_SYSTEM = """You are a fact-checker comparing a generated resume against a \
candidate's source profile. Find every claim in the resume that is NOT supported \
by the profile: invented employers, roles, dates, schools, credentials, tools, or \
numeric metrics that do not appear in the source.

Return JSON:
{"unsupported": [{"claim": "...", "why": "..."}], "verdict": "clean" | "issues"}
Ignore stylistic rewording of supported facts — only flag substantive additions."""

SCREENING_SYSTEM = """You answer ATS application questions on the candidate's \
behalf, in their voice, truthfully and briefly.
""" + NO_FABRICATION + """
If a question cannot be answered from the profile (salary expectation not given,
visa status unknown, a required disclosure), return the string "NEEDS_USER_INPUT"
as the answer for that question rather than guessing.
Return JSON: {"answers": {"<question>": "<answer>"}}"""


def tailor_resume(llm: LLM, job: JobPosting, profile: Profile, score: Score) -> str:
    prompt = (
        "=== CANDIDATE PROFILE (the only permitted source of facts) ===\n"
        f"{profile.to_prompt_block()}\n\n"
        f"CONTACT: {profile.email} | {profile.phone} | "
        f"{' | '.join(f'{k}: {v}' for k, v in profile.links.items())}\n\n"
        "=== TARGET POSTING ===\n"
        f"{job.to_prompt_block(max_desc=5000)}\n\n"
        "=== SCREENING NOTES ===\n"
        f"Keywords worth mirroring: {', '.join(score.keywords) or 'n/a'}\n"
        f"Known gaps (do not fake these): {', '.join(score.gaps) or 'none'}\n\n"
        "Write the tailored resume now."
    )
    return llm.complete(RESUME_SYSTEM, prompt, max_tokens=2600, temperature=0.3, task="resume")


def write_cover_letter(
    llm: LLM,
    job: JobPosting,
    profile: Profile,
    score: Score,
    language: str = "en",
    level_cap: str | None = None,
) -> tuple[str, dict]:
    """Returns (letter, language_report). The report is never silently discarded."""
    base = (
        "=== CANDIDATE PROFILE ===\n"
        f"{profile.to_prompt_block()}\n\n"
        "=== TARGET POSTING ===\n"
        f"{job.to_prompt_block(max_desc=4000)}\n\n"
        f"Strongest angles identified in screening: {'; '.join(score.reasons) or 'n/a'}\n\n"
        f"{language_instruction(language, level_cap)}\n\n"
        f"Write the cover letter for {profile.name}."
    )

    def _gen(extra: str = "") -> str:
        prompt = base + (f"\n\n=== REVISION REQUIRED ===\n{extra}" if extra else "")
        return llm.complete(COVER_SYSTEM, prompt, max_tokens=900,
                            temperature=0.5, task="cover")

    letter = _gen()
    report: dict = {}
    if language.lower() not in ("en", "english") and level_cap:
        letter, report = enforce_level(llm, letter, level_cap, _gen)
    return letter, report


def verify_resume(llm: LLM, resume_md: str, profile: Profile) -> dict:
    prompt = (
        "=== SOURCE PROFILE ===\n"
        f"{profile.to_prompt_block()}\n\n"
        "=== GENERATED RESUME ===\n"
        f"{resume_md}\n"
    )
    try:
        return llm.complete_json(VERIFY_SYSTEM, prompt, max_tokens=900, task="verify")
    except LLMError as e:
        return {"unsupported": [], "verdict": "unchecked", "error": str(e)}


def answer_screening(
    llm: LLM, job: JobPosting, profile: Profile, questions: list[str]
) -> dict[str, str]:
    if not questions:
        return {}
    prompt = (
        "=== CANDIDATE PROFILE ===\n"
        f"{profile.to_prompt_block()}\n\n"
        f"Standard answers the candidate has pre-approved:\n"
        f"{json.dumps(profile.standard_answers, indent=2)}\n\n"
        "=== POSTING ===\n"
        f"{job.company} — {job.title}\n{job.url}\n\n"
        "=== QUESTIONS ===\n" + "\n".join(f"- {q}" for q in questions)
    )
    try:
        data = llm.complete_json(SCREENING_SYSTEM, prompt, max_tokens=1200, task="screening")
        return {str(k): str(v) for k, v in (data.get("answers") or {}).items()}
    except LLMError:
        return {q: "NEEDS_USER_INPUT" for q in questions}


def build_autofill(profile: Profile, job: JobPosting) -> dict[str, str]:
    """Canonical field map most ATS forms can be driven from."""
    links = profile.links or {}
    first, _, last = profile.name.partition(" ")
    return {
        "first_name": first,
        "last_name": last or first,
        "full_name": profile.name,
        "email": profile.email,
        "phone": profile.phone,
        "location": profile.location,
        "linkedin": links.get("linkedin", ""),
        "github": links.get("github", ""),
        "portfolio": links.get("website", links.get("portfolio", "")),
        "work_authorization": profile.preferences.work_authorization,
        "notice_period": profile.preferences.notice_period,
        "desired_salary": (str(profile.preferences.min_salary)
                           if profile.preferences.min_salary else ""),
        "job_url": job.url,
        "job_title": job.title,
        "company": job.company,
    }


def build_package(
    llm: LLM,
    job: JobPosting,
    profile: Profile,
    score: Score,
    verify: bool = True,
    language: str = "en",
    level_cap: str | None = None,
) -> ApplicationPackage:
    resume = tailor_resume(llm, job, profile, score)
    letter, lang_report = write_cover_letter(
        llm, job, profile, score, language=language, level_cap=level_cap)

    notes: list[str] = []

    # Layer 1: deterministic. String matching, cannot be argued with.
    flags = drift.run_all(resume, profile)
    if flags:
        notes.append(drift.format_flags(flags))

    # Layer 2: model-checked. Catches invented employers, degrees, credentials.
    # It will NOT reliably catch "led" vs "contributed to" — that is layer 1's
    # job, and ultimately yours.
    if verify and not llm.offline:
        check = verify_resume(llm, resume, profile)
        issues = check.get("unsupported") or []
        if issues:
            notes.append("FABRICATION CHECK FAILED — do not send until resolved:\n" +
                         "\n".join(f"  - {i.get('claim','?')} :: {i.get('why','')}"
                                   for i in issues))

    # Layer 3: language level, if writing in something other than English.
    note = language_note(lang_report, language)
    if note:
        notes.append(note)

    return ApplicationPackage(
        job_id=job.id,
        company=job.company,
        title=job.title,
        url=job.url,
        resume_md=resume,
        cover_letter_md=letter,
        screening_answers={},
        autofill=build_autofill(profile, job),
        status="drafted",
        notes="\n\n".join(notes),
    )
